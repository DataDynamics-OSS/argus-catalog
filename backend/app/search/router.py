"""시맨틱·하이브리드 검색 API 엔드포인트.

시맨틱 검색(pgvector 코사인 유사도), 하이브리드 검색(키워드 + 시맨틱),
임베딩 관리 엔드포인트를 제공한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.database import get_session
from app.embedding import service as embedding_service
from app.embedding.registry import get_provider
from app.search import service
from app.search.schemas import (
    EntitySearchResult,
    SemanticSearchResponse,
    SemanticSearchResult,
    UnifiedSearchResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog/search", tags=["search"])


# ---------------------------------------------------------------------------
# 검색 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.3, ge=0.0, le=1.0, description="Min cosine similarity"),
    session: AsyncSession = Depends(get_session),
):
    """벡터 유사도를 이용해 데이터셋 전체를 시맨틱 검색한다."""
    logger.info("GET /catalog/search/semantic: q='%s', limit=%d", q, limit)
    provider = await get_provider()
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="임베딩 제공자가 구성되지 않았습니다. 설정 > 임베딩에서 활성화하세요.",
        )

    try:
        results = await service.semantic_search(session, q, limit=limit, threshold=threshold)
    except Exception as e:
        logger.error("시맨틱 검색 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    items = []
    for ds_id, score in results:
        summary = await service._build_dataset_summary(session, ds_id)
        if summary:
            items.append(SemanticSearchResult(
                dataset=summary, score=round(score, 4), match_type="semantic",
            ))

    return SemanticSearchResponse(
        items=items, total=len(items), query=q,
        provider=provider.provider_name(), model=provider.model_name(),
    )


@router.get("/hybrid", response_model=SemanticSearchResponse)
async def hybrid_search(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.3, ge=0.0, le=1.0),
    keyword_weight: float = Query(0.3, ge=0.0, le=1.0),
    semantic_weight: float = Query(0.7, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
):
    """키워드 매칭과 시맨틱 유사도를 결합한 하이브리드 검색.

    임베딩 제공자가 구성되지 않은 경우 키워드 전용 검색으로 폴백한다.
    """
    logger.info("GET /catalog/search/hybrid: q='%s', kw=%.1f, sem=%.1f", q, keyword_weight, semantic_weight)
    try:
        results = await service.hybrid_search(
            session, q, limit=limit, threshold=threshold,
            keyword_weight=keyword_weight, semantic_weight=semantic_weight,
        )
    except Exception as e:
        logger.error("하이브리드 검색 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    provider = await get_provider()
    items = []
    for ds_id, score, match_type in results:
        summary = await service._build_dataset_summary(session, ds_id)
        if summary:
            items.append(SemanticSearchResult(
                dataset=summary, score=round(score, 4), match_type=match_type,
            ))

    return SemanticSearchResponse(
        items=items, total=len(items), query=q,
        provider=provider.provider_name() if provider else None,
        model=provider.model_name() if provider else None,
    )


@router.get("/unified", response_model=UnifiedSearchResponse)
async def unified_search(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=100, description="데이터셋 결과 수"),
    entity_limit: int = Query(5, ge=1, le=50, description="엔티티 타입별 결과 수"),
    threshold: float = Query(0.3, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
):
    """통합 하이브리드 검색 — 데이터셋 + 용어집 + AI Agent + API.

    데이터셋은 기존 hybrid_search, 나머지는 다형 임베딩 테이블 기반.
    임베딩 미구성 시 키워드 검색으로 폴백한다.
    """
    logger.info("GET /catalog/search/unified: q='%s'", q)
    try:
        ds_results = await service.hybrid_search(session, q, limit=limit, threshold=threshold)
        entity_results = await service.hybrid_search_entities(
            session, q, list(embedding_service.ENTITY_TYPES),
            limit=entity_limit, threshold=threshold,
        )
    except Exception as e:
        logger.error("통합 검색 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    datasets = []
    for ds_id, score, match_type in ds_results:
        summary = await service._build_dataset_summary(session, ds_id)
        if summary:
            datasets.append(SemanticSearchResult(
                dataset=summary, score=round(score, 4), match_type=match_type,
            ))

    grouped: dict[str, list[EntitySearchResult]] = {t: [] for t in embedding_service.ENTITY_TYPES}
    for entity_type, scored in entity_results.items():
        for eid, score, match_type in scored:
            payload = await service.build_entity_result(session, entity_type, eid)
            if payload:
                grouped[entity_type].append(EntitySearchResult(
                    entity_type=entity_type, score=round(score, 4),
                    match_type=match_type, **payload,
                ))

    provider = await get_provider()
    total = (len(datasets) + len(grouped["glossary_term"])
             + len(grouped["ai_agent"]) + len(grouped["api"]))
    return UnifiedSearchResponse(
        query=q,
        provider=provider.provider_name() if provider else None,
        model=provider.model_name() if provider else None,
        datasets=datasets,
        glossary_terms=grouped["glossary_term"],
        ai_agents=grouped["ai_agent"],
        apis=grouped["api"],
        total=total,
    )


# ---------------------------------------------------------------------------
# 임베딩 관리 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/embeddings/stats")
async def get_embedding_stats(session: AsyncSession = Depends(get_session)):
    """임베딩 커버리지 통계를 조회한다."""
    return await embedding_service.get_embedding_stats(session)


@router.post("/embeddings/backfill")
async def backfill_embeddings(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """모든 데이터셋 + 엔티티(용어집/AI Agent/API)를 재임베딩한다. 대규모 카탈로그에서는 시간이 걸릴 수 있다."""
    logger.info("POST /catalog/search/embeddings/backfill")
    provider = await get_provider()
    if provider is None:
        raise HTTPException(status_code=503, detail="임베딩 제공자가 구성되지 않았습니다.")

    result = await embedding_service.embed_all_datasets(session)
    result["entities"] = await embedding_service.embed_all_entities(session)
    return result


@router.delete("/embeddings")
async def clear_embeddings(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """모든 임베딩을 삭제한다(예: 제공자 전환 전)."""
    logger.info("DELETE /catalog/search/embeddings")
    count = await embedding_service.clear_all_embeddings(session)
    return {"deleted": count}
