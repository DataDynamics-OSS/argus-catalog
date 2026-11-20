# SPDX-License-Identifier: Apache-2.0
"""카탈로그 직접 등록 · 수동 데이터셋 · 원천 매핑 (TODO-CAT-001, design/catalog-registration-design.md) — in-memory SQLite.

- ARGUS 카탈로그에 catalog_id 로 데이터셋 등록 → 수동 등록 데이터 소스 생성 · 재사용, URN
- 수동 데이터 소스: 목록 제외, 직접 조작 409, 사용자가 manual 유형 생성 불가, 카탈로그 이름 · 삭제 연동
- ARGUS 카탈로그 SYNC 연결 409
- 매핑 ① 중복 차단(다른 매핑 · 수집 데이터셋) ② 존재 ③ 스키마 비교는 기록만, 원천 스키마 가져오기
- 수집 제외: is_synced 생성 409 MANUAL_MAPPED, 내장 수집 건너뜀 집계
- 품질 대상 해석 · 쿼리 리니지 해석이 매핑을 쓴다
- 데이터셋 응답 · 목록의 카탈로그 · 매핑 필드와 필터

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_catalog_registration
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text

import app.catalog.models as cm
import app.catalog.sync as sync_mod
from app.catalog import catalogs_router, catalogs_service, service, source_maps
from app.catalog import router as catalog_router
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogCreate,
    CatalogUpdate,
    DatasetCreate,
    DatasetSourceMapRequest,
    DatasourceCreate,
    SchemaFieldCreate,
)
from app.catalog.sync import ColumnInfo, SourceUnavailableError, TableInfo
from tests.test_catalog_entity_stage1 import _make_session as _base_session


async def _make_session():
    """카탈로그 엔티티 테스트 세션 + 감사 로그 테이블."""
    from app.audit.models import AdminAuditLog
    make_db = await _base_session()
    async with make_db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: AdminAuditLog.__table__.create(sc, checkfirst=True))
        await db.commit()
    return make_db


class _User:
    username = "tester"


async def _expect(coro, status: int):
    try:
        await coro
    except HTTPException as e:
        assert e.status_code == status, (e.status_code, e.detail)
        return e
    raise AssertionError(f"expected HTTP {status}")


async def _argus_catalog(db, name="엑셀 자료"):
    return await catalogs_router.create_catalog(None, CatalogCreate(name=name, source_type="ARGUS"), db)


async def _manual_dataset(db, catalog_id, name="orders", path="sales.orders", fields=()):
    return await service.create_dataset(db, DatasetCreate(
        name=name, catalog_id=catalog_id, qualified_name=path,
        schema_fields=[SchemaFieldCreate(field_path=f, field_type=t) for f, t in fields]))


def _fake_source(table: TableInfo | None = None, exc: Exception | None = None):
    async def fake(_session, _ds, _path):
        if exc is not None:
            raise exc
        return table
    return fake


async def _with_source(fake, coro_fn):
    orig = sync_mod.read_source_table
    sync_mod.read_source_table = fake
    try:
        return await coro_fn()
    finally:
        sync_mod.read_source_table = orig


async def _test_manual_dataset_on_argus_catalog() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        assert cat.source_type == "ARGUS" and cat.connections == []
        d1 = await _manual_dataset(db, cat.id)
        d2 = await _manual_dataset(db, cat.id, name="customers", path="sales.customers")
        manual = await catalogs_service.manual_datasource_of(db, cat.id)
        manual_id = manual.id
        assert manual is not None and manual.type == "manual" and manual.name == cat.name
        assert manual.datasource_id == cat.catalog_key           # 카탈로그 기준 URN 과 같아지도록
        assert d1.urn == f"{cat.catalog_key}.sales.orders.dataset"
        assert d1.is_manual and d1.catalog_id == cat.id and d1.catalog_name == cat.name and d1.source_map is None
        assert (await db.get(cm.Dataset, d2.id)).datasource_id == manual_id   # 재사용
        assert d1.datasource.role == "MANUAL"

        # datasource_id · catalog_id 는 정확히 하나
        for kwargs in ({}, {"datasource_id": manual_id, "catalog_id": cat.id}):
            try:
                DatasetCreate(name="x", **kwargs)
            except ValidationError:
                pass
            else:
                raise AssertionError(kwargs)
        # 사용자가 manual 유형 데이터 소스를 직접 만들 수 없다
        try:
            DatasourceCreate(name="m", type="manual")
        except ValidationError:
            pass
        else:
            raise AssertionError("manual 유형 생성이 허용됨")

        # 없는 카탈로그 404, SYNC 가 없는 EXTERNAL 카탈로그 409
        await _expect(catalog_router.create_dataset(DatasetCreate(name="t", catalog_id=999), _User(), None, db), 404)
        ext = await catalogs_service.create_catalog(db, CatalogCreate(name="via engine"))
        await _expect(catalog_router.create_dataset(DatasetCreate(name="t", catalog_id=ext.id), _User(), None, db), 409)

        # 자동 카탈로그로 등록하면 그 데이터 소스에 들어간다(기존 흐름)
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        pg_cat = (await catalogs_service.list_datasource_catalogs(db, pg.id))[0]
        d3 = await service.create_dataset(db, DatasetCreate(name="t", catalog_id=pg_cat.id, qualified_name="public.t"))
        assert (await db.get(cm.Dataset, d3.id)).datasource_id == pg.id and not d3.is_manual


async def _test_manual_datasource_guards() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        await _manual_dataset(db, cat.id)
        manual_id = (await catalogs_service.manual_datasource_of(db, cat.id)).id
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        trino = await service.create_datasource(db, DatasourceCreate(name="trino", type="trino"))

        # 목록 제외 · 포함
        assert manual_id not in {d.id for d in await service.list_datasources(db)}
        assert manual_id in {d.id for d in await service.list_datasources(db, include_manual=True)}
        # 원천 접속 · 직접 관리 409
        await _expect(catalog_router.reject_manual_datasource(manual_id, db), 409)
        await catalog_router.reject_manual_datasource(pg.id, db)
        # 연결: 수동 데이터 소스를 다른 카탈로그에 409, ARGUS 카탈로그 SYNC 409, QUERY 는 허용
        other = await _argus_catalog(db, "다른")
        await _expect(catalogs_router.add_connection(None, other.id, CatalogConnectionCreate(
            datasource_id=manual_id, role="QUERY"), db), 409)
        await _expect(catalogs_router.add_connection(None, cat.id, CatalogConnectionCreate(
            datasource_id=pg.id, role="SYNC"), db), 409)
        resp = await catalogs_router.add_connection(None, cat.id, CatalogConnectionCreate(
            datasource_id=trino.id, role="QUERY", remote_name="excel"), db)
        assert {(c.datasource_id, c.role) for c in resp.connections} == {(manual_id, "SYNC"), (trino.id, "QUERY")}
        # 수동 데이터 소스의 SYNC 연결 해제 409
        await _expect(catalogs_router.remove_connection(None, cat.id, manual_id, db), 409)

        # 카탈로그 이름 변경 → 수동 데이터 소스 이름도
        await catalogs_router.update_catalog(None, cat.id, CatalogUpdate(name="새 이름"), db)
        assert (await db.get(cm.Datasource, manual_id)).name == "새 이름"
        # 데이터셋이 있으면 삭제 409, 비면 카탈로그와 수동 데이터 소스 함께 삭제
        await _expect(catalogs_router.delete_catalog(None, cat.id, db), 409)
        await _manual_dataset(db, other.id)
        other_manual_id = (await catalogs_service.manual_datasource_of(db, other.id)).id
        ds_id = (await db.execute(select(cm.Dataset.id).where(cm.Dataset.datasource_id == other_manual_id))).scalar()
        await service.delete_dataset(db, ds_id)
        await catalogs_router.delete_catalog(None, other.id, db)
        db.expunge_all()
        assert await db.get(cm.Datasource, other_manual_id) is None

        # 감사 로그(catalogs 분류)
        from app.audit.models import AdminAuditLog
        actions = [a for (a,) in (await db.execute(
            select(AdminAuditLog.action).where(AdminAuditLog.category == "catalogs").order_by(AdminAuditLog.id))).all()]
        assert actions == ["catalog.create", "catalog.create", "catalog.connect", "catalog.modify", "catalog.delete"], actions


async def _test_source_map_checks() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        d = await _manual_dataset(db, cat.id, fields=[("id", "NUMBER"), ("amount", "STRING"), ("memo", "STRING")])
        d2 = await _manual_dataset(db, cat.id, name="o2", path="sales.o2")
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        manual_id = (await catalogs_service.manual_datasource_of(db, cat.id)).id

        # 수동 데이터셋이 아니면 409, 수동 데이터 소스로 매핑 409, 없는 데이터 소스 404
        synced = await service.create_dataset(db, DatasetCreate(
            name="paid", datasource_id=pg.id, qualified_name="sales.Paid", is_synced=True))
        req = DatasetSourceMapRequest(datasource_id=pg.id, remote_path="sales.orders")
        await _expect(catalogs_router.check_source_map(None, synced.id, req, db), 409)
        await _expect(catalogs_router.check_source_map(None, d.id, DatasetSourceMapRequest(
            datasource_id=manual_id, remote_path="x.y"), db), 409)
        await _expect(catalogs_router.check_source_map(None, d.id, DatasetSourceMapRequest(
            datasource_id=999, remote_path="x.y"), db), 404)

        # ② ③: 원천 컬럼과 비교 — id 같음, amount 타입 다름, memo 수동에만, created 원천에만
        table = TableInfo(database="sales", name="orders", table_type="BASE TABLE", columns=[
            ColumnInfo(name="ID", data_type="NUMBER", native_type="int4", nullable=False, ordinal=1, comment="키"),
            ColumnInfo(name="amount", data_type="NUMBER", native_type="numeric", nullable=True, ordinal=2),
            ColumnInfo(name="created", data_type="DATE", native_type="timestamp", nullable=True, ordinal=3),
        ])
        p = await _with_source(_fake_source(table), lambda: catalogs_router.check_source_map(None, d.id, req, db))
        assert p.conflict is None and p.check.status == "DIFF" and p.check.source_column_count == 3
        assert {(x.name, x.change) for x in p.check.diffs} == {
            ("amount", "TYPE_DIFF"), ("created", "ONLY_SOURCE"), ("memo", "ONLY_MANUAL")}
        # 없음 · 접속 불가 · 미지원 유형도 저장은 된다(확인용)
        p = await _with_source(_fake_source(None), lambda: source_maps.preview(db, d.id, req))
        assert p.check.status == "NOT_FOUND"
        p = await _with_source(_fake_source(exc=SourceUnavailableError("접속 거부")),
                               lambda: source_maps.preview(db, d.id, req))
        assert (p.check.status, p.check.message) == ("UNREACHABLE", "접속 거부")
        hive = await service.create_datasource(db, DatasourceCreate(name="hms", type="hive"))
        p = await source_maps.preview(db, d.id, DatasetSourceMapRequest(datasource_id=hive.id, remote_path="a.b"))
        assert p.check.status == "UNSUPPORTED"

        saved = await _with_source(_fake_source(table), lambda: catalogs_router.save_source_map(
            _User(), d.id, DatasetSourceMapRequest(datasource_id=pg.id, remote_path=" Sales.Orders "), db))
        assert (saved.remote_path, saved.check_status, saved.created_by) == ("Sales.Orders", "DIFF", "tester")
        assert len(saved.check_detail.diffs) == 3

        # ① 중복: 다른 수동 데이터셋이 같은 경로(대소문자 무시) 409, 수집 데이터셋이 있는 경로 409
        e = await _expect(catalogs_router.save_source_map(_User(), d2.id, DatasetSourceMapRequest(
            datasource_id=pg.id, remote_path="sales.orders", skip_check=True), db), 409)
        assert e.detail["code"] == "SOURCE_MAP_CONFLICT" and e.detail["conflict"]["kind"] == "MANUAL_MAPPING"
        p = await source_maps.preview(db, d2.id, DatasetSourceMapRequest(datasource_id=pg.id, remote_path="SALES.PAID"))
        assert p.conflict.kind == "SYNCED_DATASET" and p.conflict.dataset_id == synced.id and p.check is None
        # skip_check 면 확인 결과 없이 저장
        s2 = await catalogs_router.save_source_map(_User(), d2.id, DatasetSourceMapRequest(
            datasource_id=pg.id, remote_path="sales.o2", skip_check=True), db)
        assert s2.check_status is None

        # 원천 스키마 가져오기 — 원천 컬럼 추가 · 타입 맞춤, 수동 컬럼 · 설명 유지
        await db.execute(text("UPDATE catalog_dataset_schemas SET description = '금액' WHERE field_path = 'amount'"))
        await db.commit()
        result = await _with_source(_fake_source(table), lambda: catalogs_router.import_source_schema(_User(), d.id, db))
        assert (result["added"], result["updated"], result["check_status"]) == (1, 2, "DIFF")  # memo 는 남아 DIFF
        fields = {f.field_path: f for f in (await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == d.id))).scalars()}
        assert set(fields) == {"id", "amount", "memo", "created"}
        assert (fields["amount"].field_type, fields["amount"].native_type, fields["amount"].description) == (
            "NUMBER", "numeric", "금액")
        # 원천을 읽을 수 없으면 가져오기는 409
        await _expect(_with_source(_fake_source(exc=SourceUnavailableError("x")),
                                   lambda: catalogs_router.import_source_schema(_User(), d.id, db)), 409)

        # 다시 확인 · 상세 응답 · 해제
        rc = await _with_source(_fake_source(None), lambda: catalogs_router.recheck_source_map(None, d.id, db))
        assert rc.check_status == "NOT_FOUND"
        detail = await service.get_dataset(db, d.id)
        assert detail.source_map.remote_path == "Sales.Orders" and detail.source_map.datasource_name == "pg"
        await catalogs_router.delete_source_map(_User(), d.id, db)
        assert await source_maps.get_map(db, d.id) is None
        await _expect(catalogs_router.delete_source_map(_User(), d.id, db), 404)


async def _test_sync_skips_mapped_tables() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        d = await _manual_dataset(db, cat.id)
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        await source_maps.save(db, d.id, DatasetSourceMapRequest(
            datasource_id=pg.id, remote_path="Sales.Orders", skip_check=True), "tester")

        # metadata-sync 경로: is_synced 생성은 409 MANUAL_MAPPED(대소문자 무시), 수동이 아닌 생성은 그대로
        e = await _expect(catalog_router.create_dataset(DatasetCreate(
            name="orders", datasource_id=pg.id, qualified_name="sales.orders", is_synced=True), _User(), None, db), 409)
        assert e.detail["code"] == "MANUAL_MAPPED"
        await service.create_dataset(db, DatasetCreate(name="other", datasource_id=pg.id, qualified_name="sales.other",
                                                       is_synced=True))
        # 수동 데이터 소스에 수집 데이터셋 409
        manual_id = (await catalogs_service.manual_datasource_of(db, cat.id)).id
        await _expect(catalog_router.create_dataset(DatasetCreate(
            name="z", datasource_id=manual_id, is_synced=True), _User(), None, db), 409)
        assert await source_maps.mapped_paths(db, pg.id) == {"sales.orders"}
        assert [(m.remote_path, m.dataset_id) for m in await catalogs_router.list_datasource_source_maps(pg.id, None, db)] \
            == [("Sales.Orders", d.id)]

        # 내장 수집: 매핑된 테이블은 건너뛰고 수를 센다
        db.add(cm.DatasourceConfiguration(datasource_id=pg.id, config_json='{"host": "db.example", "port": 5432}'))
        await db.commit()

        async def fake_read(*_a, **_k):
            return [TableInfo(database="sales", name="orders", table_type="BASE TABLE"),
                    TableInfo(database="sales", name="items", table_type="BASE TABLE")]

        orig_read, orig_validate = sync_mod._read_pg_metadata, service.validate_connection_target
        sync_mod._read_pg_metadata = fake_read
        service.validate_connection_target = lambda config, port: ("db.example", 5432)
        try:
            result = await sync_mod.sync_datasource_metadata(db, pg.datasource_id, "sales")
        finally:
            sync_mod._read_pg_metadata, service.validate_connection_target = orig_read, orig_validate
        assert result.errors == [], result.errors
        assert (result.tables_skipped, result.tables_created) == (1, 1)
        names = set((await db.execute(select(cm.Dataset.qualified_name).where(cm.Dataset.datasource_id == pg.id))).scalars())
        assert f"{pg.datasource_id}.sales.orders" not in names and f"{pg.datasource_id}.sales.items" in names


async def _test_quality_target_and_lineage_use_mapping() -> None:
    from app.quality import runs
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        d = await _manual_dataset(db, cat.id, path="x.orders")
        row = await db.get(cm.Dataset, d.id)
        # 매핑 없으면 진단 불가 사유
        try:
            await runs.resolve_engine_target(db, row, connection=False)
        except runs.EngineUnavailableError as e:
            assert "원천 매핑" in str(e)
        else:
            raise AssertionError("매핑 없는 수동 데이터셋이 진단 대상으로 해석됨")
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        await source_maps.save(db, d.id, DatasetSourceMapRequest(
            datasource_id=pg.id, remote_path="Sales.Orders", skip_check=True), "tester")
        target = await runs.resolve_engine_target(db, row, connection=False)
        assert (target.datasource_id, target.engine, target.table.schema, target.table.table) == (
            pg.id, runs.DIRECT_ENGINE, "Sales", "Orders")

        # 쿼리 리니지: pg 를 실행한 엔진(식별자 = pg)의 sales.orders 는 매핑된 수동 데이터셋에 붙는다
        await db.execute(text(
            "INSERT INTO argus_query_lineage (id, datasource_id, source_table, target_table) "
            "VALUES (1, :k, 'sales.orders', 'sales.report')"), {"k": pg.datasource_id})
        await db.commit()
        from app.catalog.lineage_resolve import (
            resolve_all_query_lineage,
            resolve_lineage_for_mapping,
        )
        await resolve_all_query_lineage(db)
        src = (await db.execute(text("SELECT source_dataset_id FROM argus_query_lineage WHERE id = 1"))).scalar()
        assert src == d.id
        # 매핑을 지우면 다시 비운다(자기 이름 x.orders 는 이 엔진 카탈로그에 없으므로 남지 않음)
        await source_maps.delete(db, d.id)
        await resolve_lineage_for_mapping(db, d.id)
        src = (await db.execute(text("SELECT source_dataset_id FROM argus_query_lineage WHERE id = 1"))).scalar()
        assert src is None


async def _test_dataset_list_fields_and_filters() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        cat = await _argus_catalog(db)
        d1 = await _manual_dataset(db, cat.id)
        d2 = await _manual_dataset(db, cat.id, name="o2", path="sales.o2")
        pg = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        d3 = await service.create_dataset(db, DatasetCreate(name="t", datasource_id=pg.id, qualified_name="public.t"))
        await source_maps.save(db, d1.id, DatasetSourceMapRequest(
            datasource_id=pg.id, remote_path="sales.orders", skip_check=True), "tester")

        page = await service.list_datasets(db, catalog_id=cat.id)
        by_id = {i.id: i for i in page.items}
        assert set(by_id) == {d1.id, d2.id}
        assert by_id[d1.id].source_map_status == "UNCHECKED" and by_id[d2.id].source_map_status == "UNMAPPED"
        assert by_id[d1.id].catalog_name == cat.name
        assert {i.id for i in (await service.list_datasets(db, manual=True)).items} == {d1.id, d2.id}
        rest = (await service.list_datasets(db, manual=False)).items
        assert [i.id for i in rest] == [d3.id] and rest[0].source_map_status is None


async def _test_catalog_system() -> None:
    """직접 등록 카탈로그의 소속 시스템 = 수동 등록 데이터 소스의 시스템(DEF-CAT-001)."""
    from app.catalog import topology_service
    make_db = await _make_session()
    async with make_db() as db:
        org = cm.Organization(name="재무팀", code="fin")
        db.add(org)
        await db.flush()
        sysm = cm.System(name="회계 시스템", org_id=org.id)
        db.add(sysm)
        await db.commit()
        sys_id = sysm.id

        # 등록 때 지정 — 데이터셋이 없어도 수동 등록 데이터 소스를 만들어 배정
        cat = await catalogs_router.create_catalog(
            None, CatalogCreate(name="예산 자료", source_type="ARGUS", system_id=sys_id), db)
        assert (cat.system_id, cat.system_name, cat.org_name) == (sys_id, "회계 시스템", "재무팀")
        manual = await catalogs_service.manual_datasource_of(db, cat.id)
        assert manual is not None and manual.system_id == sys_id
        # 트리에 안 보이는 수동 데이터 소스는 시스템 삭제 가드에서 세지 않는다
        assert await topology_service.count_system_datasources(db, sys_id) == 0
        # 다른 필드만 고치면 시스템은 그대로, null 을 보내면 해제
        cat = await catalogs_router.update_catalog(None, cat.id, CatalogUpdate(summary="요약"), db)
        assert cat.system_id == sys_id
        cat = await catalogs_router.update_catalog(None, cat.id, CatalogUpdate(system_id=None), db)
        assert cat.system_id is None and cat.org_name is None
        # 없는 시스템 404, 수집 카탈로그 409
        await _expect(catalogs_router.update_catalog(None, cat.id, CatalogUpdate(system_id=9999), db), 404)
        await _expect(catalogs_router.create_catalog(
            None, CatalogCreate(name="외부", source_type="EXTERNAL", system_id=sys_id), db), 409)
        ext = await catalogs_router.create_catalog(None, CatalogCreate(name="외부"), db)
        await _expect(catalogs_router.update_catalog(None, ext.id, CatalogUpdate(system_id=sys_id), db), 409)
        # 시스템을 지정하지 않은 빈 카탈로그에는 수동 데이터 소스를 만들지 않는다
        plain = await _argus_catalog(db, "빈 카탈로그")
        await catalogs_router.update_catalog(None, plain.id, CatalogUpdate(system_id=None), db)
        assert await catalogs_service.manual_datasource_of(db, plain.id) is None


_CASES = (
    _test_manual_dataset_on_argus_catalog,
    _test_manual_datasource_guards,
    _test_source_map_checks,
    _test_sync_skips_mapped_tables,
    _test_quality_target_and_lineage_use_mapping,
    _test_dataset_list_fields_and_filters,
    _test_catalog_system,
)


def test_catalog_registration() -> None:
    """pytest 진입점."""
    for case in _CASES:
        asyncio.run(case())


if __name__ == "__main__":
    for case in _CASES:
        asyncio.run(case())
    print("test_catalog_registration OK")
