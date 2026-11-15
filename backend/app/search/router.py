# SPDX-License-Identifier: Apache-2.0
"""시맨틱·하이브리드 검색 API 엔드포인트.

시맨틱 검색(pgvector 코사인 유사도), 하이브리드 검색(키워드 + 시맨틱),
임베딩 관리 엔드포인트를 제공한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, OptionalUser
from app.core.database import get_session
from app.embedding import service as embedding_service
from app.embedding.registry import get_provider
from app.permissions.router import check_menu
from app.search import service
from app.search.schemas import (
    EntitySearchResult,
    SemanticSearchResponse,
    SemanticSearchResult,
    UnifiedSearchResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog/search", tags=["search"])

# 통합 검색 결과 종류 → 그 결과를 여는 화면의 메뉴 키(frontend lib/permission-registry.ts 의 BUILTIN_MENUS 와 일치).
# 메뉴가 닫힌 사용자에게는 그 종류를 검색하지도 돌려주지도 않는다(DEF-064).
DATASET_MENU_KEY = "datasets"
ENTITY_MENU_KEYS: dict[str, str] = {
    "glossary_term": "glossary",
    "ai_agent": "ai-agents",
    "api": "apis",
    "mcp_server": "mcp-servers",
}


async def _allowed_entity_types(session: AsyncSession, current) -> list[str]:
    """메뉴 권한으로 허용된 엔티티 종류(관리자·미설정 메뉴는 허용)."""
    allowed = []
    for entity_type in embedding_service.ENTITY_TYPES:
        menu_key = ENTITY_MENU_KEYS.get(entity_type)
        if menu_key is None or await check_menu(session, current, menu_key):
            allowed.append(entity_type)
    return allowed


# ---------------------------------------------------------------------------
# 검색 엔드포인트
# ---------------------------------------------------------------------------

async def _require_dataset_menu(session: AsyncSession, current) -> None:
    """데이터셋 전용 검색(시맨틱 · 하이브리드)은 데이터셋 메뉴가 막힌 역할에 403 (DEF-123).

    통합 검색(/unified)은 같은 경우 빈 목록을 돌려준다 — 여기는 결과가 전부 데이터셋이라 막는다."""
    if not await check_menu(session, current, DATASET_MENU_KEY):
        logger.warning("데이터셋 검색 거부(메뉴 권한 없음): user=%s", getattr(current, "username", None))
        raise HTTPException(status_code=403, detail=f"메뉴 접근 권한이 없습니다: {DATASET_MENU_KEY}")


@router.get("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.3, ge=0.0, le=1.0, description="Min cosine similarity"),
    current: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """벡터 유사도를 이용해 데이터셋 전체를 시맨틱 검색한다.

    임베딩 제공자가 없으면 503(키워드 폴백 없음 — 폴백이 필요하면 /hybrid 사용).
    """
    logger.info("GET /catalog/search/semantic: q='%s', limit=%d", q, limit)
    await _require_dataset_menu(session, current)
    provider = await get_provider()
    if provider is None:
        logger.warning("시맨틱 검색 불가 — 임베딩 제공자 미구성")
        raise HTTPException(
            status_code=503,
            detail="임베딩 제공자가 구성되지 않았습니다. 설정 > 임베딩에서 활성화하세요.",
        )

    try:
        results = await service.semantic_search(session, q, limit=limit, threshold=threshold)
    except Exception as e:
        logger.error("시맨틱 검색 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # 결과마다 요약을 개별 조회한다 — 그 사이 삭제된 데이터셋(None)은 건너뜀
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
    current: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """키워드 매칭과 시맨틱 유사도를 결합한 하이브리드 검색.

    임베딩 제공자가 구성되지 않은 경우 키워드 전용 검색으로 폴백한다.
    """
    logger.info("GET /catalog/search/hybrid: q='%s', kw=%.1f, sem=%.1f", q, keyword_weight, semantic_weight)
    await _require_dataset_menu(session, current)
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
    current: OptionalUser = None,
    session: AsyncSession = Depends(get_session),
):
    """통합 하이브리드 검색 — 데이터셋 + 용어집 + AI Agent + API + MCP 서버.

    데이터셋은 기존 hybrid_search, 나머지는 다형 임베딩 테이블 기반.
    임베딩 미구성 시 키워드 검색으로 폴백한다.
    사용자의 메뉴 권한으로 닫힌 종류(예: AI Agent 메뉴 차단)는 빈 목록으로 돌려준다(DEF-064).
    """
    logger.info("GET /catalog/search/unified: q='%s'", q)
    datasets_allowed = await check_menu(session, current, DATASET_MENU_KEY)
    entity_types = await _allowed_entity_types(session, current)
    if not datasets_allowed or len(entity_types) < len(embedding_service.ENTITY_TYPES):
        logger.debug("통합 검색 메뉴 권한 필터: datasets=%s entities=%s", datasets_allowed, entity_types)
    try:
        ds_results = (
            await service.hybrid_search(session, q, limit=limit, threshold=threshold)
            if datasets_allowed else []
        )
        entity_results = (
            await service.hybrid_search_entities(
                session, q, entity_types, limit=entity_limit, threshold=threshold,
            )
            if entity_types else {}
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
    total = len(datasets) + sum(len(v) for v in grouped.values())
    return UnifiedSearchResponse(
        query=q,
        provider=provider.provider_name() if provider else None,
        model=provider.model_name() if provider else None,
        datasets=datasets,
        glossary_terms=grouped["glossary_term"],
        ai_agents=grouped["ai_agent"],
        apis=grouped["api"],
        mcp_servers=grouped["mcp_server"],
        total=total,
    )


# ---------------------------------------------------------------------------
# 임베딩 관리 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/embeddings/stats")
async def get_embedding_stats(session: AsyncSession = Depends(get_session)):
    """임베딩 커버리지 통계를 조회한다."""
    logger.debug("GET /catalog/search/embeddings/stats")
    return await embedding_service.get_embedding_stats(session)


@router.post("/embeddings/backfill")
async def backfill_embeddings(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """모든 데이터셋 + 엔티티(용어집/AI Agent/API/MCP 서버)를 재임베딩한다(관리자 전용).

    요청 안에서 동기 실행되므로 대규모 카탈로그에서는 시간이 걸릴 수 있다. 제공자가 없으면 503.
    """
    logger.info("POST /catalog/search/embeddings/backfill")
    provider = await get_provider()
    if provider is None:
        logger.warning("임베딩 백필 거부 — 임베딩 제공자 미구성")
        raise HTTPException(status_code=503, detail="임베딩 제공자가 구성되지 않았습니다.")

    result = await embedding_service.embed_all_datasets(session)
    result["entities"] = await embedding_service.embed_all_entities(session)
    logger.info("임베딩 백필 완료: provider=%s", provider.provider_name())
    return result


@router.delete("/embeddings")
async def clear_embeddings(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """모든 임베딩을 삭제한다(예: 제공자 전환 전, 관리자 전용)."""
    logger.info("DELETE /catalog/search/embeddings")
    count = await embedding_service.clear_all_embeddings(session)
    logger.info("임베딩 전체 삭제 완료: deleted=%s", count)
    return {"deleted": count}
