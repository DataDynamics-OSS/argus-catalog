# SPDX-License-Identifier: Apache-2.0
"""03-datasets 통합 테스트(2026-11-07 격리 인스턴스 실행)에서 발견한 결함 회귀 테스트.

- DS-T12: 리니지 등록자(created_by)는 인증 사용자 — 본문 값은 무시(이전엔 본문 그대로, 화면 등록은 null)
- DS-T17: 샘플 업로드 응답에 서버 내부 절대 경로(data_dir) 노출 금지 — 파일 이름·상대 키만
- DS-T15: Avro doc — 화면·외부 API 모두 설명 평문, 없으면 "Avro schema for {name}"

실행:
    cd backend
    PYTHONPATH=. python -m pytest -q tests/test_datasets_it03.py
"""

from __future__ import annotations

import asyncio

import app.catalog.models as cm
from tests.test_datasets_remaining_fixes import _client, _seed, _tu

_LINEAGE_TABLES = ["argus_data_pipeline", "argus_dataset_lineage", "argus_dataset_column_mapping"]


async def _make_session_with_lineage():
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.test_datasets_remaining_fixes import _TABLES

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        for t in [*_TABLES, *_LINEAGE_TABLES]:
            await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
    return async_sessionmaker(engine, expire_on_commit=False)


def test_lineage_created_by_is_authenticated_user() -> None:
    factory = asyncio.run(_make_session_with_lineage())
    ids = asyncio.run(_seed(factory))
    c, who = _client(factory)

    who["user"] = _tu("admin", admin=True)
    # 화면처럼 created_by 없이 등록 → 인증 사용자
    r = c.post("/api/v1/catalog/lineage", json={
        "source_dataset_id": ids["owned"], "target_dataset_id": ids["other"], "relation_type": "ETL",
    })
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == "admin"

    # 본문에 다른 사용자를 넣어도 무시
    r = c.post("/api/v1/catalog/lineage", json={
        "source_dataset_id": ids["owned"], "target_dataset_id": ids["other"], "relation_type": "DERIVED",
        "created_by": "mallory",
    })
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == "admin"

    # 일반 사용자는 여전히 403 (관리자 전용)
    who["user"] = _tu("alice")
    r = c.post("/api/v1/catalog/lineage", json={
        "source_dataset_id": ids["owned"], "target_dataset_id": ids["other"], "relation_type": "CDC",
    })
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DS-T14 — 복합 PK 의 일부 컬럼은 is_unique=true 로 저장돼 있어도 1:1 이 아니다
# ---------------------------------------------------------------------------

def test_erd_composite_pk_member_is_not_one_to_one() -> None:
    from app.catalog import service as cat_service
    from app.catalog.erd import build_erd
    from app.catalog.schemas import DatasourceCreate
    from tests.test_datasource_stage2 import _make_session as _make_ds_session

    async def run() -> dict:
        make_db = await _make_ds_session()
        async with make_db() as db:
            src = await cat_service.create_datasource(db, DatasourceCreate(name="my", type="mysql"))

            def ds(name, ddl=None):
                return cm.Dataset(urn=f"{src.datasource_id}.{name}.dataset", name=name,
                                  datasource_id=src.id, origin="DEV",
                                  qualified_name=f"{src.datasource_id}.{name}", ddl=ddl)

            film = ds("s.film", "CREATE TABLE film (film_id int, PRIMARY KEY (film_id))")
            # film_actor: 복합 PK(actor_id, film_id) — film_id 단독으로는 유일하지 않음
            fa = ds("s.film_actor", (
                "CREATE TABLE film_actor (actor_id int, film_id int, PRIMARY KEY (actor_id, film_id), "
                "CONSTRAINT fk_fa_film FOREIGN KEY (film_id) REFERENCES film (film_id))"))
            # film_text: 단일 PK 가 곧 FK → 1:1
            ft = ds("s.film_text", (
                "CREATE TABLE film_text (film_id int, PRIMARY KEY (film_id), "
                "CONSTRAINT fk_ft_film FOREIGN KEY (film_id) REFERENCES film (film_id))"))
            db.add_all([film, fa, ft])
            await db.flush()
            # 예전 동기화가 남긴 값처럼 복합 PK 컬럼에 is_unique=true 를 저장
            for col in ("actor_id", "film_id"):
                db.add(cm.DatasetSchema(dataset_id=fa.id, field_path=col, field_type="NUMBER",
                                        is_primary_key="true", is_unique="true", ordinal=0))
            db.add(cm.DatasetSchema(dataset_id=ft.id, field_path="film_id", field_type="NUMBER",
                                    is_primary_key="true", ordinal=0))
            db.add(cm.DatasetSchema(dataset_id=film.id, field_path="film_id", field_type="NUMBER",
                                    is_primary_key="true", ordinal=0))
            await db.commit()
            erd = await build_erd(db, film.id)
            return {r["constraint_name"]: r["cardinality"] for r in erd["relations"]}

    card = asyncio.run(run())
    assert card["fk_fa_film"] == "N:1", card
    assert card["fk_ft_film"] == "1:1", card


# ---------------------------------------------------------------------------
# DS-T15 — 외부 Avro API: native_type 이 없으면 field_type 으로 해석, null 기본값, doc 일반 텍스트
# ---------------------------------------------------------------------------

