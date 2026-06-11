# SPDX-License-Identifier: Apache-2.0
"""시맨틱·하이브리드 검색 서비스.

pgvector 기반 코사인 유사도 시맨틱 검색을 제공하며, 키워드와 시맨틱
결과를 결합하는 선택적 하이브리드 스코어링을 함께 지원한다.
"""

import logging

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetTag, Datasource, Owner
from app.catalog.schemas import DatasetSummary
from app.embedding.registry import get_provider

logger = logging.getLogger(__name__)


async def semantic_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
) -> list[tuple[int, float]]:
    """pgvector 코사인 유사도로 시맨틱 검색을 수행한다.

    연관도 순으로 정렬된 (dataset_id, similarity_score) 목록을 반환한다.
    임베딩 제공자가 초기화되지 않았으면 ``ValueError`` 를 발생시킨다.
    """
    provider = await get_provider()
    if provider is None:
        raise ValueError("Embedding provider not initialized. Enable it in Settings.")

    # 쿼리 임베딩
    query_vectors = await provider.embed([query])
    query_vec = query_vectors[0]

    # pgvector 코사인 거리 연산자: <=>
    # similarity = 1 - cosine_distance
    # 주의: ::vector 대신 CAST() 사용 — SQLAlchemy text() 의 :: 파라미터 충돌 방지
    result = await session.execute(text("""
        SELECT e.dataset_id,
               1 - (e.embedding <=> CAST(:query_vec AS vector)) AS similarity
        FROM catalog_dataset_embeddings e
        JOIN catalog_datasets d ON d.id = e.dataset_id
        WHERE d.status != 'removed'
          AND 1 - (e.embedding <=> CAST(:query_vec AS vector)) >= :threshold
        ORDER BY e.embedding <=> CAST(:query_vec AS vector)
        LIMIT :lim
    """), {
        "query_vec": str(query_vec),
        "threshold": threshold,
        "lim": limit,
    })

    results = [(row[0], float(row[1])) for row in result.fetchall()]
    logger.info("시맨틱 검색: q='%s', results=%d, threshold=%.2f", query, len(results), threshold)
    return results


async def keyword_search_ids(
    session: AsyncSession,
    query: str,
    limit: int = 50,
) -> set[int]:
    """키워드 ILIKE 검색에 매칭되는 데이터셋 ID 를 반환한다."""
    pattern = f"%{query}%"
    result = await session.execute(
        select(Dataset.id)
        .where(
            Dataset.status != "removed",
            or_(
                Dataset.name.ilike(pattern),
                Dataset.description.ilike(pattern),
                Dataset.urn.ilike(pattern),
                Dataset.qualified_name.ilike(pattern),
            ),
        )
        .limit(limit)
    )
    return {row[0] for row in result.fetchall()}


async def hybrid_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    keyword_weight: float = 0.3,
    semantic_weight: float = 0.7,
) -> list[tuple[int, float, str]]:
    """키워드 검색과 시맨틱 검색을 가중 스코어링으로 결합한다.

    연관도 순으로 정렬된 (dataset_id, combined_score, match_type) 목록을 반환한다.
    임베딩 제공자가 없으면 키워드 전용 검색으로 폴백한다.
    """
    provider = await get_provider()

    # 시맨틱 결과 (제공자 없으면 건너뜀)
    semantic_map: dict[int, float] = {}
    if provider is not None:
        try:
            semantic_results = await semantic_search(session, query, limit=limit * 2, threshold=threshold)
            semantic_map = {ds_id: score for ds_id, score in semantic_results}
        except Exception as e:
            logger.warning("시맨틱 검색 불가, 키워드로 폴백: %s", e)
    else:
        logger.info("임베딩 제공자 없음, 키워드 전용 검색 사용")

    # 키워드 결과
    keyword_ids = await keyword_search_ids(session, query, limit=limit * 2)

    # match_type 추적과 함께 점수 결합
    all_ids = set(semantic_map.keys()) | keyword_ids
    scored: list[tuple[int, float, str]] = []
    for ds_id in all_ids:
        sem_score = semantic_map.get(ds_id, 0.0)
        kw_score = 1.0 if ds_id in keyword_ids else 0.0

        if semantic_map:
            combined = semantic_weight * sem_score + keyword_weight * kw_score
        else:
            # 키워드 전용 폴백 (시맨틱 불가)
            combined = kw_score

        if sem_score > 0 and kw_score > 0:
            match_type = "hybrid"
        elif sem_score > 0:
            match_type = "semantic"
        else:
            match_type = "keyword"

        scored.append((ds_id, combined, match_type))

    scored.sort(key=lambda x: x[1], reverse=True)
    logger.info(
        "하이브리드 검색: q='%s', semantic=%d, keyword=%d, combined=%d",
        query, len(semantic_map), len(keyword_ids), len(scored),
    )
    return scored[:limit]


