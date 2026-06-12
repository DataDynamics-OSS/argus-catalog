# SPDX-License-Identifier: Apache-2.0
"""페더레이션 cross-instance 리니지 stitching 테스트 (in-memory SQLite, 자기완결형).

검증 범위:
- build_export_lineage: 로컬 리니지를 URN→URN 엣지로 노출 (+visibility)
- _harvest_lineage: peer 엣지를 instance 단위 전량 교체
- build_lineage_graph: 로컬+미러 엣지를 URN 매칭으로 stitch, 노드 해석
  (로컬/미러/미해석), depth BFS, cross-instance 흐름

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_federation_lineage
    # 또는: pytest tests/test_federation_lineage.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.federation.models as fm
from app.core.config import settings
from app.federation import client, harvester, service
from app.federation import schemas as s

_TABLES = [
    "catalog_datasources",
    "catalog_datasets",
    "argus_dataset_lineage",
    "federation_instances",
    "federation_datasets",
    "federation_lineage",
]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [cm.Base.metadata.tables[t] for t in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(db):
    """로컬 L→M 리니지 + team-a 미러(A) + team-a 리니지(A→L, A→ghost)."""
    ds = cm.Datasource(datasource_id="hub-x", name="hub-x", type="mysql", origin="PROD")
    db.add(ds)
    await db.commit()
    await db.refresh(ds)

    L = cm.Dataset(urn="hub-x.L.dataset", name="L", datasource_id=ds.id,
                   status="active", origin="PROD")
    M = cm.Dataset(urn="hub-x.M.dataset", name="M", datasource_id=ds.id,
                   status="active", origin="PROD")
    db.add_all([L, M])
    await db.commit()
    await db.refresh(L)
    await db.refresh(M)

    # 로컬 리니지 L → M
    db.add(cm.DatasetLineage(source_dataset_id=L.id, target_dataset_id=M.id,
                             relation_type="ETL", lineage_source="MANUAL"))

    # team-a 미러 + 리니지
    inst = await service.create_instance(db, s.FederatedInstanceCreate(
        instance_key="team-a", name="Team A", base_url="https://a.internal", mode="HARVEST",
    ))
    a_inst = await service.get_instance(db, inst.id)
    db.add(fm.FederatedDataset(
        instance_id=a_inst.id, remote_urn="mysql-a.A.dataset",
        federated_urn="team-a::mysql-a.A.dataset", name="A",
        datasource_name="mysql-a", datasource_type="mysql",
    ))
    # A → L (cross-instance: A 가 허브 L 로 흐름), A → ghost(미해석)
    db.add_all([
        fm.FederationLineage(instance_id=a_inst.id, source_urn="mysql-a.A.dataset",
                             target_urn="hub-x.L.dataset", relation_type="ETL"),
        fm.FederationLineage(instance_id=a_inst.id, source_urn="mysql-a.A.dataset",
                             target_urn="ghost.Z.dataset", relation_type="ETL"),
    ])
    await db.commit()
    return a_inst.id


async def _test_export_lineage(db) -> None:
    resp = await service.build_export_lineage(db)
    pairs = {(e.source_urn, e.target_urn) for e in resp.edges}
    assert ("hub-x.L.dataset", "hub-x.M.dataset") in pairs
    assert resp.total == 1


async def _test_harvest_lineage_replace() -> None:
    Session = await _make_session()
    async with Session() as db:
        inst = await service.create_instance(db, s.FederatedInstanceCreate(
            instance_key="team-b", name="Team B", base_url="https://b.internal", mode="HARVEST",
        ))
        peer = await service.get_instance(db, inst.id)

    async def fake_lineage(instance, timeout=30.0):
        return s.FederatedExportLineageResponse(
            edges=[s.FederatedExportLineageEdge(source_urn="x.1.dataset",
                                                target_urn="x.2.dataset")],
            total=1,
        )

    orig = client.fetch_export_lineage
    client.fetch_export_lineage = fake_lineage
    try:
        async with Session() as db:
            peer = await service.get_instance(db, peer.id)
            n = await harvester._harvest_lineage(db, peer)
            await db.commit()
        assert n == 1

        # 두 번째 가져오기: 다른 엣지로 전량 교체
        async def fake_lineage2(instance, timeout=30.0):
            return s.FederatedExportLineageResponse(
                edges=[s.FederatedExportLineageEdge(source_urn="x.3.dataset",
                                                    target_urn="x.4.dataset")],
                total=1,
            )

        client.fetch_export_lineage = fake_lineage2
        async with Session() as db:
            peer = await service.get_instance(db, peer.id)
            await harvester._harvest_lineage(db, peer)
            await db.commit()
        async with Session() as db:
            from sqlalchemy import select
            urns = sorted((await db.execute(
                select(fm.FederationLineage.source_urn)
            )).scalars().all())
            assert urns == ["x.3.dataset"]   # 이전 엣지 교체됨
    finally:
        client.fetch_export_lineage = orig


async def _test_stitch_graph() -> None:
    Session = await _make_session()
    async with Session() as db:
        await _seed(db)

    async with Session() as db:
        await _test_export_lineage(db)

    # depth=1: L 중심 → {L, M, A}, 엣지 L-M(local), A-L(team-a)
    async with Session() as db:
        g = await service.build_lineage_graph(db, "hub-x.L.dataset", depth=1)
        node_by_urn = {n.urn: n for n in g.nodes}
        assert set(node_by_urn) == {"hub-x.L.dataset", "hub-x.M.dataset", "mysql-a.A.dataset"}
        assert node_by_urn["hub-x.L.dataset"].source_instance_key is None       # 로컬
        assert node_by_urn["mysql-a.A.dataset"].source_instance_key == "team-a"  # 미러
        assert not node_by_urn["mysql-a.A.dataset"].unresolved
        reported = {(e.source_urn, e.target_urn): e.reported_by for e in g.edges}
        assert reported[("hub-x.L.dataset", "hub-x.M.dataset")] is None          # 로컬 엣지
        assert reported[("mysql-a.A.dataset", "hub-x.L.dataset")] == "team-a"    # cross-instance
        assert ("mysql-a.A.dataset", "ghost.Z.dataset") not in reported          # depth 밖

    # federated_urn 으로 시작해도 동일 그래프 키로 해석
    async with Session() as db:
        g2 = await service.build_lineage_graph(db, "team-a::mysql-a.A.dataset", depth=1)
        assert g2.root_urn == "mysql-a.A.dataset"
        assert {n.urn for n in g2.nodes} >= {"mysql-a.A.dataset", "hub-x.L.dataset",
                                             "ghost.Z.dataset"}

    # depth=2: ghost 까지 도달 → 미해석 노드
    async with Session() as db:
        g3 = await service.build_lineage_graph(db, "hub-x.L.dataset", depth=2)
        ghost = next(n for n in g3.nodes if n.urn == "ghost.Z.dataset")
        assert ghost.unresolved is True
        assert ("mysql-a.A.dataset", "ghost.Z.dataset") in {
            (e.source_urn, e.target_urn) for e in g3.edges
        }


async def _run() -> None:
    settings.federation_export_exclude_pii = False
    settings.federation_export_exclude_sensitivity = []
    settings.federation_export_datasource_allowlist = []
    await _test_harvest_lineage_replace()
    await _test_stitch_graph()


def test_federation_lineage() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_federation_lineage OK")
