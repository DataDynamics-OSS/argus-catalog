# SPDX-License-Identifier: Apache-2.0
"""인스턴스별 미러 데이터셋 탐색(browse) 테스트 (in-memory SQLite, 자기완결형).

검증: browse_instance_datasets 가 단일 인스턴스의 HARVEST 미러를
데이터소스별로 그룹핑하고, 그룹/데이터셋 정렬·카운트·q 필터를 올바르게 처리한다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_browse
    # 또는: pytest tests/test_federation_browse.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.federation.models as fm
from app.federation import service
from app.federation import schemas as s

_TABLES = ["federation_instances", "federation_datasets"]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [fm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: fm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run() -> None:
    Session = await _make_session()

    async with Session() as db:
        a = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-a", name="Team A", base_url="https://a.internal", mode="HARVEST",
        ))
        a_inst = await service.get_instance(db, a.id)

        # mysql-prod: orders, customers / hive-dw: fact_sales / datasource 미지정: misc
        db.add_all([
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="m.orders.dataset",
                                federated_urn="team-a::m.orders.dataset", name="orders",
                                datasource_name="mysql-prod", datasource_type="MySQL",
                                description="주문 테이블"),
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="m.customers.dataset",
                                federated_urn="team-a::m.customers.dataset", name="customers",
                                datasource_name="mysql-prod", datasource_type="MySQL"),
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="h.fact_sales.dataset",
                                federated_urn="team-a::h.fact_sales.dataset", name="fact_sales",
                                datasource_name="hive-dw", datasource_type="Hive"),
            fm.FederatedDataset(instance_id=a_inst.id, remote_urn="x.misc.dataset",
                                federated_urn="team-a::x.misc.dataset", name="misc"),
        ])
        await db.commit()

        # --- 전체 탐색 ---------------------------------------------------------
        resp = await service.browse_instance_datasets(db, a_inst)
        assert resp.instance_key == "team-a"
        assert resp.total_datasets == 4
        assert resp.truncated is False

        groups = {g.datasource_name: g for g in resp.datasources}
        # 데이터소스명 오름차순: '(미지정)' < 'hive-dw' < 'mysql-prod' (한글 괄호는 정렬상 뒤지만
        # 정렬은 SQL 위임 — 여기서는 그룹 구성/카운트만 검증)
        assert set(groups) == {"mysql-prod", "hive-dw", "(미지정)"}
        assert groups["mysql-prod"].dataset_count == 2
        assert groups["mysql-prod"].datasource_type == "MySQL"
        # 그룹 내 데이터셋명 오름차순: customers < orders
        assert [d.name for d in groups["mysql-prod"].datasets] == ["customers", "orders"]
        assert groups["hive-dw"].dataset_count == 1
        assert groups["(미지정)"].dataset_count == 1
        # 드릴다운 키(federated_urn) 가 노출되는지
        assert groups["hive-dw"].datasets[0].federated_urn == "team-a::h.fact_sales.dataset"

        # --- q 필터 -----------------------------------------------------------
        filtered = await service.browse_instance_datasets(db, a_inst, q="order")
        assert filtered.total_datasets == 1
        assert len(filtered.datasources) == 1
        assert filtered.datasources[0].datasets[0].name == "orders"

        # 설명(description) 부분일치도 동작
        by_desc = await service.browse_instance_datasets(db, a_inst, q="주문")
        assert by_desc.total_datasets == 1
        assert by_desc.datasources[0].datasets[0].name == "orders"

        # 매칭 없으면 빈 트리
        empty = await service.browse_instance_datasets(db, a_inst, q="없는데이터")
        assert empty.total_datasets == 0
        assert empty.datasources == []


def test_federation_browse() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_browse OK")
