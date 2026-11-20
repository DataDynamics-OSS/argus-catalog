# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 점검 (TODO-096 2단계) — 자기완결형, in-memory SQLite.

- GET /catalog/datasets/{id}/ai-ready/check — 체크리스트 38개(공통 33 · 유형별 5) 자동 판정과 근거, 필수·권장 충족률, 최소 요건.
- 메타데이터·스키마·품질·표준 매핑·리니지를 채우면 해당 항목이 적정으로 바뀐다.
- PUT·DELETE /catalog/datasets/{id}/ai-ready/check/{key} — 수동 판정이 자동 판정보다 우선, 지우면 되돌아감. 소유자·관리자만.
- GET /catalog/ai-ready/overview — 데이터셋별 충족률(낮은 순), db.dataset 경로(카탈로그는 이름으로 따로), 카탈로그 → DB → 검색으로 좁히기, 카탈로그(DB)별 평균.
- GET /catalog/ai-ready/scopes — 카탈로그와 그 아래 DB 선택지.

실행:
    cd backend
    pytest tests/test_ai_ready_check.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.apis.models import CatalogApi, CatalogApiLineage
from app.catalog import ai_ready_check
from app.catalog.router import router as catalog_router
from app.comments.models import Comment
from app.comments.router import router as comments_router
from app.core.auth import TokenUser, get_current_user, get_optional_user
from app.core.config import settings
from app.core.database import get_session
from app.external.router import router as external_router
from app.permissions.models import Permission
from app.quality.models import (
    DataProfile,
    QualityDefinition,
    QualityResult,
    QualityRule,
    QualityRun,
    QualityScore,
)
from app.settings.models import CatalogConfiguration
from app.standard.models import (
    StandardDictionary,
    StandardDomain,
    StandardTerm,
    TermColumnMapping,
)

V1 = "/api/v1"
_TABLES = [
    "catalog_organizations", "catalog_systems", "catalog_datasources", "catalog_catalogs",
    "catalog_datasets", "catalog_dataset_schemas", "catalog_schema_snapshots", "catalog_tags", "catalog_dataset_tags",
    "catalog_owners", "argus_data_pipeline", "argus_dataset_lineage", "catalog_taxonomies", "catalog_categories",
    "catalog_dataset_categories", "catalog_ai_ready_judgment", "catalog_dataset_distribution",
    "catalog_dataset_version", "catalog_glossary_terms", "catalog_dataset_glossary_terms", "catalog_dataset_properties",
]


def _tu(name: str, admin: bool = False) -> TokenUser:
    return TokenUser(sub=name, username=name, roles=[settings.auth_keycloak_admin_role] if admin else ["argus-user"])


@pytest.fixture
def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    extra = (CatalogConfiguration, DataProfile, QualityDefinition, QualityRun, QualityRule, QualityResult, QualityScore,
             StandardDictionary, StandardDomain, StandardTerm, TermColumnMapping, CatalogApi, CatalogApiLineage, Comment,
             Permission)

    async def _init() -> dict:
        async with engine.begin() as conn:
            for t in _TABLES:
                await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
            for model in extra:
                await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
        async with factory() as db:
            src = cm.Datasource(datasource_id="ds-a", name="src A", type="mysql", origin="PROD")
            src2 = cm.Datasource(datasource_id="ds-b", name="src B", type="mysql", origin="PROD")
            db.add_all([src, src2])
            await db.flush()
            bare = cm.Dataset(urn="ds-a.db.bare", name="db.bare", datasource_id=src.id, status="active", created_by="alice")
            other = cm.Dataset(urn="ds-b.db.other", name="db.other", datasource_id=src2.id, status="active", created_by="bob")
            gone = cm.Dataset(urn="ds-b.db.gone", name="db.gone", datasource_id=src2.id, status="removed", created_by="bob")
            db.add_all([bare, other, gone])
            await db.commit()
            return {"src": src.id, "src2": src2.id, "bare": bare.id, "other": other.id}

    ids = asyncio.run(_init())

    app = FastAPI()
    app.include_router(catalog_router, prefix=V1)
    # 내보내기(3단계)·오류 신고와 버전 이력(4단계) 테스트가 같은 픽스처를 쓴다
    app.include_router(external_router, prefix=V1)
    app.include_router(comments_router, prefix=V1)

    async def _session():
        async with factory() as db:
            yield db

    who: dict = {"user": _tu("alice")}

    def _current():
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        return who["user"]

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _current
    # 점검 목록(overview · scopes)은 AI-Ready 메뉴 가드(require_menu)가 선택 인증으로 사용자를 본다
    app.dependency_overrides[get_optional_user] = lambda: who["user"]

    def run(coro_fn):
        async def _go():
            async with factory() as db:
                await coro_fn(db)
                await db.commit()
        asyncio.run(_go())

    yield TestClient(app), who, ids, run
    asyncio.run(engine.dispose())


