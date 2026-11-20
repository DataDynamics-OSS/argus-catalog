# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그 사용 흐름 점검의 높음 결함 (in-memory SQLite, 실제 Temporal·원천 DB 불필요).

- DEF-049: 변경 요청 적용이 스키마 행을 지우고 다시 넣지 않는다 — 행 id(표준 용어 매핑)·pii_type 보존
- DEF-050: 데이터셋 편집 권한이 없는 요청자는 본인이 아닌 결재자 없이 변경 요청을 만들거나 상신할 수 없다
- DEF-051: 수집 실행(대기·실행 중)이 있는 데이터 소스는 삭제할 수 없다(응답 없는 실행은 정리 후 판단)
- DEF-052 는 tests/test_quality_reprofile.py

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_flow_high_fixes.py
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.catalog.models as cm
from app.catalog import router as catalog_router
from app.catalog import sync_runs_service
from app.change_mgmt import activities as acts
from app.change_mgmt import models as chm
from app.change_mgmt import router as cr_router
from app.core.auth import TokenUser
from tests.test_change_mgmt_remaining_fixes import _cr, _make_db, _seed_dataset


def _user(name: str, admin: bool = False) -> TokenUser:
    return TokenUser(sub=name, username=name, roles=["argus-admin", "argus-user"] if admin else ["argus-user"])


# ---------------------------------------------------------------------------
# DEF-049
# ---------------------------------------------------------------------------

async def _apply_preserves_rows(monkeypatch) -> None:
    engine, factory = await _make_db()
    async with factory() as db:
        ds = await _seed_dataset(db)
        db.add_all([
            cm.DatasetSchema(dataset_id=ds, field_path="id", field_type="NUMBER", ordinal=0, is_primary_key="true"),
            cm.DatasetSchema(dataset_id=ds, field_path="email", field_type="STRING", ordinal=1,
                             description="고객 이메일", pii_type="EMAIL"),
            cm.DatasetSchema(dataset_id=ds, field_path="old", field_type="STRING", ordinal=2),
        ])
        cr = _cr(ds, "CR-49", "APPROVED")
        cr.change_type = "SCHEMA"
        cr.schema_after = json.dumps({"fields": [{"field_path": "phone", "field_type": "STRING"},
                                                 {"field_path": "email", "field_type": "VARCHAR(320)"}],
                                      "drop": ["old"]})
        db.add(cr)
        await db.commit()
        before = {r.field_path: r.id for r in (await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == ds))).scalars()}
        cr_id = cr.id

    @asynccontextmanager
    async def _factory():
        async with factory() as s:
            yield s

    async def _no_snapshot(*_a, **_k):
        return None

    monkeypatch.setattr(acts, "async_session_factory", _factory)
    monkeypatch.setattr(acts, "save_schema_snapshot", _no_snapshot)
    await acts.apply_change(acts.ApplyInput(cr_id=cr_id))

    async with factory() as db:
        rows = {r.field_path: r for r in (await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == ds))).scalars()}
    assert set(rows) == {"id", "email", "phone"}
    # 남는 컬럼은 같은 행 — 표준 용어 매핑(schema_id CASCADE)이 지워지지 않는다
    assert rows["id"].id == before["id"] and rows["email"].id == before["email"]
    assert rows["email"].field_type == "VARCHAR(320)"
    assert rows["email"].pii_type == "EMAIL" and rows["email"].description == "고객 이메일"
    await engine.dispose()


def test_apply_change_keeps_row_ids_and_pii(monkeypatch) -> None:
    asyncio.run(_apply_preserves_rows(monkeypatch))


# ---------------------------------------------------------------------------
# DEF-050
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("user", "created_by", "approvers", "allowed"),
    [
        (_user("bob"), "alice", [], False),             # 편집 권한 없음 + 결재선 없음 → 자동 승인 우회
        (_user("bob"), "alice", ["bob"], False),        # 본인만 결재 → 우회
        (_user("bob"), None, [], False),                # 동기화 데이터셋(생성자 없음)
        (_user("bob"), "alice", ["bob", "carol"], True),
        (_user("alice"), "alice", [], True),            # 생성자는 직접 편집도 되므로 허용
        (_user("root", admin=True), "alice", [], True),
    ],
)
def test_independent_approver_required(monkeypatch, user, created_by, approvers, allowed) -> None:
    async def _is_owner(_s, _id, username):  # 생성자 · 소유자 목록 판정(TODO-154)
        return bool(created_by) and created_by == username

    monkeypatch.setattr("app.catalog.dataset_owner.is_dataset_owner", _is_owner)
    call = cr_router._require_independent_approver(None, user, 1, approvers)
    if allowed:
        asyncio.run(call)
    else:
        with pytest.raises(HTTPException) as e:
            asyncio.run(call)
        assert e.value.status_code == 422


# ---------------------------------------------------------------------------
# DEF-051
# ---------------------------------------------------------------------------

