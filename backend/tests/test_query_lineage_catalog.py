# SPDX-License-Identifier: Apache-2.0
"""쿼리 리니지를 엔진의 카탈로그 연결(``remote_name``)로 정한 카탈로그 안에서 해석한다 (TODO-001).

구성:
- hive-ds(HMS, 카탈로그 10 SYNC), ice-ds(Iceberg REST, 카탈로그 20 SYNC)에 같은 이름 테이블
  ``sales.orders``·``sales.daily`` 가 있다.
- trino-ds: 10 을 ``hive``, 20 을 ``iceberg`` 로 QUERY 연결.
- impala-ds: 10 에만 QUERY 연결(엔진 쪽 이름 없음).
- sr-ds(StarRocks): 자기 카탈로그 30 SYNC + 10 을 ``hive_ext`` 로 QUERY 연결.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest tests/test_query_lineage_catalog.py
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.lineage_resolve import (
    resolve_all_query_lineage,
    resolve_lineage_for_dataset,
    resolve_query_lineage_for_engine,
)

_DATASOURCES = [(1, "hive-ds"), (2, "ice-ds"), (3, "trino-ds"), (4, "impala-ds"), (5, "sr-ds")]
_CONNECTIONS = [  # (catalog_id, datasource pk, role, remote_name)
    (10, 1, "SYNC", None), (20, 2, "SYNC", None),
    (10, 3, "QUERY", "hive"), (20, 3, "QUERY", "Iceberg"),
    (10, 4, "QUERY", None),
    (30, 5, "SYNC", None), (10, 5, "QUERY", "hive_ext"),
]
_DATASETS = [  # (id, qualified_name, catalog_id)
    (100, "hive-ds.sales.orders", 10), (101, "hive-ds.sales.daily", 10),
    (200, "ice-ds.sales.orders", 20), (201, "ice-ds.sales.daily", 20),
    (300, "sr-ds.sales.orders", 30),
]
_LINEAGE = [  # (id, engine, source_catalog, source, target_catalog, target)
    (1, "trino-ds", "hive", "sales.orders", "iceberg", "sales.daily"),
    (2, "trino-ds", "iceberg", "sales.orders", "hive", "sales.daily"),
    (3, "impala-ds", "", "sales.orders", "", "sales.daily"),
    (4, "sr-ds", "", "sales.orders", "hive_ext", "sales.daily"),
    (5, "trino-ds", "nocat", "sales.orders", "nocat", "sales.daily"),
    (6, "", "", "sales.orders", "", "sales.daily"),
    (7, "unregistered-ds", "", "sales.orders", "", "sales.daily"),
]


async def _make(datasets=_DATASETS, lineage=_LINEAGE):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE catalog_datasources (id INTEGER PRIMARY KEY, datasource_id TEXT)",
            "CREATE TABLE catalog_catalog_connections (id INTEGER PRIMARY KEY, catalog_id INT, "
            "datasource_id INT, role TEXT, remote_name TEXT)",
            "CREATE TABLE catalog_datasets (id INTEGER PRIMARY KEY, qualified_name TEXT, catalog_id INT)",
            "CREATE TABLE argus_query_lineage (id INTEGER PRIMARY KEY, datasource_id TEXT NOT NULL DEFAULT '', "
            "source_catalog TEXT NOT NULL DEFAULT '', source_table TEXT, target_catalog TEXT NOT NULL DEFAULT '', "
            "target_table TEXT, source_dataset_id INT, target_dataset_id INT)",
        ):
            await conn.execute(text(ddl))
        for pk, key in _DATASOURCES:
            await conn.execute(text("INSERT INTO catalog_datasources VALUES (:i, :k)"), {"i": pk, "k": key})
        for cid, dpk, role, remote in _CONNECTIONS:
            await conn.execute(text(
                "INSERT INTO catalog_catalog_connections (catalog_id, datasource_id, role, remote_name) "
                "VALUES (:c, :d, :r, :n)"), {"c": cid, "d": dpk, "r": role, "n": remote})
        for did, qn, cid in datasets:
            await conn.execute(text("INSERT INTO catalog_datasets VALUES (:i, :q, :c)"),
                               {"i": did, "q": qn, "c": cid})
        for rid, key, sc, st, tc, tt in lineage:
            await conn.execute(text(
                "INSERT INTO argus_query_lineage (id, datasource_id, source_catalog, source_table, "
                "target_catalog, target_table) VALUES (:i, :k, :sc, :st, :tc, :tt)"),
                {"i": rid, "k": key, "sc": sc, "st": st, "tc": tc, "tt": tt})
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _ids(s) -> dict[int, tuple]:
    rows = (await s.execute(text(
        "SELECT id, source_dataset_id, target_dataset_id FROM argus_query_lineage"))).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


async def test_backfill_resolves_within_engine_catalog():
    engine, make = await _make()
    async with make() as s:
        r = await resolve_all_query_lineage(s)
        got = await _ids(s)
    await engine.dispose()
    assert got[1] == (100, 201)   # Trino hive.sales.orders → iceberg.sales.daily
    assert got[2] == (200, 101)   # 카탈로그만 바뀐 같은 이름 — 다른 데이터셋
    assert got[3] == (100, 101)   # Impala: 연결된 카탈로그가 하나 → 그 카탈로그
    assert got[4] == (300, 101)   # StarRocks: 이름 없음 → 자기 SYNC 카탈로그, hive_ext → 10
    assert got[5] == (None, None)  # 엔진은 아는데 'nocat' 연결이 없음 → 남김
    assert got[6] == (100, 101)   # 엔진 모름 → 종전처럼 전체에서 id 작은 쪽
    assert got[7] == (100, 101)   # 등록되지 않은 엔진 → 종전 동작
    assert r["unresolved_catalog"] == 2
    assert r["remaining_unresolved"] == 1


async def test_dataset_create_fills_only_rows_of_its_catalog():
    engine, make = await _make(datasets=[])
    async with make() as s:
        n = await resolve_lineage_for_dataset(s, 200, "ice-ds.sales.orders", 20)
        got = await _ids(s)
    await engine.dispose()
    # 카탈로그 20 의 sales.orders 는 Trino iceberg 행(2)과 엔진 모르는 행(6·7)에만 붙는다
    assert {rid for rid, (src, _) in got.items() if src == 200} == {2, 6, 7}
    assert n == 3


async def test_engine_reresolve_after_connection_added():
    engine, make = await _make()
    async with make() as s:
        await resolve_all_query_lineage(s)
        assert (await _ids(s))[5] == (None, None)
        # 연결 변경 흉내: trino-ds 의 카탈로그 10 연결을 엔진 쪽 이름 'nocat' 으로 다시 맺는다
        await s.execute(text(
            "UPDATE catalog_catalog_connections SET remote_name = 'nocat' "
            "WHERE catalog_id = 10 AND datasource_id = 3"))
        await s.commit()
        n = await resolve_query_lineage_for_engine(s, "trino-ds")
        got = await _ids(s)
    await engine.dispose()
    assert got[5] == (100, 101)
    assert n == 2


async def test_engine_with_several_catalogs_and_no_sync_is_unresolved():
    lineage = [(1, "trino-ds", "", "sales.orders", "", "sales.daily")]
    engine, make = await _make(lineage=lineage)
    async with make() as s:
        r = await resolve_all_query_lineage(s)
        got = await _ids(s)
    await engine.dispose()
    # Trino 는 카탈로그 둘에 연결되고 SYNC 가 없다 → 이름 없는 테이블은 어느 카탈로그인지 모름
    assert got[1] == (None, None)
    assert r["unresolved_catalog"] == 2