def _items(body: dict) -> dict[str, dict]:
    return {i["key"]: i for i in body["items"]}


def _fill_everything(ds_id: int, src_id: int):
    """필수 메타데이터·스키마·품질·표준 매핑·리니지·API 연결을 모두 채운다."""

    async def _go(db):
        org = cm.Organization(name="서울특별시", code="seoul")
        db.add(org)
        await db.flush()
        system = cm.System(name="인구 시스템", code="pop", org_id=org.id)
        db.add(system)
        await db.flush()
        src = await db.get(cm.Datasource, src_id)
        src.system_id = system.id
        ds = await db.get(cm.Dataset, ds_id)
        for k, v in {
            "description": "서울 인구", "legal_basis": "통계법 제3조", "publisher": "서울특별시",
            "landing_page": "https://data.seoul.go.kr/x", "ingestion_frequency": "DAILY", "contact_email": "d@seoul.go.kr",
            "data_language": "ko", "byte_size": 1000, "data_format": "CSV", "data_category": "STRUCTURED",
            "data_version": "v1", "version_notes": "첫 배포", "license": "KOGL-1", "missing_value_notation": "NULL",
            "missing_data_info": "결측 없음", "contains_synthetic": "false", "contains_machine_annotation": "false",
            "contains_pii": "false", "persistent_id": "seoul-pop", "is_synced": "true",
            "last_ingested_at": datetime.now(timezone.utc) - timedelta(hours=3),
        }.items():
            setattr(ds, k, v)
        col = cm.DatasetSchema(dataset_id=ds_id, field_path="gu", field_type="STRING", description="자치구",
                               is_primary_key="true", ordinal=0)
        day = cm.DatasetSchema(dataset_id=ds_id, field_path="day", field_type="DATE", description="기준일", ordinal=1)
        db.add_all([col, day])
        tag = cm.Tag(name="인구")
        tax = cm.Taxonomy(name="주제")
        db.add_all([tag, tax])
        await db.flush()
        cat = cm.Category(taxonomy_id=tax.id, name="일반공공행정")
        db.add(cat)
        await db.flush()
        db.add_all([cm.DatasetTag(dataset_id=ds_id, tag_id=tag.id), cm.DatasetCategory(dataset_id=ds_id, category_id=cat.id),
                    cm.Owner(dataset_id=ds_id, owner_name="alice")])
        rule = QualityRule(dataset_id=ds_id, rule_name="day 형식", check_type="DATE_FORMAT", column_name="day",
                           expected_value="YYYY-MM-DD", is_active="true")
        db.add(rule)
        await db.flush()
        db.add_all([QualityResult(rule_id=rule.id, dataset_id=ds_id, passed="false"),
                    QualityScore(dataset_id=ds_id, score=100, total_rules=1, passed_rules=1)])
        await db.flush()
        db.add(QualityResult(rule_id=rule.id, dataset_id=ds_id, passed="true"))  # 최근 결과가 통과
        d = StandardDictionary(dict_name="공통")
        db.add(d)
        await db.flush()
        term = StandardTerm(dictionary_id=d.id, term_name="자치구", term_english="gu", term_abbr="GU", physical_name="gu")
        db.add(term)
        await db.flush()
        db.add(TermColumnMapping(term_id=term.id, dataset_id=ds_id, schema_id=col.id, mapping_type="MATCHED"))
        api = CatalogApi(name="pop-api", urn="api:pop-api", display_name="인구 API")
        db.add(api)
        await db.flush()
        db.add(CatalogApiLineage(api_id=api.id, relation="provides", target_type="dataset", target_ref=ds.urn))
        db.add(cm.DatasetDistribution(dataset_id=ds_id, format="CSV", access_url="https://data.seoul.go.kr/x",
                                      download_url="https://data.seoul.go.kr/x.csv", checksum_algorithm="SHA-256",
                                      checksum="a" * 64))
        db.add(DataProfile(dataset_id=ds_id, row_count=10, profile_json=json.dumps(
            [{"column_name": "gu", "column_type": "varchar", "total_count": 10, "null_count": 1}])))
    return _go