async def _build_dataset_summary(session: AsyncSession, dataset_id: int) -> DatasetSummary | None:
    """단일 데이터셋 ID 에 대한 DatasetSummary 를 구성한다."""
    result = await session.execute(
        select(
            Dataset.id, Dataset.urn, Dataset.name,
            Datasource.name.label("datasource_name"), Datasource.type.label("datasource_type"),
            Dataset.description, Dataset.origin, Dataset.status,
            Dataset.is_synced, Dataset.created_at, Dataset.updated_at,
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id == dataset_id)
    )
    row = result.first()
    if not row:
        return None

    # 집계
    tag_count = (await session.execute(
        select(func.count()).where(DatasetTag.dataset_id == dataset_id)
    )).scalar() or 0
    owner_count = (await session.execute(
        select(func.count()).where(Owner.dataset_id == dataset_id)
    )).scalar() or 0

    return DatasetSummary(
        id=row.id, urn=row.urn, name=row.name,
        datasource_name=row.datasource_name, datasource_type=row.datasource_type,
        description=row.description, origin=row.origin, status=row.status,
        is_synced=row.is_synced, tag_count=tag_count, owner_count=owner_count,
        schema_field_count=0,
        created_at=row.created_at, updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# 통합(엔티티) 검색 — 용어집 / AI Agent / API
# ---------------------------------------------------------------------------

async def semantic_search_entities(
    session: AsyncSession,
    query: str,
    entity_types: list[str],
    limit: int = 20,
    threshold: float = 0.3,
) -> list[tuple[str, int, float]]:
    """다형 임베딩 테이블에서 시맨틱 검색.

    연관도 순으로 정렬된 (entity_type, entity_id, similarity) 목록을 반환한다.
    """
    provider = await get_provider()
    if provider is None:
        raise ValueError("Embedding provider not initialized. Enable it in Settings.")

    query_vectors = await provider.embed([query])
    query_vec = query_vectors[0]

    result = await session.execute(text("""
        SELECT e.entity_type, e.entity_id,
               1 - (e.embedding <=> CAST(:query_vec AS vector)) AS similarity
        FROM catalog_entity_embeddings e
        WHERE e.entity_type = ANY(:types)
          AND 1 - (e.embedding <=> CAST(:query_vec AS vector)) >= :threshold
        ORDER BY e.embedding <=> CAST(:query_vec AS vector)
        LIMIT :lim
    """), {
        "query_vec": str(query_vec),
        "types": entity_types,
        "threshold": threshold,
        "lim": limit,
    })

    return [(row[0], row[1], float(row[2])) for row in result.fetchall()]


async def _keyword_search_entity_ids(
    session: AsyncSession, query: str, entity_type: str, limit: int = 50,
) -> set[int]:
    """엔티티 타입별 키워드 ILIKE 검색 → ID 집합."""
    pattern = f"%{query}%"

    if entity_type == "glossary_term":
        from app.catalog.models import GlossaryTerm
        stmt = (
            select(GlossaryTerm.id)
            .where(or_(
                GlossaryTerm.name.ilike(pattern),
                GlossaryTerm.description.ilike(pattern),
            ))
            .limit(limit)
        )
    elif entity_type == "ai_agent":
        from app.agents.models import AIAgent
        stmt = (
            select(AIAgent.id)
            .where(or_(
                AIAgent.name.ilike(pattern),
                AIAgent.display_name.ilike(pattern),
                AIAgent.description.ilike(pattern),
            ))
            .limit(limit)
        )
    elif entity_type == "api":
        from app.apis.models import CatalogApi
        stmt = (
            select(CatalogApi.id)
            .where(or_(
                CatalogApi.name.ilike(pattern),
                CatalogApi.display_name.ilike(pattern),
                CatalogApi.description.ilike(pattern),
            ))
            .limit(limit)
        )
    else:
        return set()

    result = await session.execute(stmt)
    return {row[0] for row in result.fetchall()}


async def hybrid_search_entities(
    session: AsyncSession,
    query: str,
    entity_types: list[str],
    limit: int = 10,
    threshold: float = 0.3,
    keyword_weight: float = 0.3,
    semantic_weight: float = 0.7,
) -> dict[str, list[tuple[int, float, str]]]:
    """엔티티 타입별 하이브리드 검색 (데이터셋의 hybrid_search 와 동일한 결합 방식).

    반환: {entity_type: [(entity_id, combined_score, match_type), ...]}
    """
    provider = await get_provider()

    # 시맨틱 결과 (제공자 없으면 건너뜀)
    semantic_map: dict[tuple[str, int], float] = {}
    if provider is not None:
        try:
            semantic_results = await semantic_search_entities(
                session, query, entity_types, limit=limit * 2 * len(entity_types),
                threshold=threshold,
            )
            semantic_map = {(t, eid): score for t, eid, score in semantic_results}
        except Exception as e:
            logger.warning("엔티티 시맨틱 검색 불가, 키워드로 폴백: %s", e)

    grouped: dict[str, list[tuple[int, float, str]]] = {}
    for entity_type in entity_types:
        keyword_ids = await _keyword_search_entity_ids(session, query, entity_type, limit=limit * 2)
        sem_ids = {eid for (t, eid) in semantic_map if t == entity_type}

        scored: list[tuple[int, float, str]] = []
        for eid in sem_ids | keyword_ids:
            sem_score = semantic_map.get((entity_type, eid), 0.0)
            kw_score = 1.0 if eid in keyword_ids else 0.0
            combined = (
                semantic_weight * sem_score + keyword_weight * kw_score
                if semantic_map else kw_score
            )
            if sem_score > 0 and kw_score > 0:
                match_type = "hybrid"
            elif sem_score > 0:
                match_type = "semantic"
            else:
                match_type = "keyword"
            scored.append((eid, combined, match_type))

        scored.sort(key=lambda x: x[1], reverse=True)
        grouped[entity_type] = scored[:limit]

    logger.info("엔티티 하이브리드 검색: q='%s', %s", query,
                {t: len(v) for t, v in grouped.items()})
    return grouped


async def build_entity_result(
    session: AsyncSession, entity_type: str, entity_id: int,
) -> dict | None:
    """검색 결과 카드용 엔티티 요약 payload."""
    if entity_type == "glossary_term":
        from app.catalog.models import GlossaryTerm
        term = (await session.execute(
            select(GlossaryTerm).where(GlossaryTerm.id == entity_id)
        )).scalars().first()
        if not term:
            return None
        return {
            "id": term.id, "name": term.name, "display_name": None,
            "description": term.description,
            "extra": {"term_type": term.term_type},
        }
    if entity_type == "ai_agent":
        from app.agents.models import AIAgent
        agent = (await session.execute(
            select(AIAgent).where(AIAgent.id == entity_id)
        )).scalars().first()
        if not agent:
            return None
        return {
            "id": agent.id, "name": agent.name, "display_name": agent.display_name,
            "description": agent.description,
            "extra": {"status": agent.status, "category": agent.category,
                      "framework": agent.framework},
        }
    if entity_type == "api":
        from app.apis.models import CatalogApi
        api = (await session.execute(
            select(CatalogApi).where(CatalogApi.id == entity_id)
        )).scalars().first()
        if not api:
            return None
        return {
            "id": api.id, "name": api.name, "display_name": api.display_name,
            "description": api.description,
            "extra": {"status": api.status, "protocol": api.protocol,
                      "category": api.category},
        }
    return None
