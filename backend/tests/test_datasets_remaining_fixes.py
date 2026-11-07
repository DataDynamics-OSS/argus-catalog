# SPDX-License-Identifier: Apache-2.0
"""데이터셋 메뉴 잔여 결함 회귀 테스트 (자기완결형, in-memory SQLite).

- DS-T09/T10/T17/T26: 속성·스키마·샘플 변경 — 소유자(생성자) 또는 관리자
- DS-T18: 댓글 작성자 = 인증 사용자(본문 author_name 무시), 수정·삭제는 작성자 또는 관리자
- DS-T23: 속성 PUT 이 ``argus.`` 내부 키를 보존(빈 값이면 삭제)
- DS-T24: 없는 데이터셋 스키마/속성 PUT → 404, field_path 중복 → 422
- DS-T26: 외부 Avro API 가 편집본(argus.avro_schema) 우선
- DS-T27: CSV→Parquet 변환이 저장된 구분자·헤더·인코딩 사용
- (참고) relationships/ingest 관리자 전용, origin 생략 시 데이터 소스 환경, 목록 created_by

실행:
    cd backend
    PYTHONPATH=. python -m pytest -q tests/test_datasets_remaining_fixes.py
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.catalog import service
from app.catalog.schemas import DatasetCreate
from app.comments.models import Comment

_TABLES = [
    "catalog_organizations", "catalog_systems", "catalog_datasources",
    "catalog_datasets", "catalog_dataset_properties", "catalog_dataset_schemas",
    "catalog_schema_snapshots", "catalog_tags", "catalog_dataset_tags", "catalog_owners",
    "catalog_glossary_terms", "catalog_dataset_glossary_terms",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        for t in _TABLES:
            await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
        await conn.run_sync(lambda sc: Comment.__table__.create(sc, checkfirst=True))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory) -> dict:
    async with factory() as db:
        src = cm.Datasource(datasource_id="ds-dev", name="dev src", type="mysql", origin="DEV")
        db.add(src)
        await db.flush()
        owned = cm.Dataset(urn="ds-dev.db.owned.DEV.dataset", name="db.owned", datasource_id=src.id,
                           qualified_name="ds-dev.db.owned", origin="DEV", created_by="alice")
        other = cm.Dataset(urn="ds-dev.db.other.DEV.dataset", name="db.other", datasource_id=src.id,
                           qualified_name="ds-dev.db.other", origin="DEV", created_by="bob")
        db.add_all([owned, other])
        await db.flush()
        db.add(cm.DatasetSchema(dataset_id=owned.id, field_path="id", field_type="NUMBER", ordinal=0))
        await db.commit()
        return {"src": src.id, "owned": owned.id, "other": other.id}


def _tu(name: str, admin: bool = False):
    from app.core.auth import TokenUser
    from app.core.config import settings
    return TokenUser(sub=name, username=name, first_name="길동", last_name="홍",
                     email=f"{name}@x.io",
                     roles=[settings.auth_keycloak_admin_role] if admin else ["argus-user"])


def _client(factory):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from app.catalog.router import router as catalog_router
    from app.comments.router import router as comments_router
    from app.core.auth import get_current_user
    from app.core.database import get_session

    app = FastAPI()
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(comments_router, prefix="/api/v1")

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": None}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    return TestClient(app), who


# ---------------------------------------------------------------------------
# HTTP — 권한·404·422
# ---------------------------------------------------------------------------

def test_dataset_edit_endpoints_owner_or_admin(tmp_path, monkeypatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    factory = asyncio.run(_make_session())
    ids = asyncio.run(_seed(factory))
    c, who = _client(factory)
    base = "/api/v1/catalog/datasets"
    props = [{"key": "k", "value": "v"}]
    schema = [{"field_path": "id", "field_type": "NUMBER"}]

    # 소유자(비관리자) — 속성·스키마·샘플 업로드/구분자 저장 허용
    who["user"] = _tu("alice")
    assert c.put(f"{base}/{ids['owned']}/properties", json=props).status_code == 200
    assert c.put(f"{base}/{ids['owned']}/schema", json=schema).status_code == 200
    r = c.post(f"{base}/{ids['owned']}/sample", files={"file": ("s.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200, r.text
    assert c.put(f"{base}/{ids['owned']}/sample/delimiter", json={}).status_code == 200

    # 비소유자(비관리자) — 403
    for method, url, kw in [
        ("put", f"{base}/{ids['other']}/properties", {"json": props}),
        ("put", f"{base}/{ids['other']}/schema", {"json": schema}),
        ("post", f"{base}/{ids['other']}/sample/convert-to-parquet", {}),
        ("delete", f"{base}/{ids['other']}/sample", {}),
        ("put", f"{base}/{ids['other']}/sample/delimiter", {"json": {}}),
    ]:
        assert getattr(c, method)(url, **kw).status_code == 403, url

    # 관리자 — 타인 데이터셋도 허용
    who["user"] = _tu("admin", admin=True)
    assert c.put(f"{base}/{ids['other']}/properties", json=props).status_code == 200

    # DS-T24: 없는 데이터셋 → 404 (FK 409/500 아님)
    assert c.put(f"{base}/999999/schema", json=schema).status_code == 404
    assert c.put(f"{base}/999999/properties", json=props).status_code == 404

    # DS-T24: field_path 중복 → 422, 기존 스키마는 그대로
    dup = [{"field_path": "x", "field_type": "STRING"}, {"field_path": "x", "field_type": "NUMBER"}]
    r = c.put(f"{base}/{ids['owned']}/schema", json=dup)
    assert r.status_code == 422 and "x" in r.json()["detail"]

    # (참고) relationships/ingest — 관리자 전용
    who["user"] = _tu("alice")
    body = {"datasource_id": "ds-dev", "pairs": []}
    assert c.post("/api/v1/catalog/relationships/ingest", json=body).status_code == 403


# ---------------------------------------------------------------------------
# DS-T23 — 내부 키 보존
# ---------------------------------------------------------------------------

def test_properties_put_preserves_internal_keys() -> None:
    async def _run():
        factory = await _make_session()
        ids = await _seed(factory)
        async with factory() as db:
            await service.update_dataset_properties(db, ids["owned"], [
                {"key": "argus.standard_dictionary_id", "value": "7"},
                {"key": "argus.avro_schema", "value": '{"type":"record"}'},
                {"key": "snapshot", "value": "1"},
            ])
            # 동기화처럼 내부 키 없이 전체 교체 → 내부 키 보존, 일반 키는 교체
            await service.update_dataset_properties(db, ids["owned"], [{"key": "snapshot", "value": "2"}])
            rows = {p.property_key: p.property_value for p in (await db.execute(
                select(cm.DatasetProperty).where(cm.DatasetProperty.dataset_id == ids["owned"])
            )).scalars()}
            assert rows == {"argus.standard_dictionary_id": "7",
                            "argus.avro_schema": '{"type":"record"}', "snapshot": "2"}
            # 명시적으로 빈 값을 보내면 내부 키 삭제(Avro 초기화), 값을 보내면 갱신
            await service.update_dataset_properties(db, ids["owned"], [
                {"key": "argus.avro_schema", "value": ""},
                {"key": "argus.standard_dictionary_id", "value": "8"},
            ])
            rows = {p.property_key: p.property_value for p in (await db.execute(
                select(cm.DatasetProperty).where(cm.DatasetProperty.dataset_id == ids["owned"])
            )).scalars()}
            assert rows == {"argus.standard_dictionary_id": "8"}

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DS-T18 — 댓글 작성자·수정/삭제 권한
# ---------------------------------------------------------------------------

def test_comment_author_and_ownership() -> None:
    factory = asyncio.run(_make_session())
    c, who = _client(factory)

    who["user"] = _tu("alice")
    r = c.post("/api/v1/comments", json={
        "entity_type": "dataset", "entity_id": "1", "content": "<p>hi</p>",
        "author_name": "관리자(admin)", "author_email": "admin@x.io",  # 사칭 시도 — 무시
    })
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["author_name"] == "홍길동(alice)"
    assert r.json()["author_email"] == "alice@x.io"
    # author_name 없이도 생성 가능(하위 호환 스키마)
    r2 = c.post("/api/v1/comments", json={"entity_type": "dataset", "entity_id": "1", "content": "x"})
    assert r2.status_code == 200

    who["user"] = _tu("bob")
    assert c.put(f"/api/v1/comments/{cid}", json={"content": "hacked"}).status_code == 403
    assert c.delete(f"/api/v1/comments/{cid}").status_code == 403

    who["user"] = _tu("alice")
    assert c.put(f"/api/v1/comments/{cid}", json={"content": "edited"}).status_code == 200

    who["user"] = _tu("admin", admin=True)
    assert c.delete(f"/api/v1/comments/{cid}").status_code == 200
    assert c.delete(f"/api/v1/comments/{cid}").status_code == 404  # 이미 삭제됨
    assert c.put("/api/v1/comments/99999", json={"content": "x"}).status_code == 404


def test_comment_author_username_parsing() -> None:
    from app.comments.service import comment_author_username
    assert comment_author_username("홍길동(alice)") == "alice"
    assert comment_author_username("alice") == "alice"
    assert comment_author_username(None) is None


# ---------------------------------------------------------------------------
# DS-T27 — CSV→Parquet 변환이 저장된 설정 사용
# ---------------------------------------------------------------------------

def test_csv_to_parquet_uses_saved_config(tmp_path, monkeypatch) -> None:
    import pyarrow.parquet as pq

    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    factory = asyncio.run(_make_session())
    ids = asyncio.run(_seed(factory))
    c, who = _client(factory)
    base = f"/api/v1/catalog/datasets/{ids['owned']}"
    who["user"] = _tu("alice")

    # 세미콜론 구분·헤더 없음·EUC-KR
    raw = "1;서울\n2;부산\n".encode("euc-kr")
    assert c.post(f"{base}/sample", files={"file": ("s.csv", raw, "text/csv")}).status_code == 200
    cfg = {"encoding": "EUC-KR", "delimiter": ";", "has_header": False, "quote_char": "__none__"}
    assert c.put(f"{base}/sample/delimiter", json=cfg).status_code == 200
    r = c.post(f"{base}/sample/convert-to-parquet")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "rows": 2, "columns": 2}

    table = pq.read_table(tmp_path / "samples" / "ds-dev" / "db.owned" / "sample.parquet")
    assert table.column_names == ["col_0", "col_1"]
    assert table.column("col_1").to_pylist() == ["서울", "부산"]


def test_delimiter_config_escape_mode(tmp_path) -> None:
    from app.catalog.router import _load_delimiter_config
    p = tmp_path / "delimiter.json"
    assert _load_delimiter_config(p)["delimiter"] == ","  # 파일 없으면 기존 기본값
    p.write_text(json.dumps({"delimiter_mode": "escape", "delimiter_input": "\\t",
                             "quote_char": '"'}), encoding="utf-8")
    cfg = _load_delimiter_config(p)
    assert cfg["delimiter"] == "\t" and cfg["quotechar"] == '"' and cfg["has_header"] is True


# ---------------------------------------------------------------------------
# (참고) origin 기본값·목록 created_by, DS-T26 Avro 편집본
# ---------------------------------------------------------------------------

def test_create_origin_defaults_to_datasource_and_list_created_by() -> None:
    async def _run():
        factory = await _make_session()
        ids = await _seed(factory)
        async with factory() as db:
            ds = await service.create_dataset(
                db, DatasetCreate(name="db.newone", datasource_id=ids["src"]), created_by="carol",
            )
            assert ds.origin == "DEV"
            ds2 = await service.create_dataset(
                db, DatasetCreate(name="db.prod", datasource_id=ids["src"], origin="PROD"),
            )
            assert ds2.origin == "PROD"
            page = await service.list_datasets(db, page_size=100)
            by_name = {d.name: d for d in page.items}
            assert by_name["db.owned"].created_by == "alice"
            assert by_name["db.newone"].created_by == "carol"

    asyncio.run(_run())


def test_external_avro_prefers_override() -> None:
    from app.external.service import _build_avro_schema

    async def _run():
        factory = await _make_session()
        ids = await _seed(factory)
        async with factory() as db:
            auto = await _build_avro_schema(db, ids["owned"])
            assert auto["fields"][0]["name"] == "id"
            edited = {"type": "record", "name": "Edited", "fields": []}
            await service.update_dataset_properties(
                db, ids["owned"], [{"key": "argus.avro_schema", "value": json.dumps(edited)}],
            )
            assert await _build_avro_schema(db, ids["owned"]) == edited
            # 손상된 편집본은 무시하고 자동 생성으로 폴백
            await service.update_dataset_properties(
                db, ids["owned"], [{"key": "argus.avro_schema", "value": "not json"}],
            )
            assert (await _build_avro_schema(db, ids["owned"]))["fields"][0]["name"] == "id"

    asyncio.run(_run())
