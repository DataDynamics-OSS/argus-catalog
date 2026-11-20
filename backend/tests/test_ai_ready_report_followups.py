# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 — env 는 test_ai_ready_check 의 픽스처
"""오류 신고 후속 (TODO-151 · TODO-096 알림 후속). 픽스처는 test_ai_ready_check 와 같다.

- REPORT_CLOSED: 해결 · 소명하면 신고한 사람(+ 구독자)에게 알림, 데이터셋 소유자는 받지 않음. 처리 결과만 고치면 다시 안 함.
- GET /comments/reports: ``mine=owned``(소유자 목록 · 생성자) · ``mine=reported``(내가 낸 신고), 줄마다 ``can_handle``.
- GET /comments?focus=: 그 댓글(답글이면 최상위)이 있는 쪽.
- 주기 점검 잠금: 잠금을 못 잡으면 그 회차를 건너뜀, 잡으면 풀어 줌. SQLite 는 잠금 없이 돈다.

실행:
    cd backend
    pytest tests/test_ai_ready_report_followups.py
"""

from __future__ import annotations

import asyncio
import json

from app.catalog import ai_ready_alerts
from tests.test_ai_ready_alerts import _alerts, _recipients, _setup
from tests.test_ai_ready_check import V1, _tu, env  # noqa: F401 — env 는 픽스처
from tests.test_ai_ready_feedback import _post
from tests.test_ai_ready_owner_reports import _add_owner


def test_report_closed_notifies_reporter(env) -> None:
    c, who, ids, run = env
    _setup(run, ("REPORT_CLOSED", "ALL", {}), ("REPORT_CLOSED", "ALL", {"categories": ["suggestion"]}))
    who["user"] = _tu("mallory")
    bug = _post(c, ids["bare"], "bug")
    sug = _post(c, ids["bare"], "suggestion")
    url = f"{V1}/comments/{bug['id']}/status"

    who["user"] = _tu("alice")  # 생성자
    assert c.put(url, json={"status": "IN_PROGRESS"}).status_code == 200
    assert _alerts(run, "REPORT_CLOSED") == [], "처리 중은 알리지 않음"
    assert c.put(url, json={"status": "RESOLVED", "resolution": "2025-11 적재분에서 고침"}).status_code == 200
    (a,) = _alerts(run, "REPORT_CLOSED")
    assert a.severity == "INFO" and a.source_dataset_id == ids["bare"]
    assert a.change_summary.startswith("오류 신고 해결: db.bare — #") and "→ 2025-11 적재분에서 고침" in a.change_summary
    d = json.loads(a.change_detail)
    assert d["comment_id"] == bug["id"] and d["status"] == "RESOLVED" and d["handled_by"] == "alice"
    assert _recipients(run, a.id) == {"ops", "mallory"}, "구독자 + 신고자, 데이터셋 소유자(alice)는 빼고"

    assert c.put(url, json={"status": "RESOLVED", "resolution": "문구 고침"}).status_code == 200
    assert len(_alerts(run, "REPORT_CLOSED")) == 1, "처리 결과만 고치면 다시 알리지 않음"
    assert c.put(url, json={"status": "REJECTED", "resolution": "의도된 값"}).status_code == 200
    assert len(_alerts(run, "REPORT_CLOSED")) == 2, "해결 → 소명은 새 결과"

    assert c.put(f"{V1}/comments/{sug['id']}/status", json={"status": "REJECTED", "resolution": "범위 밖"}).status_code == 200
    closed = [x for x in _alerts(run, "REPORT_CLOSED") if json.loads(x.change_detail)["comment_id"] == sug["id"]]
    assert len(closed) == 2, "개선 요청은 두 규칙 모두"
    assert closed[0].change_summary.startswith("개선 요청 소명:")


