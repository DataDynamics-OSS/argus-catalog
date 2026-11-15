# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 4단계 — 오류 신고·개선 요청 처리(C31)와 데이터 버전 이력(C32). 픽스처는 test_ai_ready_check 와 같다.

- 데이터셋의 최상위 댓글 중 분류 bug·suggestion 은 접수(OPEN)되고 기한(설정 일수, 기본 10일)이 붙는다.
- 처리 상태는 데이터셋 소유자·관리자만 바꾼다. 소명(REJECTED)은 사유 필수, 해결·소명은 처리 시각을 남긴다.
- 기한을 넘긴 미처리 신고가 있으면 C31 미흡, 외부 메타데이터의 ai_ready 에 적정이 아닌 필수 항목으로 나온다.
- '버전'·'버전 노트'를 바꾸면 버전 이력에 남고, 데이터 카드에 '버전 이력' 표가 생긴다.

실행:
    cd backend
    pytest tests/test_ai_ready_feedback.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.comments.models import Comment
from app.settings.models import CatalogConfiguration
from tests.test_ai_ready_check import V1, _fill_everything, _items, _tu, env  # noqa: F401 — env 는 픽스처


def _post(c, ds_id: int, category: str, parent_id: int | None = None, entity_type: str = "dataset"):
    r = c.post(f"{V1}/comments", json={"entity_type": entity_type, "entity_id": str(ds_id), "parent_id": parent_id,
                                       "content": "<p>값이 이상합니다</p>", "content_plain": "값이 이상합니다",
                                       "category": category})
    assert r.status_code == 200, r.text
    return r.json()


def _days_until(iso: str) -> float:
    due = datetime.fromisoformat(iso)
    due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
    return (due - datetime.now(timezone.utc)).total_seconds() / 86400


def test_report_intake_and_category_changes(env):
    c, _who, ids, run = env
    bug = _post(c, ids["bare"], "bug")
    assert bug["status"] == "OPEN" and 9.9 < _days_until(bug["due_at"]) <= 10 and bug["overdue"] is False

    assert _post(c, ids["bare"], "general")["status"] is None
    assert _post(c, ids["bare"], "feature")["status"] is None            # 기능 요청은 카탈로그 기능 몫
    assert _post(c, ids["bare"], "bug", parent_id=bug["id"])["status"] is None  # 답글은 처리 대상 아님
    assert _post(c, ids["bare"], "bug", entity_type="model")["status"] is None  # 데이터셋만

    # 기한 일수 설정
    async def _cfg(db):
        db.add(CatalogConfiguration(category="ai_ready", config_key="ai_ready_report_due_days", config_value="5"))
    run(_cfg)
    sug = _post(c, ids["bare"], "suggestion")
    assert sug["status"] == "OPEN" and 4.9 < _days_until(sug["due_at"]) <= 5

    # 분류를 바꾸면 처리 상태가 붙거나 떨어진다
    r = c.put(f"{V1}/comments/{sug['id']}", json={"content": "<p>x</p>", "category": "general"}).json()
    assert r["status"] is None and r["due_at"] is None
    r = c.put(f"{V1}/comments/{sug['id']}", json={"content": "<p>x</p>", "category": "bug"}).json()
    assert r["status"] == "OPEN" and r["due_at"]


def test_report_status_permissions_and_resolution(env):
    c, who, ids, _run = env
    bug = _post(c, ids["bare"], "bug")
    url = f"{V1}/comments/{bug['id']}/status"

    who["user"] = _tu("mallory")
    assert c.put(url, json={"status": "IN_PROGRESS"}).status_code == 403
    who["user"] = _tu("alice")  # 데이터셋 생성자
    r = c.put(url, json={"status": "IN_PROGRESS"})
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS" and r.json()["handled_by"] == "alice"
    assert c.put(url, json={"status": "REJECTED"}).status_code == 422          # 소명은 사유 필수
    assert c.put(url, json={"status": "DONE"}).status_code == 422
    r = c.put(url, json={"status": "REJECTED", "resolution": "측정 기준이 다릅니다"}).json()
    assert r["status"] == "REJECTED" and r["resolved_at"] and r["resolution"] == "측정 기준이 다릅니다"
    assert r["updated_at"] == bug["updated_at"]                                # '수정됨' 표시는 그대로
    r = c.put(url, json={"status": "OPEN"}).json()
    assert r["status"] == "OPEN" and r["resolved_at"] is None

    who["user"] = _tu("root", admin=True)
    assert c.put(url, json={"status": "RESOLVED", "resolution": "수정함"}).json()["handled_by"] == "root"

    general = _post(c, ids["bare"], "general")
    assert c.put(f"{V1}/comments/{general['id']}/status", json={"status": "RESOLVED"}).status_code == 404


