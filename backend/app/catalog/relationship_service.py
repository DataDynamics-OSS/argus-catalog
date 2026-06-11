"""사용 기반 컬럼 관계 — 쿼리 워크로드의 JOIN 키를 데이터셋/필드로 해석·집계한다.

확장(metadata-sync)이 SQL 에서 추출한 JOIN 키 컬럼쌍(테이블/컬럼 이름)을 받아 해당
datasource 내 데이터셋/필드로 해석하고, 무방향 정규화 후 빈도(join_count)를 upsert 한다.
데이터 흐름(lineage)이 아니라 "어떤 컬럼이 어떤 컬럼과 자주 함께 조인되는가"를 집계한다.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _clean(name: str | None) -> str:
    """식별자 정규화 — 백틱/따옴표 제거 + 소문자(대소문자 무시 매칭용)."""
    return (name or "").strip().strip('`"').lower()


async def _resolve_datasource(session: AsyncSession, ref) -> int | None:
    """datasource_id(string) 또는 type 으로 내부 int id 해석. datasource_id 우선."""
    if ref is None or ref == "":
        return None
    row = (await session.execute(text(
        "SELECT id FROM catalog_datasources WHERE datasource_id = :d OR type = :d "
        "ORDER BY (datasource_id = :d) DESC LIMIT 1"
    ), {"d": str(ref)})).fetchone()
    return row[0] if row else None


async def ingest_join_keys(
    session: AsyncSession, datasource_ref, pairs: list[dict], username: str | None = None,
) -> dict:
    """JOIN 키 컬럼쌍을 해석·정규화·집계한다.

    pairs: [{"a_table","a_col","b_table","b_col"}, ...] (테이블/컬럼은 SQL 상의 이름).
    같은 datasource 내에서 테이블→데이터셋, 컬럼→field_path 로 해석되는 쌍만 적재한다.
    """
    ds_id = await _resolve_datasource(session, datasource_ref)
    if ds_id is None:
        return {"resolved": 0, "skipped": len(pairs), "reason": "datasource not found"}

    ds_rows = (await session.execute(text(
        "SELECT id, lower(qualified_name) AS qn, lower(name) AS nm "
        "FROM catalog_datasets WHERE datasource_id = :ds"
    ), {"ds": ds_id})).fetchall()
    by_qn = {r.qn: r.id for r in ds_rows}
    by_name: dict[str, list[int]] = defaultdict(list)
    for r in ds_rows:
        by_name[r.nm].append(r.id)

    def resolve_table(tn: str | None) -> int | None:
        t = _clean(tn)
        if not t:
            return None
        if t in by_qn:                                   # 1) qualified_name 정확 매칭
            return by_qn[t]
        suffix = {r.id for r in ds_rows if r.qn.endswith("." + t)}  # 2) qualified_name 접미 매칭
        if len(suffix) == 1:
            return next(iter(suffix))
        last = t.split(".")[-1]                           # 3) 이름(마지막 세그먼트) 유일 매칭
        if len(by_name.get(last, [])) == 1:
            return by_name[last][0]
        return None

    field_cache: dict[int, dict[str, str]] = {}

    async def resolve_field(dsid: int, col: str | None) -> str | None:
        c = _clean(col)
        if not c:
            return None
        if dsid not in field_cache:
            frows = (await session.execute(text(
                "SELECT field_path FROM catalog_dataset_schemas WHERE dataset_id = :d"
            ), {"d": dsid})).fetchall()
            field_cache[dsid] = {fr.field_path.lower(): fr.field_path for fr in frows}
        return field_cache[dsid].get(c)

    resolved = 0
    skipped = 0
    unresolved: set[str] = set()                          # 미해석(카탈로그에 없는) 테이블명
    for p in pairs:
        at, bt = p.get("a_table"), p.get("b_table")
        da = resolve_table(at)
        db = resolve_table(bt)
        if da is None or db is None:
            skipped += 1
            if da is None and at:
                unresolved.add(_clean(at))
            if db is None and bt:
                unresolved.add(_clean(bt))
            continue
        fa = await resolve_field(da, p.get("a_col"))
        fb = await resolve_field(db, p.get("b_col"))
        if fa is None or fb is None:
            skipped += 1
            continue
        # 무방향 정규화 — (dataset_id, field) 가 작은 쪽이 항상 a.
        if (da, fa) > (db, fb):
            da, fa, db, fb = db, fb, da, fa
        if da == db and fa == fb:                         # self 관계는 무의미
            skipped += 1
            continue
        # 명시(JOIN ON) vs 암묵(WHERE 등치) 카운트 구분.
        exp = 1 if p.get("kind", "explicit") != "implicit" else 0
        imp = 1 - exp
        await session.execute(text("""
            INSERT INTO catalog_column_relationship
              (dataset_a_id, field_a, dataset_b_id, field_b, relation_type,
               join_count, explicit_count, implicit_count, first_seen_at, last_seen_at)
            VALUES (:da, :fa, :db, :fb, 'JOIN_KEY', 1, :exp, :imp, now(), now())
            ON CONFLICT (dataset_a_id, field_a, dataset_b_id, field_b, relation_type)
            DO UPDATE SET join_count = catalog_column_relationship.join_count + 1,
                          explicit_count = catalog_column_relationship.explicit_count + :exp,
                          implicit_count = catalog_column_relationship.implicit_count + :imp,
                          last_seen_at = now()
        """), {"da": da, "fa": fa, "db": db, "fb": fb, "exp": exp, "imp": imp})
        resolved += 1

    await session.commit()
    logger.info("컬럼 관계 적재 완료: 해석=%d 건너뜀=%d 미해석=%d (datasource=%s)",
                resolved, skipped, len(unresolved), datasource_ref)
    return {"resolved": resolved, "skipped": skipped, "unresolved_tables": sorted(unresolved)}


async def reset_relationships(session: AsyncSession, datasource_ref=None) -> int:
    """관계 전체(또는 한 datasource 범위) 삭제 — 전체 재계산(rebuild) 전에 호출한다."""
    if datasource_ref:
        ds_id = await _resolve_datasource(session, datasource_ref)
        if ds_id is None:
            return 0
        # 양쪽 데이터셋이 모두 이 datasource 에 속한 관계만 삭제(같은 datasource 범위).
        result = await session.execute(text("""
            DELETE FROM catalog_column_relationship r
            WHERE EXISTS (SELECT 1 FROM catalog_datasets d
                          WHERE d.id = r.dataset_a_id AND d.datasource_id = :ds)
              AND EXISTS (SELECT 1 FROM catalog_datasets d
                          WHERE d.id = r.dataset_b_id AND d.datasource_id = :ds)
        """), {"ds": ds_id})
    else:
        result = await session.execute(text("DELETE FROM catalog_column_relationship"))
    await session.commit()
    return result.rowcount or 0


async def get_relationships(session: AsyncSession, dataset_id: int) -> dict:
    """데이터셋 상세용 — 이 데이터셋의 컬럼 관계 + 함께 쓰인 데이터셋 집계."""
    rows = (await session.execute(text("""
        SELECT r.dataset_a_id, r.field_a, r.dataset_b_id, r.field_b, r.relation_type,
               r.join_count, r.explicit_count, r.implicit_count, r.last_seen_at,
               da.name AS a_name, db.name AS b_name, da.urn AS a_urn, db.urn AS b_urn,
               dsa.name AS a_dsname, dsb.name AS b_dsname,
               dsa.type AS a_dstype, dsb.type AS b_dstype
        FROM catalog_column_relationship r
        JOIN catalog_datasets da ON r.dataset_a_id = da.id
        JOIN catalog_datasets db ON r.dataset_b_id = db.id
        JOIN catalog_datasources dsa ON da.datasource_id = dsa.id
        JOIN catalog_datasources dsb ON db.datasource_id = dsb.id
        WHERE r.dataset_a_id = :d OR r.dataset_b_id = :d
        ORDER BY r.join_count DESC, r.last_seen_at DESC
    """), {"d": dataset_id})).fetchall()

    column_rels = []
    table_agg: dict[int, dict] = {}
    for r in rows:
        if r.dataset_a_id == dataset_id:
            my_field, other_id, other_name, other_field = r.field_a, r.dataset_b_id, r.b_name, r.field_b
            other_dsname, other_dstype, other_urn = r.b_dsname, r.b_dstype, r.b_urn
        else:
            my_field, other_id, other_name, other_field = r.field_b, r.dataset_a_id, r.a_name, r.field_a
            other_dsname, other_dstype, other_urn = r.a_dsname, r.a_dstype, r.a_urn
        column_rels.append({
            "field": my_field,
            "relatedDatasetId": other_id,
            "relatedDatasetName": other_name,
            "relatedDatasourceName": other_dsname,
            "relatedDatasourceType": other_dstype,
            "relatedUrn": other_urn,
            "relatedField": other_field,
            "relationType": r.relation_type,
            "joinCount": r.join_count,
            "explicitCount": r.explicit_count,
            "implicitCount": r.implicit_count,
            "lastSeen": r.last_seen_at.isoformat() if r.last_seen_at else None,
        })
        agg = table_agg.setdefault(other_id, {
            "datasetId": other_id, "name": other_name,
            "datasourceName": other_dsname, "datasourceType": other_dstype,
            "urn": other_urn, "joinCount": 0,
        })
        agg["joinCount"] += r.join_count

    table_rels = sorted(table_agg.values(), key=lambda x: -x["joinCount"])
    return {"tableRelationships": table_rels, "columnRelationships": column_rels}


async def get_relationship_graph(
    session: AsyncSession, dataset_id: int, depth: int = 2, max_nodes: int = 60,
) -> dict:
    """관계 그래프 — focus 데이터셋에서 catalog_column_relationship 을 무방향 BFS 로 depth 홉까지.

    nodes: 데이터셋(메타 + 최소 depth + isFocus). edges: 데이터셋쌍 단위 집계
    (join_count 합 + 연결 컬럼 목록). 리니지 그래프처럼 React Flow 로 그릴 수 있는 형태.
    """
    nodes: dict[int, dict] = {}
    edges: dict[tuple, dict] = {}
    seen_rels: set = set()

    def ensure_node(did, name, dsname, dstype, urn, d):
        n = nodes.get(did)
        if n is None:
            nodes[did] = {
                "id": did, "name": name, "datasourceName": dsname,
                "datasourceType": dstype, "urn": urn, "depth": d,
                "isFocus": did == dataset_id,
            }
        elif d < n["depth"]:
            n["depth"] = d

    focus = (await session.execute(text("""
        SELECT d.name, ds.name AS dsname, ds.type AS dstype, d.urn
        FROM catalog_datasets d JOIN catalog_datasources ds ON d.datasource_id = ds.id
        WHERE d.id = :id
    """), {"id": dataset_id})).first()
    if focus is None:
        return {"nodes": [], "edges": []}
    ensure_node(dataset_id, focus[0], focus[1], focus[2], focus[3], 0)

    visited: set[int] = set()
    frontier = [dataset_id]
    for level in range(depth):
        if not frontier:
            break
        rows = (await session.execute(text("""
            SELECT r.dataset_a_id, r.field_a, r.dataset_b_id, r.field_b,
                   r.join_count, r.explicit_count, r.implicit_count,
                   da.name AS a_name, db.name AS b_name, da.urn AS a_urn, db.urn AS b_urn,
                   dsa.name AS a_dsname, dsa.type AS a_dstype,
                   dsb.name AS b_dsname, dsb.type AS b_dstype
            FROM catalog_column_relationship r
            JOIN catalog_datasets da ON r.dataset_a_id = da.id
            JOIN catalog_datasets db ON r.dataset_b_id = db.id
            JOIN catalog_datasources dsa ON da.datasource_id = dsa.id
            JOIN catalog_datasources dsb ON db.datasource_id = dsb.id
            WHERE r.dataset_a_id = ANY(:ids) OR r.dataset_b_id = ANY(:ids)
            ORDER BY r.join_count DESC
        """), {"ids": frontier})).fetchall()
        visited.update(frontier)
        next_set: set[int] = set()
        for r in rows:
            rel_key = (r.dataset_a_id, r.field_a, r.dataset_b_id, r.field_b)
            if rel_key in seen_rels:
                continue
            # 노드 수 상한 — 이미 알려진 노드만 잇는 엣지는 허용, 새 노드는 상한까지만.
            new_endpoints = [
                nid for nid in (r.dataset_a_id, r.dataset_b_id) if nid not in nodes
            ]
            if new_endpoints and len(nodes) + len(new_endpoints) > max_nodes:
                continue
            seen_rels.add(rel_key)
            ensure_node(r.dataset_a_id, r.a_name, r.a_dsname, r.a_dstype, r.a_urn, level + 1)
            ensure_node(r.dataset_b_id, r.b_name, r.b_dsname, r.b_dstype, r.b_urn, level + 1)
            key = (min(r.dataset_a_id, r.dataset_b_id), max(r.dataset_a_id, r.dataset_b_id))
            e = edges.get(key)
            if e is None:
                e = {
                    "source": key[0], "target": key[1], "joinCount": 0,
                    "explicitCount": 0, "implicitCount": 0, "columns": [],
                }
                edges[key] = e
            e["joinCount"] += r.join_count
            e["explicitCount"] += r.explicit_count
            e["implicitCount"] += r.implicit_count
            e["columns"].append({
                "sourceDatasetId": r.dataset_a_id, "sourceField": r.field_a,
                "targetDatasetId": r.dataset_b_id, "targetField": r.field_b,
                "joinCount": r.join_count,
                "explicitCount": r.explicit_count, "implicitCount": r.implicit_count,
            })
            for nid in (r.dataset_a_id, r.dataset_b_id):
                if nid not in visited:
                    next_set.add(nid)
        frontier = list(next_set - visited)

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