def test_bare_dataset_fails_required(env):
    c, _who, ids, _run = env
    r = c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check")
    assert r.status_code == 200, r.text
    body = r.json()
    items = _items(body)
    assert len(items) == 38
    assert sum(1 for i in ai_ready_check.COMMON_ITEMS if i.level == "REQUIRED") == 15
    assert items["C01"]["status"] == "FAIL" and "비어" in items["C01"]["evidence"]
    assert items["C05"]["status"] == "FAIL" and "라이선스" in items["C05"]["evidence"] and "배포본" in items["C05"]["evidence"]
    # 소유자가 혼자 채울 수 없는 항목은 누가 어디서 채우는지 알린다(TODO-149) — 최소 요건 '필수 메타데이터'도 같은 문장
    ev = items["C05"]["evidence"]
    assert "taxonomies.map" in ev and "분류 체계" in ev, ev
    assert "소관기관 — 관리자" in ev and "시스템에 조직을 지정" in ev, ev
    meta = next(x for x in body["min_requirements"]["checks"] if x["key"] == "metadata")
    assert "taxonomies.map" in meta["detail"]
    assert items["C04"]["status"] == "PASS" and "/dcat" in items["C04"]["evidence"]
    assert items["C27"]["status"] is None and items["C33"]["status"] is None
    assert items["C09"]["status"] is None and items["C09"]["source"] is None  # 규칙이 없어 판정 필요
    assert items["C22"]["status"] == "FAIL"  # 개인정보 포함 여부 미정
    assert items["C12"]["mode"] == "MANUAL" and items["C12"]["status"] is None
    assert items["T2"]["status"] == "NA"  # 이미지·영상 아님
    assert items["C31"]["status"] == "PASS" and "접수된 신고 없음" in items["C31"]["evidence"]
    assert body["required"]["passed"] == 2 and body["required"]["total"] == 15  # C04·C31 만 적정(필수 15, 해당없음 없음)
    assert body["min_requirements"]["met"] is False
    assert c.get(f"{V1}/catalog/datasets/99999/ai-ready/check").status_code == 404


def test_filled_dataset_passes_auto_items(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))
    body = c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json()
    items = _items(body)
    auto_pass = ["C01", "C02", "C03", "C04", "C05", "C06", "C08", "C09", "C10", "C11", "C13", "C14", "C15", "C16", "C17",
                 "C20", "C23", "C25", "C26", "C27", "C30", "C31", "C32", "C33"]
    assert {k: items[k]["status"] for k in auto_pass} == {k: "PASS" for k in auto_pass}, \
        {k: items[k]["evidence"] for k in auto_pass if items[k]["status"] != "PASS"}
    assert items["C22"]["status"] == "NA" and items["C24"]["status"] == "NA"
    assert items["C07"]["status"] == "NA"  # 1GB 미만
    assert items["C21"]["status"] == "FAIL" and "편향" in items["C21"]["evidence"]  # 편향·한계가 비어 있음
    assert "알려진 편향 또는 한계가 비어" in items["C21"]["evidence"], "조사는 받침에 맞춰(DEF-146)"
    # 필수 15: C22 는 해당없음, 나머지 모두 적정 → 14/14 (기한 넘긴 신고가 생기면 C31 미흡 — test_ai_ready_feedback)
    assert body["required"] == {"passed": 14, "total": 14, "rate": 100.0}
    mr = {x["key"]: x["met"] for x in body["min_requirements"]["checks"]}
    assert mr == {"missing": True, "metadata": True, "freshness": True, "format": True}
    assert body["min_requirements"]["met"] is True


