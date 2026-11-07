# SPDX-License-Identifier: Apache-2.0
"""통합 검색 — 로컬 + HARVEST 미러(pgvector/키워드) + LIVE peer fan-out 병합."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.embedding.registry import get_provider
from app.federation import breaker, client
from app.federation.drilldown import _record_peer_error
from app.federation.models import (
    FederatedDataset,
    FederatedInstance,
)
from app.federation.registry import _MIRROR_MODES, _escape_like, list_active_peers
from app.federation.schemas import (
    FederatedSearchHit,
    FederatedSearchResponse,
)
from app.search import service as search_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scatter-gather 통합 검색
# ---------------------------------------------------------------------------

async def _local_hits(
    session: AsyncSession, query: str, limit: int, threshold: float
) -> list[FederatedSearchHit]:
    """이 허브(로컬)의 hybrid_search 결과를 연합 hit 으로 변환."""
    scored = await search_service.hybrid_search(
        session, query, limit=limit, threshold=threshold
    )
    hits: list[FederatedSearchHit] = []
    for ds_id, score, match_type in scored:
        summary = await search_service._build_dataset_summary(session, ds_id)
        if not summary:
            continue
        hits.append(FederatedSearchHit(
            urn=summary.urn,
            name=summary.name,
            datasource_name=summary.datasource_name,
            datasource_type=summary.datasource_type,
            description=summary.description,
            origin=summary.origin,
            score=round(score, 4),
            match_type=match_type,
            source_instance_key=None,        # None = 로컬
            source_instance_name=None,
            source_base_url=None,
        ))
    return hits


async def _peer_hits(
    instance: FederatedInstance, query: str, limit: int, threshold: float
) -> list[FederatedSearchHit]:
    """단일 peer 호출 → 연합 hit 변환. 실패 시 예외를 전파(호출자가 집계).

    circuit breaker 가 열려 있으면 네트워크 호출 없이 즉시 실패시킨다.
    """
    key = instance.instance_key
    if breaker.is_open(key):
        logger.debug("circuit open 으로 peer 검색 생략: instance=%s", key)
        raise breaker.CircuitOpenError(key)
    try:
        export = await client.search_peer(
            instance, query, limit=limit, threshold=threshold
        )
    except Exception as e:
        _record_peer_error(key, e)
        raise
    breaker.record_success(key)
    logger.debug("peer 검색 결과: instance=%s hits=%d", key, len(export.items))
    return [
        FederatedSearchHit(
            **hit.model_dump(),
            source_instance_key=instance.instance_key,
            source_instance_name=instance.name,
            source_base_url=instance.base_url,
        )
        for hit in export.items
    ]



# ---- HARVEST 미러 검색 (로컬 pgvector + 키워드) ---------------------------

async def _mirror_keyword_ids(
    session: AsyncSession, query: str, limit: int,
) -> set[int]:
    """미러 데이터셋 키워드 매칭 → federation_datasets.id 집합 (ACTIVE·미러모드 한정)."""
    pattern = f"%{_escape_like(query)}%"
    rows = (await session.execute(
        select(FederatedDataset.id)
        .join(FederatedInstance, FederatedInstance.id == FederatedDataset.instance_id)
        .where(
            FederatedInstance.status == "ACTIVE",
            FederatedInstance.mode.in_(_MIRROR_MODES),
            or_(
                FederatedDataset.name.ilike(pattern, escape="\\"),
                FederatedDataset.description.ilike(pattern, escape="\\"),
                FederatedDataset.federated_urn.ilike(pattern, escape="\\"),
                FederatedDataset.qualified_name.ilike(pattern, escape="\\"),
            ),
        )
        .limit(limit)
    )).all()
    return {r[0] for r in rows}


async def _mirror_semantic(
    session: AsyncSession, query_vec: list[float], limit: int, threshold: float,
    model_name: str, dimension: int,
) -> dict[int, float]:
    """미러 임베딩 pgvector 검색 → {federation_dataset_id: similarity}.

    ``<=>`` 는 pgvector 코사인 거리 연산자라 ``1 - 거리`` 를 유사도로 쓴다. 질의 벡터는
    문자열로 바인딩한 뒤 ``CAST(:qv AS vector)`` 로 변환한다.

    질의 벡터와 같은 모델·차원으로 만든 임베딩만 비교한다(모델 교체 직후 재임베딩 전
    벡터가 섞여 차원 오류나 의미 없는 유사도가 나오는 것을 막는다).
    """
    result = await session.execute(text("""
        SELECT e.federation_dataset_id,
               1 - (e.embedding <=> CAST(:qv AS vector)) AS similarity
        FROM federation_dataset_embeddings e
        JOIN federation_datasets fd ON fd.id = e.federation_dataset_id
        JOIN federation_instances fi ON fi.id = fd.instance_id
        WHERE fi.status = 'ACTIVE' AND fi.mode IN ('HARVEST', 'HYBRID')
          AND e.model_name = :model AND e.dimension = :dim
          AND 1 - (e.embedding <=> CAST(:qv AS vector)) >= :threshold
        ORDER BY e.embedding <=> CAST(:qv AS vector)
        LIMIT :lim
    """), {
        "qv": str(query_vec), "threshold": threshold, "lim": limit,
        "model": model_name, "dim": dimension,
    })
    return {row[0]: float(row[1]) for row in result.fetchall()}


async def search_mirror(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    keyword_weight: float = 0.3,
    semantic_weight: float = 0.7,
) -> list[FederatedSearchHit]:
    """HARVEST 미러를 로컬에서 hybrid 검색한다.

    임베딩 제공자가 없거나 pgvector 가 없으면 키워드 전용으로 폴백한다. 점수 결합은
    데이터셋 ``hybrid_search`` 와 동일한 방식.
    """
    semantic_map: dict[int, float] = {}
    provider = await get_provider()
    if provider is not None:
        try:
            vectors = await provider.embed([query])
            # SAVEPOINT — 시맨틱 SQL 실패(pgvector 없음·차원 불일치)가 트랜잭션을 abort 시켜
            # 뒤의 키워드 폴백 쿼리까지 실패하는 것을 막는다.
            async with session.begin_nested():
                semantic_map = await _mirror_semantic(
                    session, vectors[0], limit * 2, threshold,
                    provider.model_name(), provider.dimension(),
                )
        except Exception as e:  # noqa: BLE001 — 키워드로 degrade
            logger.warning("미러 시맨틱 검색 불가, 키워드로 폴백: %s", e)

    keyword_ids = await _mirror_keyword_ids(session, query, limit * 2)

    scored: dict[int, tuple[float, str]] = {}
    for fid in set(semantic_map) | keyword_ids:
        sem = semantic_map.get(fid, 0.0)
        kw = 1.0 if fid in keyword_ids else 0.0
        combined = (semantic_weight * sem + keyword_weight * kw) if semantic_map else kw
        if sem > 0 and kw > 0:
            mt = "hybrid"
        elif sem > 0:
            mt = "semantic"
        else:
            mt = "keyword"
        scored[fid] = (combined, mt)

    top_ids = sorted(scored, key=lambda i: scored[i][0], reverse=True)[:limit]
    logger.debug(
        "미러 검색 후보: semantic=%d keyword=%d top=%d",
        len(semantic_map), len(keyword_ids), len(top_ids),
    )
    if not top_ids:
        return []

    # 상위 미러 행 + 출처 인스턴스 정보 로드
    rows = (await session.execute(
        select(
            FederatedDataset.id, FederatedDataset.federated_urn, FederatedDataset.name,
            FederatedDataset.datasource_name, FederatedDataset.datasource_type,
            FederatedDataset.description, FederatedDataset.origin,
            FederatedInstance.instance_key, FederatedInstance.name.label("instance_name"),
            FederatedInstance.base_url,
        )
        .join(FederatedInstance, FederatedInstance.id == FederatedDataset.instance_id)
        .where(FederatedDataset.id.in_(top_ids))
    )).all()

    hits: list[FederatedSearchHit] = []
    for r in rows:
        combined, mt = scored[r.id]
        hits.append(FederatedSearchHit(
            urn=r.federated_urn, name=r.name,
            datasource_name=r.datasource_name, datasource_type=r.datasource_type,
            description=r.description, origin=r.origin,
            score=round(combined, 4), match_type=mt,
            source_instance_key=r.instance_key,
            source_instance_name=r.instance_name,
            source_base_url=r.base_url,
        ))
    return hits


async def federated_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    include_local: bool = True,
) -> FederatedSearchResponse:
    """로컬 + ACTIVE peer 들을 병합한 통합 검색.

    모드별 경로:
    - HARVEST/HYBRID peer → 로컬 미러를 pgvector 로 검색(빠름·일관·내결함성).
    - LIVE peer → 요청 시점에 실시간 fan-out(scatter-gather).

    LIVE peer 가 도달 실패하면 ``instances_failed`` 로 보고하고 나머지로 degrade 한다.
    HARVEST 미러는 단일 로컬 쿼리라 peer 가 죽어도 검색이 동작한다.
    """
    peers = await list_active_peers(session)
    live_peers = [p for p in peers if p.mode not in _MIRROR_MODES]
    mirror_peers = [p for p in peers if p.mode in _MIRROR_MODES]
    logger.debug(
        "연합 검색 시작: limit=%d include_local=%s live=%d mirror=%d",
        limit, include_local, len(live_peers), len(mirror_peers),
    )

    # LIVE peer fan-out 은 DB 세션을 쓰지 않으므로 먼저 띄워 병렬로 진행시킨다.
    live_keys = [p.instance_key for p in live_peers]
    live_future = asyncio.gather(
        *(_peer_hits(p, query, limit, threshold) for p in live_peers),
        return_exceptions=True,
    )

    merged: list[list[FederatedSearchHit]] = []
    failed: list[str] = []

    # 로컬/미러 검색은 같은 AsyncSession 을 쓰므로 순차 실행한다(AsyncSession 은 동시 사용 불가).
    # SAVEPOINT 로 감싸 한쪽 SQL 오류가 트랜잭션을 망가뜨려 다른 쪽까지 실패시키지 않게 한다.
    db_searches: list[tuple[str, Callable[[], Awaitable[list[FederatedSearchHit]]]]] = []
    if include_local:
        db_searches.append(("local", lambda: _local_hits(session, query, limit, threshold)))
    if mirror_peers:
        db_searches.append(("mirror", lambda: search_mirror(
            session, query, limit=limit, threshold=threshold,
        )))
    for label, run_search in db_searches:
        try:
            async with session.begin_nested():
                merged.append(await run_search())
        except Exception as e:  # noqa: BLE001 — 로컬/미러 실패는 부분 실패로 degrade
            # 로컬/미러 실패는 peer 단위 instances_failed 에 넣지 않는다.
            logger.warning("연합 검색 부분 실패 [%s]: %s", label, e)

    for key, result in zip(live_keys, await live_future):
        if isinstance(result, BaseException):
            logger.warning("연합 검색 부분 실패 [%s]: %s", key, result)
            failed.append(key)
            continue
        merged.append(result)

    flat: list[FederatedSearchHit] = [hit for group in merged for hit in group]
    flat.sort(key=lambda h: h.score, reverse=True)
    flat = flat[:limit]

    # 시도한 인스턴스 수: 로컬 + 미러가 커버한 peer + LIVE peer
    instances_queried = (1 if include_local else 0) + len(mirror_peers) + len(live_peers)
    logger.debug(
        "연합 검색 완료: queried=%d failed=%d hits=%d",
        instances_queried, len(failed), len(flat),
    )

    return FederatedSearchResponse(
        query=query,
        items=flat,
        total=len(flat),
        instances_queried=instances_queried,
        instances_failed=failed,
    )
