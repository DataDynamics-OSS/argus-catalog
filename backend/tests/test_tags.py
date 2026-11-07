# SPDX-License-Identifier: Apache-2.0
"""태그 서비스·권한 테스트 (in-memory SQLite, 자기완결형).

검증:
- 스키마: 이름 공백 정리, 색상 #rrggbb 만 허용(소문자 정규화)
- 생성·수정: 대소문자 무시 이름 중복 409, 수정 시 연결 유지
- 사용 현황: 데이터셋·OCI 모델·TAG 범위 알림 규칙 집계
- 삭제: TAG 범위 알림 규칙이 있으면 409, 없으면 데이터셋 연결 CASCADE
- 데이터셋 연결: 없는 태그 404, 중복 연결 멱등, 해제 시 과거 중복 행까지 정리
- 소유자 제거가 다른 데이터셋의 소유자 행을 건드리지 않는지
- tags.manage 는 명시 허용형(미설정이면 admin 전용)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_tags
    # 또는: pytest tests/test_tags.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.oci_hub.models as om
from app.alert.models import AlertRule
from app.catalog import service
from app.catalog.schemas import TagCreate, TagUpdate
from app.permissions import router as perm
from app.permissions.models import Permission


def _test_schema_validation() -> None:
    t = TagCreate(name="  PII ", color="#AABBCC")
    assert t.name == "PII" and t.color == "#aabbcc"
    for bad in ({"name": "x", "color": "#3b82f6ff"}, {"name": "x", "color": "blue1"}, {"name": "   "}):
        try:
            TagCreate(**bad)
        except ValidationError:
            continue
        raise AssertionError(f"검증 통과하면 안 됨: {bad}")
    assert TagUpdate(color=None).model_dump(exclude_unset=True) == {"color": None}


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # SQLite 는 FK(ON DELETE CASCADE)를 기본으로 끈다
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    tables = [
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        cm.Base.metadata.tables["catalog_tags"],
        cm.Base.metadata.tables["catalog_dataset_tags"],
        cm.Base.metadata.tables["catalog_owners"],
        cm.Base.metadata.tables["catalog_dataset_schemas"],
        om.OciModel.__table__,
        om.OciModelTag.__table__,
        AlertRule.__table__,
        Permission.__table__,
    ]
    # FK 를 켰으므로 참조 대상 테이블(분류 체계·조직 등)도 함께 만든다
    pending, seen = list(tables), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        if t not in tables:
            tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _expect(coro, status: int) -> None:
    try:
        await coro
    except service.TagError as e:
        assert e.status_code == status, (e.status_code, e.message)
        return
    raise AssertionError(f"TagError({status}) 가 나야 함")


async def _run_tags() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        pii = await service.create_tag(db, TagCreate(name="PII", color="#ef4444"))
        gold = await service.create_tag(db, TagCreate(name="gold"))

        # --- 생성·수정 ---------------------------------------------------------------
        await _expect(service.create_tag(db, TagCreate(name="pii")), 409)          # 대소문자만 다름
        await _expect(service.update_tag(db, gold.id, TagUpdate(name="Pii")), 409)
        renamed = await service.update_tag(db, gold.id, TagUpdate(name="Gold", color="#EAB308"))
        assert renamed and renamed.name == "Gold" and renamed.color == "#eab308"   # 자기 이름 대소문자 변경 허용
        kept = await service.update_tag(db, gold.id, TagUpdate(name=None, color=None, description="d"))
        assert kept and kept.name == "Gold" and kept.color == "#eab308" and kept.description == "d"
        assert await service.update_tag(db, 9999, TagUpdate(name="z")) is None

        # --- 데이터셋 연결 -----------------------------------------------------------
        ds = cm.Datasource(datasource_id="d1", name="ds", type="trino", origin="PROD")
        db.add(ds)
        await db.flush()
        d1 = cm.Dataset(urn="x.1.dataset", name="t1", datasource_id=ds.id, origin="PROD", status="active")
        d2 = cm.Dataset(urn="x.2.dataset", name="t2", datasource_id=ds.id, origin="PROD", status="active")
        db.add_all([d1, d2])
        await db.commit()

        async def links(dataset_id: int, tag_id: int) -> int:
            return int(await db.scalar(select(func.count(cm.DatasetTag.id)).where(
                cm.DatasetTag.dataset_id == dataset_id, cm.DatasetTag.tag_id == tag_id)) or 0)

        assert await service.add_dataset_tag(db, 9999, pii.id) is False
        await _expect(service.add_dataset_tag(db, d1.id, 9999), 404)
        assert await service.add_dataset_tag(db, d1.id, pii.id)
        assert await service.add_dataset_tag(db, d1.id, pii.id)                  # 멱등
        assert await links(d1.id, pii.id) == 1
        db.add(cm.DatasetTag(dataset_id=d1.id, tag_id=pii.id))                   # 과거에 생긴 중복 행
        await db.commit()
        assert await service.remove_dataset_tag(db, d1.id, pii.id)
        assert await links(d1.id, pii.id) == 0
        assert await service.remove_dataset_tag(db, d1.id, pii.id) is False

        # --- 소유자 제거는 해당 데이터셋 범위만 ----------------------------------------
        o2 = cm.Owner(dataset_id=d2.id, owner_name="kim", owner_type="TECHNICAL_OWNER")
        db.add(o2)
        await db.commit()
        assert await service.remove_dataset_owner(db, o2.id, d1.id) is False      # 다른 데이터셋 경로로는 불가
        assert await service.remove_dataset_owner(db, o2.id, d2.id)

        # --- 사용 현황·삭제 ------------------------------------------------------------
        await service.add_dataset_tag(db, d1.id, pii.id)
        model = om.OciModel(name="org/llm")
        db.add(model)
        await db.flush()
        db.add(om.OciModelTag(model_id=model.id, tag_id=pii.id))
        rule = AlertRule(rule_name="PII 스키마 감시", scope_type="TAG", scope_id=pii.id, trigger_type="ANY")
        db.add(rule)
        await db.commit()

        usage = await service.get_tag_usage(db, pii.id)
        assert usage and usage.total_datasets == 1
        assert [m.name for m in usage.oci_models] == ["org/llm"]
        assert [(r.name, r.is_active) for r in usage.alert_rules] == [("PII 스키마 감시", True)]

        await _expect(service.delete_tag(db, pii.id), 409)                        # 알림 규칙이 감시 중
        await db.delete(rule)
        await db.commit()
        assert await service.delete_tag(db, pii.id)
        assert await links(d1.id, pii.id) == 0                                    # CASCADE
        assert int(await db.scalar(select(func.count(om.OciModelTag.id))) or 0) == 0
        assert await service.delete_tag(db, pii.id) is False


async def _run_permission() -> None:
    session_factory = await _make_session()
    user = SimpleNamespace(is_admin=False, is_superuser=False)
    admin = SimpleNamespace(is_admin=True, is_superuser=False)
    async with session_factory() as db:
        assert "tags.manage" in perm.DEFAULT_DENY_FEATURES
        assert await perm.check_feature_granted(db, admin, "tags.manage")
        assert not await perm.check_feature_granted(db, user, "tags.manage")         # 미설정 = admin 전용
        await perm._put_matrix(db, "FEATURE", {"tags.manage": ["argus-user"]})
        assert await perm.check_feature_granted(db, user, "tags.manage")


def test_tags() -> None:
    """pytest 진입점."""
    _test_schema_validation()
    asyncio.run(_run_tags())
    asyncio.run(_run_permission())


if __name__ == "__main__":
    test_tags()
    print("test_tags OK")
