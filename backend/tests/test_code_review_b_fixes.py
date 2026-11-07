# SPDX-License-Identifier: Apache-2.0
"""코드 리뷰 결함 수정(B: mcp_servers·apis·settings) 회귀 테스트 (자기완결형, DB 없음).

검증:
- C13: argus-forge 서버의 source_path 에 ``/`` 가 없어도 Forge 정의 조회가 IndexError 없이 None
- C14: mcp.json 가져오기 시 비밀류 인자 값·endpoint 쿼리스트링 값은 ``***`` 로 가려 저장
- C30: 최상위가 객체가 아닌 JSON(``123``·``null``·배열)은 TypeError(500) 가 아니라 SpecParseError(400)
- C31: 수동 API 등록에서 이름 누락은 409 가 아니라 ApiInputError → 400
- C43: LLM 연결 테스트는 전역 제공자를 교체·종료하지 않고, 임시 제공자는 테스트 뒤 닫는다

실행:
    cd backend
    pytest tests/test_code_review_b_fixes.py
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.apis import openapi_parser as parser
from app.apis import router as api_router
from app.apis import service as api_service
from app.apis.schemas import ApiCreate
from app.core.auth import TokenUser
from app.mcp_servers import interop
from app.mcp_servers import service as mcp_service

ADMIN = TokenUser(sub="admin", username="admin", roles=["argus-admin"])


# ---------------------------------------------------------------------------
# C13 — _forge_of 의 source_path 분해
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_path", ["", None, "noslash", "platforms/", "/"])
def test_forge_of_tolerates_malformed_source_path(source_path):
    server = SimpleNamespace(framework="argus-forge", name="zz-unknown-x", target_platform="zz-unknown-y",
                             source_path=source_path)
    assert mcp_service._forge_of(server) is None


def test_platform_dir():
    assert mcp_service._platform_dir("platforms/hive/server.py") == "hive"
    assert mcp_service._platform_dir("platforms") is None
    assert mcp_service._platform_dir("platforms/") is None
    assert mcp_service._platform_dir(None) is None


# ---------------------------------------------------------------------------
# C14 — mcp.json 가져오기 비밀값 마스킹
# ---------------------------------------------------------------------------

def test_mcp_json_masks_secret_args_and_url_query():
    out = interop.parse_mcp_json({"mcpServers": {
        "local": {"command": "npx", "args": [
            "-y", "some-mcp", "--token", "t0p", "--api-key=k3y", "--client_secret", "s3c",
            "--password=pw", "--port", "8080",
        ], "env": {"API_TOKEN": "x"}},
        "remote": {"url": "https://mcp.example.com/mcp?api_key=abc&tenant=t1#frag"},
    }})
    by_name = {o["server"]["name"]: o for o in out}
    args = by_name["local"]["server"]["launch_config"]["args"]
    assert args == ["-y", "some-mcp", "--token", "***", "--api-key=***", "--client_secret", "***",
                    "--password=***", "--port", "8080"]
    joined = " ".join(args)
    for secret in ("t0p", "k3y", "s3c", "pw"):
        assert secret not in joined
    assert by_name["local"]["server"]["launch_config"]["env_keys"] == ["API_TOKEN"]
    endpoint = by_name["remote"]["deployment"]["endpoint"]
    assert endpoint == "https://mcp.example.com/mcp?api_key=***&tenant=***#frag"
    assert "abc" not in endpoint


def test_mcp_json_url_without_query_unchanged():
    out = interop.parse_mcp_json({"mcpServers": {"r": {"url": "https://mcp.example.com/mcp"}}})
    assert out[0]["deployment"]["endpoint"] == "https://mcp.example.com/mcp"


# ---------------------------------------------------------------------------
# C30 — 최상위가 객체가 아닌 JSON
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["123", "null", "[1, 2]", '"str"'])
def test_non_object_json_is_spec_parse_error(text):
    with pytest.raises(parser.SpecParseError, match="최상위가 객체가 아닙니다"):
        parser.load_spec(text)


def test_object_json_still_loads():
    assert parser.load_spec('{"openapi": "3.0.0"}') == {"openapi": "3.0.0"}


# ---------------------------------------------------------------------------
# C31 — 수동 등록 이름 누락은 400
# ---------------------------------------------------------------------------

async def test_manual_api_without_name_is_input_error():
    with pytest.raises(api_service.ApiInputError):
        await api_service.create_api(None, ApiCreate())


async def test_manual_api_without_name_router_400():
    with pytest.raises(HTTPException) as ei:
        await api_router.create_api(ApiCreate(), ADMIN, None, session=None)
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# C43 — LLM 연결 테스트가 전역 제공자를 건드리지 않음
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.closed = False

    async def generate(self, prompt, **kw):
        if self.fail:
            raise RuntimeError("boom")
        return {"text": "OK"}

    async def close(self):
        self.closed = True

    def model_name(self):
        return "fake"


@pytest.mark.parametrize("fail", [False, True])
async def test_llm_test_keeps_global_provider(monkeypatch, fail):
    from app.ai import registry
    from app.settings import router as srouter

    current = _FakeLLM()
    temp = _FakeLLM(fail=fail)
    monkeypatch.setattr(registry, "_current_provider", current)
    monkeypatch.setattr(registry, "create_provider", lambda cfg: temp)

    r = await srouter.test_llm(ADMIN, srouter.LLMConfig(provider="openai", api_key="sk-new"))
    assert r.success is (not fail)
    # 전역 제공자는 그대로·열린 상태, 임시 제공자만 닫힘
    assert await registry.get_provider() is current and not current.closed
    assert temp.closed
