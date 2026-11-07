# SPDX-License-Identifier: Apache-2.0
"""09-federation 통합 테스트(2026-11-07) 결함 회귀.

- FED-T09: peer export 상세(LIVE 드릴다운)는 외부 API 메타데이터 캐시를 쓰지 않는다.
- FED-T18: 가져오기 실패 사유는 httpx 원문(전체 URL·MDN 링크) 대신 상태 코드·조치 한 줄.

외부 API 캐시(``metadata:{urn}``, TTL 300초)는 데이터셋 수정 시 무효화되지 않으므로, export 상세가
캐시를 쓰면 peer 에서 설명을 고쳐도 허브 드릴다운에 최대 TTL 동안 옛 값이 보였다(미러·검색은 새 값).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_federation_it09_fixes.py
"""

from __future__ import annotations

import asyncio

import httpx

import app.external.service as ext
from app.federation import export_router, visibility
from app.federation.harvester import describe_harvest_error


def test_export_dataset_detail_bypasses_metadata_cache(monkeypatch):
    calls: list[dict] = []

    async def fake_get_metadata(session, identifier, no_cache=False):
        calls.append({"identifier": identifier, "no_cache": no_cache})
        return {"dataset_id": 1, "urn": identifier, "status": "active", "description": "최신"}

    async def exportable(session, dataset_id):
        return True

    async def exposed(session):
        return ["description"]

    async def passthrough(session, metadata, exposed_fields):
        return metadata

    monkeypatch.setattr(ext, "get_dataset_metadata", fake_get_metadata)
    monkeypatch.setattr(visibility, "is_dataset_exportable", exportable)
    monkeypatch.setattr(export_router.service, "get_effective_exposed_fields", exposed)
    monkeypatch.setattr(export_router.service, "augment_and_filter_detail", passthrough)

    out = asyncio.run(export_router.export_dataset_detail(urn="pg.t.dataset", session=None))

    assert out["description"] == "최신"
    assert calls == [{"identifier": "pg.t.dataset", "no_cache": True}]


def _status_error(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://peer:4600/api/v1/federation/export/datasets?limit=200")
    resp = httpx.Response(code, json=body or {}, request=req)
    return httpx.HTTPStatusError(f"Client error '{code}' for url '{req.url}'", request=req, response=resp)


def test_describe_harvest_error_auth_failure_is_short_and_actionable():
    msg = describe_harvest_error(_status_error(401))
    assert msg == "peer 인증 실패(HTTP 401) — 서비스 토큰을 확인하세요"
    assert "http" not in msg  # URL·MDN 링크 없음


def test_describe_harvest_error_other_status_keeps_peer_detail():
    assert describe_harvest_error(_status_error(500, {"detail": "boom"})) == "peer 응답 오류(HTTP 500): boom"
    assert describe_harvest_error(_status_error(502)) == "peer 응답 오류(HTTP 502)"


def test_describe_harvest_error_connection_and_generic():
    req = httpx.Request("GET", "http://peer:4600/x")
    assert describe_harvest_error(httpx.ConnectError("Name or service not known", request=req)) == \
        "peer 연결 실패: Name or service not known"
    assert describe_harvest_error(httpx.ReadTimeout("t", request=req)) == "peer 응답 시간 초과"
    assert describe_harvest_error(ValueError("bad page")) == "bad page"
