# SPDX-License-Identifier: Apache-2.0
"""분류 체계(Taxonomies) 잔여 결함 회귀 테스트 — design/integration-test/06-taxonomies.md 알려진 이슈.

- TAX-T09: 없는 데이터셋↔분류 매핑 삭제 → 404
- TAX-T15: 루트 분류(parent_id NULL) 같은 이름 중복 생성·이름 변경·이동 → 409
- TAX-T16: PUT 의 name/sort_order 명시적 null → 422 (생략은 허용)
- TAX-T17: 트리 롤업·미분류 수에서 removed 데이터셋을 목록과 같이 제외
- 직접 매핑 id 조회(하위 분류 매핑 제외) — 상위 분류 화면의 "제거" 는 직접 매핑 행에만
"""

from __future__ import annotations

import asyncio

from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.catalog import schemas as s

_TABLES = (
    cm.System, cm.Datasource, cm.Catalog, cm.CatalogConnection, cm.Dataset,
    cm.Taxonomy, cm.Category, cm.DatasetCategory,
)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        for model in _TABLES:
            await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
    return async_sessionmaker(engine, expire_on_commit=False)


def _test_schema_validation() -> None:
    # 생략은 허용
    assert s.TaxonomyUpdate().model_fields_set == set()
    assert s.CategoryUpdate(description="x").model_fields_set == {"description"}
    # parent_id null 은 루트로 이동 — 허용
    assert s.CategoryUpdate(parent_id=None).parent_id is None
    for model in (s.TaxonomyUpdate, s.CategoryUpdate):
        for field in ("name", "sort_order"):
            try:
                model(**{field: None})
            except ValidationError:
                pass
            else:
                raise AssertionError(f"{model.__name__}.{field}=null 이 거부되지 않음")


async def _seed(factory) -> dict:
    async with factory() as db:
        ds = cm.Datasource(datasource_id="ds-tax", name="ds-tax", type="mysql")
        db.add(ds)
        await db.flush()
        a = cm.Dataset(urn="urn:a", name="a", datasource_id=ds.id, status="active")
        b = cm.Dataset(urn="urn:b", name="b", datasource_id=ds.id, status="active")
        r = cm.Dataset(urn="urn:r", name="r", datasource_id=ds.id, status="removed")
        db.add_all([a, b, r])
        await db.commit()
        return {"a": a.id, "b": b.id, "r": r.id}


