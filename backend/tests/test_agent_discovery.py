# SPDX-License-Identifier: Apache-2.0
"""미등록 에이전트 발견(``/ai-agents/discoveries``) 단위 테스트.

실행 환경이 카탈로그에 없는 이름으로 호출 기록·정책 판정 기록을 보내면 404 를 돌려주면서 발견 기록에 모이는지,
목록·등록·무시·되돌리기와 자동 '등록됨' 처리가 맞는지 확인한다.
설계: design/ai-agent-bulk-registration-design.md §5

실행:
    cd backend
    pytest tests/test_agent_discovery.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents import discovery
from app.agents.models import AIAgent
from app.core.auth import TokenUser
from app.core.config import settings
from tests.test_agent_manifest import ADMIN, ALICE, BOB, REQUIRED, Api, _make_factory, agent_doc

BASE = "/api/v1/ai-agents"
# 발견 기록으로 새로 만들 때 필요한 값 — 소유자 이메일은 비우면 요청자 이메일
REG = {"department": "고객지원팀", "category": "customer-service"}


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    yield a
    await a.client.aclose()


async def invoke(api, name: str, consumer: str = "web-chat"):
    return await api.client.post(f"{BASE}/{name}/invocations", json={"status": "success", "consumer": consumer})


async def hook(api, name: str):
    return await api.client.post(f"{BASE}/{name}/hook-events", json={"stage": "pre_exec", "decision": "allow"})


async def discoveries(api, **params) -> dict:
    r = await api.client.get(f"{BASE}/discoveries", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def only(api, name: str) -> dict:
    items = [d for d in (await discoveries(api, status=""))["items"] if d["name"] == name]
    assert len(items) == 1, items
    return items[0]


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------


async def test_unknown_agent_reports_are_recorded_and_still_404(api):
    api.as_(ADMIN)
    assert (await invoke(api, "cs.shadow", consumer="mobile")).status_code == 404
    assert (await invoke(api, "cs.shadow")).status_code == 404
    assert (await hook(api, "cs.shadow")).status_code == 404

    d = await only(api, "cs.shadow")
    assert d["status"] == "new" and d["seen_count"] == 3
    assert d["invocation_count"] == 2 and d["hook_event_count"] == 1
    assert d["reported_by"] == "admin" and d["last_consumer"] == "web-chat"
    assert d["first_seen_at"] <= d["last_seen_at"]
    stats = (await api.client.get(f"{BASE}/stats")).json()
    assert stats["unregistered_agent_count"] == 1


async def test_registered_agents_and_bad_names_are_not_recorded(api):
    api.as_(ALICE)
    await api.client.post(BASE, json={**REQUIRED, "name": "cs.known"})
    assert (await invoke(api, "cs.known")).status_code == 200
    assert (await invoke(api, "-bad")).status_code == 404
    assert (await invoke(api, "x" * 256)).status_code == 404
    assert (await discoveries(api, status=""))["items"] == []


async def test_anonymous_reports_are_rejected_before_recording(api):
    assert (await invoke(api.as_(None), "cs.anon")).status_code == 401
    assert (await discoveries(api.as_(ADMIN), status=""))["total"] == 0


async def test_new_name_cap_per_day(api, monkeypatch):
    monkeypatch.setattr(discovery, "MAX_NEW_PER_DAY", 2)
    api.as_(ADMIN)
    for n in ("a.one", "a.two", "a.three"):
        await invoke(api, n)
    await invoke(api, "a.one")  # 이미 있는 이름은 상한과 무관하게 갱신
    rows = {d["name"]: d["seen_count"] for d in (await discoveries(api, status=""))["items"]}
    assert rows == {"a.one": 2, "a.two": 1}


# ---------------------------------------------------------------------------
# 목록
# ---------------------------------------------------------------------------


async def test_list_requires_login_filters_and_orders_by_last_seen(api):
    api.as_(ADMIN)
    for n in ("cs.first", "hr.second", "cs.third"):
        await invoke(api, n)
    await invoke(api, "cs.first")  # 가장 최근
    assert [d["name"] for d in (await discoveries(api))["items"]] == ["cs.first", "cs.third", "hr.second"]
    assert [d["name"] for d in (await discoveries(api, search="cs."))["items"]] == ["cs.first", "cs.third"]
    page = await discoveries(api, page_size=1, page=2)
    assert page["total"] == 3 and [d["name"] for d in page["items"]] == ["cs.third"]
    assert (await api.client.get(f"{BASE}/discoveries", params={"status": "bogus"})).status_code == 400
    assert (await api.as_(None).client.get(f"{BASE}/discoveries")).status_code == 401


# ---------------------------------------------------------------------------
# 등록 · 자동 등록됨 처리
# ---------------------------------------------------------------------------


async def test_register_creates_draft_owned_by_requester(api):
    await invoke(api.as_(ADMIN), "cs.shadow")
    did = (await only(api, "cs.shadow"))["id"]

    # 화면 등록과 같은 필수 항목 — 부서·분류가 없으면 422, 기록은 그대로 '처리 전'
    r = await api.as_(ALICE).client.post(f"{BASE}/discoveries/{did}/register")
    assert r.status_code == 422 and "부서(department)" in r.json()["detail"] and "분류(category)" in r.json()["detail"]
    r = await api.client.post(f"{BASE}/discoveries/{did}/register", json={**REG, "owner_email": "alice"})
    assert r.status_code == 422 and "이메일" in r.json()["detail"]
    assert (await only(api, "cs.shadow"))["status"] == "new"
    assert (await api.client.get(f"{BASE}/cs.shadow")).status_code == 404

    r = await api.client.post(f"{BASE}/discoveries/{did}/register", json=REG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["discovery"]["status"] == "registered" and body["discovery"]["agent_name"] == "cs.shadow"
    assert body["discovery"]["status_changed_by"] == "alice"
    agent = (await api.client.get(f"{BASE}/cs.shadow")).json()
    assert agent["status"] == "draft" and agent["owner_email"] == "alice@corp.example"
    assert agent["created_by"] == "alice" and agent["managed_by"] == "discovered"
    assert agent["department"] == "고객지원팀" and agent["category"] == "customer-service"
    assert "1회 기록" in agent["description"]

    assert (await api.client.post(f"{BASE}/discoveries/{did}/register", json=REG)).status_code == 409
    assert (await api.client.post(f"{BASE}/discoveries/99999/register", json=REG)).status_code == 404
    # 등록 후에는 정상 수집되고, 미등록 수에서 빠진다
    assert (await invoke(api, "cs.shadow")).status_code == 200
    assert (await only(api, "cs.shadow"))["seen_count"] == 1
    assert (await api.client.get(f"{BASE}/stats")).json()["unregistered_agent_count"] == 0
    # 등록한 사람이 소유자이므로 정의 파일로 이어서 관리할 수 있다
    doc = agent_doc("cs.shadow")
    assert (await api.apply(doc)).json()["results"][0]["action"] == "updated"


async def test_creating_agent_elsewhere_marks_discovery_registered(api):
    api.as_(ALICE)
    await invoke(api, "cs.ui")
    await invoke(api, "cs.yaml")
    assert (await api.client.post(BASE, json={**REQUIRED, "name": "cs.ui"})).status_code == 200
    assert (await api.apply(agent_doc("cs.yaml"))).json()["summary"]["created"] == 1
    for n in ("cs.ui", "cs.yaml"):
        d = await only(api, n)
        assert d["status"] == "registered" and d["agent_name"] == n and d["status_changed_by"] == "alice"
    assert (await discoveries(api))["total"] == 0


async def test_register_links_existing_agent_with_same_name(api):
    await invoke(api.as_(ADMIN), "cs.race")
    did = (await only(api, "cs.race"))["id"]
    async with api.factory() as db:  # 발견 처리를 거치지 않고 같은 이름이 생긴 경우(동시 생성 등)
        db.add(AIAgent(name="cs.race", urn="cs.race.agent", status="active", version="1.0.0", created_by="bob",
                       managed_by="ui"))
        await db.commit()
    r = await api.as_(ALICE).client.post(f"{BASE}/discoveries/{did}/register")
    assert r.status_code == 200 and r.json()["agent"]["status"] == "active"
    async with api.factory() as db:
        assert len((await db.execute(select(AIAgent).where(AIAgent.name == "cs.race"))).scalars().all()) == 1


async def test_deleted_agent_reappearing_goes_back_to_new(api):
    api.as_(ALICE)
    await invoke(api, "cs.gone")
    did = (await only(api, "cs.gone"))["id"]
    assert (await api.client.post(f"{BASE}/discoveries/{did}/register", json=REG)).status_code == 200
    assert (await api.client.delete(f"{BASE}/cs.gone")).status_code == 200
    assert (await invoke(api, "cs.gone")).status_code == 404
    d = await only(api, "cs.gone")
    assert d["status"] == "new" and d["agent_id"] is None and d["seen_count"] == 2
    assert (await api.client.post(f"{BASE}/discoveries/{did}/register", json=REG)).status_code == 200


# ---------------------------------------------------------------------------
# 무시 · 되돌리기
# ---------------------------------------------------------------------------


async def test_ignore_and_reopen_are_admin_only(api):
    await invoke(api.as_(ADMIN), "cs.noise")
    did = (await only(api, "cs.noise"))["id"]
    assert (await api.as_(BOB).client.post(f"{BASE}/discoveries/{did}/ignore")).status_code == 403

    r = await api.as_(ADMIN).client.post(f"{BASE}/discoveries/{did}/ignore")
    assert r.status_code == 200 and r.json()["status"] == "ignored" and r.json()["status_changed_by"] == "admin"
    assert (await discoveries(api))["total"] == 0
    assert [d["name"] for d in (await discoveries(api, status="ignored"))["items"]] == ["cs.noise"]
    await invoke(api, "cs.noise")  # 다시 들어와도 무시 유지, 횟수만 증가
    d = await only(api, "cs.noise")
    assert d["status"] == "ignored" and d["seen_count"] == 2
    assert (await api.client.get(f"{BASE}/stats")).json()["unregistered_agent_count"] == 0

    assert (await api.as_(BOB).client.post(f"{BASE}/discoveries/{did}/reopen")).status_code == 403
    assert (await api.as_(ADMIN).client.post(f"{BASE}/discoveries/{did}/reopen")).json()["status"] == "new"
    assert (await api.client.post(f"{BASE}/discoveries/{did}/register", json=REG)).status_code == 200
    assert (await api.client.post(f"{BASE}/discoveries/{did}/ignore")).status_code == 409
    assert (await api.client.post(f"{BASE}/discoveries/99999/ignore")).status_code == 404


async def test_register_fills_owner_and_department_from_requester(api):
    """소유자·부서는 비우면 요청자 값(토큰의 부서) — 분류만 넣으면 된다. 연결만 할 때는 필수 항목을 묻지 않는다."""
    carol = TokenUser(sub="7", username="carol", email="carol@corp.example", department="운영팀",
                      roles=[settings.auth_keycloak_user_role])
    await invoke(api.as_(ADMIN), "ops.helper")
    did = (await only(api, "ops.helper"))["id"]
    r = await api.as_(carol).client.post(f"{BASE}/discoveries/{did}/register", json={"category": "operations"})
    assert r.status_code == 200, r.text
    a = (await api.client.get(f"{BASE}/ops.helper")).json()
    assert (a["owner_email"], a["department"], a["category"]) == ("carol@corp.example", "운영팀", "operations")
    # 넣은 값이 요청자 값보다 우선
    await invoke(api.as_(ADMIN), "ops.other")
    did = (await only(api, "ops.other"))["id"]
    r = await api.as_(carol).client.post(f"{BASE}/discoveries/{did}/register",
                                         json={"owner_email": "bob@corp.example", "department": "보안팀", "category": "x"})
    a = (await api.client.get(f"{BASE}/ops.other")).json()
    assert r.status_code == 200 and (a["owner_email"], a["department"]) == ("bob@corp.example", "보안팀")