def test_license_default_from_settings_and_custom_url(env):
    c, _who, ids, run = env

    async def _cfg(db):
        db.add(CatalogConfiguration(category="ai_ready", config_key="ai_ready_license", config_value="CUSTOM"))
    run(_cfg)
    item = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())["C23"]
    assert item["status"] == "FAIL" and "URL" in item["evidence"]

    async def _url(db):
        db.add(CatalogConfiguration(category="ai_ready", config_key="ai_ready_license_url", config_value="https://x.go.kr/t"))
    run(_url)
    item = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())["C23"]
    assert item["status"] == "PASS" and "기관 기본값" in item["evidence"]


def test_schema_change_without_description_fails_c20(env):
    c, _who, ids, run = env

    async def _go(db):
        db.add(cm.DatasetSchema(dataset_id=ids["bare"], field_path="new_col", field_type="INT", ordinal=0))
        db.add(cm.SchemaSnapshot(dataset_id=ids["bare"], schema_json="[]", change_summary="추가 1개",
                                 changes_json=json.dumps([{"type": "ADD", "field": "new_col"}])))
    run(_go)
    item = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())["C20"]
    assert item["status"] == "FAIL" and "new_col" in item["evidence"]


def test_pii_needs_privacy_measures(env):
    c, _who, ids, run = env

    async def _go(db):
        db.add(cm.DatasetSchema(dataset_id=ids["bare"], field_path="email", field_type="STRING", pii_type="EMAIL", ordinal=0))
    run(_go)
    items = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())
    assert items["C22"]["status"] == "FAIL" and "보호조치" in items["C22"]["evidence"]
    assert items["C24"]["status"] is None  # 개인정보가 있으면 수동 판정 필요
    assert items["T5"]["status"] == "FAIL"