def test_external_avro_types_without_native_type() -> None:
    from app.external.service import _description_doc, _resolve_avro_type

    # 수동 등록·스키마 편집 그리드처럼 native_type 이 비어 있는 경우 (이전엔 모두 "string")
    assert _resolve_avro_type("INTEGER", None, False) == "int"
    assert _resolve_avro_type("DECIMAL(12,2)", "", True) == [
        "null", {"type": "bytes", "logicalType": "decimal", "precision": 12, "scale": 2}]
    assert _resolve_avro_type("varchar", None, True) == ["null", "string"]
    # 카탈로그 공통 타입은 기존 매핑 그대로
    assert _resolve_avro_type("NUMBER", None, False) == "long"
    assert _resolve_avro_type("STRING", None, False) == "string"
    # native_type 이 있으면 우선
    assert _resolve_avro_type("NUMBER", "bigint unsigned", False) == "long"

    assert _description_doc("<p><strong>IT 주문</strong> 원장</p><ul><li><p>금액 &amp; 수량</p></li></ul><p></p>") \
        == "IT 주문 원장 금액 & 수량"
    assert _description_doc(None) == ""


# 화면 frontend/apps/web/features/datasets/avro.test.ts 의 "descriptionToAvroDoc" 케이스와 같은 입력·기대값 —
# 두 구현(화면 Avro 탭 / 외부 Avro API)의 doc 이 같아야 한다 (DS-T15 확인 필요 → 수정).
AVRO_DOC_CASES = [
    ("## 개요\nDVD 대여점이 보유한 **영화 상품 마스터**. `film_id` 기준.\n\n## 주요 컬럼\n- `title` — 제목",
     "DVD 대여점이 보유한 영화 상품 마스터. film_id 기준. title — 제목"),
    ("<p>주문 <b>원장</b> &amp; 결제&nbsp;내역</p><p>보관 &lt;5년&gt; &#8211; 감사</p>",
     "주문 원장 & 결제 내역 보관 <5년> – 감사"),
    ("[문서](http://x) 참고 *중요* rental_rate 와 _강조_", "문서 참고 중요 rental_rate 와 강조"),
    ("한*기울임*끝", "한기울임끝"),
    ("## 제목만", ""),
    ("", ""),
]


def test_avro_doc_plain_text_matches_ui_rule() -> None:
    from app.external.service import _description_doc

    for src, expected in AVRO_DOC_CASES:
        assert _description_doc(src) == expected, src


def test_external_avro_nullable_default_and_doc() -> None:
    from app.external.service import _build_avro_schema
    from tests.test_datasets_remaining_fixes import _make_session

    async def run() -> dict:
        make_db = await _make_session()
        async with make_db() as db:
            src = cm.Datasource(datasource_id="pg-1", name="pg", type="postgresql", origin="DEV")
            db.add(src)
            await db.flush()
            d = cm.Dataset(urn="pg-1.s.orders.dataset", name="s.orders", datasource_id=src.id,
                           qualified_name="pg-1.s.orders", origin="DEV", description="<p>주문 <b>원장</b></p>")
            db.add(d)
            await db.flush()
            db.add_all([
                cm.DatasetSchema(dataset_id=d.id, field_path="order_id", field_type="INTEGER",
                                 nullable="false", ordinal=0),
                cm.DatasetSchema(dataset_id=d.id, field_path="amount", field_type="DECIMAL(12,2)",
                                 nullable="true", ordinal=1),
            ])
            await db.commit()
            return await _build_avro_schema(db, d.id)

    avro = asyncio.run(run())
    assert avro["doc"] == "주문 원장"
    f = {x["name"]: x for x in avro["fields"]}
    assert f["order_id"]["type"] == "int" and "default" not in f["order_id"]
    assert f["amount"]["type"][0] == "null" and f["amount"]["default"] is None


# ---------------------------------------------------------------------------
# DS-T17 — 샘플 업로드 응답에 서버 내부 경로를 싣지 않는다
# ---------------------------------------------------------------------------

def test_sample_upload_responses_do_not_leak_server_path(tmp_path, monkeypatch) -> None:
    from app.core.config import settings
    from tests.test_datasets_remaining_fixes import _make_session

    monkeypatch.setattr(settings, "data_dir", tmp_path / "secret-data-dir")
    factory = asyncio.run(_make_session())
    ids = asyncio.run(_seed(factory))
    c, who = _client(factory)
    who["user"] = _tu("alice")

    r = c.post(f"/api/v1/catalog/datasets/{ids['owned']}/sample",
               files={"file": ("s.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"status": "ok", "file": "sample.csv", "size": 8}
    assert "secret-data-dir" not in r.text and str(tmp_path) not in r.text
    assert (tmp_path / "secret-data-dir" / "samples" / "ds-dev.db.owned" / "sample.csv").is_file()

    # 동기화 프로세스용 parquet 업로드(관리자) — 상대 키만
    who["user"] = _tu("root", admin=True)
    r = c.post("/api/v1/catalog/samples/upload", content=b"PAR1fake",
               headers={"X-Datasource-Id": "ds-dev", "X-Dataset-Name": "db.owned"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "file": "sample.parquet", "key": "ds-dev/db.owned/sample.parquet", "size": 8}
    assert "secret-data-dir" not in r.text and str(tmp_path) not in r.text
