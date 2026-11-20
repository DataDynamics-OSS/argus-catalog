# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 메타데이터 (TODO-096 1단계) — 자기완결형, in-memory SQLite.

- PUT /catalog/datasets/{id} 로 AI-Ready 항목 저장·응답(날짜·목록·불리언 형), 명시한 null 은 지움, 생략은 유지.
- 검증: 라이선스 코드, http(s) URL, ISO 639-1 언어, 이용 유형, 이메일, 시간범위 앞뒤(같은 요청 · 저장값과 합쳐서).
- DEF-102: 기존 확장 메타데이터도 null 을 보내면 지워진다(전에는 무시되어 화면에서 비울 수 없었다).
- GET /catalog/ai-ready/defaults — 설정 › AI-Ready 의 기관 기본값과 선택지. PUT /settings/ai-ready 는 관리자 전용.
- GET /catalog/datasets/{id}/ai-ready/suggestions — 최신 프로파일에서 시간범위·결측 정보 제안(저장하지 않음).

실행:
    cd backend
    pytest tests/test_ai_ready_metadata.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
from app.audit.models import AdminAuditLog
from app.catalog.router import router as catalog_router
from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.permissions.models import Permission
from app.quality.models import DataProfile
from app.settings.models import CatalogConfiguration
from app.settings.router import router as settings_router

_TABLES = [
    "catalog_organizations", "catalog_systems", "catalog_datasources",
    "catalog_catalogs", "catalog_catalog_connections",
    "catalog_datasets", "catalog_dataset_properties", "catalog_dataset_schemas",
    "catalog_schema_snapshots", "catalog_tags", "catalog_dataset_tags", "catalog_owners",
    "catalog_glossary_terms", "catalog_dataset_glossary_terms", "catalog_dataset_version",
]
V1 = "/api/v1"


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

    async def _init() -> int:
        async with engine.begin() as conn:
            for t in _TABLES:
                await conn.run_sync(lambda sc, t=t: cm.Base.metadata.tables[t].create(sc, checkfirst=True))
            for model in (Permission, CatalogConfiguration, AdminAuditLog, DataProfile):
                await conn.run_sync(lambda sc, m=model: m.__table__.create(sc, checkfirst=True))
        async with factory() as db:
            src = cm.Datasource(datasource_id="ds-dev", name="dev src", type="mysql", origin="DEV")
            db.add(src)
            await db.flush()
            ds = cm.Dataset(urn="ds-dev.db.t1", name="db.t1", datasource_id=src.id, origin="DEV",
                            status="active", created_by="alice", freshness_sla="매일 06:00까지")
            db.add(ds)
            await db.commit()
            return ds.id
    ds_id = asyncio.run(_init())

    app = FastAPI()
    for r in (catalog_router, settings_router):
        app.include_router(r, prefix=V1)

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
    c = TestClient(app)

    def add_profile(columns: list[dict], row_count: int, when: datetime):
        async def _add():
            async with factory() as db:
                db.add(DataProfile(dataset_id=ds_id, row_count=row_count, profile_json=json.dumps(columns),
                                   profiled_at=when))
                await db.commit()
        asyncio.run(_add())

    yield c, who, ds_id, add_profile
    asyncio.run(engine.dispose())


FULL = {
    "legal_basis": "통계법 제3조",
    "publisher": "서울특별시",
    "landing_page": "https://data.seoul.go.kr/dataset/pop",
    "contact_email": "data@seoul.go.kr",
    "data_language": "KO, en, ko",
    "persistent_id": "seoul-pop-2025",
    "temporal_start": "2025-01-01",
    "temporal_end": "2025-12-31",
    "spatial_coverage": "서울특별시 25개 자치구",
    "data_version": "v1.2",
    "version_notes": "결측치 처리 방식 변경",
    "license": "KOGL-1",
    "license_url": "https://example.go.kr/terms",
    "rights": "상업적 이용 허용",
    "allowed_uses": ["ai_training", "VIEW", "VIEW"],
    "contains_synthetic": False,
    "contains_machine_annotation": True,
    "data_biases": "도시 지역 측정소 편중",
    "known_limitations": "단기 체류 외국인 제외",
    "missing_data_info": "센서 점검 2.1%",
    "missing_value_notation": "NULL, -9999=측정불가",
    "privacy_measures": "주민번호 삭제, 연령 5세 범주화",
}


