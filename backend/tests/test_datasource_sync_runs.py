# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 수집 실행 1단계 (design/datasource-sync-scope-design.md) — 자기완결형, in-memory SQLite.

- 범위 저장·조회, 범위 없으면 수집 차단, 유형별 제약(Kudu 전체만, 미지원 유형, 엔진은 SYNC 연결 필요)
- metadata-sync 로 작업 전송(비밀값 복호화·범위 포함), 중복 실행 409, 서비스 연결 실패 503
- 진행 보고 반영(항목은 종료 때 한 번), 끝난 실행의 늦은 보고 무시, 5분 무보고 → FAILED 후 최종 보고 수용
- 취소(서비스가 모르면 바로 CANCELLED), 내장 sync 백그라운드 실행(컨테이너별 진행·부분 실패)
- 연결 테스트: metadata-sync 유형은 /v2/test 로

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_datasource_sync_runs.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select

import app.alert.models as am  # noqa: F401 — 알림 테이블 메타데이터 등록
import app.catalog.models as cm
from app.alert import service as alert_service
from app.alert.schemas import AlertRuleCreate
from app.catalog import catalogs_service, service
from app.catalog import sync as catalog_sync
from app.catalog import sync_runs_router as rt
from app.catalog import sync_runs_service as svc
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogCreate,
    DatasetCreate,
    DatasetUpdate,
    DatasourceCreate,
    SyncRunReport,
    SyncRunStart,
    SyncScheduleUpdate,
    SyncScope,
    SyncScopePreviewRequest,
    SyncSettingsUpdate,
)
from tests.test_catalog_entity_stage1 import _make_session as _make_base_session

_TABLES = ["catalog_datasource_sync_settings", "catalog_datasource_sync_runs", "catalog_datasource_sync_run_items",
           "argus_alert_rule", "argus_lineage_alert", "argus_alert_notification"]
ALL = SyncScope(include_all=True)


class _User:
    username = "tester"


async def _make_session():
    make_db = await _make_base_session()
    async with make_db() as db:
        conn = await db.connection()
        tables = [cm.Base.metadata.tables[t] for t in _TABLES]
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables, checkfirst=True))
        await db.commit()
    return make_db


async def _expect(coro, status: int) -> str:
    try:
        await coro
    except HTTPException as e:
        assert e.status_code == status, (e.status_code, e.detail)
        return e.detail
    raise AssertionError(f"expected HTTP {status}")


