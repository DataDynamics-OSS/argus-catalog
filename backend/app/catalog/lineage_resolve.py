# SPDX-License-Identifier: Apache-2.0
"""쿼리 기반 lineage 의 dataset_id 해석.

확장(metadata-sync)은 ``argus_query_lineage`` 에 테이블 이름(source_table/target_table)만
적재하고 source_dataset_id/target_dataset_id 는 NULL 로 둔다. 카탈로그 lineage API 는 이
dataset_id 가 채워져 있어야 엣지를 노출하므로, 여기서 이름→데이터셋을 해석해 채운다.

매칭: 데이터셋 qualified_name 이 lineage 테이블명과 (1) 정확히 같거나 (2) ``.테이블명``
으로 끝나면 그 데이터셋으로 본다(대소문자 무시). 같은 datasource 범위 정보가 lineage 행에
없어 전역 매칭이며, 먼저 매칭된 데이터셋이 채운다(중복 시 first-wins).
"""

from __future__ import annotations

import logging

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def resolve_lineage_for_dataset(
    session: AsyncSession, dataset_id: int, qualified_name: str | None,
) -> int:
    """새/변경된 데이터셋의 qualified_name 과 매칭되는 lineage 행의 dataset_id 를 채운다(증분).

    이미 채워진 행은 건드리지 않는다(first-wins). 채운 행이 있을 때만 커밋하며,
    채운 행 수(source·target 합계)를 반환한다.
    """
    if not qualified_name:
        return 0
    q = qualified_name.lower()
    # 매칭 후보: qualified_name 자체 + 각 '.' 뒤의 접미사(예: a.b.c → a.b.c, b.c, c).
    # "q 가 '.테이블명' 으로 끝남" 과 같은 의미를 LIKE 없이 IN 비교로 표현한다 —
    # LIKE 는 테이블명의 '_'·'%' 를 와일드카드로 오해석하고, '||' 는 MariaDB 에서 OR 라서 쓰지 않는다.
    candidates = sorted({q, *(q[i + 1:] for i, ch in enumerate(q) if ch == "." and q[i + 1:])})
    total = 0
    for name_col, id_col in (("source_table", "source_dataset_id"),
                             ("target_table", "target_dataset_id")):
        # 컬럼명은 위 고정 튜플에서만 오므로 f-string 삽입이 안전하다. 매칭 값은 바인딩 파라미터.
        stmt = text(f"""
            UPDATE argus_query_lineage
            SET {id_col} = :did
            WHERE {id_col} IS NULL
              AND lower({name_col}) IN :cands
        """).bindparams(bindparam("cands", expanding=True))
        res = await session.execute(stmt, {"did": dataset_id, "cands": candidates})
        total += res.rowcount or 0
    if total:
        await session.commit()
        logger.debug("리니지 dataset_id 채움: dataset_id=%d rows=%d", dataset_id, total)
    return total


async def resolve_all_query_lineage(session: AsyncSession) -> dict:
    """전체 백필 — 모든 데이터셋 기준으로 NULL dataset_id 인 lineage 행을 해석한다.

    반환: ``{"resolved": 채운 행 수, "remaining_unresolved": source/target 중 하나라도 NULL 인 행 수}``.
    """
    rows = (await session.execute(text(
        "SELECT id, qualified_name FROM catalog_datasets WHERE qualified_name IS NOT NULL"
    ))).fetchall()
    logger.info("쿼리 리니지 해석 시작: datasets=%d", len(rows))
    resolved = 0
    for r in rows:
        resolved += await resolve_lineage_for_dataset(session, r[0], r[1])
    remaining = (await session.execute(text(
        "SELECT count(*) FROM argus_query_lineage "
        "WHERE source_dataset_id IS NULL OR target_dataset_id IS NULL"
    ))).scalar() or 0
    logger.info("쿼리 리니지 해석 완료: 채움=%d 미해석 잔여=%d", resolved, remaining)
    return {"resolved": resolved, "remaining_unresolved": int(remaining)}