def test_save_and_read_ai_ready_fields(env):
    c, _who, ds_id, _p = env
    r = c.put(f"{V1}/catalog/datasets/{ds_id}", json=FULL)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["legal_basis"] == "통계법 제3조"
    assert d["data_language"] == "ko,en"                      # 소문자·중복 제거
    assert d["allowed_uses"] == ["VIEW", "AI_TRAINING"]       # 정의 순서·중복 제거
    assert d["temporal_start"] == "2025-01-01" and d["temporal_end"] == "2025-12-31"
    assert d["contains_synthetic"] is False and d["contains_machine_annotation"] is True
    assert d["license"] == "KOGL-1"
    # 다시 읽어도 같다
    assert c.get(f"{V1}/catalog/datasets/{ds_id}").json()["allowed_uses"] == ["VIEW", "AI_TRAINING"]


def test_null_clears_and_omitted_keeps(env):
    c, _who, ds_id, _p = env
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json=FULL).status_code == 200
    r = c.put(f"{V1}/catalog/datasets/{ds_id}",
              json={"publisher": None, "temporal_start": None, "contains_synthetic": None, "data_biases": "  "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["publisher"] is None and d["temporal_start"] is None and d["contains_synthetic"] is None
    assert d["data_biases"] is None                           # 공백만이면 지움
    assert d["license"] == "KOGL-1" and d["temporal_end"] == "2025-12-31"  # 보내지 않은 항목은 유지
    # 빈 목록 = 모두 허용하지 않음, null = 정하지 않음
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"allowed_uses": []}).json()["allowed_uses"] == []
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"allowed_uses": None}).json()["allowed_uses"] is None


@pytest.mark.parametrize("body", [
    {"license": "MIT"},
    {"landing_page": "ftp://x"},
    {"license_url": "example.com"},
    {"data_language": "kor"},
    {"allowed_uses": ["SELL"]},
    {"contact_email": "not-an-email"},
    {"temporal_start": "2025-02-01", "temporal_end": "2025-01-01"},
])
def test_validation_rejects(env, body):
    c, _who, ds_id, _p = env
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json=body).status_code == 422


def test_temporal_checked_against_stored_value(env):
    c, _who, ds_id, _p = env
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"temporal_start": "2025-06-01"}).status_code == 200
    r = c.put(f"{V1}/catalog/datasets/{ds_id}", json={"temporal_end": "2025-01-01", "publisher": "X"})
    assert r.status_code == 422
    assert "시작일" in r.json()["detail"]
    d = c.get(f"{V1}/catalog/datasets/{ds_id}").json()
    assert d["temporal_end"] is None and d["publisher"] is None  # 같은 요청의 다른 변경도 반영되지 않음


def test_extended_field_null_clears_def102(env):
    """DEF-102: 화면에서 '최신성 SLA' 같은 확장 항목을 비우고 저장하면(null) 지워져야 한다."""
    c, _who, ds_id, _p = env
    assert c.get(f"{V1}/catalog/datasets/{ds_id}").json()["freshness_sla"] == "매일 06:00까지"
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"note": "메모"}).json()["freshness_sla"] == "매일 06:00까지"
    r = c.put(f"{V1}/catalog/datasets/{ds_id}", json={"freshness_sla": None, "note": None})
    assert r.status_code == 200, r.text
    assert r.json()["freshness_sla"] is None and r.json()["note"] is None


def test_only_owner_or_admin_can_edit(env):
    c, who, ds_id, _p = env
    who["user"] = _tu("bob")
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"publisher": "X"}).status_code == 403


