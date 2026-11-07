# SPDX-License-Identifier: Apache-2.0
"""용어집 서비스·권한 테스트 (in-memory SQLite, 자기완결형).

검증:
- 생성: 이름 공백 정리·유형별 중복 409(분류와 용어는 같은 이름 허용), 상위 검증(없음 404 / 용어 아래 400)
- 수정: 자기 자신·하위로 이동(순환) 400, 상위로 끌어올리기 허용, 분류→용어 변경 가드
- 데이터셋 연결: 없는 용어 404, 분류 400, 중복 연결 멱등, 해제 시 과거 중복 행까지 정리
- 명시 허용형 기능 권한(glossary.edit): 미설정이면 admin 전용, 역할 명시 시 허용,
  매트릭스 저장에서 '전 역할 허용'이 행으로 유지되는지

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_glossary
    # 또는: pytest tests/test_glossary.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.embedding.service as emb
from app.catalog import service
from app.catalog.schemas import GlossaryTermCreate
from app.permissions import router as perm
from app.permissions.models import Permission
from app.settings.models import CatalogConfiguration


def _test_schema_validation() -> None:
    assert GlossaryTermCreate(name="  고객  ").name == "고객"
    for bad in ({"name": "   "}, {"name": "x", "term_type": "term"}):
        try:
            GlossaryTermCreate(**bad)
        except ValidationError:
            continue
        raise AssertionError(f"검증 통과하면 안 됨: {bad}")


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        cm.Base.metadata.tables["catalog_glossary_terms"],
        cm.Base.metadata.tables["catalog_dataset_glossary_terms"],
        Permission.__table__,
        # /permissions/me 의 데이터셋 등록 판정이 설정(catalog_dataset_create_by_users)을 읽음
        CatalogConfiguration.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _expect(coro, status: int) -> None:
    try:
        await coro
    except service.GlossaryError as e:
        assert e.status_code == status, (e.status_code, e.message)
        return
    raise AssertionError(f"GlossaryError({status}) 가 나야 함")


async def _new(db, name: str, term_type: str = "TERM", parent_id: int | None = None):
    return await service.create_glossary_term(
        db, GlossaryTermCreate(name=name, term_type=term_type, parent_id=parent_id))


async def _run_glossary() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        a = await _new(db, "A", "CATEGORY")
        b = await _new(db, "B", "CATEGORY", a.id)
        c = await _new(db, "C", "CATEGORY", b.id)
        t = await _new(db, "고객 생애 가치", "TERM", c.id)

        # --- 생성 검증 -------------------------------------------------------------
        await _expect(_new(db, "A", "CATEGORY"), 409)           # 같은 이름의 분류
        same_name_term = await _new(db, "A", "TERM")            # 분류와 같은 이름의 용어는 허용(유형별 유일)
        assert same_name_term.name == "A" and same_name_term.term_type == "TERM"
        await _expect(_new(db, "A", "TERM"), 409)               # 같은 이름의 용어
        empty_cat = await _new(db, "빈 분류", "CATEGORY")
        await _expect(service.update_glossary_term(db, empty_cat.id, {"name": "A", "term_type": "TERM"}), 409)
        await _expect(service.update_glossary_term(db, empty_cat.id, {"name": "A"}), 409)  # 분류끼리 충돌
        await _expect(service.update_glossary_term(db, same_name_term.id, {"term_type": "CATEGORY"}), 409)
        converted = await service.update_glossary_term(db, empty_cat.id, {"term_type": "TERM"})
        assert converted and converted.term_type == "TERM"
        await _expect(_new(db, "X", parent_id=9999), 404)       # 없는 상위
        await _expect(_new(db, "X", parent_id=t.id), 400)       # 용어 아래

        # --- 이동(순환) 검증 -------------------------------------------------------
        await _expect(service.update_glossary_term(db, a.id, {"parent_id": a.id}), 400)
        await _expect(service.update_glossary_term(db, a.id, {"parent_id": c.id}), 400)
        moved = await service.update_glossary_term(db, c.id, {"parent_id": a.id})  # 상위로 끌어올리기
        assert moved and moved.parent_id == a.id
        root = await service.update_glossary_term(db, c.id, {"parent_id": None})
        assert root and root.parent_id is None
        await _expect(service.update_glossary_term(db, t.id, {"name": "빈 분류"}), 409)  # 위에서 용어로 바꾼 항목
        renamed = await service.update_glossary_term(db, t.id, {"name": "B"})       # 분류 이름과 같아도 됨
        assert renamed and renamed.name == "B"
        await service.update_glossary_term(db, t.id, {"name": "고객 생애 가치"})
        same = await service.update_glossary_term(db, t.id, {"name": "고객 생애 가치", "description": "d"})
        assert same and same.description == "d"                  # 자기 이름 유지는 중복 아님
        kept = await service.update_glossary_term(db, t.id, {"name": None})
        assert kept and kept.name == "고객 생애 가치"            # 명시적 null 은 변경 없음
        await _expect(service.update_glossary_term(db, c.id, {"term_type": "TERM"}), 400)  # 하위 있음
        assert await service.update_glossary_term(db, 9999, {"name": "z"}) is None

        # --- 데이터셋 연결 ---------------------------------------------------------
        ds = cm.Datasource(datasource_id="d1", name="ds", type="trino", origin="PROD")
        db.add(ds)
        await db.flush()
        d = cm.Dataset(urn="x.1.dataset", name="t1", datasource_id=ds.id, origin="PROD", status="active")
        db.add(d)
        await db.commit()

        async def links() -> int:
            return int(await db.scalar(select(func.count(cm.DatasetGlossaryTerm.id)).where(
                cm.DatasetGlossaryTerm.dataset_id == d.id, cm.DatasetGlossaryTerm.term_id == t.id)) or 0)

        assert await service.add_dataset_glossary_term(db, 9999, t.id) is False
        await _expect(service.add_dataset_glossary_term(db, d.id, 9999), 404)
        await _expect(service.add_dataset_glossary_term(db, d.id, a.id), 400)
        assert await service.add_dataset_glossary_term(db, d.id, t.id)
        assert await service.add_dataset_glossary_term(db, d.id, t.id)      # 멱등
        assert await links() == 1
        db.add(cm.DatasetGlossaryTerm(dataset_id=d.id, term_id=t.id))     # 과거에 생긴 중복 행
        await db.commit()
        assert await service.remove_dataset_glossary_term(db, d.id, t.id)
        assert await links() == 0
        assert await service.remove_dataset_glossary_term(db, d.id, t.id) is False

        # --- 삭제 (임베딩 테이블은 pgvector 라 SQLite 에 없음 → 정리 호출만 대체) -----
        orig = emb.delete_entity_embedding

        async def _noop(*_a, **_k):
            return None

        emb.delete_entity_embedding = _noop
        try:
            assert await service.count_glossary_term_children(db, a.id) == 1   # B
            assert await service.glossary_term_children_by_type(db, a.id) == (1, 0)   # 분류 B
            assert await service.glossary_term_children_by_type(db, c.id) == (0, 1)   # 용어 t
            assert await service.glossary_term_children_by_type(db, t.id) == (0, 0)
            assert await service.delete_glossary_term(db, t.id)
        finally:
            emb.delete_entity_embedding = orig


def _user(admin: bool = False, superuser: bool = False):
    return SimpleNamespace(is_admin=admin, is_superuser=superuser)


async def _run_permission() -> None:
    session_factory = await _make_session()
    key = "glossary.edit"
    async with session_factory() as db:
        # 미설정 → admin 전용 (open-by-default 인 일반 기능과 반대)
        assert await perm.check_feature_granted(db, _user(admin=True), key)
        assert not await perm.check_feature_granted(db, _user(), key)
        assert not await perm.check_feature_granted(db, _user(superuser=True), key)
        assert await perm.check_feature(db, _user(), "datasets.edit")          # 일반 기능은 그대로 허용

        # superuser 만 허용
        await perm._put_matrix(db, "FEATURE", {key: ["argus-superuser"]})
        assert await perm.check_feature_granted(db, _user(superuser=True), key)
        assert not await perm.check_feature_granted(db, _user(), key)
        me = await perm.get_my_permissions(_user(superuser=True), db)
        assert key in me["granted_features"] and key not in me["denied_features"]

        # 전 역할 허용 — 명시 허용형은 행이 남아야 허용이 유지된다
        await perm._put_matrix(db, "FEATURE", {key: ["argus-superuser", "argus-user"], "datasets.edit": [
            "argus-superuser", "argus-user"]})
        assert await perm.check_feature_granted(db, _user(), key)
        assert (await perm._get_matrix(db, "FEATURE")).get("datasets.edit") is None  # 일반 기능은 행 없음으로 정규화

        # 아무 역할도 체크 안 함 → admin 전용 센티널
        await perm._put_matrix(db, "FEATURE", {key: []})
        assert not await perm.check_feature_granted(db, _user(superuser=True), key)
        me = await perm.get_my_permissions(_user(), db)
        assert key not in me["granted_features"]


def test_glossary() -> None:
    """pytest 진입점."""
    _test_schema_validation()
    asyncio.run(_run_glossary())
    asyncio.run(_run_permission())


if __name__ == "__main__":
    test_glossary()
    print("test_glossary OK")
