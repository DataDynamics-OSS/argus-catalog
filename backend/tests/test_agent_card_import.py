# SPDX-License-Identifier: Apache-2.0
"""외부 에이전트 소개 정보 읽어 들이기(``POST /ai-agents/import/card``) 단위 테스트.

외부 주소 호출은 가짜 응답(``httpx.MockTransport``)으로 바꾸고, 주소 검사(``app.core.outbound``)는 실제 규칙을 쓴다
(DNS 조회가 필요 없도록 IP 주소를 쓴다).
설계: design/ai-agent-bulk-registration-design.md §5.3

실행:
    cd backend
    pytest tests/test_agent_card_import.py
"""

from __future__ import annotations

import json

import httpx
import pytest
import yaml

from app.agents import card_import
from tests.test_agent_manifest import Api, _make_factory, agent_doc

HOST = "http://203.0.113.10"

A2A_CARD = {
    "name": "Refund Helper",
    "description": "환불 문의를 처리한다.",
    "url": "https://203.0.113.10/a2a/refund",
    "version": "2.1.0",
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "provider": {"organization": "고객지원팀", "url": "https://corp.example"},
    "capabilities": {"streaming": True, "pushNotifications": False},
    "securitySchemes": {"bearer": {"type": "http", "scheme": "Bearer"}},
    "defaultInputModes": ["text/plain"],
    "skills": [
        {"id": "refund-check", "name": "환불 가능 여부 판단", "tags": ["환불", "결제"]},
        {"id": "handoff", "name": "상담원 연결", "tags": ["환불"]},
    ],
}


@pytest.fixture
async def api(monkeypatch):
    a = Api(await _make_factory())
    a.routes = {}  # 주소 → (상태, 본문, 헤더)
    a.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        a.calls.append(url)
        status, body, headers = a.routes.get(url, (404, b"not found", {}))
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        return httpx.Response(status, content=body, headers=headers)

    monkeypatch.setattr(card_import, "_transport", lambda: httpx.MockTransport(handler))
    yield a
    await a.client.aclose()


async def card(api: Api, url: str, **extra) -> httpx.Response:
    return await api.client.post("/api/v1/ai-agents/import/card", json={"url": url, **extra})


# ---------------------------------------------------------------------------
# 정상 흐름
# ---------------------------------------------------------------------------


async def test_a2a_card_to_preview_then_apply(api):
    api.routes[f"{HOST}/a2a/refund/.well-known/agent-card.json"] = (200, A2A_CARD, {})
    r = await card(api, f"{HOST}/a2a/refund")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_url"] == f"{HOST}/a2a/refund/.well-known/agent-card.json"
    assert body["name"] == "refund-helper" and body["exists"] is False
    assert body["missing"] == ["owner", "category"]  # 부서는 provider.organization 에서
    doc = body["document"]
    assert doc["metadata"] == {"name": "refund-helper", "displayName": "Refund Helper", "owner": None,
                               "department": "고객지원팀", "category": None, "tags": ["환불", "결제"]}
    assert doc["spec"]["version"] == "2.1.0"
    assert doc["spec"]["interface"] == {"endpoint": "https://203.0.113.10/a2a/refund", "protocol": "a2a",
                                        "invocationMethod": "jsonrpc", "streaming": True, "authMethod": "bearer"}
    assert doc["spec"]["capabilities"] == ["환불 가능 여부 판단", "상담원 연결"]
    assert doc["spec"]["release"]["source"] == body["source_url"]
    assert "status" not in doc["spec"]  # 새로 만들면 초안, 있으면 현재 상태 유지
    assert any("refund-helper" in n for n in body["notes"]) and any("기술 2개" in n for n in body["notes"])
    assert yaml.safe_load(body["yaml"]) == doc
    assert (await api.get("/refund-helper")).status_code == 404  # 미리 보기만, 저장하지 않음

    # 사용자가 비어 있는 항목을 채워 반영
    doc["metadata"].update(owner="alice@corp.example", category="customer-service")
    r = await api.apply(doc)
    assert r.json()["summary"]["created"] == 1, r.text
    d = (await api.get("/refund-helper")).json()
    assert d["status"] == "draft" and d["endpoint"] == "https://203.0.113.10/a2a/refund" and d["streaming"] is True


async def test_falls_back_to_agent_json_and_site_root(api):
    api.routes[f"{HOST}/.well-known/agent.json"] = (200, {"name": "ops-bot"}, {})
    r = await card(api, f"{HOST}/some/path/")
    assert r.status_code == 200, r.text
    assert r.json()["source_url"] == f"{HOST}/.well-known/agent.json"
    assert api.calls == [
        f"{HOST}/some/path/.well-known/agent-card.json", f"{HOST}/some/path/.well-known/agent.json",
        f"{HOST}/.well-known/agent-card.json", f"{HOST}/.well-known/agent.json",
    ]