def test_defaults_from_settings(env):
    c, who, _ds_id, _p = env
    r = c.get(f"{V1}/catalog/ai-ready/defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["defaults"] == {"publisher": None, "data_language": "ko", "license": None,
                                "license_url": None, "contact_email": None}
    assert {"code": "KOGL-1", "label": "공공누리 제1유형 (출처표시)"} in body["licenses"]
    assert [u["code"] for u in body["allowed_uses"]] == ["VIEW", "ANALYSIS", "COMBINE", "AI_TRAINING"]

    # 설정은 관리자만
    assert c.put(f"{V1}/settings/ai-ready", json={"publisher": "X"}).status_code == 403
    who["user"] = _tu("root", admin=True)
    assert c.put(f"{V1}/settings/ai-ready", json={"license": "MIT"}).status_code == 422
    r = c.put(f"{V1}/settings/ai-ready", json={
        "publisher": " 서울특별시 ", "language": "ko,EN", "license": "KOGL-1",
        "license_url": "", "contact_email": "data@seoul.go.kr", "max_missing_pct": 10, "max_missed_cycles": 3,
    })
    assert r.status_code == 200, r.text
    assert c.get(f"{V1}/settings/ai-ready").json() == {
        "publisher": "서울특별시", "language": "ko,en", "license": "KOGL-1",
        "license_url": "", "contact_email": "data@seoul.go.kr", "max_missing_pct": 10.0, "max_missed_cycles": 3,
        "report_due_days": 10, "laws": "", "public_mode": "REQUEST", "public_scheme": "http", "public_port": None,
        "public_url": "",
    }
    assert c.put(f"{V1}/settings/ai-ready", json={"max_missing_pct": 101}).status_code == 422
    assert c.put(f"{V1}/settings/ai-ready", json={"report_due_days": 0}).status_code == 422
    who["user"] = None  # 기본값 조회는 로그인 없이도(데이터셋 상세와 같음)
    assert c.get(f"{V1}/catalog/ai-ready/defaults").json()["defaults"] == {
        "publisher": "서울특별시", "data_language": "ko,en", "license": "KOGL-1",
        "license_url": None, "contact_email": "data@seoul.go.kr",
    }


def test_settings_partial_update_keeps_other_fields(env):
    """DEF-143 — 보낸 필드만 바꾸고 나머지는 저장된 값을 그대로 둔다(병합)."""
    c, who, _ds_id, _add = env
    who["user"] = _tu("root", admin=True)
    url = f"{V1}/settings/ai-ready"
    assert c.put(url, json={"publisher": "행정안전부", "license": "KOGL-1", "contact_email": "d@x.kr",
                            "laws": "공공데이터법\n대기환경보전법"}).status_code == 200
    assert c.put(url, json={"public_mode": "CUSTOM", "public_url": "https://catalog.example.kr/"}).status_code == 200
    assert c.put(url, json={"public_mode": "IP"}).status_code == 200
    body = c.get(url).json()
    assert (body["publisher"], body["license"], body["contact_email"], body["laws"]) == \
        ("행정안전부", "KOGL-1", "d@x.kr", "공공데이터법\n대기환경보전법"), "부분 갱신이 기관 기본값을 지우지 않음"
    assert (body["public_mode"], body["public_url"]) == ("IP", "https://catalog.example.kr")

    assert c.put(url, json={"public_mode": "CUSTOM"}).status_code == 200, "저장된 공개 주소가 있으면 방식만 바꿔도 된다"
    assert c.put(url, json={"laws": ""}).status_code == 200
    body = c.get(url).json()
    assert (body["laws"], body["publisher"], body["public_mode"]) == ("", "행정안전부", "CUSTOM")

    assert c.put(url, json={"public_mode": "CUSTOM", "public_url": ""}).status_code == 422, "주소를 비우면 422"
    assert c.put(url, json={"public_url": ""}).status_code == 422, "병합 결과(CUSTOM · 빈 주소)도 검사"
    assert c.get(url).json()["public_url"] == "https://catalog.example.kr", "거부된 저장은 반영되지 않음"


