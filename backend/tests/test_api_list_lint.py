# SPDX-License-Identifier: Apache-2.0
"""API 목록 이름순 정렬(PostgreSQL C 비교) · 린팅 스펙 버전 지정 오류 테스트 (in-memory SQLite, 자기완결형).

실행:
    cd backend
    pytest tests/test_api_list_lint.py
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.apis import service
from app.apis.models import CatalogApi, CatalogApiSpec
from app.core.database import Base

SPEC = json.dumps({
    "openapi": "3.0.3", "info": {"title": "T", "version": "1.0.0"},
    "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
})


def _sql(order: tuple, dialect) -> str:
    return " ".join(str(o.compile(dialect=dialect)) for o in order)


def test_name_sort_uses_c_collation_on_postgresql():
    # glibc 로케일 정렬은 한글·영문이 섞이면 순서가 뒤바뀐다 — PostgreSQL 은 C(바이트) 비교로 고정
    assert 'COLLATE "C"' in _sql(service._list_order("name", "postgresql"), postgresql.dialect())


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "mariadb", ""])
def test_name_sort_plain_on_other_dialects(dialect):
    assert "COLLATE" not in _sql(service._list_order("name", dialect), sqlite.dialect())


@pytest.mark.parametrize("sort", ["updated", "created", "bogus"])
def test_other_sorts_unchanged(sort):
    assert service._list_order(sort, "postgresql") == service._LIST_SORTS.get(sort, service._LIST_SORTS["updated"])


async def test_lint_unknown_spec_id_raises():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[CatalogApi.__table__, CatalogApiSpec.__table__])
    make_session = async_sessionmaker(engine, expire_on_commit=False)
    async with make_session() as s:
        a = CatalogApi(name="it-a", urn="it-a.api", protocol="REST", source="spec")
        b = CatalogApi(name="it-b", urn="it-b.api", protocol="REST", source="manual")
        s.add_all([a, b])
        await s.flush()
        spec = CatalogApiSpec(api_id=a.id, version="1.0.0", format="openapi3", raw=SPEC, is_current="true")
        s.add(spec)
        await s.commit()
        spec_id = spec.id

    async with make_session() as s:
        ok = await service.lint_api(s, "it-a", spec_id=spec_id)
        assert ok.spec_id == spec_id and ok.version == "1.0.0"
        current = await service.lint_api(s, "it-a")
        assert current.spec_id == spec_id
        with pytest.raises(service.SpecVersionNotFoundError):
            await service.lint_api(s, "it-a", spec_id=999999)      # 없는 버전
        with pytest.raises(service.SpecVersionNotFoundError):
            await service.lint_api(s, "it-b", spec_id=spec_id)      # 다른 API 의 버전
        empty = await service.lint_api(s, "it-b")                   # 스펙 없는 수동 API — 빈 결과(404 아님)
        assert empty.spec_id is None and empty.findings == []
        assert await service.lint_api(s, "no-such") is None         # 없는 API → None(라우터 404)
    await engine.dispose()
