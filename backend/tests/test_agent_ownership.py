# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 분류와 책임 — 필수 항목, 책임자 상태, 정보 완성도, 목록 필터·정렬, 부서별 통계, 저장된 보기, 일괄 변경.

설계: design/ai-agent-ownership-design.md

실행:
    cd backend
    pytest tests/test_agent_ownership.py
"""

from __future__ import annotations

import pytest

from app.core.auth import TokenUser
from app.core.config import settings
from app.usermgr.models import ArgusRole, ArgusUser
from tests.test_agent_manifest import (
    ADMIN,
    ALICE,
    BOB,
    REQUIRED,
    Api,
    _make_factory,
    agent_doc,
    legacy_agent,
)

BASE = "/api/v1/ai-agents"
CAROL = TokenUser(sub="4", username="carol", email="carol@corp.example", department="운영팀",
                  roles=[settings.auth_keycloak_user_role])


@pytest.fixture
async def api():
    a = Api(await _make_factory())
    async with a.factory() as db:
        role = ArgusRole(role_id="argus-user", name="사용자")
        db.add(role)
        await db.flush()
        for username, status, dept in (("alice", "active", "고객지원팀"), ("bob", "active", None),
                                       ("carol", "inactive", None)):
            db.add(ArgusUser(username=username, email=f"{username}@corp.example", first_name=username, last_name="k",
                             password_hash="x", status=status, role_id=role.id, department=dept))
        await db.commit()
    yield a
    await a.client.aclose()


def owned(name: str, owner: str, department: str = "고객지원팀", **spec) -> dict:
    d = agent_doc(name, **spec)
    d["metadata"].update(owner=owner, department=department)
    return d


async def names(api: Api, query: str = "") -> list[str]:
    r = await api.client.get(f"{BASE}{query}")
    assert r.status_code == 200, r.text
    return [a["name"] for a in r.json()["items"]]


# ---------------------------------------------------------------------------
# 필수 항목
# ---------------------------------------------------------------------------


async def test_create_requires_owner_department_category(api):
    r = await api.client.post(BASE, json={"name": "cs.a"})
    assert r.status_code == 422
    assert all(k in r.json()["detail"] for k in ("owner_email", "department", "category"))
    r = await api.client.post(BASE, json={**REQUIRED, "name": "cs.a", "department": "  "})
    assert r.status_code == 422 and "department" in r.json()["detail"]
    r = await api.client.post(BASE, json={**REQUIRED, "name": "cs.a", "owner_email": "alice"})
    assert r.status_code == 422 and "이메일" in r.json()["detail"]
    assert (await api.client.post(BASE, json={**REQUIRED, "name": "cs.a"})).status_code == 200

    # 기존 에이전트 수정은 비어 있는 채로도 된다
    await legacy_agent(api, "cs.old", "admin")
    r = await api.client.patch(f"{BASE}/cs.old", json={"description": "설명만 고침"})
    assert r.status_code == 200, r.text


async def test_numeric_limits_match_manifest(api):
    """최대 단계 1 이상·예산 0 이상 1억 미만 — 화면·API 등록/수정도 정의 파일 반영과 같은 기준(통합 시험 AGT-T22).

    전에는 등록·수정이 검사 없이 음수·0 을 저장해 정책 번들로 내려갔고, 1억 이상 예산은 DB 자릿수 초과로 500 이었다.
    """
    assert (await api.client.post(BASE, json={**REQUIRED, "name": "cs.n", "max_steps": 0})).status_code == 422
    assert (await api.client.post(BASE, json={**REQUIRED, "name": "cs.n", "budget_limit": -1})).status_code == 422
    assert (await api.client.post(BASE, json={**REQUIRED, "name": "cs.n", "max_steps": 1, "budget_limit": 0})).status_code == 200
    for bad in ({"max_steps": -1}, {"max_steps": 0}, {"budget_limit": -5}, {"budget_limit": 1e8}, {"budget_limit": 1e20}):
        r = await api.client.patch(f"{BASE}/cs.n", json=bad)
        assert r.status_code == 422, (bad, r.status_code, r.text)
    r = await api.client.patch(f"{BASE}/cs.n", json={"max_steps": 15, "budget_limit": 99_999_999.5})
    assert r.status_code == 200, r.text
    d = (await api.client.get(f"{BASE}/cs.n")).json()
    assert d["max_steps"] == 15 and d["budget_limit"] == 99_999_999.5
    doc = owned("cs.m", "alice@corp.example")
    doc["spec"]["governance"] = {"budgetLimit": 1e9}
    res = (await api.client.post(f"{BASE}/apply", json={"agents": [doc]})).json()["results"][0]
    assert res["action"] == "failed" and "budgetLimit" in res["error"]


# ---------------------------------------------------------------------------
# 책임자 상태 · 완성도 · 통계
# ---------------------------------------------------------------------------


async def test_owner_states_filter_detail_and_stats(api):
    await api.apply(owned("a.ok", "Alice@corp.example"), owned("a.inactive", "carol@corp.example", "운영팀"),
                    owned("a.unknown", "dave@corp.example", "운영팀"))
    await legacy_agent(api, "a.missing", "admin")

    items = {a["name"]: a for a in (await api.client.get(BASE)).json()["items"]}
    assert {n: a["owner_state"] for n, a in items.items()} == {
        "a.ok": "ok", "a.inactive": "inactive", "a.unknown": "unknown", "a.missing": "missing"}
    assert sorted(await names(api, "?owner_state=attention")) == ["a.inactive", "a.missing", "a.unknown"]
    assert sorted(await names(api, "?owner_state=missing,inactive")) == ["a.inactive", "a.missing"]
    assert (await api.client.get(f"{BASE}?owner_state=bogus")).status_code == 422
    assert (await api.get("/a.unknown")).json()["owner_state"] == "unknown"

    st = (await api.get("/stats")).json()
    assert st["owner_attention_count"] == 3
    by = {r["department"]: r for r in st["by_department"]}
    assert by["운영팀"]["agents"] == 2 and by["운영팀"]["owner_attention"] == 2
    assert by[None]["missing_required"] == 1 and by["고객지원팀"]["missing_required"] == 0


async def test_completeness_score_missing_items_and_filter(api):
    full = owned("cs.full", "alice@corp.example", description="결제 취소와 환불 문의를 처리하고 상담원에게 넘긴다.",
                 inputSchema={"type": "object"}, outputSchema={"type": "object"})
    await api.apply(full, owned("cs.partial", "alice@corp.example"))
    await legacy_agent(api, "cs.empty", "admin")
    r = await api.client.post(f"{BASE}/cs.full/evals", json={"eval_type": "offline", "metric_key": "acc",
                                                             "metric_value": 0.9})
    assert r.status_code == 200, r.text

    items = {a["name"]: a for a in (await api.client.get(BASE)).json()["items"]}
    assert (items["cs.full"]["completeness"], items["cs.full"]["missing_fields"]) == (100, [])
    # agent_doc: 설명 짧음·입출력 형식 없음·평가 없음 → 100 - 15 - 10 - 10
    assert items["cs.partial"]["completeness"] == 65
    assert items["cs.partial"]["missing_fields"] == ["description", "io_schema", "evaluation"]
    # 비어 있는 에이전트는 도구가 없으므로 '도구 위험도'만 채운 것으로 본다
    assert items["cs.empty"]["completeness"] == 10
    assert items["cs.empty"]["max_risk"] is None and items["cs.partial"]["max_risk"] == "high"

    assert await names(api, "?incomplete=true&sort=completeness") == ["cs.empty", "cs.partial"]
    assert await names(api, "?sort=-completeness") == ["cs.full", "cs.partial", "cs.empty"]
    assert (await api.get("/cs.partial")).json()["missing_fields"] == ["description", "io_schema", "evaluation"]
    assert (await api.get("/stats")).json()["incomplete_count"] == 2

    # 위험도 없는 도구가 있으면 '도구 위험도'가 빠진다
    await api.apply(owned("cs.norisk", "alice@corp.example", tools=[{"name": "x.y"}]))
    assert "tool_risk" in (await api.get("/cs.norisk")).json()["missing_fields"]


# ---------------------------------------------------------------------------
# 목록 필터 · 정렬
# ---------------------------------------------------------------------------


async def test_list_filters_and_sorts(api):
    a = owned("cs.a", "alice@corp.example")
    a["metadata"]["tags"] = ["환불", "결제"]
    b = owned("hr.b", "bob@corp.example", "인사팀", tools=[{"name": "t", "risk": "critical"}])
    b["metadata"]["tags"] = ["환불"]
    c = owned("ops.c", "bob@corp.example", "운영팀", tools=[{"name": "t", "risk": "low"}])
    c["metadata"]["tags"] = ["운영"]
    await api.apply(a, b, c)
    await api.as_(ALICE).client.post(BASE, json={**REQUIRED, "name": "ui.d", "department": "운영팀"})
    api.as_(ADMIN)

    assert sorted(await names(api, "?department=인사팀,운영팀")) == ["hr.b", "ops.c", "ui.d"]
    assert sorted(await names(api, "?owner=BOB@corp.example")) == ["hr.b", "ops.c"]
    assert sorted(await names(api, "?tag=환불")) == ["cs.a", "hr.b"]
    assert await names(api, "?tag=환불,결제") == ["cs.a"]
    assert await names(api, "?risk=critical") == ["hr.b"]
    assert sorted(await names(api, "?risk=high")) == ["cs.a", "hr.b"]
    assert (await api.client.get(f"{BASE}?risk=extreme")).status_code == 422
    assert await names(api, "?managed_by=ui") == ["ui.d"]
    assert await names(api, "?sort=name") == ["cs.a", "hr.b", "ops.c", "ui.d"]
    assert await names(api, "?sort=-name") == ["ui.d", "ops.c", "hr.b", "cs.a"]
    assert (await names(api, "?sort=risk"))[:2] == ["hr.b", "cs.a"]
    assert (await api.client.get(f"{BASE}?sort=bogus")).status_code == 422

    # 내 에이전트(owner=me) — 로그인 필요
    api.as_(BOB)
    assert sorted(await names(api, "?owner=me")) == ["hr.b", "ops.c"]
    api.as_(None)
    assert (await api.client.get(f"{BASE}?owner=me")).status_code == 401
    assert len(await names(api)) == 4  # 목록 자체는 비로그인도 조회

    # 페이지: 계산 조건으로 거른 뒤 자른다
    api.as_(ADMIN)
    r = (await api.client.get(f"{BASE}?sort=name&page=2&page_size=3")).json()
    assert r["total"] == 4 and [x["name"] for x in r["items"]] == ["ui.d"]


# ---------------------------------------------------------------------------
# 저장된 보기
# ---------------------------------------------------------------------------


async def test_saved_views_personal_shared_and_permissions(api):
    api.as_(CAROL)
    ids = [v["id"] for v in (await api.client.get(f"{BASE}/views")).json()]
    assert ids == ["builtin:mine", "builtin:department", "builtin:high-risk", "builtin:owner-attention",
                   "builtin:incomplete"]

    # 토큰에 부서가 없으면(로컬 로그인) 사용자 목록의 부서로 '우리 부서' 보기를 만든다 — 통합 시험 AGT-T17 에서 발견
    api.as_(ALICE)
    dept = [v for v in (await api.client.get(f"{BASE}/views")).json() if v["id"] == "builtin:department"]
    assert [(v["name"], v["query"]) for v in dept] == [("우리 부서(고객지원팀)", {"department": "고객지원팀"})]
    api.as_(BOB)
    assert "builtin:department" not in [v["id"] for v in (await api.client.get(f"{BASE}/views")).json()]

    api.as_(ALICE)
    r = await api.client.post(f"{BASE}/views", json={"name": "내 고위험", "query": {"owner": "me", "risk": "high"}})
    assert r.status_code == 201 and r.json()["kind"] == "personal" and r.json()["editable"] is True
    api.as_(BOB)
    bob_personal = (await api.client.post(f"{BASE}/views", json={"name": "밥 전용", "query": {"tag": "x"}})).json()
    bob_shared = (await api.client.post(f"{BASE}/views", json={"name": "공유 보기", "shared": True,
                                                                "query": {"department": "운영팀"}})).json()

    api.as_(ALICE)
    views = (await api.client.get(f"{BASE}/views")).json()
    custom = [(v["name"], v["kind"], v["editable"]) for v in views if v["kind"] != "builtin"]
    assert custom == [("내 고위험", "personal", True), ("공유 보기", "shared", False)]
    body = {"name": "바꿈", "query": {}}
    assert (await api.client.put(f"{BASE}/views/{bob_shared['id']}", json=body)).status_code == 403
    assert (await api.client.put(f"{BASE}/views/{bob_personal['id']}", json=body)).status_code == 404
    assert (await api.client.delete(f"{BASE}/views/{bob_personal['id']}")).status_code == 404

    api.as_(ADMIN)  # 관리자는 공유 보기를 고칠 수 있다
    r = await api.client.put(f"{BASE}/views/{bob_shared['id']}", json={"name": "운영팀 공유", "shared": True,
                                                                       "query": {"department": "운영팀"}})
    assert r.status_code == 200 and r.json()["name"] == "운영팀 공유"

    api.as_(BOB)
    assert (await api.client.delete(f"{BASE}/views/{bob_personal['id']}")).status_code == 204
    assert (await api.client.post(f"{BASE}/views", json={"name": "x", "query": {"colour": "red"}})).status_code == 422
    assert (await api.client.post(f"{BASE}/views", json={"name": "x", "query": {"sort": "bogus"}})).status_code == 422
    api.as_(None)
    assert (await api.client.post(f"{BASE}/views", json={"name": "x", "query": {}})).status_code == 401
    anon = (await api.client.get(f"{BASE}/views")).json()
    assert [v["name"] for v in anon if v["kind"] != "builtin"] == ["운영팀 공유"]


# ---------------------------------------------------------------------------
# 일괄 변경
# ---------------------------------------------------------------------------


async def bulk(api: Api, names_: list[str], dry_run: bool = False, **set_) -> dict:
    r = await api.client.post(f"{BASE}/bulk", json={"names": names_, "set": set_, "dry_run": dry_run})
    assert r.status_code == 200, r.text
    return r.json()


async def test_bulk_update_permissions_history_and_dry_run(api):
    await api.apply(owned("cs.mine", "alice@corp.example"), owned("hr.bobs", "bob@corp.example"))
    await api.as_(ALICE).client.post(BASE, json={**REQUIRED, "name": "ui.mine"})

    res = await bulk(api, ["cs.mine", "ui.mine", "hr.bobs", "no.such"], dry_run=True, status="deprecated",
                     department="결제팀", add_tags=["정리대상"])
    by = {r["name"]: r for r in res["results"]}
    assert res["summary"] == {"updated": 2, "unchanged": 0, "failed": 2}
    assert "권한 없음" in by["hr.bobs"]["error"] and "없습니다" in by["no.such"]["error"]
    assert "status: active → deprecated" in by["cs.mine"]["changes"]
    assert "정의 파일" in by["cs.mine"]["note"] and "note" not in by["ui.mine"]
    assert (await api.get("/cs.mine")).json()["status"] == "active"  # 미리 보기는 저장 안 함

    res = await bulk(api, ["cs.mine", "ui.mine"], status="deprecated", department="결제팀", add_tags=["정리대상"])
    assert res["summary"]["updated"] == 2
    d = (await api.get("/cs.mine")).json()
    assert (d["status"], d["department"], d["updated_by"]) == ("deprecated", "결제팀", "alice")
    assert "정리대상" in d["tags"]
    hist = (await api.get("/cs.mine/status-history")).json()
    assert (hist[0]["to_status"], hist[0]["note"], hist[0]["changed_by"]) == ("deprecated", "일괄 변경", "alice")

    # 같은 값이면 변경 없음, 태그 빼기
    assert (await bulk(api, ["cs.mine"], department="결제팀"))["summary"]["unchanged"] == 1
    res = await bulk(api, ["cs.mine"], remove_tags=["정리대상"])
    assert "정리대상" not in (await api.get("/cs.mine")).json()["tags"] and res["summary"]["updated"] == 1


async def test_bulk_blocked_is_admin_only_and_validation(api):
    await api.apply(owned("cs.a", "alice@corp.example"))
    api.as_(ALICE)
    res = await bulk(api, ["cs.a"], status="blocked")
    assert res["summary"]["failed"] == 1 and "관리자" in res["results"][0]["error"]
    api.as_(ADMIN)
    assert (await bulk(api, ["cs.a"], status="blocked"))["summary"]["updated"] == 1
    api.as_(ALICE)  # 해제도 관리자만
    assert (await bulk(api, ["cs.a"], status="active"))["summary"]["failed"] == 1

    for body in ({"names": ["cs.a"], "set": {}},
                 {"names": ["cs.a"], "set": {"owner": "not-an-email"}},
                 {"names": [], "set": {"department": "x"}},
                 {"names": [f"a{i}" for i in range(201)], "set": {"department": "x"}},
                 {"names": ["cs.a"], "set": {"description": "x"}}):
        assert (await api.client.post(f"{BASE}/bulk", json=body)).status_code == 422, body
    api.as_(None)
    assert (await api.client.post(f"{BASE}/bulk", json={"names": ["cs.a"], "set": {"department": "x"}})
            ).status_code == 401


# ---------------------------------------------------------------------------
# 쓰기 권한 — 정보 수정·삭제·하위 항목은 관리자·등록자·소유자(이메일)
# ---------------------------------------------------------------------------


async def test_owner_by_email_can_edit_agent_and_sub_resources(api):
    """admin 이 등록하고 소유자를 alice 로 지정 → alice 도 수정·하위 항목 변경·삭제 가능, bob 은 403."""
    r = await api.as_(ADMIN).client.post(BASE, json={**REQUIRED, "name": "cs.owned"})   # owner_email=alice
    assert r.status_code == 200, r.text
    a = f"{BASE}/cs.owned"

    api.as_(ALICE)
    assert (await api.client.patch(a, json={"description": "소유자 수정"})).status_code == 200
    tool = await api.client.post(f"{a}/tools", json={"name": "lookup_order"})
    assert tool.status_code == 200, tool.text
    mcp = await api.client.post(f"{a}/mcp-servers", json={"name": "orders-mcp", "url": "http://mcp:8080"})
    assert mcp.status_code == 200, mcp.text
    assert (await api.client.patch(f"{a}/mcp-servers/{mcp.json()['id']}",
                                   json={"allowed_tools": ["get_order"]})).status_code == 200
    line = await api.client.post(f"{a}/lineage", json={"target_ref": "cs.billing"})
    assert line.status_code == 200, line.text
    ver = await api.client.post(f"{a}/versions", json={"version": "1.1.0"})
    assert ver.status_code == 200 and ver.json()["created_by"] == "alice"   # 버전도 만든 사람 기록
    ev = await api.client.post(f"{a}/evals", json={"eval_type": "offline", "metric_key": "accuracy", "metric_value": 0.9})
    assert ev.status_code == 200 and ev.json()["created_by"] == "alice"

    # 등록자·소유자가 아닌 사용자는 모든 쓰기가 403
    api.as_(BOB)
    for method, path, body in (
        ("PATCH", a, {"description": "x"}),
        ("POST", f"{a}/tools", {"name": "t"}),
        ("POST", f"{a}/mcp-servers", {"name": "m"}),
        ("PATCH", f"{a}/mcp-servers/{mcp.json()['id']}", {"allowed_tools": []}),
        ("POST", f"{a}/lineage", {"target_ref": "x"}),
        ("POST", f"{a}/versions", {"version": "9.9.9"}),
        ("POST", f"{a}/evals", {"eval_type": "o", "metric_key": "k", "metric_value": 1}),
        ("DELETE", f"{a}/tools/{tool.json()['id']}", None),
        ("DELETE", f"{a}/mcp-servers/{mcp.json()['id']}", None),
        ("DELETE", f"{a}/lineage/{line.json()['id']}", None),
        ("DELETE", a, None),
    ):
        r = await api.client.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert "등록자·소유자 또는 관리자" in r.json()["detail"]

    # 없는 에이전트는 권한보다 먼저 404
    assert (await api.client.post(f"{BASE}/cs.none/tools", json={"name": "t"})).status_code == 404

    api.as_(ALICE)
    assert (await api.client.delete(f"{a}/tools/{tool.json()['id']}")).status_code == 200
    assert (await api.client.delete(f"{a}/mcp-servers/{mcp.json()['id']}")).status_code == 200
    assert (await api.client.delete(f"{a}/lineage/{line.json()['id']}")).status_code == 200
    assert (await api.client.delete(a)).status_code == 200


async def test_creator_can_edit_but_only_admin_blocks(api):
    """등록자는 소유자를 남으로 바꿔도 계속 수정 가능. blocked 설정·해제는 관리자만(일괄 처리와 같은 규칙)."""
    r = await api.as_(BOB).client.post(BASE, json={**REQUIRED, "name": "cs.bobs"})      # 등록자 bob, 소유자 alice
    assert r.status_code == 200, r.text
    a = f"{BASE}/cs.bobs"
    assert (await api.client.post(f"{a}/tools", json={"name": "t"})).status_code == 200
    r = await api.client.patch(a, json={"status": "blocked"})
    assert r.status_code == 403 and "관리자만" in r.json()["detail"]
    assert (await api.as_(ADMIN).client.patch(a, json={"status": "blocked"})).status_code == 200
    assert (await api.as_(ALICE).client.patch(a, json={"status": "active"})).status_code == 403
    assert (await api.as_(ALICE).client.patch(a, json={"description": "차단 중 설명 수정"})).status_code == 200
    # 소유자 이메일은 대소문자를 가리지 않는다
    upper = TokenUser(sub="9", username="alice2", email="ALICE@corp.example", roles=[settings.auth_keycloak_user_role])
    assert (await api.as_(upper).client.patch(a, json={"description": "대문자 이메일"})).status_code == 200