def test_suggestions_from_latest_profile(env):
    c, _who, ds_id, add_profile = env
    assert c.get(f"{V1}/catalog/datasets/999/ai-ready/suggestions").status_code == 404
    assert c.get(f"{V1}/catalog/datasets/{ds_id}/ai-ready/suggestions").json() == {
        "profile": None, "temporal": None, "missing": None}

    add_profile([{"column_name": "id", "column_type": "INTEGER", "total_count": 100, "null_count": 0}],
                100, datetime(2026, 1, 1, tzinfo=timezone.utc))
    cols = [
        {"column_name": "id", "column_type": "INTEGER", "total_count": 200, "null_count": 0},
        {"column_name": "created_at", "column_type": "TIMESTAMP", "total_count": 200, "null_count": 4,
         "null_percent": 2.0, "min_value": "2024-03-01 09:00:00", "max_value": "2025-02-28 18:00:00"},
        {"column_name": "base_dt", "column_type": "date", "total_count": 200, "null_count": 0,
         "min_value": "2025-01-01", "max_value": "2025-01-31"},
        {"column_name": "note", "column_type": "VARCHAR(100)", "total_count": 200, "null_count": 50,
         "null_percent": 25.0, "min_value": "2020-01-01", "max_value": "2030-01-01"},  # 문자 컬럼은 시간범위에서 제외
    ]
    add_profile(cols, 200, datetime(2026, 11, 1, tzinfo=timezone.utc))
    s = c.get(f"{V1}/catalog/datasets/{ds_id}/ai-ready/suggestions").json()
    assert s["profile"]["row_count"] == 200                   # 최신 프로파일
    assert s["temporal"] == {"temporal_start": "2024-03-01", "temporal_end": "2025-02-28", "column": "created_at"}
    assert s["missing"]["missing_columns"] == 2
    assert s["missing"]["missing_ratio"] == 6.75               # (4 + 50) / 800
    text = s["missing"]["missing_data_info"]
    assert "2026-11-01" in text and "200행" in text
    assert text.index("note 25%") < text.index("created_at 2%")  # 결측률 높은 순
    # 제안은 저장하지 않는다
    assert c.get(f"{V1}/catalog/datasets/{ds_id}").json()["temporal_start"] is None


def test_pii_fields_and_compliance_tags_are_lists_todo140(env):
    """TODO-143: 개인정보 항목 · 규제 태그는 목록으로 주고받고, 값 안의 쉼표가 쪼개지지 않는다(예전 쉼표 문자열도 받는다)."""
    c, _who, ds_id, _p = env
    r = c.put(f"{V1}/catalog/datasets/{ds_id}",
              json={"pii_fields": ["이름", "주소(시, 구, 동)"], "compliance_tags": ["개인정보보호법", "GDPR"]})
    assert r.status_code == 200, r.text
    assert r.json()["pii_fields"] == ["이름", "주소(시, 구, 동)"]
    assert c.get(f"{V1}/catalog/datasets/{ds_id}").json()["compliance_tags"] == ["개인정보보호법", "GDPR"]
    # 예전 클라이언트의 쉼표 문자열
    r = c.put(f"{V1}/catalog/datasets/{ds_id}", json={"pii_fields": "전화번호, 이메일"})
    assert r.json()["pii_fields"] == ["전화번호", "이메일"]
    # 빈 목록 · null 은 지운다
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"pii_fields": []}).json()["pii_fields"] is None
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"compliance_tags": None}).json()["compliance_tags"] is None
    # 규제 태그 저장 컬럼(255자)을 넘으면 422
    assert c.put(f"{V1}/catalog/datasets/{ds_id}", json={"compliance_tags": ["가" * 260]}).status_code == 422
