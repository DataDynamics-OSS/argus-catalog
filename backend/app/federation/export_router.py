# SPDX-License-Identifier: Apache-2.0
"""이 인스턴스가 peer 로서 다른 허브에 노출하는 페더레이션 export API.

허브가 연합 검색·HARVEST·드릴다운을 위해 이 인스턴스를 호출하는 진입점이다. 기존
``/api/v1/external/*`` 와 동일하게 읽기 전용이며, 라우터 전역 의존성
``verify_export_token`` 으로 서비스 토큰을 검증한다(``federation_export_token`` 미설정
시에는 ``/external`` 정책처럼 인증을 강제하지 않는다).
"""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.embedding.registry import get_provider
from app.federation import capabilities, service, visibility
from app.federation.auth import verify_export_token
from app.federation.schemas import (
    CapabilitiesResponse,
    FederatedDatasetHit,
    FederatedExportDatasetsResponse,
    FederatedExportLineageResponse,
    FederatedExportSearchResponse,
)
from app.search import service as search_service

logger = logging.getLogger(__name__)

# 서비스 토큰 검증을 라우터 전역 의존성으로 적용 — settings.federation_export_token 이
# 설정된 경우에만 강제(미설정 시 통과, /external 과 동일 정책).
router = APIRouter(
    prefix="/federation/export",
    tags=["federation-export"],
    dependencies=[Depends(verify_export_token)],
)


