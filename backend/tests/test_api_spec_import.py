# SPDX-License-Identifier: Apache-2.0
"""스펙 가져오기 형식 검사 — AsyncAPI 는 가져오기에서 거부하고, 저장된 스펙 재해석은 그대로 허용.

실행:
    cd backend
    pytest tests/test_api_spec_import.py
"""

from __future__ import annotations

import json

import pytest

from app.apis import openapi_parser as parser

ASYNCAPI_JSON = json.dumps({
    "asyncapi": "2.6.0", "info": {"title": "Ev", "version": "1"},
    "channels": {"orders": {"subscribe": {"message": {"payload": {"type": "object"}}}}},
})
ASYNCAPI_YAML = "asyncapi: 3.0.0\ninfo:\n  title: Ev\n  version: '1'\nchannels: {}\n"
OPENAPI = json.dumps({
    "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
    "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
})
SWAGGER_YAML = "swagger: '2.0'\ninfo: {title: S, version: '1'}\npaths:\n  /a:\n    get:\n      responses: {'200': {description: ok}}\n"


@pytest.mark.parametrize("text", [ASYNCAPI_JSON, ASYNCAPI_YAML])
def test_asyncapi_rejected_on_import(text):
    with pytest.raises(parser.SpecParseError, match="AsyncAPI 스펙은 가져오기"):
        parser.parse(text, for_import=True)


def test_asyncapi_still_readable_when_not_importing():
    # 예전에 저장된 AsyncAPI 스펙(버전 비교 등)은 계속 읽을 수 있어야 한다
    parsed = parser.parse(ASYNCAPI_JSON)
    assert parsed["format"] == "asyncapi" and parsed["endpoints"] == []


@pytest.mark.parametrize("text,fmt", [(OPENAPI, "openapi3"), (SWAGGER_YAML, "openapi2")])
def test_openapi_import_ok(text, fmt):
    parsed = parser.parse(text, for_import=True)
    assert parsed["format"] == fmt and len(parsed["endpoints"]) == 1


def test_unknown_rejected():
    with pytest.raises(parser.SpecParseError, match="지원하지 않는 스펙"):
        parser.parse('{"foo": 1}', for_import=True)