def test_measure_columns_need_unit_for_c08(env):
    """C08 데이터 사전 — 설명 80% 와 함께 측정값 컬럼(숫자형, 키 · 코드 제외)의 단위 80% 이상이어야 적정."""
    c, _who, ids, run = env

    async def _go(db):
        db.add_all([
            cm.DatasetSchema(dataset_id=ids["bare"], field_path="id", field_type="INTEGER", description="번호",
                             is_primary_key="true", ordinal=0),
            cm.DatasetSchema(dataset_id=ids["bare"], field_path="store_id", field_type="INTEGER", description="매장", ordinal=1),
            cm.DatasetSchema(dataset_id=ids["bare"], field_path="amount", field_type="DECIMAL", description="판매 금액", ordinal=2),
            cm.DatasetSchema(dataset_id=ids["bare"], field_path="name", field_type="STRING", description="이름", ordinal=3),
        ])
    run(_go)
    item = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())["C08"]
    assert item["status"] == "FAIL", item
    assert "측정값 컬럼 단위 0/1개" in item["evidence"] and "단위 80% 미만" in item["evidence"]
    assert "산정기준 0개" in item["evidence"]

    async def _unit(db):
        from sqlalchemy import select
        col = (await db.execute(select(cm.DatasetSchema).where(cm.DatasetSchema.field_path == "amount"))).scalar_one()
        col.unit = "원"
        col.calculation = "판매가 × 수량"
    run(_unit)
    item = _items(c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").json())["C08"]
    assert item["status"] == "PASS", item
    assert "측정값 컬럼 단위 1/1개(100%)" in item["evidence"] and "산정기준 1개" in item["evidence"]


def test_measure_column_detection():
    m = ai_ready_check.is_measure_column
    assert m("price", "DECIMAL(10,2)", "false") and m("rental_rate", "NUMBER", "false") and m("paid", "INT", "false")
    for name in ("id", "user_id", "userId", "area_cd", "seq_no", "CUSTOMER_ID", "storeNo"):
        assert not m(name, "INTEGER", "false"), name
    assert not m("amount", "INTEGER", "true")      # 기본키
    assert not m("amount", "STRING", "false")      # 숫자형 아님


def test_schema_unit_and_calculation_survive_resync(env):
    """단위 · 산정기준은 키가 있을 때만 바뀐다 — 수집(키 없음)은 보존, 편집 화면은 null 로 지운다."""
    c, _who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}/schema"
    r = c.put(url, json=[{"field_path": "amount", "field_type": "NUMBER", "unit": " 원 ", "calculation": "판매가 × 수량"}])
    assert r.status_code == 200, r.text
    assert r.json()[0]["unit"] == "원" and r.json()[0]["calculation"] == "판매가 × 수량"
    # metadata-sync 처럼 단위 · 산정기준 키 없이 다시 보냄 — 담당자 값 보존
    r = c.put(url, json=[{"field_path": "amount", "field_type": "DECIMAL", "description": "원천 코멘트"}])
    assert r.status_code == 200, r.text
    f = r.json()[0]
    assert f["field_type"] == "DECIMAL" and f["unit"] == "원" and f["calculation"] == "판매가 × 수량"
    # 편집 화면이 비운 값을 보냄 — 지움
    r = c.put(url, json=[{"field_path": "amount", "field_type": "DECIMAL", "unit": None, "calculation": ""}])
    assert r.json()[0]["unit"] is None and r.json()[0]["calculation"] is None


def test_schema_description_cleared_only_by_edit_screen(env):
    """DEF-166 — 수집은 빈 논리명 · 설명이면 기존 값을 두고, 화면 편집(clear_empty=true)은 비운 칸을 지운다."""
    c, _who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}/schema"
    r = c.put(url, json=[{"field_path": "amount", "field_type": "NUMBER", "display_name": "금액", "description": "판매 금액"}])
    assert r.status_code == 200, r.text
    # 수집처럼 원천 코멘트 없이(빈 값 · null) — 담당자 값 보존
    for desc in ("", None):
        f = c.put(url, json=[{"field_path": "amount", "field_type": "NUMBER", "display_name": "", "description": desc}]).json()[0]
        assert f["display_name"] == "금액" and f["description"] == "판매 금액"
    # 화면 편집이 비움 — 지움
    r = c.put(f"{url}?clear_empty=true", json=[{"field_path": "amount", "field_type": "NUMBER", "display_name": "", "description": ""}])
    assert r.status_code == 200, r.text
    assert r.json()[0]["display_name"] is None and r.json()[0]["description"] is None
    # 화면 편집이라도 키를 보내지 않은 속성은 그대로(값을 넣으면 바뀜)
    c.put(url, json=[{"field_path": "amount", "field_type": "NUMBER", "description": "다시 적음"}])
    f = c.put(f"{url}?clear_empty=true", json=[{"field_path": "amount", "field_type": "NUMBER", "display_name": "금액"}]).json()[0]
    assert f["display_name"] == "금액" and f["description"] == "다시 적음"