def test_overdue_report_fails_c31_and_lists(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))
    late = _post(c, ids["bare"], "bug")
    done = _post(c, ids["bare"], "suggestion")
    c.put(f"{V1}/comments/{done['id']}/status", json={"status": "RESOLVED", "resolution": "반영"})
    _post(c, ids["other"], "suggestion")

    items = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())
    assert items["C31"]["status"] == "PASS" and "접수 2건" in items["C31"]["evidence"]

    async def _expire(db):
        await db.execute(update(Comment).where(Comment.id == late["id"])
                         .values(due_at=datetime.now(timezone.utc) - timedelta(days=1)))
    run(_expire)

    body = c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json()
    assert _items(body)["C31"]["status"] == "FAIL" and "1건" in _items(body)["C31"]["evidence"]
    meta = c.get(f"{V1}/external/datasets/{ids['bare']}/metadata", params={"no_cache": True}).json()["ai_ready"]
    assert [u["key"] for u in meta["unmet_required"]] == ["C31"]

    page = c.get(f"{V1}/comments/reports", params={"state": "overdue"}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == late["id"] and page["items"][0]["overdue"] is True
    assert page["items"][0]["entity_path"] == "db.bare" and page["items"][0]["entity_name"] == "db.bare"
    assert page["counts"] == {"open": 2, "overdue": 1, "closed": 1, "all": 3}
    closed = c.get(f"{V1}/comments/reports", params={"state": "closed"}).json()
    assert [i["id"] for i in closed["items"]] == [done["id"]]
    one = c.get(f"{V1}/comments/reports", params={"state": "all", "dataset_id": ids["other"]}).json()
    assert one["total"] == 1 and one["counts"]["all"] == 1
    assert c.get(f"{V1}/comments/reports", params={"state": "all", "category": "bug"}).json()["total"] == 1


def test_version_history(env):
    c, who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}"
    assert c.put(url, json={"data_version": "v1", "version_notes": "첫 배포"}).status_code == 200
    c.put(url, json={"version_notes": "첫 배포 — 2025년 자료"})          # 같은 버전의 노트만 고침 → 그 줄을 고침
    c.put(url, json={"description": "설명만 바꿈"})                       # 버전과 무관
    c.put(url, json={"data_version": "v2", "version_notes": "강남구 누락 보정"})
    rows = c.get(f"{url}/versions").json()
    assert [(r["version"], r["notes"], r["created_by"]) for r in rows] == [
        ("v2", "강남구 누락 보정", "alice"), ("v1", "첫 배포 — 2025년 자료", "alice")]

    c.put(url, json={"data_version": None})                               # 버전을 비워도 이력은 남김
    assert len(c.get(f"{url}/versions").json()) == 2
    c.put(url, json={"data_version": "v2"})
    items = _items(c.get(f"{url}/ai-ready/check").json())
    assert items["C32"]["status"] == "PASS" and "버전 이력 2개" in items["C32"]["evidence"]

    card = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "## 버전 이력" in card and "| v1 | 첫 배포 — 2025년 자료 | alice |" in card

    who["user"] = _tu("mallory")
    assert c.delete(f"{url}/versions/{rows[1]['id']}").status_code == 403
    who["user"] = _tu("alice")
    assert c.delete(f"{url}/versions/{rows[1]['id']}").status_code == 200
    assert c.delete(f"{url}/versions/{rows[1]['id']}").status_code == 404
    assert [r["version"] for r in c.get(f"{url}/versions").json()] == ["v2"]
    assert c.get(f"{V1}/catalog/datasets/99999/versions").status_code == 404


def test_report_without_plain_text_gets_plain_from_html(env):
    """API 로 content_plain 없이 만든 신고도 처리 목록 · 알림 요약에 본문이 보이게 서버가 평문을 만든다(DEF-133).
    수정에서 평문을 빼면 새 본문으로 다시 만든다 — 예전 평문이 남지 않게."""
    from app.core.html_sanitize import html_to_plain

    c, _who, ids, _run = env
    r = c.post(f"{V1}/comments", json={"entity_type": "dataset", "entity_id": str(ids["bare"]), "category": "bug",
                                       "content": "<p>pm10 값 <b>-999</b> &amp; 결측</p><ul><li>1월</li><li>2월</li></ul>"})
    assert r.status_code == 200, r.text
    bug = r.json()
    assert bug["content_plain"] == "pm10 값 -999 & 결측\n1월\n2월"
    items = c.get(f"{V1}/comments/reports").json()["items"]
    assert next(i for i in items if i["id"] == bug["id"])["content_plain"].startswith("pm10 값 -999")

    r = c.put(f"{V1}/comments/{bug['id']}", json={"content": "<p>고쳤습니다</p>"}).json()
    assert r["content_plain"] == "고쳤습니다"
    r = c.put(f"{V1}/comments/{bug['id']}", json={"content": "<p>x</p>", "content_plain": "직접 보낸 평문"}).json()
    assert r["content_plain"] == "직접 보낸 평문"

    assert html_to_plain("a < b  ") == "a < b" and html_to_plain(None) == "" and html_to_plain("<p>a</p><p>b</p>") == "a\nb"
