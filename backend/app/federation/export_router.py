# SPDX-License-Identifier: Apache-2.0
"""이 인스턴스가 peer 로서 다른 허브에 노출하는 페더레이션 export API.

허브가 연합 검색을 위해 이 인스턴스를 fan-out 호출하는 진입점이다. 기존
``/api/v1/external/*`` 와 동일하게 읽기 전용이며, Phase 0 PoC 에서는 ``/external`` 의
정책을 따라 인증을 강제하지 않는다(서비스 토큰 검증은 Phase 1 에서 추가).
"""

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

    # visibility 정책이 있으면 노출 불가 데이터셋을 결과에서 제외
    if visibility.has_visibility_policy():
        allowed = await visibility.exportable_dataset_ids(
            session, [ds_id for ds_id, _, _ in scored]
        )
        scored = [t for t in scored if t[0] in allowed]

    items: list[FederatedDatasetHit] = []
    for ds_id, score, match_type in scored:
        summary = await search_service._build_dataset_summary(session, ds_id)
        if not summary:
            continue
        items.append(FederatedDatasetHit(
            urn=summary.urn,
            name=summary.name,
            datasource_name=summary.datasource_name,
            datasource_type=summary.datasource_type,
            description=summary.description,
            origin=summary.origin,
            score=round(score, 4),
            match_type=match_type,
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
    exposed = await service.get_exposed_fields(session)
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
    exposed = await service.get_exposed_fields(session)
    if not capabilities.is_exposed("lineage", exposed):
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

    metadata = await get_dataset_metadata(session, urn)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    if not await visibility.is_dataset_exportable(session, metadata["dataset_id"]):
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    # 노출 정책으로 항목 필터링(+ ddl/glossary 보강)
    exposed = await service.get_exposed_fields(session)
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
    from app.catalog.router import _sample_dir_by_datasource
    from app.catalog.service import get_dataset_by_urn

    # 노출 정책에서 sample 이 꺼져 있으면 존재 노출 없이 404
    exposed = await service.get_exposed_fields(session)
    if not capabilities.is_exposed("sample", exposed):
        raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")

    dataset = await get_dataset_by_urn(session, urn)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")
    if not await visibility.is_dataset_exportable(session, dataset.id):
        raise HTTPException(status_code=404, detail=f"데이터셋을 찾을 수 없습니다: {urn}")

    parquet_path = _sample_dir_by_datasource(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if not parquet_path.is_file():
        raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")

    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    n = min(table.num_rows, limit)
    columns = table.column_names
    rows = []
    for i in range(n):
        rows.append([
            str(table.column(c)[i].as_py()) if table.column(c)[i].as_py() is not None else None
            for c in range(table.num_columns)
        ])
    return {"format": "parquet", "columns": columns, "rows": rows, "row_count": n}