def _http(factory, ids) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.catalog.taxonomy_router import router
    from app.core.auth import TokenUser, get_current_user
    from app.core.config import settings
    from app.core.database import get_session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: TokenUser(
        sub="admin", username="admin", roles=[settings.auth_keycloak_admin_role])

    c = TestClient(app)
    base = "/api/v1/catalog"

    r = c.post(f"{base}/taxonomies", json={"name": "it-tx", "description": "설명"})
    assert r.status_code == 201, r.text
    tx = r.json()["id"]

    # --- TAX-T16: 명시적 null → 422, 생략·정상 값은 200
    assert c.put(f"{base}/taxonomies/{tx}", json={"name": None}).status_code == 422
    assert c.put(f"{base}/taxonomies/{tx}", json={"sort_order": None}).status_code == 422
    r = c.put(f"{base}/taxonomies/{tx}", json={"name": "it-tx2", "description": "변경"})
    assert r.status_code == 200 and r.json()["name"] == "it-tx2" and r.json()["description"] == "변경", r.text

    # --- TAX-T15: 루트 분류 이름 중복
    r = c.post(f"{base}/categories", json={"taxonomy_id": tx, "name": "Root"})
    assert r.status_code == 201, r.text
    root = r.json()["id"]
    r = c.post(f"{base}/categories", json={"taxonomy_id": tx, "name": "Root"})
    assert r.status_code == 409, r.text
    assert "같은 이름" in r.json()["detail"]
    r = c.post(f"{base}/categories", json={"taxonomy_id": tx, "name": "Root2"})
    assert r.status_code == 201
    root2 = r.json()["id"]
    # 이름 변경으로 루트 중복 → 409, 자기 이름 유지는 허용
    assert c.put(f"{base}/categories/{root2}", json={"name": "Root"}).status_code == 409
    assert c.put(f"{base}/categories/{root2}", json={"name": "Root2", "sort_order": 3}).status_code == 200
    # 하위 → 루트로 이동 시 같은 이름 루트가 있으면 409
    r = c.post(f"{base}/categories", json={"taxonomy_id": tx, "parent_id": root2, "name": "Root"})
    assert r.status_code == 201, r.text  # 다른 상위 아래에선 같은 이름 허용
    child = r.json()["id"]
    assert c.put(f"{base}/categories/{child}", json={"parent_id": None}).status_code == 409
    # 하위끼리 중복도 409(서비스 검사)
    assert c.post(f"{base}/categories", json={"taxonomy_id": tx, "parent_id": root2, "name": "Root"}).status_code == 409
    # 다른 분류체계의 같은 이름 루트는 허용
    tx_other = c.post(f"{base}/taxonomies", json={"name": "it-other"}).json()["id"]
    assert c.post(f"{base}/categories", json={"taxonomy_id": tx_other, "name": "Root"}).status_code == 201

    # TAX-T16: 분류 null
    assert c.put(f"{base}/categories/{root}", json={"name": None}).status_code == 422
    assert c.put(f"{base}/categories/{root}", json={"sort_order": None}).status_code == 422

    # --- TAX-T09: 없는 매핑 삭제 → 404, 있는 매핑 삭제 → 200
    assert c.post(f"{base}/datasets/{ids['a']}/categories/{root}").status_code == 200
    assert c.delete(f"{base}/datasets/{ids['b']}/categories/{root}").status_code == 404
    assert c.delete(f"{base}/datasets/{ids['a']}/categories/{root}").status_code == 200
    assert c.delete(f"{base}/datasets/{ids['a']}/categories/{root}").status_code == 404

    # --- TAX-T17: removed 데이터셋은 트리 롤업·미분류에서 제외
    for did, cid in ((ids["a"], root), (ids["r"], root), (ids["r"], child)):
        assert c.post(f"{base}/datasets/{did}/categories/{cid}").status_code == 200
    tree = c.get(f"{base}/taxonomies/{tx}/tree").json()
    counts = {n["id"]: n["dataset_count"] for n in tree["categories"]}
    assert counts[root] == 1, tree   # a 만 (r 제외)
    assert counts[root2] == 0, tree  # child 에는 removed r 만
    # 활성 a·b 중 a 만 매핑 → 미분류 1 (이전: total 2 − mapped 2(a, r) = 0)
    assert tree["uncategorized_count"] == 1, tree

    # --- 상위 분류 목록엔 하위 매핑도 보이지만 해제는 직접 매핑만 — 직접 매핑 id 로 "제거" 노출을 가른다
    assert c.post(f"{base}/datasets/{ids['b']}/categories/{child}").status_code == 200
    assert c.get(f"{base}/categories/{root2}/direct-dataset-ids").json() == []
    assert sorted(c.get(f"{base}/categories/{child}/direct-dataset-ids").json()) == sorted([ids["b"], ids["r"]])
    assert c.get(f"{base}/categories/{root}/direct-dataset-ids").json() == sorted([ids["a"], ids["r"]])
    assert c.delete(f"{base}/datasets/{ids['b']}/categories/{root2}").status_code == 404  # 하위 매핑은 상위에서 해제 불가
    assert c.get(f"{base}/categories/999999/direct-dataset-ids").status_code == 404


async def _run() -> None:
    factory = await _make_session()
    ids = await _seed(factory)
    _http(factory, ids)


def test_taxonomy_remaining_fixes() -> None:
    _test_schema_validation()
    asyncio.run(_run())