class _Sync:
    """metadata-sync 가짜 — 받은 요청을 기록하고 정해 둔 응답을 돌려준다."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.responses:
            return self.responses[key]
        if key == ("POST", "/v2/jobs"):
            return httpx.Response(202, json={"accepted": True})
        return httpx.Response(404, json={"detail": "없음"})


def _patch_httpx(fake: _Sync):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(fake.handler)
        return real(*a, **k)

    svc.httpx.AsyncClient = factory  # type: ignore[assignment]
    return lambda: setattr(svc.httpx, "AsyncClient", real)


async def _hive(db, name="hms"):
    ds = await service.create_datasource(db, DatasourceCreate(name=name, type="hive"))
    await service.save_datasource_configuration(db, ds.id, {
        "metastore_host": "hms", "metastore_port": "9083", "password": "s3cret"})
    return ds


# ---------------------------------------------------------------------------

async def _test_scope_settings_and_guards() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        hive = await _hive(db)
        st = await rt.get_sync_scope(hive.id, _User(), db)
        assert (st.scope, st.executor, st.container_label, st.supports_containers) == \
            (None, "METADATA_SYNC", "데이터베이스", True)
        # 범위 없으면 수집 차단
        assert "범위" in await _expect(rt.start_sync_run(hive.id, _User(), None, db), 400)

        scope = SyncScope.model_validate({"include": [{"path": ["sales"]}, {"path": ["hr"]}]})
        st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=scope), _User(), db)
        assert [i.path for i in st.scope.include] == [["sales"], ["hr"]] and st.updated_by == "tester"
        # 기존 설정을 다른 범위로 다시 저장 — onupdate 로 만료된 updated_at 을 읽다 500 이 나던 문제(DEF-019)
        first_at = st.updated_at
        st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=SyncScope.model_validate(
            {"include": [{"path": ["sales"], "tables": ["orders"]}]})), _User(), db)
        assert st.scope.include[0].tables == ["orders"] and st.updated_at is not None and first_at is not None

        # Kudu 는 전체만
        kudu = await service.create_datasource(db, DatasourceCreate(name="k", type="kudu"))
        assert not (await rt.get_sync_scope(kudu.id, _User(), db)).supports_containers
        await _expect(rt.put_sync_scope(kudu.id, SyncSettingsUpdate(scope=scope), _User(), db), 400)
        await rt.put_sync_scope(kudu.id, SyncSettingsUpdate(scope=ALL), _User(), db)

        # 아직 화면 수집을 지원하지 않는 유형
        impala = await service.create_datasource(db, DatasourceCreate(name="imp", type="impala"))
        assert (await rt.get_sync_scope(impala.id, _User(), db)).executor is None
        await _expect(rt.start_sync_run(impala.id, _User(), SyncRunStart(scope=ALL), db), 400)

        # 엔진은 카탈로그에 SYNC 로 연결해야 수집
        trino = await service.create_datasource(db, DatasourceCreate(name="tr", type="trino"))
        await service.save_datasource_configuration(db, trino.id, {"host": "t"})
        await _expect(rt.start_sync_run(trino.id, _User(), SyncRunStart(scope=ALL), db), 409)
        # 화면이 미리 막을 수 있게 설정 응답이 연결 없음을 알린다(DEF-068)
        assert (await rt.get_sync_scope(trino.id, _User(), db)).sync_catalog_missing is True
        cat = await catalogs_service.create_catalog(db, CatalogCreate(name="via trino"))
        await catalogs_service.add_connection(db, cat.id, CatalogConnectionCreate(datasource_id=trino.id, role="SYNC"))
        assert (await rt.get_sync_scope(trino.id, _User(), db)).sync_catalog_missing is False
        # 엔진이 아니면 늘 false
        assert (await rt.get_sync_scope(hive.id, _User(), db)).sync_catalog_missing is False

        # 접속 설정 없음
        nocfg = await service.create_datasource(db, DatasourceCreate(name="h2", type="hive"))
        await _expect(rt.start_sync_run(nocfg.id, _User(), SyncRunStart(scope=ALL), db), 400)
        await _expect(rt.get_sync_scope(9999, _User(), db), 404)


async def _test_dispatch_report_and_stale() -> None:
    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    try:
        async with make_db() as db:
            hive = await _hive(db)
            scope = SyncScope.model_validate({"include": [{"path": ["sales"]}]})
            await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=scope), _User(), db)
            run = await rt.start_sync_run(hive.id, _User(), None, db)
            assert (run.status, run.executor, run.requested_by) == ("QUEUED", "METADATA_SYNC", "tester")

            body = json.loads(fake.requests[-1].content)
            assert body["job_id"] == run.id and body["type"] == "hive" and body["datasource_key"] == hive.datasource_id
            assert body["config"]["password"] == "s3cret"          # 복호화한 값을 서비스로
            assert body["scope"]["include"] == [{"path": ["sales"]}]

            # 실행 중 중복 409
            await _expect(rt.start_sync_run(hive.id, _User(), None, db), 409)

            # 진행 보고
            r = await rt.report_sync_run(run.id, SyncRunReport(
                status="RUNNING", phase="COLLECTING", current_target="sales.orders",
                progress_done=3, progress_total=10, created=2, updated=1), _User(), db)
            assert (r.status, r.progress_done, r.progress_total, r.created_count, r.silent_seconds) == \
                ("RUNNING", 3, 10, 2, 0)
            # 최종 보고 — 항목은 이때 한 번
            r = await rt.report_sync_run(run.id, SyncRunReport(
                status="PARTIAL", phase="DONE", progress_done=10, progress_total=10, created=8, failed=2,
                error_message="2개 실패", items=[{"message": "sales.a: boom"}, {"message": "sales.b: boom"}],
                items_total=2), _User(), db)
            assert (r.status, r.failed_count, r.items_total) == ("PARTIAL", 2, 2) and r.finished_at
            items = await rt.list_sync_run_items(run.id, _User(), 200, None, None, db)
            assert [i.message for i in items] == ["sales.a: boom", "sales.b: boom"]
            # 끝난 뒤 늦은 보고는 무시
            r = await rt.report_sync_run(run.id, SyncRunReport(status="RUNNING", progress_done=1), _User(), db)
            assert r.status == "PARTIAL"

            # 재시도 — 같은 범위로 새 실행
            run2 = await rt.retry_sync_run(run.id, _User(), db)
            assert run2.id != run.id and run2.scope.include[0].path == ["sales"]
            await _expect(rt.retry_sync_run(run2.id, _User(), db), 409)

            # 5분 무보고 → FAILED, 이후 최종 보고는 받아들인다
            row = await db.get(cm.DatasourceSyncRun, run2.id)
            row.heartbeat_at = svc._utcnow() - timedelta(minutes=3)
            await db.commit()
            r = await rt.get_sync_run(run2.id, _User(), db)
            assert r.status == "QUEUED" and r.silent_seconds >= 179     # 2분 넘으면 화면은 "응답 없음"
            row.heartbeat_at = svc._utcnow() - timedelta(minutes=6)
            await db.commit()
            active = await rt.list_sync_runs(_User(), "ACTIVE", None, None, 0, 50, db)
            assert active == []
            r = await rt.get_sync_run(run2.id, _User(), db)
            assert r.status == "FAILED" and r.error_message == svc.STALE_MESSAGE
            r = await rt.report_sync_run(run2.id, SyncRunReport(status="SUCCEEDED", phase="DONE", created=5),
                                         _User(), db)
            assert r.status == "SUCCEEDED"

            runs = await rt.list_datasource_sync_runs(hive.id, _User(), 20, db)
            assert [x.id for x in runs] == [run2.id, run.id] and runs[0].datasource_name == "hms"
            # 데이터셋 목록 필터 링크용 외부 식별자(TODO-074)
            assert runs[0].datasource_key == hive.datasource_id
            await _expect(rt.get_sync_run(9999, _User(), db), 404)
            await _expect(rt.report_sync_run(9999, SyncRunReport(status="RUNNING"), _User(), db), 404)
    finally:
        restore()


async def _test_dispatch_failures_and_cancel() -> None:
    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    try:
        async with make_db() as db:
            hive = await _hive(db)
            # 서비스 다운 → 503, 실행은 FAILED 로 남는다
            fake.down = True
            await _expect(rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db), 503)
            fake.down = False
            runs = await rt.list_datasource_sync_runs(hive.id, _User(), 20, db)
            assert runs[0].status == "FAILED"

            # 서비스가 같은 데이터 소스를 이미 처리 중(409)·설정 오류(422 → 400)
            fake.responses[("POST", "/v2/jobs")] = httpx.Response(409, json={"detail": "이미 수집 중"})
            assert await _expect(rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db), 409) == "이미 수집 중"
            fake.responses[("POST", "/v2/jobs")] = httpx.Response(422, json={"detail": "URI 없음"})
            await _expect(rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db), 400)
            del fake.responses[("POST", "/v2/jobs")]

            # 취소 — 서비스가 작업을 모르면(404) 바로 CANCELLED
            run = await rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db)
            r = await rt.cancel_sync_run(run.id, _User(), db)
            assert r.status == "CANCELLED" and r.cancel_requested_by == "tester"
            assert fake.requests[-1].method == "DELETE" and fake.requests[-1].url.path == f"/v2/jobs/{run.id}"
            await _expect(rt.cancel_sync_run(run.id, _User(), db), 409)

            # 취소 수락(200) — 상태는 서비스의 최종 보고로 바뀐다
            run = await rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db)
            fake.responses[("DELETE", f"/v2/jobs/{run.id}")] = httpx.Response(200, json={"status": "RUNNING"})
            r = await rt.cancel_sync_run(run.id, _User(), db)
            assert r.status == "QUEUED" and r.cancel_requested_by == "tester"
    finally:
        restore()


async def _test_builtin_run() -> None:
    make_db = await _make_session()
    started: list[int] = []
    real_run, real_sync = svc._run_builtin, catalog_sync.sync_datasource_metadata

    async def fake_sync(session, key, database=None, table_filter=None):
        res = catalog_sync.SyncResult(datasource_id=key)
        if database == "bad":
            raise RuntimeError("connection reset")
        res.tables_created, res.tables_updated = 2, 1
        return res

    async def record(run_id, session_factory=None):
        started.append(run_id)

    svc._run_builtin = record
    catalog_sync.sync_datasource_metadata = fake_sync
    try:
        async with make_db() as db:
            my = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
            await service.save_datasource_configuration(db, my.id, {"host": "h", "username": "u", "password": "p"})
            scope = SyncScope.model_validate({"include": [{"path": ["shop"]}, {"path": ["bad"]}]})
            run = await rt.start_sync_run(my.id, _User(), SyncRunStart(scope=scope), db)
            assert run.executor == "BUILTIN" and started == [run.id]
        await asyncio.sleep(0)
        await real_run(run.id, session_factory=make_db)
        async with make_db() as db:
            r = await rt.get_sync_run(run.id, _User(), db)
            assert (r.status, r.phase, r.progress_done, r.progress_total) == ("PARTIAL", "DONE", 2, 2)
            assert (r.created_count, r.updated_count, r.failed_count) == (2, 1, 1)
            items = await rt.list_sync_run_items(run.id, _User(), 200, None, None, db)
            assert len(items) == 1 and items[0].message.startswith("bad: RuntimeError")

            # 취소 요청이 있으면 다음 컨테이너 전에 멈춘다
            run2 = await rt.start_sync_run(my.id, _User(), SyncRunStart(scope=scope), db)
            await rt.cancel_sync_run(run2.id, _User(), db)
        await real_run(run2.id, session_factory=make_db)
        async with make_db() as db:
            r = await rt.get_sync_run(run2.id, _User(), db)
            assert (r.status, r.progress_done) == ("CANCELLED", 0)
    finally:
        svc._run_builtin, catalog_sync.sync_datasource_metadata = real_run, real_sync


async def _test_connection_test_routing() -> None:
    calls = []

    async def fake(t, config):
        calls.append(t)
        return {"ok": True, "message": "ok"}

    real = svc.test_via_sync_service
    svc.test_via_sync_service = fake
    try:
        assert (await service.test_datasource_connection("hive", {}))["ok"] is True
        assert (await service.test_datasource_connection("iceberg-rest", {}))["ok"] is True
        assert (await service.test_datasource_connection("iceberg-hadoop", {}))["ok"] is True
        assert (await service.test_datasource_connection("impala", {}))["unsupported"] is True
        assert calls == ["hive", "iceberg-rest", "iceberg-hadoop"]
    finally:
        svc.test_via_sync_service = real

    fake_sync = _Sync()
    restore = _patch_httpx(fake_sync)
    try:
        fake_sync.responses[("POST", "/v2/test")] = httpx.Response(200, json={"ok": False, "message": "거절"})
        assert await svc.test_via_sync_service("hive", {}) == {"ok": False, "message": "거절", "latency_ms": None}
        fake_sync.down = True
        assert (await svc.test_via_sync_service("hive", {}))["ok"] is False
        assert (await svc.sync_service_status())["reachable"] is False
    finally:
        restore()


async def _synced(db, ds, *names, status="active"):
    for n in names:
        db.add(cm.Dataset(urn=f"{ds.datasource_id}.{n}.dataset", name=n.rsplit(".", 1)[1], datasource_id=ds.id,
                          is_synced="true", status=status, origin="PROD"))
    await db.commit()


async def _test_container_of_preview_and_remove_outside() -> None:
    c = svc.container_of
    assert c("hive", "k", "k.sales.orders.dataset") == "sales"
    assert c("iceberg-rest", "k", "k.sales.emea.orders.dataset") == "sales.emea"
    assert c("iceberg-hadoop", "k", "k.sales.emea.orders.dataset") == "sales.emea"
    assert c("trino", "k", "k.hive.default.t.dataset") == "hive"
    assert c("greenplum", "k", "k.gp.public.t.dataset") == "public"
    assert c("hive", "k", "other.sales.t.dataset") is None and c("hive", "k", "k.t.dataset") is None

    make_db = await _make_session()
    async with make_db() as db:
        hive = await _hive(db)
        await _synced(db, hive, "sales.a", "sales.b", "hr.c", "tmp.d")
        await _synced(db, hive, "hr.gone", status="removed")
        scope = SyncScope.model_validate({"include": [{"path": ["sales"]}]})
        pv = await rt.preview_sync_scope(hive.id, SyncScopePreviewRequest(scope=scope), _User(), db)
        assert (pv.outside_count, pv.outside_containers, pv.in_scope_count) == (2, ["hr", "tmp"], 2)
        # 기본은 그대로 둠
        st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=scope), _User(), db)
        assert st.removed_outside is None
        # 제거로 표시
        st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=scope, remove_outside=True), _User(), db)
        assert st.removed_outside == 2
        rows = (await db.execute(select(cm.Dataset.urn, cm.Dataset.status))).all()
        statuses = {u.split(".")[1] + "." + u.split(".")[2]: st_ for u, st_ in rows}
        assert statuses == {"sales.a": "active", "sales.b": "active", "hr.c": "removed", "tmp.d": "removed",
                            "hr.gone": "removed"}

    # metadata-sync 가 API 로 만든 데이터셋(is_synced=true)만 수집 데이터셋으로 센다 — 수동 등록은 제외(DEF-020)
    async with make_db() as db:
        h2 = await _hive(db, name="hms2")
        made = await service.create_dataset(db, DatasetCreate(name="a", datasource_id=h2.id, qualified_name="sales.a",
                                                              is_synced=True))
        manual = await service.create_dataset(db, DatasetCreate(name="m", datasource_id=h2.id, qualified_name="hr.m"))
        assert (made.is_synced, manual.is_synced) == ("true", "false")
        pv = await rt.preview_sync_scope(h2.id, SyncScopePreviewRequest(scope=SyncScope.model_validate(
            {"include": [{"path": ["hr"]}]})), _User(), db)
        assert (pv.outside_count, pv.in_scope_count) == (1, 0)
        # 이전 버전이 표시 없이 만든 데이터셋은 다음 갱신에서 표시된다
        upd = await service.update_dataset(db, manual.id, DatasetUpdate(is_synced=True))
        assert upd.is_synced == "true"


async def _test_browse() -> None:
    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    try:
        async with make_db() as db:
            hive = await _hive(db)
            await _synced(db, hive, "sales.a", "sales.b")
            await _synced(db, hive, "hr.x", status="removed")
            fake.responses[("POST", "/v2/browse")] = httpx.Response(200, json={"items": [
                {"name": "hr", "path": ["hr"]}, {"name": "sales", "path": ["sales"]},
                {"name": "sys", "path": ["sys"], "system": True}]})
            r = await rt.browse_datasource(hive.id, _User(), [], None, 0, db)
            assert [(i.name, i.collected, i.removed, i.system) for i in r.items] == \
                [("hr", 0, 1, False), ("sales", 2, 0, False), ("sys", 0, 0, True)]
            body = json.loads(fake.requests[-1].content)
            assert body["type"] == "hive" and body["config"]["password"] == "s3cret" and body["path"] == []
            # 검색·페이지
            r = await rt.browse_datasource(hive.id, _User(), [], "SAL", 0, db)
            assert [i.name for i in r.items] == ["sales"] and r.total == 1 and r.next_cursor is None
            r = await svc.browse(db, hive.id, [], None, 0, limit=2)
            assert r.next_cursor == 2 and len(r.items) == 2
            # 서비스 오류 매핑
            fake.responses[("POST", "/v2/browse")] = httpx.Response(422, json={"detail": "설정 오류"})
            await _expect(rt.browse_datasource(hive.id, _User(), [], None, 0, db), 400)
            fake.responses[("POST", "/v2/browse")] = httpx.Response(502, json={"detail": "연결 실패"})
            await _expect(rt.browse_datasource(hive.id, _User(), [], None, 0, db), 502)
            fake.down = True
            await _expect(rt.browse_datasource(hive.id, _User(), [], None, 0, db), 503)
            # Kudu·미지원 유형
            kudu = await service.create_datasource(db, DatasourceCreate(name="k", type="kudu"))
            await _expect(rt.browse_datasource(kudu.id, _User(), [], None, 0, db), 400)

            # 내장(mysql) 탐색은 백엔드가 직접
            my = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
            await service.save_datasource_configuration(db, my.id, {"host": "h", "username": "u", "password": "p"})
            await _synced(db, my, "shop.orders")

            async def fake_builtin(ds, config, path):
                assert config["password"] == "p"
                return [{"name": "shop", "path": ["shop"]}, {"name": "mysql", "path": ["mysql"], "system": True}]

            real = svc._browse_builtin
            svc._browse_builtin = fake_builtin
            try:
                r = await rt.browse_datasource(my.id, _User(), [], None, 0, db)
                assert [(i.name, i.collected, i.system) for i in r.items] == [("shop", 1, False), ("mysql", 0, True)]
            finally:
                svc._browse_builtin = real
    finally:
        restore()


async def _test_table_scope_and_excludes() -> None:
    """3단계 — 일부 테이블·제외 패턴: 판정·미리보기·범위 밖 제거·탐색 테이블 건수·내장 수집 필터·metadata-sync 전달."""
    sp = svc.split_of
    assert sp("trino", "k", "k.hive.default.t.dataset") == ("hive", "default.t")
    assert sp("greenplum", "k", "k.gp.public.t.dataset") == ("public", "t")
    assert sp("iceberg-rest", "k", "k.sales.emea.t.dataset") == ("sales.emea", "t")
    assert sp("mssql", "k", "k.app.dbo.t.dataset") == ("app", "dbo.t")
    assert sp("oracle", "k", "k.HR.EMPLOYEES.dataset") == ("HR", "EMPLOYEES")
    assert svc.executor_for("oracle") == svc.executor_for("mssql") == svc.executor_for("hive")
    scope = SyncScope.model_validate({
        "include": [{"path": ["sales"]}, {"path": ["hr"], "tables": ["people", "People", " teams "]}],
        "exclude": [{"table_pattern": " TMP_* "}, {"path": ["sales"], "table_pattern": "*_bak"}]})
    assert scope.include[1].tables == ["people", "People", "teams"] and scope.exclude[0].table_pattern == "TMP_*"
    allows = svc.scope_allows
    assert allows(scope, "sales", "orders") and not allows(scope, "sales", "tmp_x") and not allows(scope, "sales", "o_bak")
    assert allows(scope, "hr", "PEOPLE") and not allows(scope, "hr", "salary") and not allows(scope, "fin", "t")
    assert allows(scope, None, None)
    trino_all = SyncScope.model_validate({"include_all": True, "exclude": [{"table_pattern": "tmp_*"}]})
    assert allows(trino_all, "hive", "default.orders") and not allows(trino_all, "hive", "default.tmp_1")
    # Kudu 는 전체 + 제외 패턴은 허용
    svc._check_scope_for_type("kudu", trino_all)

    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    try:
        async with make_db() as db:
            hive = await _hive(db)
            await _synced(db, hive, "sales.a", "sales.tmp_b", "sales.c_bak", "hr.people", "hr.salary", "fin.x")
            pv = await rt.preview_sync_scope(hive.id, SyncScopePreviewRequest(scope=scope), _User(), db)
            assert (pv.outside_count, pv.in_scope_count) == (4, 2)
            assert pv.outside_containers == ["fin", "hr.salary", "sales.c_bak", "sales.tmp_b"]
            st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=scope, remove_outside=True), _User(), db)
            assert st.removed_outside == 4
            stored = json.loads((await db.get(cm.DatasourceSyncSetting, hive.id)).scope_json)
            assert stored["include"][0] == {"path": ["sales"]} and stored["include"][1]["tables"][0] == "people"

            # 탐색 — 테이블 항목은 컨테이너·상대 이름과 테이블별 건수
            fake.responses[("POST", "/v2/browse")] = httpx.Response(200, json={"items": [
                {"name": "people", "path": ["hr", "people"], "kind": "table", "container": ["hr"], "table": "people"},
                {"name": "salary", "path": ["hr", "salary"], "kind": "table", "container": ["hr"], "table": "salary"},
                {"name": "new", "path": ["hr", "new"], "kind": "table", "container": ["hr"], "table": "new"}]})
            r = await rt.browse_datasource(hive.id, _User(), ["hr"], None, 0, db)
            assert [(i.name, i.kind, i.container, i.table, i.collected, i.removed) for i in r.items] == [
                ("people", "table", ["hr"], "people", 1, 0), ("salary", "table", ["hr"], "salary", 0, 1),
                ("new", "table", ["hr"], "new", 0, 0)]
            assert json.loads(fake.requests[-1].content)["path"] == ["hr"]

            # metadata-sync 에 일부 테이블·제외 패턴을 그대로 전달(빈 필드는 빼고)
            fake.responses[("POST", "/v2/jobs")] = httpx.Response(202, json={"job_id": 1, "status": "QUEUED"})
            await rt.start_sync_run(hive.id, _User(), None, db)
            body = json.loads(fake.requests[-1].content)
            assert body["scope"]["include"][0] == {"path": ["sales"]}
            assert body["scope"]["exclude"][1] == {"path": ["sales"], "table_pattern": "*_bak"}
    finally:
        restore()

    # 내장 수집 — 테이블 필터를 넘기고, 필터가 거른 테이블은 제거로도 표시하지 않는다
    make_db = await _make_session()
    seen: list = []

    async def fake_sync(session, key, database=None, table_filter=None):
        seen.append((database, [n for n in ("orders", "tmp_x", "refunds") if table_filter(database, n)]))
        return catalog_sync.SyncResult(datasource_id=key)

    async def no_task(run_id, session_factory=None):
        pass

    real, real_run = catalog_sync.sync_datasource_metadata, svc._run_builtin
    catalog_sync.sync_datasource_metadata, svc._run_builtin = fake_sync, no_task
    try:
        async with make_db() as db:
            my = await service.create_datasource(db, DatasourceCreate(name="m2", type="mysql"))
            await service.save_datasource_configuration(db, my.id, {"host": "h", "username": "u", "password": "p"})
            sc = SyncScope.model_validate({"include": [{"path": ["shop"], "tables": ["orders", "tmp_x"]}],
                                           "exclude": [{"table_pattern": "tmp_*"}]})
            await rt.put_sync_scope(my.id, SyncSettingsUpdate(scope=sc), _User(), db)
            run_id = (await svc.start_run(db, my.id, None, "tester")).id
        await real_run(run_id, make_db)
        assert seen == [("shop", ["orders"])]
    finally:
        catalog_sync.sync_datasource_metadata, svc._run_builtin = real, real_run


async def _test_monitoring_filters_alerts_and_purge() -> None:
    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    try:
        async with make_db() as db:
            hive = await _hive(db)
            other = await _hive(db, "hms2")
            rule = await alert_service.create_rule(db, AlertRuleCreate(
                rule_name="sync fail", scope_type="ALL", trigger_type="SYNC_FAILED"), "admin")
            await alert_service.create_rule(db, AlertRuleCreate(
                rule_name="sync fail no partial", scope_type="DATASOURCE", scope_id=hive.id,
                trigger_type="SYNC_FAILED", trigger_config='{"include_partial": false}'), "admin")
            await db.commit()

            r1 = await rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db)
            await rt.report_sync_run(r1.id, SyncRunReport(status="PARTIAL", phase="DONE", failed=1, items=[
                {"message": "a: boom", "target": "sales.a"}, {"level": "WARNING", "message": "b: slow"}]), _User(), db)
            r2 = await rt.start_sync_run(other.id, _User(), SyncRunStart(scope=ALL), db)
            await rt.report_sync_run(r2.id, SyncRunReport(status="FAILED", phase="DONE",
                                                          error_message="연결 실패"), _User(), db)
            r3 = await rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db)

            alerts = (await db.execute(select(am.LineageAlert))).scalars().all()
            # PARTIAL: ALL 규칙만(두 번째 규칙은 include_partial=false), FAILED(다른 데이터 소스): ALL 규칙만
            assert sorted((a.alert_type, a.entity_ref, a.severity, a.rule_id) for a in alerts) == sorted([
                ("SYNC_FAILED", "hms", "WARNING", rule.id),
                ("SYNC_FAILED", "hms2", "BREAKING", rule.id)])

            # 필터
            assert [r.id for r in await rt.list_sync_runs(_User(), "PROBLEM", None, None, 0, 50, db)] == [r2.id, r1.id]
            assert [r.id for r in await rt.list_sync_runs(_User(), "ACTIVE", None, None, 0, 50, db)] == [r3.id]
            assert [r.id for r in await rt.list_sync_runs(_User(), None, hive.id, None, 0, 50, db)] == [r3.id, r1.id]
            assert [r.id for r in await rt.list_sync_runs(_User(), None, None, None, 1, 1, db)] == [r2.id]
            future = svc._utcnow() + timedelta(hours=1)
            assert await rt.list_sync_runs(_User(), None, None, future, 0, 50, db) == []
            items = await rt.list_sync_run_items(r1.id, _User(), 200, "WARNING", None, db)
            assert [i.message for i in items] == ["b: slow"]
            items = await rt.list_sync_run_items(r1.id, _User(), 200, None, "SALES.A", db)
            assert [i.message for i in items] == ["a: boom"]
            sm = await rt.sync_runs_summary(_User(), db)
            assert (sm.active, sm.failed_24h, sm.succeeded_24h) == (1, 2, 0)

            # 응답 없음으로 실패 처리돼도 SYNC_FAILED
            row = await db.get(cm.DatasourceSyncRun, r3.id)
            row.heartbeat_at = svc._utcnow() - timedelta(minutes=6)
            await db.commit()
            assert await svc.finalize_stale(db) == 1
            n_alerts = len((await db.execute(select(am.LineageAlert))).scalars().all())
            assert n_alerts == 4   # ALL + DATASOURCE(FAILED 는 include_partial 무관)

            # SYNC_STALE — 예약이 켜진 데이터 소스만(4단계), 성공 없음 → 기준 시간 지나면 한 번만
            stale_rule = await alert_service.create_rule(db, AlertRuleCreate(
                rule_name="stale", scope_type="ALL", trigger_type="SYNC_STALE", trigger_config='{"stale_hours": 2}'),
                "admin")
            await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=ALL), _User(), db)
            later = svc._utcnow() + timedelta(hours=3)
            assert await svc.evaluate_stale_alerts(db, now=later) == 0     # 범위만 있고 예약 없음 → 대상 아님
            await rt.put_sync_schedule(hive.id, SyncScheduleUpdate(enabled=True, interval_minutes=60), _User(), db)
            assert await svc.evaluate_stale_alerts(db) == 0
            assert await svc.evaluate_stale_alerts(db, now=later) == 1
            assert await svc.evaluate_stale_alerts(db, now=later) == 0      # 열린 알림이 있으면 다시 안 만듦
            # 성공한 수집이 있으면 그 시각부터
            r4 = await rt.start_sync_run(hive.id, _User(), SyncRunStart(scope=ALL), db)
            await rt.report_sync_run(r4.id, SyncRunReport(status="SUCCEEDED", phase="DONE"), _User(), db)
            for a in (await db.execute(select(am.LineageAlert))).scalars():
                a.status = "RESOLVED"
            await db.commit()
            assert await svc.evaluate_stale_alerts(db, now=svc._utcnow() + timedelta(hours=1)) == 0
            # stale_hours 생략 → 예약 주기의 2배(60분 → 2시간). 다른 데이터 소스 규칙이라 hive 열린 알림과 겹치지 않음
            (await db.get(am.AlertRule, stale_rule.id)).is_active = "false"
            await db.commit()
            await rt.put_sync_scope(other.id, SyncSettingsUpdate(scope=ALL), _User(), db)
            await rt.put_sync_schedule(other.id, SyncScheduleUpdate(enabled=True, interval_minutes=60), _User(), db)
            await alert_service.create_rule(db, AlertRuleCreate(
                rule_name="stale default", scope_type="DATASOURCE", scope_id=other.id, trigger_type="SYNC_STALE"), "admin")
            r5 = await rt.start_sync_run(other.id, _User(), SyncRunStart(scope=ALL), db)
            await rt.report_sync_run(r5.id, SyncRunReport(status="SUCCEEDED", phase="DONE"), _User(), db)
            assert await svc.evaluate_stale_alerts(db, now=svc._utcnow() + timedelta(minutes=100)) == 0
            assert await svc.evaluate_stale_alerts(db, now=svc._utcnow() + timedelta(minutes=130)) == 1

            # 이력 정리 — 보관 기간 지난 끝난 실행만
            old = await db.get(cm.DatasourceSyncRun, r1.id)
            old.queued_at = svc._utcnow() - timedelta(days=100)
            await db.commit()
            assert await svc.purge_old_runs(db, 90) == 1
            assert await db.get(cm.DatasourceSyncRun, r1.id) is None
            assert await rt.list_sync_run_items(r2.id, _User(), 200, None, None, db) == []
    finally:
        restore()


async def _test_schedule() -> None:
    make_db = await _make_session()
    fake = _Sync()
    restore = _patch_httpx(fake)
    svc._schedule_backoff.clear()
    try:
        async with make_db() as db:
            hive = await _hive(db)
            # 범위 없이 켤 수 없음, 켜려면 주기 필요
            assert "범위" in await _expect(rt.put_sync_schedule(
                hive.id, SyncScheduleUpdate(enabled=True, interval_minutes=60), _User(), db), 400)
            try:
                SyncScheduleUpdate(enabled=True)
                raise AssertionError("interval 필요")
            except ValueError:
                pass
            try:
                SyncScheduleUpdate(enabled=True, interval_minutes=5)
                raise AssertionError("15분 미만 거부")
            except ValueError:
                pass
            await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=ALL), _User(), db)
            st = await rt.put_sync_schedule(hive.id, SyncScheduleUpdate(enabled=True, interval_minutes=60), _User(), db)
            assert st.schedule_enabled and st.schedule_interval_minutes == 60 and st.next_scheduled_at is not None

            now = svc._utcnow()
            assert await svc.run_due_schedules(db, now=now) == 0                       # 아직 아님
            assert await svc.run_due_schedules(db, now=now + timedelta(minutes=61)) == 1
            runs = await rt.list_sync_runs(_User(), None, hive.id, None, 0, 50, db)
            assert [(r.trigger_type, r.requested_by) for r in runs] == [("SCHEDULE", "scheduler")]
            # 실행 중이면 건너뜀, 끝나면 그 접수 시각 + 주기
            assert await svc.run_due_schedules(db, now=now + timedelta(minutes=200)) == 0
            await rt.report_sync_run(runs[0].id, SyncRunReport(status="SUCCEEDED", phase="DONE"), _User(), db)
            st = await rt.get_sync_scope(hive.id, _User(), db)
            assert abs((st.next_scheduled_at - (svc._aware(runs[0].queued_at) + timedelta(minutes=60))).total_seconds()) < 1
            assert await svc.run_due_schedules(db, now=now + timedelta(minutes=200)) == 1

            # 실행 행 없이 실패(접속 설정 없음) → 주기만큼 쉬었다 다시
            nocfg = await service.create_datasource(db, DatasourceCreate(name="h2", type="hive"))
            await rt.put_sync_scope(nocfg.id, SyncSettingsUpdate(scope=ALL), _User(), db)
            await rt.put_sync_schedule(nocfg.id, SyncScheduleUpdate(enabled=True, interval_minutes=30), _User(), db)
            t = svc._utcnow() + timedelta(minutes=31)
            assert await svc.run_due_schedules(db, now=t) == 0 and nocfg.id in svc._schedule_backoff
            assert await svc.run_due_schedules(db, now=t + timedelta(minutes=1)) == 0

            # 끄기 · 범위를 지우면 예약도 꺼짐
            st = await rt.put_sync_schedule(hive.id, SyncScheduleUpdate(enabled=False), _User(), db)
            assert not st.schedule_enabled and st.schedule_interval_minutes == 60 and st.next_scheduled_at is None
            await rt.put_sync_schedule(hive.id, SyncScheduleUpdate(enabled=True, interval_minutes=15), _User(), db)
            st = await rt.put_sync_scope(hive.id, SyncSettingsUpdate(scope=None), _User(), db)
            assert not st.schedule_enabled
            # 지원하지 않는 유형
            impala = await service.create_datasource(db, DatasourceCreate(name="imp", type="impala"))
            await _expect(rt.put_sync_schedule(impala.id, SyncScheduleUpdate(enabled=False), _User(), db), 400)
    finally:
        restore()
        svc._schedule_backoff.clear()


_CASES = (
    _test_schedule,
    _test_container_of_preview_and_remove_outside,
    _test_table_scope_and_excludes,
    _test_browse,
    _test_monitoring_filters_alerts_and_purge,
    _test_scope_settings_and_guards,
    _test_dispatch_report_and_stale,
    _test_dispatch_failures_and_cancel,
    _test_builtin_run,
    _test_connection_test_routing,
)


def test_datasource_sync_runs() -> None:
    """pytest 진입점."""
    for case in _CASES:
        asyncio.run(case())


if __name__ == "__main__":
    for case in _CASES:
        asyncio.run(case())
    print("test_datasource_sync_runs OK")
