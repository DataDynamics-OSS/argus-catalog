# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 — env 는 test_ai_ready_check 의 픽스처
"""AI-Ready 소유자 기준 · 신고 처리와 알림 · 최종 적재 시각 · 외부 공개 주소 (DEF-138~142). 픽스처는 test_ai_ready_check 와 같다.

- DEF-138: 소유자 목록 사용자("이름 (아이디)" · "아이디" 형식)도 신고 처리 · 배포본 · 수동 판정을 할 수 있고, C30 · 데이터 카드가
  생성자를 소유자로 본다.
- TODO-154: 소유자 목록 사용자도 메타데이터 수정 · 스키마 등 일반 편집을 한다. 데이터셋 삭제와 소유자 추가 · 삭제는
  생성자 · 관리자만(권한 상승 방지).
- DEF-139: 최종 적재 시각이 없으면 미충족 안내가 메타데이터 탭을 가리키고, 데이터셋 PUT 으로 남기면 충족.
- DEF-140: 해결 · 소명하면 접수 · 기한 알림을 바로 시스템이 해결. 신고를 지워도.
- DEF-141: 해결 · 소명된 신고는 분류를 못 바꾼다(400). 처리 중인 신고를 바꿨다 되돌려도 접수 알림은 하나.
- DEF-142: 로그인 사용자가 외부 공개 주소(방식 · 주소)를 읽는다.
- DEF-165: 담당자가 처리 중으로 둔 신고도 분류를 못 바꾼다(400) — 처리자 기록이 지워지지 않게.

실행:
    cd backend
    pytest tests/test_ai_ready_owner_reports.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

import app.catalog.models as cm
from app.catalog import ai_ready_alerts
from app.catalog.dataset_owner import managed_dataset_ids, owner_username
from app.settings.models import CatalogConfiguration
from tests.test_ai_ready_alerts import _alerts, _call, _setup
from tests.test_ai_ready_check import V1, _items, _tu, env  # noqa: F401 — env 는 픽스처
from tests.test_ai_ready_feedback import _post


def _add_owner(run, ds_id: int, name: str):
    async def _go(db):
        db.add(cm.Owner(dataset_id=ds_id, owner_name=name, owner_type="TECHNICAL_OWNER"))
    run(_go)


def test_owner_name_formats() -> None:
    assert owner_username("홍길동 (bob)") == "bob"
    assert owner_username(" bob ") == "bob"
    assert owner_username(None) == ""


def test_owner_list_user_handles_reports_and_ai_ready(env) -> None:
    c, who, ids, run = env
    _add_owner(run, ids["bare"], "홍길동 (bob)")
    _add_owner(run, ids["bare"], "carol")
    assert _call(run, managed_dataset_ids, "bob", {ids["bare"], ids["other"]}) == {ids["bare"], ids["other"]}, \
        "bare 는 소유자 목록, other 는 생성자"
    assert _call(run, managed_dataset_ids, "carol", {ids["bare"], ids["other"]}) == {ids["bare"]}

    who["user"] = _tu("alice")
    bug = _post(c, ids["bare"], "bug")
    status_url = f"{V1}/comments/{bug['id']}/status"

    who["user"] = _tu("mallory")
    assert c.put(status_url, json={"status": "IN_PROGRESS"}).status_code == 403
    assert c.post(f"{V1}/catalog/datasets/{ids['bare']}/distributions",
                  json={"access_url": "https://x.example/a"}).status_code == 403

    for user in ("bob", "carol"):  # 두 저장 형식 모두
        who["user"] = _tu(user)
        r = c.put(status_url, json={"status": "IN_PROGRESS"})
        assert r.status_code == 200 and r.json()["handled_by"] == user, r.text
    r = c.post(f"{V1}/catalog/datasets/{ids['bare']}/distributions", json={"access_url": "https://x.example/a"})
    assert r.status_code == 200, r.text
    assert c.put(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check/C01",
                 json={"status": "PASS", "note": "확인"}).status_code == 200
    assert c.put(f"{V1}/catalog/datasets/{ids['bare']}", json={"description": "x"}).status_code == 200, \
        "메타데이터 수정도 소유자 목록 사용자가 한다(TODO-154)"
    assert c.post(f"{V1}/catalog/datasets/99999/distributions",
                  json={"access_url": "https://x.example/a"}).status_code == 404


def test_owner_list_user_edits_but_cannot_delete_or_manage_owners(env) -> None:
    """TODO-154 — 목록 사용자는 일반 편집, 삭제 · 소유자 추가 · 삭제는 생성자 · 관리자만."""
    c, who, ids, run = env
    _add_owner(run, ids["bare"], "carol")
    url = f"{V1}/catalog/datasets/{ids['bare']}"

    who["user"] = _tu("mallory")
    assert c.put(url, json={"description": "m"}).status_code == 403
    assert c.put(f"{url}/schema", json=[]).status_code == 403

    who["user"] = _tu("carol")
    r = c.put(url, json={"description": "carol 수정", "last_ingested_at": datetime.now(timezone.utc).isoformat()})
    assert r.status_code == 200 and r.json()["description"] == "carol 수정", r.text
    assert c.put(f"{url}/schema", json=[]).status_code == 200
    r = c.post(f"{url}/owners", json={"owner_name": "mallory", "owner_type": "TECHNICAL_OWNER"})
    assert r.status_code == 403 and "생성자 또는 관리자" in r.json()["detail"], r.text
    owners = c.get(url).json()["owners"]
    carol_id = next(o["id"] for o in owners if o["owner_name"] == "carol")
    assert c.delete(f"{url}/owners/{carol_id}").status_code == 403
    assert c.delete(url).status_code == 403
    assert c.delete(f"{V1}/catalog/datasets/99999").status_code == 404

    who["user"] = _tu("alice")  # 생성자
    assert c.post(f"{url}/owners", json={"owner_name": "dave", "owner_type": "TECHNICAL_OWNER"}).status_code == 200
    assert c.delete(f"{url}/owners/{carol_id}").status_code == 200
    who["user"] = _tu("carol")  # 목록에서 빠지면 편집도 못 한다
    assert c.put(url, json={"description": "y"}).status_code == 403


def test_c30_and_data_card_count_creator(env) -> None:
    c, _who, ids, run = env

    async def _contact(db):
        await db.execute(update(cm.Dataset).where(cm.Dataset.id == ids["bare"]).values(contact_email="d@x.kr"))
    run(_contact)

    items = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())
    assert items["C30"]["status"] == "PASS" and "소유자 1명" in items["C30"]["evidence"], "생성자만 있어도 소유자"
    card = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "alice(CREATOR)" in card and "메타데이터 탭의 소유자" not in card

    _add_owner(run, ids["bare"], "앨리스 (alice)")  # 생성자가 목록에도 있으면 한 번만
    items = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())
    assert "소유자 1명" in items["C30"]["evidence"]
    card = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "앨리스 (alice)(TECHNICAL_OWNER)" in card and "CREATOR" not in card


def test_last_ingested_at_from_metadata(env) -> None:
    c, _who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}"
    assert c.put(url, json={"ingestion_frequency": "DAILY"}).status_code == 200

    def freshness():
        body = c.get(f"{url}/ai-ready/check").json()
        return next(x for x in body["min_requirements"]["checks"] if x["key"] == "freshness")

    f = freshness()
    assert f["met"] is False and "메타데이터 탭" in f["detail"] and "최종 적재 시각" in f["detail"]

    now = datetime.now(timezone.utc).replace(microsecond=0)
    r = c.put(url, json={"last_ingested_at": (now - timedelta(hours=2)).isoformat()})
    assert r.status_code == 200 and r.json()["last_ingested_at"]
    assert freshness()["met"] is True
    assert c.put(url, json={"last_ingested_at": None}).json()["last_ingested_at"] is None, "비우고 저장하면 지운다"


def test_resolving_report_resolves_alerts_now(env) -> None:
    c, who, ids, run = env
    _setup(run, ("REPORT_RECEIVED", "ALL", {}), ("REPORT_DUE", "ALL", {"days_before": 30}))
    bug = _post(c, ids["bare"], "bug")
    sug = _post(c, ids["bare"], "suggestion")
    other = _post(c, ids["bare"], "bug")
    assert _call(run, ai_ready_alerts.evaluate_due_alerts)["created"] == 3
    assert len(_alerts(run, "REPORT_RECEIVED")) == 3

    def live_for(comment_id: int) -> list[str]:
        import json
        return [a.alert_type for a in _alerts(run) if a.status != "RESOLVED"
                and json.loads(a.change_detail).get("comment_id") == comment_id]

    who["user"] = _tu("alice")
    assert c.put(f"{V1}/comments/{bug['id']}/status", json={"status": "IN_PROGRESS"}).status_code == 200
    assert sorted(live_for(bug["id"])) == ["REPORT_DUE", "REPORT_RECEIVED"], "처리 중이면 그대로"
    assert c.put(f"{V1}/comments/{bug['id']}/status", json={"status": "RESOLVED", "resolution": "고침"}).status_code == 200
    assert live_for(bug["id"]) == [], "해결하면 접수 · 기한 알림을 바로 해결"
    assert all(a.resolved_by == "system" for a in _alerts(run) if a.status == "RESOLVED")

    assert c.put(f"{V1}/comments/{sug['id']}/status",
                 json={"status": "REJECTED", "resolution": "의도된 값"}).status_code == 200
    assert live_for(sug["id"]) == [], "소명도"
    assert sorted(live_for(other["id"])) == ["REPORT_DUE", "REPORT_RECEIVED"], "다른 신고의 알림은 그대로"

    assert c.delete(f"{V1}/comments/{other['id']}").status_code == 200
    assert live_for(other["id"]) == [], "신고를 지워도"
    assert _call(run, ai_ready_alerts.evaluate_due_alerts) == {"created": 0, "resolved": 0}


def test_handled_report_category_locked(env) -> None:
    c, who, ids, run = env
    _setup(run, ("REPORT_RECEIVED", "ALL", {}))
    bug = _post(c, ids["bare"], "bug")
    url = f"{V1}/comments/{bug['id']}"

    # 처리 전: 바꿨다 되돌려도 접수 알림은 하나, 신고가 아닐 동안 알림은 해결
    r = c.put(url, json={"content": "<p>x</p>", "category": "general"}).json()
    assert r["status"] is None
    assert [a.status for a in _alerts(run, "REPORT_RECEIVED")] == ["RESOLVED"]
    r = c.put(url, json={"content": "<p>x</p>", "category": "suggestion"}).json()
    assert r["status"] == "OPEN"
    assert len(_alerts(run, "REPORT_RECEIVED")) == 1, "다시 접수돼도 접수 알림을 또 만들지 않음"

    # 해결 뒤: 분류 변경은 400, 처리 기록은 그대로. 같은 분류로 본문만 고치는 것은 된다
    assert c.put(f"{url}/status", json={"status": "RESOLVED", "resolution": "반영함"}).status_code == 200
    r = c.put(url, json={"content": "<p>y</p>", "category": "general"})
    assert r.status_code == 400 and "분류를 바꿀 수 없습니다" in r.json()["detail"]
    r = c.put(url, json={"content": "<p>y</p>", "category": "suggestion"})
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["resolution"], body["handled_by"]) == ("RESOLVED", "반영함", "alice")
    assert body["resolved_at"]
    assert c.put(url, json={"content": "<p>z</p>"}).status_code == 200, "분류를 보내지 않으면 본문만"

    # 다시 열면(처리 중) 분류를 바꿀 수 있다
    assert c.put(f"{url}/status", json={"status": "OPEN"}).status_code == 200
    assert c.put(url, json={"content": "<p>z</p>", "category": "bug"}).status_code == 200


def test_in_progress_report_category_locked(env) -> None:
    c, who, ids, _run = env
    who["user"] = _tu("alice")
    bug = _post(c, ids["bare"], "bug")
    url = f"{V1}/comments/{bug['id']}"
    assert c.put(f"{url}/status", json={"status": "IN_PROGRESS"}).status_code == 200

    for category in ("general", "suggestion"):
        r = c.put(url, json={"content": "<p>y</p>", "category": category})
        assert r.status_code == 400 and r.json()["detail"] == \
            "담당자가 처리 중인 신고는 분류를 바꿀 수 없습니다. 새 댓글로 남겨 주세요.", r.text
    r = c.put(url, json={"content": "<p>y</p>", "category": "bug"})
    assert r.status_code == 200, "같은 분류로 본문만 고치는 것은 된다"
    assert (r.json()["status"], r.json()["handled_by"]) == ("IN_PROGRESS", "alice"), "처리자 기록 유지"

    assert c.put(f"{url}/status", json={"status": "OPEN"}).status_code == 200
    r = c.put(url, json={"content": "<p>y</p>", "category": "general"})
    assert r.status_code == 200 and r.json()["status"] is None, "접수 상태로 되돌리면 바꿀 수 있다"


def test_public_base_for_users(env) -> None:
    c, who, _ids, run = env
    r = c.get(f"{V1}/catalog/ai-ready/public-base")
    assert r.status_code == 200 and r.json()["mode"] == "REQUEST" and r.json()["base"].endswith("/")

    async def _cfg(db):
        db.add_all([CatalogConfiguration(category="ai_ready", config_key="ai_ready_public_mode", config_value="CUSTOM"),
                    CatalogConfiguration(category="ai_ready", config_key="ai_ready_public_url",
                                         config_value="https://catalog.example.kr")])
    run(_cfg)
    who["user"] = _tu("mallory")  # 관리자가 아니어도
    assert c.get(f"{V1}/catalog/ai-ready/public-base").json() == {"mode": "CUSTOM", "base": "https://catalog.example.kr/"}
    dcat = c.get(f"{V1}/external/datasets/{_ids['bare']}/dcat").json()
    assert dcat["@id"].startswith("https://catalog.example.kr/api/v1/external/datasets/"), "DCAT @id 와 같은 주소"

    who["user"] = None
    assert c.get(f"{V1}/catalog/ai-ready/public-base").status_code == 401
