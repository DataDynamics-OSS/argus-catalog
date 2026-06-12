# SPDX-License-Identifier: Apache-2.0
"""페더레이션 capability(노출 정책/표시 선택) 테스트 (in-memory SQLite, 자기완결형).

검증:
- 레지스트리 + filter_metadata(섹션/스키마 컬럼 단위 필터)
- 노출자 정책 get/set (catalog_configuration CSV)
- 소비자 display_fields create/update 라운드트립
- augment_and_filter_detail 의 ddl/glossary 보강 + 노출 필터

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_capabilities
    # 또는: pytest tests/test_federation_capabilities.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.federation.models as fm
from app.federation import capabilities as cap
from app.federation import service
from app.federation import schemas as s
from app.settings.models import CatalogConfiguration


def _test_registry_and_filter() -> None:
    keys = cap.all_keys()
    assert "schema" in keys and "schema.pii" in keys and "sample" in keys
    assert len(cap.registry_items()) == len(keys)

    md = {
        "dataset_id": 1, "urn": "u", "name": "n", "datasource": {"name": "d"},
        "description": "desc", "status": "active", "tags": ["a"],
        "owners": [{"name": "o", "type": "T"}], "ddl": "CREATE",
        "schema": [{
            "field_path": "c1", "field_type": "int", "native_type": "INT",
            "nullable": "true", "is_primary_key": "true", "pii_type": "EMAIL",
            "description": "x", "ordinal": 1,
        }],
    }
    # description + schema(타입/PK만) 노출 — tags/owners/ddl/status 제외, pii/nullable 컬럼 제외
    f = cap.filter_metadata(md, ["description", "schema", "schema.type", "schema.pk"])
    assert set(f.keys()) == {"dataset_id", "urn", "name", "datasource", "description", "schema"}
    col = f["schema"][0]
    assert col["field_path"] == "c1" and col["field_type"] == "int"
    assert col["is_primary_key"] == "true"
    assert "pii_type" not in col and "nullable" not in col

    # schema 미노출이면 schema 키 자체가 빠짐
    f2 = cap.filter_metadata(md, ["tags"])
    assert "schema" not in f2 and f2["tags"] == ["a"]

    # 별도 엔드포인트 게이팅
    assert cap.is_exposed("sample", ["sample"]) is True
    assert cap.is_exposed("sample", ["schema"]) is False

    # 기본 노출 — PII 제외 옵션
    assert "schema.pii" in cap.default_exposed(exclude_pii=False)
    assert "schema.pii" not in cap.default_exposed(exclude_pii=True)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        fm.Base.metadata.tables["federation_instances"],
        CatalogConfiguration.__table__,
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        cm.Base.metadata.tables["catalog_dataset_schemas"],
        cm.Base.metadata.tables["catalog_glossary_terms"],
        cm.Base.metadata.tables["catalog_dataset_glossary_terms"],
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run_db() -> None:
    Session = await _make_session()
    async with Session() as db:
        # --- 노출자 정책 get/set ------------------------------------------------
        exposed = await service.get_exposed_fields(db)
        assert len(exposed) == len(cap.all_keys())          # 기본=전부
        saved = await service.set_exposed_fields(db, ["schema", "tags", "bogus"])
        assert saved == ["schema", "tags"]                  # 검증으로 bogus 제거
        assert await service.get_exposed_fields(db) == ["schema", "tags"]

        # --- 소비자 display_fields create/update --------------------------------
        r = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="a", name="A", base_url="http://a",
            display_fields=["schema", "schema.pk", "tags", "nope"],
        ))
        assert r.display_fields == ["schema", "schema.pk", "tags"]
        inst = await service.get_instance(db, r.id)
        r2 = await service.update_instance(db, inst, s.FederatedInstanceUpdate(
            display_fields=["tags"],
        ))
        assert r2.display_fields == ["tags"]

        # --- ddl/glossary 보강 + 필터 -------------------------------------------
        ds = cm.Datasource(datasource_id="d1", name="ds", type="trino", origin="PROD")
        db.add(ds)
        await db.flush()
        d = cm.Dataset(urn="x.1.dataset", name="t1", datasource_id=ds.id,
                       origin="PROD", status="active", ddl="CREATE TABLE t1(...)",
                       summary="주문 요약", tier="GOLD", sensitivity="CONFIDENTIAL",
                       row_count=1000, ingestion_frequency="DAILY")
        db.add(d)
        await db.flush()
        term = cm.GlossaryTerm(name="고객")
        db.add(term)
        await db.flush()
        db.add(cm.DatasetGlossaryTerm(dataset_id=d.id, term_id=term.id))
        await db.commit()

        base_md = {"dataset_id": d.id, "urn": d.urn, "name": d.name,
                   "datasource": {"name": "ds"}, "description": "desc"}
        # ddl/glossary 노출 시 보강됨
        out = await service.augment_and_filter_detail(
            db, dict(base_md), ["description", "ddl", "glossary"],
        )
        assert out["ddl"] == "CREATE TABLE t1(...)"
        assert out["glossary"] == ["고객"]
        # 미노출 시 보강 안 됨
        out2 = await service.augment_and_filter_detail(db, dict(base_md), ["description"])
        assert "ddl" not in out2 and "glossary" not in out2

        # 확장 메타데이터 — 노출 시 전체 항목 dict 로 포함
        out3 = await service.augment_and_filter_detail(
            db, dict(base_md), ["extended"],
        )
        ext = out3.get("extended", {})
        assert ext.get("tier") == "GOLD"
        assert ext.get("sensitivity") == "CONFIDENTIAL"
        assert ext.get("row_count") == 1000
        assert ext.get("summary") == "주문 요약"
        assert ext.get("ingestion_frequency") == "DAILY"
        # 미노출 시 없음
        out4 = await service.augment_and_filter_detail(db, dict(base_md), ["description"])
        assert "extended" not in out4

        # 스키마 논리명(display_name) 보강 — 식별 컬럼이라 필터에서 유지
        db.add(cm.DatasetSchema(dataset_id=d.id, field_path="c1", field_type="int",
                                display_name="고객ID", ordinal=1))
        await db.commit()
        md_with_schema = {**base_md, "schema": [{"field_path": "c1", "ordinal": 1}]}
        out5 = await service.augment_and_filter_detail(
            db, md_with_schema, ["schema"],
        )
        assert out5["schema"][0].get("display_name") == "고객ID"


def test_federation_capabilities() -> None:
    """pytest 진입점."""
    _test_registry_and_filter()
    asyncio.run(_run_db())


if __name__ == "__main__":
    _test_registry_and_filter()
    asyncio.run(_run_db())
    print("test_federation_capabilities OK")
