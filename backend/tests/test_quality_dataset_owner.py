# SPDX-License-Identifier: Apache-2.0
"""데이터셋 품질 탭의 동작(진단 · 프로파일 · 규칙 · 지표 할당 · 주기)은 데이터셋 소유자 또는 관리자만.

조회(점수 · 추이 · 결과 · 규칙 목록 · 실행 상태)는 지금까지처럼 누구나 본다.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.catalog.models as cm
from app.core.auth import TokenUser
from app.quality.models import QualityDefinition, QualityRule, QualityRun
from tests.test_quality_trino_runs import (  # noqa: F401 — _no_alerts 는 autouse fixture
    _app,
    _make_sessionmaker,
    _no_alerts,
    _run,
    _seed,
)

BASE = "/api/v1/quality"


def _user(username: str, *, admin: bool = False) -> TokenUser:
    return TokenUser(sub=f"u-{username}", username=username, email="", first_name="", last_name="",
                     roles=["argus-admin" if admin else "argus-user"])


def _setup(tmp_path, owner: str | None = "bob"):
    sm = _make_sessionmaker(str(tmp_path / "q.db"))
    ds_id, (rule_id,) = asyncio.run(_seed(sm, [{"check_type": "NOT_NULL", "column": "use_yn"}]))

    async def own(s):
        (await s.get(cm.Dataset, ds_id)).created_by = owner
        d = QualityDefinition(name="여부 Y/N", category="YN", check_type="ACCEPTED_VALUES", expected_value="Y,N", threshold=100,
                              severity="WARNING")
        s.add(d)
        await s.flush()
        return d.id
    definition_id = _run(sm, own)
    return sm, ds_id, rule_id, definition_id


def _writes(client: TestClient, ds_id: int, rule_id: int, definition_id: int) -> dict[str, int]:
    """품질 탭의 쓰기 동작 — 실행 큐에 넣는 것은 맨 끝(앞의 동작이 409 로 가려지지 않게)."""
    out = {
        "create_rule": client.post(f"{BASE}/rules", json={
            "dataset_id": ds_id, "rule_name": "amt", "check_type": "NOT_NULL", "column_name": "amt"}).status_code,
        "update_rule": client.put(f"{BASE}/rules/{rule_id}", json={"rule_name": "바꿈"}).status_code,
        "assign": client.post(f"{BASE}/definitions/{definition_id}/assign", json={
            "columns": [{"dataset_id": ds_id, "column_name": "use_yn"}]}).status_code,
        "run": client.post(f"{BASE}/datasets/{ds_id}/runs").status_code,
    }
    return out


def test_non_owner_cannot_change_quality_but_can_read(tmp_path) -> None:
    sm, ds_id, rule_id, definition_id = _setup(tmp_path)
    carol = TestClient(_app(sm, _user("carol")))

    assert set(_writes(carol, ds_id, rule_id, definition_id).values()) == {403}
    assert carol.delete(f"{BASE}/rules/{rule_id}").status_code == 403
    denied = carol.post(f"{BASE}/datasets/{ds_id}/runs")
    assert "소유자 또는 관리자" in denied.json()["detail"]
    assert _run(sm, lambda s: s.scalar(select(QualityRule.rule_name).where(QualityRule.id == rule_id))) == "r0"

    # 보기는 그대로 — 점수 · 추이 · 결과 · 규칙 · 실행 · Trino 연결 상태
    for path in (f"/rules?dataset_id={ds_id}", f"/datasets/{ds_id}/score", f"/datasets/{ds_id}/score/history",
                 f"/datasets/{ds_id}/results", f"/datasets/{ds_id}/runs", f"/datasets/{ds_id}/engine"):
        assert carol.get(BASE + path).status_code in (200, 404), path  # 404 는 아직 점수가 없을 때


def test_owner_and_admin_can_change_quality(tmp_path) -> None:
    sm, ds_id, rule_id, definition_id = _setup(tmp_path)
    for who in (_user("bob"), _user("root", admin=True)):
        codes = _writes(TestClient(_app(sm, who)), ds_id, rule_id, definition_id)
        assert 403 not in codes.values(), (who.username, codes)
        run_id = _run(sm, lambda s: s.scalar(select(QualityRun.id).order_by(QualityRun.id.desc())))
        assert TestClient(_app(sm, who)).post(f"{BASE}/runs/{run_id}/cancel").status_code == 200


def test_assigning_across_datasets_needs_every_dataset(tmp_path) -> None:
    sm, ds_id, _rule_id, definition_id = _setup(tmp_path)

    async def other(s):
        src = (await s.execute(select(cm.Datasource.id).where(cm.Datasource.datasource_id == "pg-1"))).scalar_one()
        d = cm.Dataset(urn="pg-1.public.items.dataset", name="items", datasource_id=src,
                       qualified_name="pg-1.public.items", created_by="carol")
        s.add(d)
        await s.flush()
        s.add(cm.DatasetSchema(dataset_id=d.id, field_path="use_yn", field_type="STRING", native_type="char(1)"))
        return d.id
    theirs = _run(sm, other)
    bob = TestClient(_app(sm, _user("bob")))
    both = {"columns": [{"dataset_id": ds_id, "column_name": "use_yn"}, {"dataset_id": theirs, "column_name": "use_yn"}]}
    assert bob.post(f"{BASE}/definitions/{definition_id}/assign", json=both).status_code == 403
    mine = bob.post(f"{BASE}/definitions/{definition_id}/assign", json={"columns": both["columns"][:1]})
    assert mine.status_code == 200
    async def assigned(s):
        return list((await s.scalars(select(QualityRule.id).where(QualityRule.definition_id == definition_id))).all())
    rule_ids = _run(sm, assigned)
    assert TestClient(_app(sm, _user("carol"))).post(
        f"{BASE}/definitions/{definition_id}/unassign", json={"rule_ids": list(rule_ids)}).status_code == 403
    assert bob.post(f"{BASE}/definitions/{definition_id}/unassign", json={"rule_ids": list(rule_ids)}).status_code == 200


def test_dataset_without_creator_is_admin_only(tmp_path) -> None:
    sm, ds_id, rule_id, definition_id = _setup(tmp_path, owner=None)
    assert set(_writes(TestClient(_app(sm, _user("bob"))), ds_id, rule_id, definition_id).values()) == {403}