def test_manual_judgment_overrides_and_reverts(env):
    c, who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check"
    r = c.put(f"{url}/C25", json={"status": "PASS", "note": "  DOI 부여 예정 문서  "})
    assert r.status_code == 200, r.text
    item = _items(r.json())["C25"]
    assert item["status"] == "PASS" and item["source"] == "MANUAL" and item["auto_status"] == "FAIL"
    assert item["judgment"]["note"] == "DOI 부여 예정 문서" and item["judgment"]["judged_by"] == "alice"
    assert r.json()["recommended"]["passed"] >= 1

    # 덮어쓰기
    item = _items(c.put(f"{url}/C25", json={"status": "NA"}).json())["C25"]
    assert item["status"] == "NA" and item["judgment"]["note"] is None

    # 잘못된 항목·상태
    assert c.put(f"{url}/C99", json={"status": "PASS"}).status_code == 422
    assert c.put(f"{url}/C25", json={"status": "OK"}).status_code == 422

    # 소유자가 아니면 403, 관리자는 가능
    who["user"] = _tu("mallory")
    assert c.put(f"{url}/C25", json={"status": "PASS"}).status_code == 403
    assert c.delete(f"{url}/C25").status_code == 403
    who["user"] = _tu("root", admin=True)
    item = _items(c.delete(f"{url}/C25").json())["C25"]
    assert item["status"] == "FAIL" and item["source"] == "AUTO" and item["judgment"] is None
    assert c.put(f"{V1}/catalog/datasets/99999/ai-ready/check/C25", json={"status": "PASS"}).status_code == 404


def test_split_path():
    assert ai_ready_check.split_path("ds-a.sakila.film.dataset", "ds-a") == ("sakila", "film")
    assert ai_ready_check.split_path("pg-1.shop.public.orders.dataset", "pg-1") == ("shop.public", "orders")
    assert ai_ready_check.split_path("kafka-1.events.dataset", "kafka-1") == ("", "events")
    assert ai_ready_check.split_path("other.db.t.dataset", None) == ("db", "t")


def test_overview_scopes_and_filters(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))

    async def _cat(db):
        cat = cm.Catalog(catalog_key="cat-a", name="카탈로그 A", auto_created=True)
        db.add(cat)
        await db.flush()
        (await db.get(cm.Dataset, ids["bare"])).catalog_id = cat.id
        ids["cat"] = cat.id
    run(_cat)

    scopes = c.get(f"{V1}/catalog/ai-ready/scopes").json()
    assert [(x["name"], x["datasets"], [d["name"] for d in x["dbs"]]) for x in scopes] == [
        ("카탈로그 A", 1, ["db"]), ("(카탈로그 없음)", 1, ["db"]),
    ]

    body = c.get(f"{V1}/catalog/ai-ready/overview").json()
    assert body["total"] == 2  # removed 제외
    assert [x["dataset_id"] for x in body["items"]] == [ids["other"], ids["bare"]]  # 필수 충족률 낮은 순
    paths = {x["dataset_id"]: x["path"] for x in body["items"]}
    assert paths == {ids["bare"]: "db.bare", ids["other"]: "db.other"}  # db.dataset — 카탈로그는 따로
    names = {x["dataset_id"]: x["catalog_name"] for x in body["items"]}
    assert names == {ids["bare"]: "카탈로그 A", ids["other"]: None}
    assert body["summary"]["datasets"] == 2 and body["summary"]["min_met"] == 1
    assert body["breakdown"]["by"] == "catalog"
    groups = {g["catalog_name"]: g for g in body["breakdown"]["groups"]}
    assert groups["카탈로그 A"]["required_rate"] == 100.0
    assert body["items"][1]["unmet_required"] == []

    # 카탈로그 → DB → 검색
    body = c.get(f"{V1}/catalog/ai-ready/overview", params={"catalog_id": ids["cat"]}).json()
    assert [x["dataset_id"] for x in body["items"]] == [ids["bare"]]
    assert body["breakdown"]["by"] == "db" and body["breakdown"]["groups"][0]["db"] == "db"
    assert c.get(f"{V1}/catalog/ai-ready/overview", params={"catalog_id": 0}).json()["total"] == 1
    assert c.get(f"{V1}/catalog/ai-ready/overview", params={"catalog_id": ids["cat"], "db": "nope"}).json()["total"] == 0
    body = c.get(f"{V1}/catalog/ai-ready/overview", params={"q": "DB.BA", "sort": "name"}).json()
    assert [x["path"] for x in body["items"]] == ["db.bare"]
    assert c.get(f"{V1}/catalog/ai-ready/overview", params={"q": "cat-a"}).json()["total"] == 0  # 카탈로그 키는 검색 대상 아님
    body = c.get(f"{V1}/catalog/ai-ready/overview", params={"size": 1, "page": 2}).json()
    assert body["total"] == 2 and len(body["items"]) == 1 and body["items"][0]["dataset_id"] == ids["bare"]

    # 내림차순 — 충족률 높은 순, 경로 역순
    body = c.get(f"{V1}/catalog/ai-ready/overview", params={"desc": "true"}).json()
    assert [x["dataset_id"] for x in body["items"]] == [ids["bare"], ids["other"]]
    body = c.get(f"{V1}/catalog/ai-ready/overview", params={"sort": "name", "desc": "true"}).json()
    assert [x["path"] for x in body["items"]] == ["db.other", "db.bare"]