@router.get("/search", response_model=FederatedExportSearchResponse)
async def export_search(
    q: str = Query(..., min_length=1, max_length=500, description="검색어"),
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.3, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
):
    """이 인스턴스의 데이터셋을 hybrid 검색해 허브에 반환한다.

    임베딩 제공자가 있으면 시맨틱+키워드, 없으면 키워드 전용으로 폴백한다
    (``search_service.hybrid_search`` 의 기본 동작).
    """
    logger.info("GET /federation/export/search: q='%s', limit=%d", q, limit)
    try:
        scored = await search_service.hybrid_search(
            session, q, limit=limit, threshold=threshold
        )
    except Exception as e:  # noqa: BLE001
        logger.error("export 검색 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # visibility 정책(+ import 사본 항상 제외)으로 노출 불가 데이터셋을 결과에서 제외
    allowed = await visibility.exportable_dataset_ids(
        session, [ds_id for ds_id, _, _ in scored]
    )
    scored = [t for t in scored if t[0] in allowed]

    # 상세와 동일한 노출 항목(exposed_fields) 정책을 검색 결과에도 적용한다.
    exposed = await service.get_effective_exposed_fields(session)

    items: list[FederatedDatasetHit] = []
    for ds_id, score, match_type in scored:
        summary = await search_service._build_dataset_summary(session, ds_id)
        if not summary:
            continue
        hit = FederatedDatasetHit(
            urn=summary.urn,
            name=summary.name,
            datasource_name=summary.datasource_name,
            datasource_type=summary.datasource_type,
            description=summary.description,
            origin=summary.origin,
            score=round(score, 4),
            match_type=match_type,
        )
        items.append(FederatedDatasetHit.model_validate(
            capabilities.filter_export_item(hit.model_dump(), exposed)
        ))

    provider = await get_provider()
    return FederatedExportSearchResponse(
        items=items,
        total=len(items),
        query=q,
        provider=provider.provider_name() if provider else None,
        model=provider.model_name() if provider else None,
    )


@router.get("/datasets", response_model=FederatedExportDatasetsResponse)
async def export_datasets(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    updated_after: datetime | None = Query(
        None, description="이 시각 이후 변경분만(증분 동기화). 미지정=전체",
    ),
    session: AsyncSession = Depends(get_session),
):
    """HARVEST 대상 — 이 인스턴스의 데이터셋 목록을 페이지로 반환한다.

    각 항목에 스키마 컬럼을 포함해 허브가 자신의 임베딩 모델로 재임베딩할 수 있게 한다.
    ``updated_after`` 로 증분 가져오기를 지원한다.
    """
    logger.info("GET /federation/export/datasets: limit=%d offset=%d after=%s",
                limit, offset, updated_after)
    return await service.build_export_datasets(
        session, limit=limit, offset=offset, updated_after=updated_after,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def export_capabilities(session: AsyncSession = Depends(get_session)):
    """advertise — 이 인스턴스가 외부에 노출하는 정보 항목 목록을 반환한다.

    허브가 peer 등록 시 호출해, 어떤 정보를 가져올 수 있는지 체크리스트로 보여준다.
    """
    logger.debug("GET /federation/export/capabilities")
    exposed = await service.get_effective_exposed_fields(session)
    return CapabilitiesResponse(
        items=capabilities.registry_items(),
        groups=capabilities.groups(),
        exposed=exposed,
    )


@router.get("/lineage", response_model=FederatedExportLineageResponse)
async def export_lineage(
    limit: int = Query(10_000, ge=1, le=100_000),
    session: AsyncSession = Depends(get_session),
):
    """cross-instance stitching 용 — 이 인스턴스의 리니지 엣지(URN→URN)를 반환한다.

    visibility 정책으로 양 끝이 모두 노출 가능한 엣지만 포함한다. 노출 정책에서
    ``lineage`` 가 꺼져 있으면 빈 결과를 반환한다.
    """
    logger.info("GET /federation/export/lineage: limit=%d", limit)
    exposed = await service.get_effective_exposed_fields(session)
    if not capabilities.is_exposed("lineage", exposed):
        logger.debug("노출 정책상 lineage 비노출 — 빈 리니지 반환")
        return FederatedExportLineageResponse(edges=[], total=0)
    return await service.build_export_lineage(session, limit=limit)


@router.get("/dataset")
async def export_dataset_detail(
    urn: str = Query(..., description="Dataset URN"),
    session: AsyncSession = Depends(get_session),
):
    """LIVE drill-down — URN 의 전체 메타데이터(스키마/태그/소유자/속성)를 반환한다.

    visibility 정책으로 노출 불가한 데이터셋은 404 로 숨긴다(존재 여부 노출 방지).
    """
    from app.external.service import get_dataset_metadata

    logger.debug("GET /federation/export/dataset: urn=%s", urn)
    # 외부 API 메타데이터 캐시(TTL, 데이터셋 수정 시 무효화되지 않음)를 쓰면 peer 의 수정이
    # 최대 TTL 동안 허브 상세에 반영되지 않는다(미러는 새 값, 상세는 옛 값). LIVE 드릴다운은
    # 사용자 클릭 단위라 부하가 작으므로 항상 DB 에서 새로 만든다.
    metadata = await get_dataset_metadata(session, urn, no_cache=True)
    # removed(삭제 표시) 데이터셋은 목록 export 와 마찬가지로 노출하지 않는다.
    if metadata is None or metadata.get("status") == "removed":
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    if not await visibility.is_dataset_exportable(session, metadata["dataset_id"]):
        # 존재 여부를 숨기려 404 로 응답하지만, 운영 진단을 위해 정책 차단 사실은 남긴다.
        logger.debug("visibility 정책으로 상세 export 차단: urn=%s", urn)
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    # 노출 정책으로 항목 필터링(+ ddl/glossary 보강)
    exposed = await service.get_effective_exposed_fields(session)
    return await service.augment_and_filter_detail(session, metadata, exposed)


@router.get("/dataset/sample")
async def export_dataset_sample(
    urn: str = Query(..., description="Dataset URN"),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """LIVE drill-down — URN 의 샘플 데이터(parquet→JSON)를 반환한다.

    visibility 정책으로 노출 불가하거나 샘플이 없으면 404.
    """
    from app.catalog.router import _sample_dir_by_datasource_or_400
    from app.catalog.service import get_dataset_by_urn

    # 노출 정책에서 sample 이 꺼져 있으면 존재 노출 없이 404
    logger.debug("GET /federation/export/dataset/sample: urn=%s limit=%d", urn, limit)
    exposed = await service.get_effective_exposed_fields(session)
    if not capabilities.is_exposed("sample", exposed):
        logger.debug("노출 정책상 sample 비노출: urn=%s", urn)
        raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")

    dataset = await get_dataset_by_urn(session, urn)
    if dataset is None or dataset.status == "removed":
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    if not await visibility.is_dataset_exportable(session, dataset.id):
        logger.debug("visibility 정책으로 샘플 export 차단: urn=%s", urn)
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")

    parquet_path = _sample_dir_by_datasource_or_400(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if not parquet_path.is_file():
        logger.debug("샘플 parquet 없음: urn=%s", urn)
        raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")

    # parquet 읽기·변환은 동기 CPU/IO 작업 — 이벤트 루프를 막지 않도록 스레드로 넘긴다.
    return await asyncio.to_thread(_read_parquet_sample, parquet_path, limit)


def _read_parquet_sample(parquet_path, limit: int) -> dict:
    """parquet 앞 ``limit`` 행을 {format, columns, rows, row_count} 로 변환(값은 문자열)."""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    n = min(table.num_rows, limit)
    head = table.slice(0, n).to_pylist()
    columns = table.column_names
    rows = [
        [None if rec[c] is None else str(rec[c]) for c in columns]
        for rec in head
    ]
    return {"format": "parquet", "columns": columns, "rows": rows, "row_count": n}