async def test_catalog_card_format_and_overrides(api):
    # 이 카탈로그가 내보낸 카드(GET /ai-agents/{name}/card)를 다른 카탈로그에서 읽어 들이는 경우
    assert (await api.apply(agent_doc("cs.refund"))).json()["summary"]["created"] == 1
    exported = (await api.get("/cs.refund/card")).json()
    api.routes[f"{HOST}/cards/refund.json"] = (200, exported, {})

    r = await card(api, f"{HOST}/cards/refund.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "cs.refund" and body["exists"] is True
    assert "이미 등록되어 있습니다" in body["notes"][0]
    assert body["missing"] == ["category"]  # 소유자는 provider.contact(이메일), 부서는 organization
    m = body["document"]["metadata"]
    assert (m["owner"], m["department"]) == ("alice@corp.example", "고객지원팀")
    assert body["document"]["spec"]["interface"]["endpoint"] == "https://agents.corp.example/refund"

    r = await card(api, f"{HOST}/cards/refund.json", name="imported.refund", owner="bob@corp.example",
                   department="운영팀", category="ops")
    body = r.json()
    assert body["name"] == "imported.refund" and body["exists"] is False and body["missing"] == []
    assert body["document"]["metadata"]["owner"] == "bob@corp.example"


async def test_name_fallback_to_host_when_not_ascii(api):
    api.routes[f"{HOST}/x.json"] = (200, {"name": "환불 도우미"}, {})
    body = (await card(api, f"{HOST}/x.json")).json()
    assert body["name"] == "203.0.113.10"
    assert body["document"]["metadata"]["displayName"] == "환불 도우미"


async def test_redirect_followed_when_allowed(api):
    api.routes[f"{HOST}/a.json"] = (302, b"", {"location": "/b.json"})
    api.routes[f"{HOST}/b.json"] = (200, {"name": "moved"}, {})
    r = await card(api, f"{HOST}/a.json")
    assert r.status_code == 200 and r.json()["source_url"] == f"{HOST}/b.json"


# ---------------------------------------------------------------------------
# 보안 · 오류
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:4600/api/v1",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "ftp://203.0.113.10/card.json",
    "file:///etc/passwd",
])
async def test_blocked_addresses_never_called(api, url):
    r = await card(api, url)
    assert r.status_code == 400, r.text
    assert api.calls == []


async def test_redirect_to_blocked_address_rejected(api):
    api.routes[f"{HOST}/a.json"] = (302, b"", {"location": "http://169.254.169.254/latest/meta-data"})
    r = await card(api, f"{HOST}/a.json")
    assert r.status_code == 400 and "차단" in r.json()["detail"]
    assert api.calls == [f"{HOST}/a.json"]


async def test_upstream_problems_are_502(api):
    api.routes[f"{HOST}/big.json"] = (200, b'{"name":"' + b"x" * (card_import.MAX_BYTES + 10) + b'"}', {})
    api.routes[f"{HOST}/html.json"] = (200, b"<html>login</html>", {})
    api.routes[f"{HOST}/noname.json"] = (200, {"description": "이름 없음"}, {})
    api.routes[f"{HOST}/err.json"] = (500, b"boom", {})
    expected = {"big": "너무 큽니다", "html": "JSON 형식이 아닙니다", "noname": "name 없음", "err": "HTTP 500"}
    for key, msg in expected.items():
        r = await card(api, f"{HOST}/{key}.json")
        assert r.status_code == 502, (key, r.text)
        assert msg in r.json()["detail"], (key, r.text)

    r = await card(api, f"{HOST}/nothing")
    assert r.status_code == 502 and "찾지 못했습니다" in r.json()["detail"]


async def test_redirect_loop_limited(api):
    api.routes[f"{HOST}/loop.json"] = (302, b"", {"location": "/loop.json"})
    r = await card(api, f"{HOST}/loop.json")
    assert r.status_code == 502 and "리다이렉트" in r.json()["detail"]
    assert len(api.calls) == card_import.MAX_REDIRECTS + 1


async def test_login_required(api):
    api.as_(None)
    r = await card(api, f"{HOST}/x.json")
    assert r.status_code == 401
    assert api.calls == []


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def test_candidate_urls():
    assert card_import.candidate_urls("https://a.example/card.JSON") == ["https://a.example/card.JSON"]
    assert card_import.candidate_urls("https://a.example") == [
        "https://a.example/.well-known/agent-card.json", "https://a.example/.well-known/agent.json",
    ]


def test_slugify():
    assert card_import.slugify("  Refund Helper (v2) ") == "refund-helper-v2"
    assert card_import.slugify("cs.Refund_Bot") == "cs.refund_bot"
    assert card_import.slugify("환불") == ""