async def _delete_blocked_while_running() -> None:
    engine, factory = await _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
            sc, tables=[cm.DatasourceSyncRun.__table__, cm.DatasourceSyncSetting.__table__]))
    now = datetime.now(timezone.utc)
    async with factory() as db:
        src = cm.Datasource(datasource_id="m-run", name="m-run", type="mysql")
        db.add(src)
        await db.flush()
        run = cm.DatasourceSyncRun(datasource_id=src.id, executor="BUILTIN", scope_json="{}",
                                   status="RUNNING", queued_at=now, heartbeat_at=now)
        db.add(run)
        await db.commit()
        src_id, run_id = src.id, run.id

        assert await sync_runs_service.active_run_id(db, src_id) == run_id
        with pytest.raises(HTTPException) as e:
            await catalog_router.delete_datasource(_user("root", admin=True), src_id, False, db)
        assert e.value.status_code == 409 and f"#{run_id}" in e.value.detail

        # 응답이 끊긴 실행은 실패 처리되고 더는 막지 않는다
        run.heartbeat_at = now - timedelta(hours=1)
        run.queued_at = now - timedelta(hours=1)
        await db.commit()

    async def _no_after(*_a, **_k):
        return None

    sync_runs_service._after_terminal, saved = _no_after, sync_runs_service._after_terminal
    try:
        async with factory() as db:
            assert await sync_runs_service.active_run_id(db, src_id) is None
            assert (await db.get(cm.DatasourceSyncRun, run_id)).status == "FAILED"
    finally:
        sync_runs_service._after_terminal = saved
    await engine.dispose()


def test_delete_datasource_blocked_while_sync_running() -> None:
    asyncio.run(_delete_blocked_while_running())


# ---------------------------------------------------------------------------
# DEF-070: 예전 동기 API 와 화면 수집은 같은 데이터 소스를 동시에 수집하지 않는다
# ---------------------------------------------------------------------------

async def _legacy_and_screen_sync_exclusive(monkeypatch) -> None:
    from app.catalog.schemas import SyncScope

    engine, factory = await _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(
            sc, tables=[cm.DatasourceSyncRun.__table__, cm.DatasourceSyncSetting.__table__]))

    async def _cfg(*_a, **_k):
        return {}

    monkeypatch.setattr(sync_runs_service, "_load_config", _cfg)
    now = datetime.now(timezone.utc)
    async with factory() as db:
        src = cm.Datasource(datasource_id="m-ex", name="m-ex", type="mysql")
        db.add(src)
        await db.commit()
        src_id = src.id

        # 1) 예전 API 가 동기화 중이면 화면 수집 시작은 409 — 실행 행도 만들지 않는다
        lock = asyncio.Lock()
        await lock.acquire()
        catalog_router._sync_locks[src_id] = lock
        try:
            with pytest.raises(sync_runs_service.SyncRunError) as e:
                await sync_runs_service.start_run(db, src_id, SyncScope(include_all=True), "u")
            assert e.value.status_code == 409
            assert (await db.execute(select(cm.DatasourceSyncRun))).first() is None
        finally:
            lock.release()
            catalog_router._sync_locks.pop(src_id, None)

        # 2) 화면 수집 실행이 있으면 예전 API 는 409 — 원천에 접속하지 않는다
        db.add(cm.DatasourceSyncRun(datasource_id=src_id, executor="BUILTIN", scope_json="{}",
                                    status="RUNNING", queued_at=now, heartbeat_at=now))
        await db.commit()

        async def _no_sync(*_a, **_k):
            raise AssertionError("수집 중에는 예전 동기화가 돌면 안 된다")

        import app.catalog.sync as sync_mod
        monkeypatch.setattr(sync_mod, "sync_datasource_metadata", _no_sync)
        with pytest.raises(HTTPException) as e:
            await catalog_router.sync_datasource(None, src_id, None, db)
        assert e.value.status_code == 409 and "수집이 진행 중" in e.value.detail
        assert not catalog_router._sync_locks[src_id].locked()
    await engine.dispose()


def test_legacy_and_screen_sync_exclusive(monkeypatch) -> None:
    asyncio.run(_legacy_and_screen_sync_exclusive(monkeypatch))


# ---------------------------------------------------------------------------
# DEF-041: PostgreSQL 원천 프로파일링은 스키마 이름이 DB 이름과 같아도 스키마를 붙인다
#   카탈로그 서버의 원천 프로파일(_pg_table_sql)은 없앴고(TODO-095), 품질 워커의 원천 직접 경로가
#   qualified_name 의 끝 두 토막(스키마 · 테이블)을 늘 함께 쓴다(runs.resolve_engine_target).
# ---------------------------------------------------------------------------

def test_pg_profile_table_keeps_schema_named_like_database() -> None:
    from app.quality.rule_direct import source_table

    # 스키마 sales 가 접속 DB sales 와 같은 이름 — 예전에는 "orders" 만 남아 search_path 에서 못 찾았다
    assert source_table("postgresql", "sales", "orders") == '"sales"."orders"'
    assert source_table("postgresql", "public", "orders") == '"public"."orders"'
    assert source_table("postgresql", 'my"s', "t") == '"my""s"."t"'
