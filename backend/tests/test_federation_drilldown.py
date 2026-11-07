# SPDX-License-Identifier: Apache-2.0
"""페더레이션 Phase 2 단위 테스트 — visibility 거버넌스 + LIVE drill-down 프록시.

자기완결형(in-memory SQLite). 검증 범위:
- resolve_federated_urn: ``{instance_key}::{remote_urn}`` 파싱/해석
- visibility: exclude_pii / exclude_sensitivity / datasource allow-list 필터
- build_export_datasets 가 visibility 정책을 반영
- federated_dataset_detail/sample: peer 프록시 결과에 출처 정보 부가

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_drilldown
    # 또는: pytest tests/test_federation_drilldown.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.federation.models as fm
from app.core.config import settings
from app.federation import client, service
from app.federation import schemas as s
from app.settings.models import CatalogConfiguration

_TABLES = [
    "catalog_datasources",
    "catalog_datasets",
    "catalog_dataset_schemas",
    "federation_instances",
    "federation_datasets",  # 미러 샘플 조회(federated_dataset_sample)
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # catalog_configuration — export 노출 항목(exposed_fields) 정책 조회용
    tables = [cm.Base.metadata.tables[t] for t in _TABLES] + [CatalogConfiguration.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_datasets(db) -> dict[str, int]:
    """4개 데이터셋: 공개/PII/민감/별도DS. 반환 {label: dataset_id}."""
    pub = cm.Datasource(datasource_id="mysql-pub", name="mysql-pub", type="mysql", origin="PROD")
    sec = cm.Datasource(datasource_id="secret-db", name="secret-db", type="mysql", origin="PROD")
    db.add_all([pub, sec])
    await db.commit()
    await db.refresh(pub)
    await db.refresh(sec)

    rows = {
        "plain": cm.Dataset(urn="mysql-pub.a.dataset", name="a", datasource_id=pub.id,
                            status="active", origin="PROD", contains_pii="false",
                            sensitivity="INTERNAL"),
        "pii": cm.Dataset(urn="mysql-pub.b.dataset", name="b", datasource_id=pub.id,
                          status="active", origin="PROD", contains_pii="true",
                          sensitivity="INTERNAL"),
        "restricted": cm.Dataset(urn="mysql-pub.c.dataset", name="c", datasource_id=pub.id,
                                 status="active", origin="PROD", contains_pii="false",
                                 sensitivity="RESTRICTED"),
        "otherds": cm.Dataset(urn="secret-db.d.dataset", name="d", datasource_id=sec.id,
                              status="active", origin="PROD", contains_pii="false",
                              sensitivity="INTERNAL"),
    }
    db.add_all(list(rows.values()))
    await db.commit()
    for r in rows.values():
        await db.refresh(r)
    return {k: v.id for k, v in rows.items()}


def _reset_visibility() -> None:
    settings.federation_export_exclude_pii = False
    settings.federation_export_exclude_sensitivity = []
    settings.federation_export_datasource_allowlist = []


async def _test_visibility() -> None:
    from app.federation import visibility

    Session = await _make_session()
    async with Session() as db:
        ids = await _seed_datasets(db)
        allids = list(ids.values())

        _reset_visibility()
        # 정책 없음 → 전부 노출
        assert not visibility.has_visibility_policy()
        assert await visibility.exportable_dataset_ids(db, allids) == set(allids)

        # exclude_pii → pii 제외
        settings.federation_export_exclude_pii = True
        assert visibility.has_visibility_policy()
        allowed = await visibility.exportable_dataset_ids(db, allids)
        assert ids["pii"] not in allowed and ids["plain"] in allowed
        assert not await visibility.is_dataset_exportable(db, ids["pii"])
        _reset_visibility()

        # exclude_sensitivity → restricted 제외
        settings.federation_export_exclude_sensitivity = ["RESTRICTED"]
        allowed = await visibility.exportable_dataset_ids(db, allids)
        assert ids["restricted"] not in allowed and ids["plain"] in allowed
        _reset_visibility()

        # datasource allow-list → 다른 DS 제외
        settings.federation_export_datasource_allowlist = ["mysql-pub"]
        allowed = await visibility.exportable_dataset_ids(db, allids)
        assert ids["otherds"] not in allowed
        assert {ids["plain"], ids["pii"], ids["restricted"]} <= allowed
        _reset_visibility()

        # build_export_datasets 가 정책 반영 (exclude_pii)
        settings.federation_export_exclude_pii = True
        page = await service.build_export_datasets(db, limit=100, offset=0)
        urns = {it.urn for it in page.items}
        assert "mysql-pub.b.dataset" not in urns      # pii 제외
        assert "mysql-pub.a.dataset" in urns
        assert page.total == 3
        _reset_visibility()

        # build_export_datasets 가 노출 항목(exposed_fields) 정책도 반영 —
        # description/origin/schema 를 끄면 목록 export 에서도 비워진다.
        await db.execute(cm.Dataset.__table__.update()
                         .where(cm.Dataset.id == ids["plain"])
                         .values(description="secret desc", summary="secret sum"))
        db.add(cm.DatasetSchema(dataset_id=ids["plain"], field_path="c1",
                                field_type="int", description="col desc", ordinal=0))
        await db.commit()

        page = await service.build_export_datasets(db, limit=100, offset=0)
        item = next(it for it in page.items if it.urn == "mysql-pub.a.dataset")
        assert item.description == "secret desc" and item.summary == "secret sum"
        assert item.fields and item.fields[0].field_type == "int"   # 기본=전부 노출

        await service.set_exposed_fields(db, ["schema", "origin"])
        page = await service.build_export_datasets(db, limit=100, offset=0)
        item = next(it for it in page.items if it.urn == "mysql-pub.a.dataset")
        assert item.description is None and item.summary is None
        assert item.origin == "PROD"
        assert [f.field_path for f in item.fields] == ["c1"]
        assert item.fields[0].field_type is None and item.fields[0].description is None

        await service.set_exposed_fields(db, ["description"])
        page = await service.build_export_datasets(db, limit=100, offset=0)
        item = next(it for it in page.items if it.urn == "mysql-pub.a.dataset")
        assert item.description == "secret desc" and item.fields == []


async def _test_resolve_and_drilldown() -> None:
    Session = await _make_session()
    async with Session() as db:
        await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="Team A", base_url="https://a.internal",
        ))

        # 파싱/해석
        assert await service.resolve_federated_urn(db, "no-delimiter") is None
        assert await service.resolve_federated_urn(db, "ghost::x.dataset") is None
        resolved = await service.resolve_federated_urn(db, "team-a::mysql.x.dataset")
        assert resolved is not None
        inst, remote = resolved
        assert inst.instance_key == "team-a" and remote == "mysql.x.dataset"

        # drill-down detail 프록시 (client 모킹)
        async def fake_detail(instance, urn, timeout=5.0):
            return {"dataset_id": 7, "urn": urn, "name": "x", "schema": []}

        async def fake_sample(instance, urn, limit=100, timeout=5.0):
            return {"format": "parquet", "columns": ["id"], "rows": [["1"]], "row_count": 1}

        caps_calls: list[str] = []

        async def fake_caps(instance, timeout=5.0):
            caps_calls.append(instance.instance_key)
            if caps_fail["v"]:
                raise RuntimeError("peer down")
            return s.CapabilitiesResponse(items=[], groups=[], exposed=["description", "schema"])

        caps_fail = {"v": False}
        orig_d = client.fetch_export_dataset
        orig_s = client.fetch_export_sample
        orig_c = client.fetch_export_capabilities
        client.fetch_export_dataset = fake_detail
        client.fetch_export_sample = fake_sample
        client.fetch_export_capabilities = fake_caps
        try:
            detail = await service.federated_dataset_detail(db, "team-a::mysql.x.dataset")
            assert detail is not None
            assert detail["source_instance_key"] == "team-a"
            assert detail["remote_urn"] == "mysql.x.dataset"
            assert detail["metadata"]["name"] == "x"
            # peer 노출 항목 — 화면이 peer 가 노출하지 않는 샘플·리니지 탭을 숨기는 근거
            assert detail["peer_exposed"] == ["description", "schema"]

            # capabilities 조회 실패 → None(알 수 없음, 화면은 탭 유지). 상세 조회는 성공
            caps_fail["v"] = True
            detail = await service.federated_dataset_detail(db, "team-a::mysql.x.dataset")
            assert detail is not None and detail["peer_exposed"] is None
            caps_fail["v"] = False

            # 가져오기 경로는 표시 정보가 필요 없어 capabilities 를 부르지 않는다
            caps_calls.clear()
            detail = await service.federated_dataset_detail(
                db, "team-a::mysql.x.dataset", include_peer_exposed=False,
            )
            assert detail is not None and detail["peer_exposed"] is None and caps_calls == []

            sample = await service.federated_dataset_sample(db, "team-a::mysql.x.dataset")
            assert sample is not None
            assert sample["source_instance_key"] == "team-a"
            assert sample["columns"] == ["id"]

            # 미해석 URN → None
            assert await service.federated_dataset_detail(db, "ghost::x") is None
        finally:
            client.fetch_export_dataset = orig_d
            client.fetch_export_sample = orig_s
            client.fetch_export_capabilities = orig_c


async def _run() -> None:
    await _test_visibility()
    await _test_resolve_and_drilldown()


def test_federation_drilldown() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_drilldown OK")
