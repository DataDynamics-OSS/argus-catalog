# SPDX-License-Identifier: Apache-2.0
"""쿼리 기반 lineage 의 dataset_id 해석.

확장(query-service 의 write-lineage 배치)은 ``argus_query_lineage`` 에 테이블 이름(source_table/
target_table)과 함께 쿼리를 실행한 엔진(``datasource_id`` — 데이터 소스 식별자 문자열)과 엔진 쪽
카탈로그 이름(``source_catalog``/``target_catalog``, Trino ``hive`` 등)을 적재하고 source_dataset_id/
target_dataset_id 는 NULL 로 둔다. 카탈로그 lineage API 는 이 dataset_id 가 채워져 있어야 엣지를
노출하므로, 여기서 이름→데이터셋을 해석해 채운다.

카탈로그 정하기(design/catalog-entity-design.md 6.5):
- 엔진이 등록된 데이터 소스면 그 엔진의 카탈로그 연결(``catalog_catalog_connections``)로 테이블이 속한
  카탈로그를 정하고, 그 카탈로그의 데이터셋에서만 찾는다.
  - 카탈로그 이름이 있으면 그 엔진 연결 중 ``remote_name`` 이 같은(대소문자 무시) 카탈로그.
  - 이름이 없으면(Impala·Hive 등) 엔진에 연결된 카탈로그가 하나뿐일 때 그 카탈로그, 여럿이면 엔진의
    SYNC 카탈로그가 하나일 때 그 카탈로그.
  - 정할 수 없으면(연결이 없거나 이름이 맞지 않음) 해석하지 않고 남긴다 — 연결을 고친 뒤 다시 해석된다.
- 엔진을 모르면(빈 값 — 구 수집기·레거시 경로, 또는 등록되지 않은 식별자) 종전처럼 모든 데이터셋에서 찾는다.

이름 매칭: 데이터셋 qualified_name 이 lineage 테이블명과 (1) 정확히 같거나 (2) ``.테이블명``
으로 끝나면 그 데이터셋으로 본다(대소문자 무시). 한 범위에서 여러 데이터셋이 맞으면 id 가 작은
데이터셋, 이미 채워진 쪽은 건드리지 않는다(first-wins).

수동 데이터셋 원천 매핑(TODO-CAT-001): 데이터 소스 X 의 경로 P 에 매핑된 수동 데이터셋은 X 의 SYNC 카탈로그에
있는 ``{X}.{P}`` 데이터셋처럼 해석 대상에 넣는다(``_mapped_rows``). 매핑된 테이블은 수집이 데이터셋을 만들지
않으므로, 엔진 쿼리의 그 테이블은 수동 데이터셋에 붙는다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 해석 범위 표식 — 카탈로그 id(int) 외의 두 경우
_GLOBAL = "global"          # 엔진을 모름 → 모든 데이터셋에서 찾는다(종전 동작)
_UNRESOLVED = "unresolved"  # 엔진은 아는데 카탈로그를 정할 수 없음 → 남긴다

_SIDES = (("source_table", "source_catalog", "source_dataset_id"),
          ("target_table", "target_catalog", "target_dataset_id"))

_ROW_COLS = ("id, datasource_id, source_catalog, source_table, target_catalog, target_table, "
             "source_dataset_id, target_dataset_id")


def _suffixes(qualified_name: str) -> set[str]:
    """qualified_name 자체 + 각 '.' 뒤의 접미사(소문자). 예: a.b.c → {a.b.c, b.c, c}."""
    q = qualified_name.lower()
    return {q, *(q[i + 1:] for i, ch in enumerate(q) if ch == "." and q[i + 1:])}


@dataclass
class EngineCatalogs:
    """엔진(데이터 소스 식별자)별 카탈로그 연결 — 테이블이 속한 카탈로그를 정한다."""

    known: set[str] = field(default_factory=set)
    by_remote: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))
    connected: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    sync: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))

    def scope(self, engine_key: str | None, catalog_name: str | None):
        """(엔진, 엔진 쪽 카탈로그 이름) → 카탈로그 id | ``_GLOBAL`` | ``_UNRESOLVED``."""
        key = (engine_key or "").strip()
        if not key or key not in self.known:
            return _GLOBAL
        name = (catalog_name or "").strip().lower()
        if name:
            ids = self.by_remote.get((key, name), set())
            return next(iter(ids)) if len(ids) == 1 else _UNRESOLVED
        ids = self.connected.get(key, set())
        if len(ids) == 1:
            return next(iter(ids))
        ids = self.sync.get(key, set())
        return next(iter(ids)) if len(ids) == 1 else _UNRESOLVED


async def load_engine_catalogs(session: AsyncSession) -> EngineCatalogs:
    """등록된 데이터 소스와 카탈로그 연결을 읽는다(작은 표 — 해석 1회에 한 번)."""
    ec = EngineCatalogs()
    for (key,) in (await session.execute(text("SELECT datasource_id FROM catalog_datasources"))).fetchall():
        if key:
            ec.known.add(key)
    rows = (await session.execute(text(
        "SELECT d.datasource_id, c.catalog_id, c.role, c.remote_name "
        "FROM catalog_catalog_connections c JOIN catalog_datasources d ON d.id = c.datasource_id"
    ))).fetchall()
    for key, catalog_id, role, remote_name in rows:
        ec.connected[key].add(catalog_id)
        if role == "SYNC":
            ec.sync[key].add(catalog_id)
        if remote_name and remote_name.strip():
            ec.by_remote[(key, remote_name.strip().lower())].add(catalog_id)
    return ec


def _build_index(datasets: Iterable) -> dict:
    """(id, qualified_name, catalog_id) 들 → {범위: {접미사: dataset_id}}. id 오름차순 first-wins."""
    index: dict = {_GLOBAL: {}}
    for did, qn, catalog_id in sorted((d for d in datasets if d[1]), key=lambda d: d[0]):
        for suf in _suffixes(qn):
            index[_GLOBAL].setdefault(suf, did)
            if catalog_id is not None:
                index.setdefault(catalog_id, {}).setdefault(suf, did)
    return index


async def _apply(session: AsyncSession, rows, ec: EngineCatalogs, index: dict) -> tuple[int, int]:
    """lineage 행들의 빈 dataset_id 를 범위 안 데이터셋으로 채운다(커밋은 호출자).

    반환: (채운 칸 수, 카탈로그를 정할 수 없어 남긴 칸 수).
    """
    updates: dict[str, list[dict]] = {"source_dataset_id": [], "target_dataset_id": []}
    unresolved = 0
    for row in rows:
        m = row._mapping
        for name_col, cat_col, id_col in _SIDES:
            if m[id_col] is not None or not m[name_col]:
                continue
            scope = ec.scope(m["datasource_id"], m[cat_col])
            if scope == _UNRESOLVED:
                unresolved += 1
                continue
            did = index.get(scope, {}).get(m[name_col].lower())
            if did is not None:
                updates[id_col].append({"rid": m["id"], "did": did})
    total = 0
    for id_col, params in updates.items():
        if not params:
            continue
        # 컬럼명은 위 고정 튜플에서만 오므로 f-string 삽입이 안전하다. 값은 바인딩 파라미터.
        # executemany 의 rowcount 는 드라이버마다 달라(-1 등) 보낸 칸 수를 센다 — 방금 NULL 로 읽은 칸들이다.
        await session.execute(text(
            f"UPDATE argus_query_lineage SET {id_col} = :did WHERE id = :rid AND {id_col} IS NULL"
        ), params)
        total += len(params)
    return total, unresolved


async def resolve_lineage_for_dataset(
    session: AsyncSession, dataset_id: int, qualified_name: str | None, catalog_id: int | None = None,
    engines: EngineCatalogs | None = None,
) -> int:
    """새/변경된 데이터셋과 매칭되는 lineage 행의 dataset_id 를 채운다(증분).

    엔진을 아는 행은 그 행의 카탈로그가 이 데이터셋의 카탈로그(``catalog_id``)일 때만 채운다. 이미
    채워진 행은 건드리지 않는다(first-wins). 채운 행이 있을 때만 커밋하며 채운 칸 수를 반환한다.
    """
    if not qualified_name:
        return 0
    cands = sorted(_suffixes(qualified_name))
    # 매칭 후보를 IN 비교로 거른다 — LIKE 는 테이블명의 '_'·'%' 를 와일드카드로 오해석하고,
    # '||' 는 MariaDB 에서 OR 라서 쓰지 않는다.
    stmt = text(
        f"SELECT {_ROW_COLS} FROM argus_query_lineage "
        "WHERE (source_dataset_id IS NULL AND lower(source_table) IN :cands) "
        "   OR (target_dataset_id IS NULL AND lower(target_table) IN :cands)"
    ).bindparams(bindparam("cands", expanding=True))
    rows = (await session.execute(stmt, {"cands": cands})).fetchall()
    if not rows:
        return 0
    ec = engines or await load_engine_catalogs(session)
    total, _ = await _apply(session, rows, ec, _build_index([(dataset_id, qualified_name, catalog_id)]))
    if total:
        await session.commit()
        logger.debug("리니지 dataset_id 채움: dataset_id=%d rows=%d", dataset_id, total)
    return total


async def resolve_query_lineage_for_engine(session: AsyncSession, engine_key: str | None,
                                           reset: bool = False) -> int:
    """한 엔진(데이터 소스 식별자)의 lineage 행을 다시 해석한다 — 카탈로그 연결을 바꾼 뒤.

    ``reset=True`` 면 그 엔진 행의 dataset_id 를 먼저 비운다 — 연결 해제·추가로 테이블이 속한 카탈로그가
    바뀌었을 수 있어서다(예: 연결된 카탈로그가 하나 → 둘이 되면 이름 없는 테이블은 더 이상 정할 수 없다).
    채운 칸 수를 반환하고 커밋한다(비우기만 하고 채운 것이 없어도).
    """
    if not engine_key:
        return 0
    cleared = 0
    if reset:
        res = await session.execute(text(
            "UPDATE argus_query_lineage SET source_dataset_id = NULL, target_dataset_id = NULL "
            "WHERE datasource_id = :key AND (source_dataset_id IS NOT NULL OR target_dataset_id IS NOT NULL)"
        ), {"key": engine_key})
        cleared = res.rowcount or 0
    rows = (await session.execute(text(
        f"SELECT {_ROW_COLS} FROM argus_query_lineage "
        "WHERE datasource_id = :key AND (source_dataset_id IS NULL OR target_dataset_id IS NULL)"
    ), {"key": engine_key})).fetchall()
    total = 0
    if rows:
        ec = await load_engine_catalogs(session)
        scopes = {ec.scope(r.datasource_id, r.source_catalog) for r in rows}
        scopes |= {ec.scope(r.datasource_id, r.target_catalog) for r in rows}
        datasets = await _datasets_in(session, sorted(s for s in scopes if isinstance(s, int)))
        if _GLOBAL in scopes:  # 엔진이 등록되지 않은 경우(호출자가 모르는 식별자를 넘김) — 전체에서 찾는다
            datasets = await _all_datasets(session)
        total, _ = await _apply(session, rows, ec, _build_index(datasets))
    if total or cleared:
        await session.commit()
    logger.info("엔진 리니지 재해석: engine=%s 비움=%d 행=%d 채움=%d", engine_key, cleared, len(rows), total)
    return total


async def _datasets_in(session: AsyncSession, catalog_ids: list[int]) -> list:
    """카탈로그들의 (id, qualified_name, catalog_id) — 그 카탈로그로 매핑된 수동 데이터셋 포함."""
    if not catalog_ids:
        return []
    stmt = text(
        "SELECT id, qualified_name, catalog_id FROM catalog_datasets "
        "WHERE qualified_name IS NOT NULL AND catalog_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = list((await session.execute(stmt, {"ids": catalog_ids})).fetchall())
    return rows + await _mapped_rows(session, catalog_ids)


async def _all_datasets(session: AsyncSession) -> list:
    """모든 데이터셋의 (id, qualified_name, catalog_id) — 매핑된 수동 데이터셋의 원천 이름 포함."""
    rows = list((await session.execute(text(
        "SELECT id, qualified_name, catalog_id FROM catalog_datasets WHERE qualified_name IS NOT NULL"
    ))).fetchall())
    return rows + await _mapped_rows(session, None)


async def _mapped_rows(session: AsyncSession, catalog_ids: list[int] | None, dataset_id: int | None = None) -> list:
    """원천 매핑된 수동 데이터셋 → (dataset_id, ``{데이터 소스 식별자}.{경로}``, 그 데이터 소스의 SYNC 카탈로그).

    매핑 테이블이 아직 없는 설치(DDL 미적용)에서는 빈 목록.
    """
    sql = ("SELECT m.dataset_id, d.datasource_id, m.remote_path, c.catalog_id "
           "FROM catalog_dataset_source_maps m "
           "JOIN catalog_datasources d ON d.id = m.datasource_id "
           "LEFT JOIN catalog_catalog_connections c ON c.datasource_id = m.datasource_id AND c.role = 'SYNC'")
    conds, params = [], {}
    if catalog_ids is not None:
        conds.append("c.catalog_id IN :ids")
        params["ids"] = catalog_ids
    if dataset_id is not None:
        conds.append("m.dataset_id = :did")
        params["did"] = dataset_id
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    stmt = text(sql)
    if catalog_ids is not None:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    try:
        # savepoint — 실패해도 호출자의 미커밋 변경은 그대로 둔다
        async with session.begin_nested():
            rows = (await session.execute(stmt, params)).fetchall()
    except Exception as e:  # noqa: BLE001 — 매핑 테이블이 없는 이전 스키마
        logger.debug("원천 매핑 조회 생략: %s", e)
        return []
    return [(did, f"{key}.{path}", cid) for did, key, path, cid in rows]


async def resolve_lineage_for_mapping(session: AsyncSession, dataset_id: int) -> int:
    """수동 데이터셋의 원천 매핑이 바뀐 뒤 — 이 데이터셋에 붙은 칸을 비우고 매핑 이름으로 다시 해석한다.

    비운 칸은 데이터셋 자신의 이름으로도 다시 해석된다(매핑을 해제한 경우). 채운 칸 수를 반환한다.
    """
    cleared = 0
    for col in ("source_dataset_id", "target_dataset_id"):
        res = await session.execute(text(
            f"UPDATE argus_query_lineage SET {col} = NULL WHERE {col} = :did"), {"did": dataset_id})
        cleared += res.rowcount or 0
    if cleared:
        await session.commit()
    total = 0
    for did, qn, cid in await _mapped_rows(session, None, dataset_id):
        total += await resolve_lineage_for_dataset(session, did, qn, cid)
    own = (await session.execute(text(
        "SELECT qualified_name, catalog_id FROM catalog_datasets WHERE id = :did"), {"did": dataset_id})).first()
    if own is not None:
        total += await resolve_lineage_for_dataset(session, dataset_id, own[0], own[1])
    logger.info("매핑 리니지 재해석: dataset=%d 비움=%d 채움=%d", dataset_id, cleared, total)
    return total


async def resolve_query_lineage_for_catalog(session: AsyncSession, catalog_id: int | None) -> int:
    """한 카탈로그의 데이터셋으로 미해석 lineage 행을 채운다 — 수집(sync)이 데이터셋을 만든 뒤.

    엔진을 아는 행은 그 행의 카탈로그가 이 카탈로그일 때만, 엔진을 모르는 행은 이름만으로 채운다
    (데이터셋 생성 때의 증분 해석과 같은 규칙). 채운 칸 수를 반환하고, 채운 것이 있으면 커밋한다.
    """
    if catalog_id is None:
        return 0
    rows = (await session.execute(text(
        f"SELECT {_ROW_COLS} FROM argus_query_lineage "
        "WHERE source_dataset_id IS NULL OR target_dataset_id IS NULL"
    ))).fetchall()
    if not rows:
        return 0
    datasets = await _datasets_in(session, [catalog_id])
    if not datasets:
        return 0
    total, _ = await _apply(session, rows, await load_engine_catalogs(session), _build_index(datasets))
    if total:
        await session.commit()
    logger.info("카탈로그 리니지 해석: catalog_id=%d 미해석 행=%d 채움=%d", catalog_id, len(rows), total)
    return total


async def resolve_all_query_lineage(session: AsyncSession) -> dict:
    """전체 백필 — NULL dataset_id 인 모든 lineage 행을 해석한다.

    반환: ``{"resolved": 채운 칸 수, "unresolved_catalog": 엔진은 아는데 카탈로그를 정하지 못한 칸 수,
    "remaining_unresolved": source/target 중 하나라도 NULL 인 행 수}``.
    """
    rows = (await session.execute(text(
        f"SELECT {_ROW_COLS} FROM argus_query_lineage "
        "WHERE source_dataset_id IS NULL OR target_dataset_id IS NULL"
    ))).fetchall()
    datasets = await _all_datasets(session)
    logger.info("쿼리 리니지 해석 시작: rows=%d datasets=%d", len(rows), len(datasets))
    ec = await load_engine_catalogs(session)
    resolved, no_catalog = await _apply(session, rows, ec, _build_index(datasets))
    if resolved:
        await session.commit()
    remaining = (await session.execute(text(
        "SELECT count(*) FROM argus_query_lineage "
        "WHERE source_dataset_id IS NULL OR target_dataset_id IS NULL"
    ))).scalar() or 0
    logger.info("쿼리 리니지 해석 완료: 채움=%d 카탈로그 미정=%d 미해석 잔여=%d",
                resolved, no_catalog, remaining)
    return {"resolved": resolved, "unresolved_catalog": no_catalog,
            "remaining_unresolved": int(remaining)}