def test_reports_mine_filters_and_can_handle(env) -> None:
    c, who, ids, run = env
    _add_owner(run, ids["bare"], "밥 (bob)")  # bob 은 bare 의 소유자 목록 · other 의 생성자, alice 는 bare 의 생성자
    who["user"] = _tu("mallory")
    m1 = _post(c, ids["bare"], "bug")
    who["user"] = _tu("bob")
    b1 = _post(c, ids["bare"], "suggestion")
    b2 = _post(c, ids["other"], "bug")

    def ids_of(**params):
        r = c.get(f"{V1}/comments/reports", params={"state": "all", **params})
        assert r.status_code == 200, r.text
        return {i["id"]: i["can_handle"] for i in r.json()["items"]}, r.json()["counts"]["all"]

    who["user"] = _tu("bob")
    assert ids_of(mine="owned") == ({m1["id"]: True, b1["id"]: True, b2["id"]: True}, 3), "소유자 목록 + 생성자"
    assert ids_of(mine="reported") == ({b1["id"]: True, b2["id"]: True}, 2)
    assert ids_of(mine="owned", dataset_id=ids["other"]) == ({b2["id"]: True}, 1), "데이터셋 조건과 함께면 교집합"

    who["user"] = _tu("alice")
    assert ids_of(mine="owned") == ({m1["id"]: True, b1["id"]: True}, 2)
    assert ids_of()[0] == {m1["id"]: True, b1["id"]: True, b2["id"]: False}

    who["user"] = _tu("mallory")
    assert ids_of(mine="reported") == ({m1["id"]: False}, 1)
    assert ids_of(mine="owned") == ({}, 0)
    who["user"] = _tu("root", admin=True)
    assert set(ids_of()[0].values()) == {True}, "관리자는 모두 처리"

    who["user"] = None
    assert c.get(f"{V1}/comments/reports", params={"mine": "owned"}).status_code == 401
    assert c.get(f"{V1}/comments/reports", params={"mine": "nope"}).status_code == 422
    assert set(i["can_handle"] for i in c.get(f"{V1}/comments/reports", params={"state": "all"}).json()["items"]) == {False}


def test_comment_focus_page(env) -> None:
    c, _who, ids, _run = env
    posted = [_post(c, ids["bare"], "general") for _ in range(7)]  # 최근 순: 6,5,4,3,2 | 1,0
    reply = _post(c, ids["bare"], "general", parent_id=posted[0]["id"])

    def page(focus):
        r = c.get(f"{V1}/comments", params={"entity_type": "dataset", "entity_id": str(ids["bare"]),
                                            "page_size": 5, "focus": focus})
        assert r.status_code == 200, r.text
        return r.json()

    body = page(posted[0]["id"])
    assert body["page"] == 2 and posted[0]["id"] in [x["id"] for x in body["items"]]
    assert page(reply["id"])["page"] == 2, "답글은 최상위 댓글의 쪽"
    assert page(posted[6]["id"])["page"] == 1
    assert page(999999)["page"] == 1, "없는 댓글이면 요청한 쪽 그대로"


class _FakeConn:
    def __init__(self, got):
        self.got, self.sql = got, []

    async def scalar(self, stmt, params=None):
        self.sql.append(str(stmt))
        return self.got

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))


class _FakeEngine:
    def __init__(self, dialect: str, got):
        self.dialect = type("D", (), {"name": dialect})()
        self.conn = _FakeConn(got)

    def connect(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


def test_run_lock_skips_when_another_process_runs(monkeypatch) -> None:
    calls = []

    async def fake_checks(session):
        calls.append(session)
        return {"unmet": {}, "due": {}}

    class _Session:
        async def __aenter__(self):
            return "session"

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ai_ready_alerts, "run_checks", fake_checks)

    busy = _FakeEngine("postgresql", False)
    assert asyncio.run(ai_ready_alerts.run_checks_once(busy, _Session)) is None and calls == []
    assert busy.conn.sql == ["SELECT pg_try_advisory_lock(:k)"], "못 잡으면 풀지도 않음"

    free = _FakeEngine("postgresql", True)
    assert asyncio.run(ai_ready_alerts.run_checks_once(free, _Session)) == {"unmet": {}, "due": {}}
    assert calls == ["session"] and free.conn.sql[-1] == "SELECT pg_advisory_unlock(:k)"

    maria = _FakeEngine("mariadb", 1)
    asyncio.run(ai_ready_alerts.run_checks_once(maria, _Session))
    assert maria.conn.sql == ["SELECT GET_LOCK(:n, 0)", "SELECT RELEASE_LOCK(:n)"]

    lite = _FakeEngine("sqlite", None)
    asyncio.run(ai_ready_alerts.run_checks_once(lite, _Session))
    assert lite.conn.sql == [] and len(calls) == 3, "SQLite 는 잠금 없이"
