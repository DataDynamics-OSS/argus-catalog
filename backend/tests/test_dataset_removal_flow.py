# SPDX-License-Identifier: Apache-2.0
"""데이터셋 제거·삭제 흐름 결함 회귀 테스트 (자기완결형, in-memory SQLite).

- DEF-055: 동기화가 데이터셋을 removed 로 바꾸면 모든 컬럼 DROP 으로 알림 규칙을 평가한다
  (매핑 깨짐 → 하위 데이터셋 알림, 요약 앞 "테이블 제거됨"). 외부 metadata-sync 의 status=removed 수정도 같다.
- DEF-059: 데이터셋 단건 삭제도 데이터 소스 삭제처럼 댓글·DATASET 범위 알림 규칙·쿼리 리니지 참조를 정리한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_dataset_removal_flow.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, text

import app.catalog.models as cm
from app.alert.models import AlertNotification, AlertRule, LineageAlert
from app.catalog import service
from app.catalog import sync as catalog_sync
from app.catalog.schemas import DatasourceCreate
from app.comments.models import Comment
from app.permissions.models import Permission
from tests.test_datasource_stage2 import _make_session as _stage2_session


async def _make_session():
    make_db = await _stage2_session()
    engine = make_db.kw["bind"]
    async with engine.begin() as conn:
        for t in (cm.Base.metadata.tables["catalog_tags"], cm.Base.metadata.tables["catalog_dataset_tags"],
                  cm.Base.metadata.tables["catalog_owners"], cm.DatasetColumnMapping.__table__,
                  LineageAlert.__table__, AlertNotification.__table__, Permission.__table__):
            await conn.run_sync(lambda sc, tt=t: tt.create(sc, checkfirst=True))
    return make_db


def _ds(datasource, name):
    return cm.Dataset(urn=f"{datasource.datasource_id}.{name}.dataset", name=name,
                      datasource_id=datasource.id, origin="DEV",
                      qualified_name=f"{datasource.datasource_id}.{name}", status="active")


async def _seed_lineage(db):
    """원천 app.src(amount, id) → 하위 app.dst(amt) 매핑, 모든 데이터셋 대상 매핑 깨짐 규칙."""
    ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
    src, dst = _ds(ds, "app.src"), _ds(ds, "app.dst")
    db.add_all([src, dst])
    await db.flush()
    for i, col in enumerate(("amount", "id")):
        db.add(cm.DatasetSchema(dataset_id=src.id, field_path=col, field_type="INT", ordinal=i))
    db.add(cm.DatasetSchema(dataset_id=dst.id, field_path="amt", field_type="INT", ordinal=0))
    lin = cm.DatasetLineage(source_dataset_id=src.id, target_dataset_id=dst.id, relation_type="ETL",
                            lineage_source="MANUAL")
    db.add(lin)
    await db.flush()
    db.add(cm.DatasetColumnMapping(dataset_lineage_id=lin.id, source_column="amount", target_column="amt"))
    db.add(AlertRule(rule_name="매핑", scope_type="ALL", trigger_type="MAPPING_BROKEN"))
    await db.commit()
    return ds, src.id, dst.id


# ---------------------------------------------------------------------------
# DEF-055
# ---------------------------------------------------------------------------

async def _test_removed_alert_helper() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        _, src_id, dst_id = await _seed_lineage(db)
        made = await service.evaluate_dataset_removed_alerts(db, src_id)
        await db.commit()
        assert made == 1
        alert = (await db.execute(select(LineageAlert))).scalar_one()
        assert alert.source_dataset_id == src_id and alert.affected_dataset_id == dst_id
        assert alert.severity == "BREAKING"
        assert alert.change_summary.startswith("테이블 제거됨 — 매핑 컬럼 변경: 'amount' ")

        # 컬럼이 없는 데이터셋은 평가하지 않는다
        assert await service.evaluate_dataset_removed_alerts(db, dst_id + 100) == 0


async def _test_sync_removed_triggers_alert() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds, src_id, dst_id = await _seed_lineage(db)
        await service.save_datasource_configuration(db, ds.id, {"host": "h", "password": "pw"})
        dsid = ds.datasource_id

    # 원본에는 app.dst 만 남고 app.src 가 사라졌다
    tables = [catalog_sync.TableInfo(database="app", name="dst", table_type="BASE TABLE", table_comment="",
                                     columns=[catalog_sync.ColumnInfo(name="amt", data_type="INT",
                                                                      native_type="int", nullable=True, ordinal=0)])]

    async def fake_read(*_a, **_k):
        return list(tables)

    async def no_sample(*_a, **_k):
        return None

    orig = catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows
    catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = fake_read, no_sample
    try:
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert res.tables_removed == 1, res
            assert (await db.get(cm.Dataset, src_id)).status == "removed"
            # (남은 app.dst 의 컬럼 비교로 생기는 스키마 변경 알림은 여기서 보지 않는다)
            removed_alerts = [a for a in (await db.execute(select(LineageAlert))).scalars().all()
                              if a.change_summary.startswith("테이블 제거됨")]
            assert [(a.source_dataset_id, a.affected_dataset_id) for a in removed_alerts] == [(src_id, dst_id)]

            # 이미 removed 인 데이터셋은 다음 동기화에서 다시 평가하지 않는다
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
            assert res.tables_removed == 0
            again = [a for a in (await db.execute(select(LineageAlert))).scalars().all()
                     if a.change_summary.startswith("테이블 제거됨")]
            assert len(again) == 1
    finally:
        catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows = orig


async def _test_removed_alert_failure_does_not_break(monkeypatch_fn) -> None:
    make_db = await _make_session()
    async with make_db() as db:
        _, src_id, _ = await _seed_lineage(db)
        restore = monkeypatch_fn()
        try:
            assert await service.evaluate_dataset_removed_alerts(db, src_id) == 0
        finally:
            restore()
        # 세션은 계속 쓸 수 있다
        d = await db.get(cm.Dataset, src_id)
        d.status = "removed"
        await db.commit()
        assert (await db.get(cm.Dataset, src_id)).status == "removed"


# ---------------------------------------------------------------------------
# DEF-059
# ---------------------------------------------------------------------------

async def _test_delete_dataset_cleans_references() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        d, keep = _ds(ds, "db.t"), _ds(ds, "db.k")
        db.add_all([d, keep])
        await db.flush()
        db.add_all([
            AlertRule(rule_name="dset", scope_type="DATASET", scope_id=d.id, trigger_type="ANY"),
            AlertRule(rule_name="keep", scope_type="DATASET", scope_id=keep.id, trigger_type="ANY"),
            AlertRule(rule_name="src", scope_type="DATASOURCE", scope_id=ds.id, trigger_type="ANY"),
            Comment(entity_type="dataset", entity_id=str(d.id), content="c", author_name="a"),
            Comment(entity_type="dataset", entity_id=str(keep.id), content="c", author_name="a"),
        ])
        await db.execute(text(
            "INSERT INTO argus_query_lineage (source_table, target_table, source_dataset_id,"
            " target_dataset_id) VALUES ('a','b',:s,:t)"), {"s": keep.id, "t": d.id})
        await db.commit()
        d_id, keep_id = d.id, keep.id

        assert await service.delete_dataset(db, d_id)

        rules = sorted((await db.execute(select(AlertRule.rule_name))).scalars().all())
        assert rules == ["keep", "src"]
        comments = (await db.execute(select(Comment.entity_id))).scalars().all()
        assert comments == [str(keep_id)]
        ql = (await db.execute(text(
            "SELECT source_dataset_id, target_dataset_id FROM argus_query_lineage"))).one()
        assert ql == (keep_id, None)


def test_removed_alert_helper() -> None:
    asyncio.run(_test_removed_alert_helper())


def test_sync_removed_triggers_alert() -> None:
    asyncio.run(_test_sync_removed_triggers_alert())


def test_removed_alert_failure_does_not_break(monkeypatch) -> None:
    import app.alert.service as alert_service

    def patch():
        async def boom(*_a, **_k):
            raise RuntimeError("평가 실패")
        monkeypatch.setattr(alert_service, "evaluate_rules_and_create_alerts", boom)
        return monkeypatch.undo

    asyncio.run(_test_removed_alert_failure_does_not_break(patch))


def test_delete_dataset_cleans_references() -> None:
    asyncio.run(_test_delete_dataset_cleans_references())


def test_update_status_removed_triggers_alert(monkeypatch) -> None:
    """외부 metadata-sync 는 PUT 데이터셋(status=removed)으로 표시한다 — 처음 removed 가 될 때만 평가."""
    import app.embedding.service as embedding_service
    from app.catalog.schemas import DatasetUpdate

    calls: list[int] = []

    async def fake_eval(_s, dataset_id):
        calls.append(dataset_id)
        return 0

    async def fake_build(_s, dataset):
        return dataset.status

    async def no_embed(*_a, **_k):
        return None

    monkeypatch.setattr(service, "evaluate_dataset_removed_alerts", fake_eval)
    monkeypatch.setattr(service, "_build_dataset_response", fake_build)
    monkeypatch.setattr(embedding_service, "embed_dataset_background", no_embed)

    async def go():
        make_db = await _make_session()
        async with make_db() as db:
            _, src_id, _ = await _seed_lineage(db)
            assert await service.update_dataset(db, src_id, DatasetUpdate(status="removed")) == "removed"
            assert await service.update_dataset(db, src_id, DatasetUpdate(status="removed")) == "removed"
            assert await service.update_dataset(db, src_id, DatasetUpdate(status="active")) == "active"
            return src_id

    src_id = asyncio.run(go())
    assert calls == [src_id]