def test_items_catalog(env):
    c, *_ = env
    items = c.get(f"{V1}/catalog/ai-ready/items").json()
    assert [i["key"] for i in items][:3] == ["C01", "C02", "C03"] and items[-1]["key"] == "T5"
    # 표 · 배지에 쓰는 짧은 이름 — 모든 항목에 따로 있다(번호 그대로가 아님)
    assert all(i["short"] and i["short"] != i["key"] for i in items)
    assert set(ai_ready_check.ITEM_SHORT) == {i["key"] for i in items}


def test_overview_and_scopes_follow_ai_ready_menu(env):
    """AI-Ready 점검 메뉴가 막힌 역할은 목록 API 도 403 — 데이터셋별 점검은 그대로(DEF-126)."""
    c, who, ids, run = env

    async def _block(db):
        db.add(Permission(kind="MENU", perm_key="ai-ready", role_id="argus-superuser"))
    run(_block)
    from app.permissions import router as perm_router
    perm_router._menu_cache.clear()  # 앞 테스트가 남긴 '미설정' 캐시

    assert c.get(f"{V1}/catalog/ai-ready/overview").status_code == 403
    assert c.get(f"{V1}/catalog/ai-ready/scopes").status_code == 403
    assert c.get(f"{V1}/catalog/ai-ready/items").status_code == 200
    assert c.get(f"{V1}/catalog/datasets/{ids['bare']}/ai-ready/check").status_code == 200
    who["user"] = _tu("root", admin=True)
    assert c.get(f"{V1}/catalog/ai-ready/overview").status_code == 200


def test_manual_dataset_creator_points_to_catalog_screen(env):
    """수동 데이터셋(직접 등록 카탈로그)은 소관기관을 카탈로그 화면에서 시스템으로 지정하라고 안내하고, 지정하면 채워진다(DEF-CAT-001)."""
    c, _who, _ids, run = env
    made: dict = {}

    async def _mk(db):
        m = cm.Datasource(datasource_id="cat-manual", name="엑셀 자료", type="manual", origin="PROD")
        db.add(m)
        await db.flush()
        d = cm.Dataset(urn="cat-manual.x.dataset", name="x", datasource_id=m.id, status="active", created_by="alice")
        db.add(d)
        await db.flush()
        made.update(src=m.id, ds=d.id)
    run(_mk)
    ev = _items(c.get(f"{V1}/catalog/datasets/{made['ds']}/ai-ready/check").json())["C05"]["evidence"]
    assert "catalogs.manage" in ev and "카탈로그의 시스템을 지정" in ev and "데이터 소스 화면에서" not in ev, ev

    async def _assign(db):
        org = cm.Organization(name="재무팀", code="fin")
        db.add(org)
        await db.flush()
        sysm = cm.System(name="회계 시스템", org_id=org.id)
        db.add(sysm)
        await db.flush()
        (await db.get(cm.Datasource, made["src"])).system_id = sysm.id
    run(_assign)
    ev = _items(c.get(f"{V1}/catalog/datasets/{made['ds']}/ai-ready/check").json())["C05"]["evidence"]
    assert "소관기관" not in ev, ev
