# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 2단계 보강 단위 테스트 (자기완결형, in-memory SQLite).

- 조회 API 인증·관리/동기화 권한(명시 허용형)
- 생성 시 유형별 메타데이터 시드, 기동 시드는 모든 데이터 소스 대상
- 삭제 시 FK 없는 참조 정리(알림 규칙·댓글·쿼리 리니지·관계 관측치)와 삭제 영향 항목
- 입력 검증(이름 길이·type 형식·호스트/포트), 연결 오류 분류
- 동기화 중복 실행 409, 조직/시스템 code 길이

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_datasource_stage2
"""

from __future__ import annotations

import asyncio
import socket

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.alert.models import AlertRule
from app.catalog import router as catalog_router
from app.catalog import service
from app.catalog.schemas import DatasourceCreate, DatasourceUpdate
from app.catalog.topology_service import _slugify
from app.change_mgmt.models import ChangeRequest
from app.comments.models import Comment
from app.core.auth import get_current_user
from app.mcp_servers.models import McpDeployment
from app.models.models import RegisteredModel
from app.permissions.router import DEFAULT_DENY_FEATURES

_CATALOG_TABLES = [
    "catalog_organizations", "catalog_systems", "catalog_datasources",
    "catalog_datasource_configurations", "catalog_datasets", "catalog_dataset_schemas",
    "catalog_schema_snapshots", "argus_data_pipeline", "argus_dataset_lineage",
    "catalog_datasource_data_types", "catalog_datasource_table_types",
    "catalog_datasource_storage_formats", "catalog_datasource_features",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        tables = [cm.Base.metadata.tables[t] for t in _CATALOG_TABLES]
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
        for model in (AlertRule, Comment, ChangeRequest, RegisteredModel, McpDeployment):
            await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
        # ORM 모델이 없는 원시 테이블(운영 DDL 의 필요한 컬럼만)
        await conn.execute(text(
            "CREATE TABLE argus_query_lineage (id INTEGER PRIMARY KEY, source_table TEXT,"
            " target_table TEXT, source_dataset_id INT, target_dataset_id INT)"))
        await conn.execute(text(
            "CREATE TABLE relationship_observations (id INTEGER PRIMARY KEY,"
            " datasource_id TEXT, a_table TEXT)"))
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# 권한·인증
# ---------------------------------------------------------------------------

def _deps(route) -> set:
    out, stack = set(), [route.dependant]
    while stack:
        d = stack.pop()
        if d.call is not None:
            out.add(d.call)
        stack.extend(d.dependencies)
    return out


def _test_routes_require_auth() -> None:
    from app.main import app

    reads = {
        ("GET", "/api/v1/catalog/datasources"),
        ("GET", "/api/v1/catalog/datasources/{datasource_id}/metadata"),
        ("GET", "/api/v1/catalog/datasources/{datasource_id}/dataset-count"),
        ("GET", "/api/v1/catalog/datasources/{datasource_id}/delete-impact"),
        ("GET", "/api/v1/catalog/datasources/{datasource_id}/erd"),
        ("GET", "/api/v1/catalog/topology"),
        ("GET", "/api/v1/catalog/organizations"),
        ("GET", "/api/v1/catalog/systems"),
    }
    found = set()
    for r in app.routes:
        for m in getattr(r, "methods", ()) or ():
            key = (m, getattr(r, "path", ""))
            if key in reads:
                found.add(key)
                assert get_current_user in _deps(r), f"인증 없음: {key}"
    assert found == reads, reads - found

    for f in ("datasources.manage", "datasources.sync", "datasources.connection"):
        assert f in DEFAULT_DENY_FEATURES


# ---------------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------------

def _test_validation() -> None:
    DatasourceCreate(name=" ok ", type="iceberg-rest")
    assert DatasourceCreate(name=" ok ", type="mysql").name == "ok"
    for bad in [
        {"name": "x" * 101, "type": "mysql"},
        {"name": "   ", "type": "mysql"},
        {"name": "n", "type": "My SQL"},
        {"name": "n", "type": "a" * 20},        # datasource_id 36자 초과
        {"name": "n", "type": "../x"},
        {"name": "n", "type": "mysql", "logo_url": "javascript:alert(1)"},
    ]:
        try:
            DatasourceCreate(**bad)
            raise AssertionError(f"expected ValidationError: {bad}")
        except ValidationError:
            pass
    try:
        DatasourceUpdate(name="  ")
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass

    v = service.validate_connection_target
    assert v({"host": "db.internal", "port": "5433"}, 5432) == ("db.internal", 5433)
    assert v({"host": "10.0.0.1"}, 3306) == ("10.0.0.1", 3306)
    assert v({"host": "::1"}, 1)[0] == "::1"
    for bad in [{"host": "/tmp"}, {"host": "a,b"}, {"host": "a b"}, {"host": ""},
                {"host": "h", "port": 0}, {"host": "h", "port": 70000}, {"host": "h", "port": "x"}]:
        try:
            v(bad, 1)
            raise AssertionError(f"expected ValueError: {bad}")
        except ValueError:
            pass

    c = service._classify_connection_error
    assert "시간 초과" in c(asyncio.TimeoutError(), 8)
    assert "거부" in c(ConnectionRefusedError(), 8)
    assert "호스트" in c(socket.gaierror(), 8)
    assert "인증" in c(Exception("Access denied for user 'x'"), 8)
    # 원시 메시지(예: 패킷 오류로 드러나는 서비스 지문)는 노출하지 않는다
    assert "Packet" not in c(Exception("Packet sequence number wrong"), 8)

    assert len(_slugify("가" * 300)) <= 90


async def _test_connection_test_unsupported_and_validation() -> None:
    r = await service.test_datasource_connection("hive", {"host": "h"})
    assert r["ok"] is False and r.get("unsupported") is True
    r = await service.test_datasource_connection("postgresql", {"host": "/var/run/postgresql"})
    assert r["ok"] is False and "호스트" in r["message"]


# ---------------------------------------------------------------------------
# 메타데이터 시드
# ---------------------------------------------------------------------------

async def _count_meta(db, ds_id: int) -> int:
    return await db.scalar(select(func.count()).select_from(cm.DatasourceDataType)
                           .where(cm.DatasourceDataType.datasource_id == ds_id)) or 0


async def _test_seed_on_create() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        a = await service.create_datasource(db, DatasourceCreate(name="a", type="mysql"))
        b = await service.create_datasource(db, DatasourceCreate(name="b", type="mysql"))
        assert await _count_meta(db, a.id) > 0
        assert await _count_meta(db, b.id) > 0          # 같은 유형 두 번째도 시드
        before = await _count_meta(db, a.id)
        await service.seed_datasource_metadata(db)      # 기동 시드는 멱등
        assert await _count_meta(db, a.id) == before


# ---------------------------------------------------------------------------
# 삭제 — 고아 참조 정리, 영향
# ---------------------------------------------------------------------------

async def _test_delete_cleans_references() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        other = await service.create_datasource(db, DatasourceCreate(name="o", type="mysql"))
        d = cm.Dataset(urn="u1", name="db.t", datasource_id=ds.id, origin="DEV",
                       qualified_name="q", status="active")
        keep_d = cm.Dataset(urn="u2", name="db.k", datasource_id=other.id, origin="DEV",
                            qualified_name="q2", status="active")
        db.add_all([d, keep_d])
        await db.flush()
        db.add_all([
            AlertRule(rule_name="ds", scope_type="DATASOURCE", scope_id=ds.id, trigger_type="ANY"),
            AlertRule(rule_name="dset", scope_type="DATASET", scope_id=d.id, trigger_type="ANY"),
            AlertRule(rule_name="keep", scope_type="DATASET", scope_id=keep_d.id, trigger_type="ANY"),
            Comment(entity_type="dataset", entity_id=str(d.id), content="c", author_name="a"),
            Comment(entity_type="dataset", entity_id=str(keep_d.id), content="c", author_name="a"),
        ])
        await db.execute(text(
            "INSERT INTO argus_query_lineage (source_table, target_table, source_dataset_id,"
            " target_dataset_id) VALUES ('a','b',:s,:t)"), {"s": d.id, "t": keep_d.id})
        await db.execute(text(
            "INSERT INTO relationship_observations (datasource_id, a_table) VALUES (:a,'x'),(:b,'y')"),
            {"a": ds.datasource_id, "b": other.datasource_id})
        await db.commit()

        impact = await service.get_datasource_delete_impact(db, ds.id)
        assert impact["alert_rule_count"] == 2
        assert impact["dataset_count"] == 1
        for k in ("change_request_count", "model_count", "mcp_deployment_count"):
            assert impact[k] == 0

        assert await service.delete_datasource(db, ds.id, force=True)

        rules = (await db.execute(select(AlertRule.rule_name))).scalars().all()
        assert rules == ["keep"]
        comments = (await db.execute(select(Comment.entity_id))).scalars().all()
        assert comments == [str(keep_d.id)]
        ql = (await db.execute(text(
            "SELECT source_dataset_id, target_dataset_id FROM argus_query_lineage"))).one()
        assert ql == (None, keep_d.id)
        obs = (await db.execute(text("SELECT datasource_id FROM relationship_observations"))).scalars().all()
        assert obs == [other.datasource_id]


# ---------------------------------------------------------------------------
# 동기화 중복 실행
# ---------------------------------------------------------------------------

async def _test_sync_conflict() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        lock = asyncio.Lock()
        await lock.acquire()
        catalog_router._sync_locks[ds.id] = lock
        try:
            for call in (
                catalog_router.sync_datasource(None, ds.id, None, db),
                catalog_router.delete_datasource(None, ds.id, False, db),
            ):
                try:
                    await call
                    raise AssertionError("expected 409")
                except HTTPException as e:
                    assert e.status_code == 409
        finally:
            lock.release()
            catalog_router._sync_locks.pop(ds.id, None)


async def _run() -> None:
    _test_routes_require_auth()
    _test_validation()
    await _test_connection_test_unsupported_and_validation()
    await _test_seed_on_create()
    await _test_delete_cleans_references()
    await _test_sync_conflict()


def test_datasource_stage2() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_datasource_stage2 OK")
