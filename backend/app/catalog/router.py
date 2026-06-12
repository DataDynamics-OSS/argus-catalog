# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그 API 엔드포인트."""

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import service
from app.catalog.schemas import (
    CatalogStats,
    ColumnMappingCreate,
    DatasetCreate,
    DatasetLineageCreate,
    DatasetLineageResponse,
    DatasetPropertyCreate,
    DatasetPropertyResponse,
    DatasetResponse,
    DatasetUpdate,
    DatasourceConfigurationResponse,
    DatasourceConfigurationSave,
    DatasourceConnectionTest,
    DatasourceConnectionTestResult,
    DatasourceCreate,
    DatasourceMetadataResponse,
    DatasourceResponse,
    DatasourceUpdate,
    FKLineageReplaceRequest,
    GlossaryTermCreate,
    GlossaryTermResponse,
    GlossaryTermUpdate,
    OwnerCreate,
    OwnerResponse,
    PaginatedDatasets,
    PipelineCreate,
    PipelineResponse,
    PipelineUpdate,
    SchemaFieldCreate,
    SchemaFieldResponse,
    TagCreate,
    TagResponse,
    TagUsage,
)
from app.core.auth import AdminUser, CurrentUser, assert_owner_or_admin
from app.core.config import settings
from app.core.database import get_session
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


# ---------------------------------------------------------------------------
# 대시보드
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=CatalogStats)
async def get_catalog_stats(session: AsyncSession = Depends(get_session)):
    """카탈로그 개요 통계를 조회한다."""
    return await service.get_catalog_stats(session)


# ---------------------------------------------------------------------------
# 데이터 소스 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/datasources", response_model=list[DatasourceResponse])
async def list_datasources(session: AsyncSession = Depends(get_session)):
    """전체 데이터 소스 목록을 조회한다."""
    return await service.list_datasources(session)


@router.post("/datasources", response_model=DatasourceResponse)
async def create_datasource(_guard: AdminUser, req: DatasourceCreate, session: AsyncSession = Depends(get_session)):
    """새 데이터 소스를 등록한다."""
    return await service.create_datasource(session, req)


@router.post("/datasources/test-connection", response_model=DatasourceConnectionTestResult)
async def test_datasource_connection(_guard: AdminUser, req: DatasourceConnectionTest):
    """주어진 연결 설정으로 실제 접속을 시도해 성공 여부를 반환한다(저장 전 검증 가능)."""
    return await service.test_datasource_connection(req.type, req.config)


@router.put("/datasources/{datasource_id}", response_model=DatasourceResponse)
async def update_datasource(_guard: AdminUser,
    datasource_id: int, req: DatasourceUpdate, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 메타데이터(예: 표시 이름)를 수정한다."""
    datasource = await service.update_datasource(session, datasource_id, req)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return datasource


@router.get("/datasources/{datasource_id}/metadata", response_model=DatasourceMetadataResponse)
async def get_datasource_metadata(
    datasource_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 메타데이터(데이터 타입, 테이블 타입, 저장 포맷, 기능)를 조회한다."""
    metadata = await service.get_datasource_metadata(session, datasource_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return metadata


@router.get("/datasources/{datasource_id}/configuration", response_model=DatasourceConfigurationResponse)
async def get_datasource_configuration(
    datasource_id: int, session: AsyncSession = Depends(get_session),
    # 접속 정보(비밀번호 포함) — 기능 권한(datasources.connection)으로 통제
    _perm=Depends(require_feature("datasources.connection")),
):
    """데이터 소스의 연결/설정 정보를 조회한다."""
    config = await service.get_datasource_configuration(session, datasource_id)
    if not config:
        raise HTTPException(status_code=404, detail="데이터 소스 설정을 찾을 수 없습니다.")
    return config


@router.put("/datasources/{datasource_id}/configuration", response_model=DatasourceConfigurationResponse)
async def save_datasource_configuration(_guard: AdminUser,
    datasource_id: int,
    req: DatasourceConfigurationSave,
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스의 연결/설정 정보를 저장하거나 갱신한다."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return await service.save_datasource_configuration(session, datasource_id, req.config)


@router.get("/datasources/{datasource_id}/dataset-count")
async def get_datasource_dataset_count(
    datasource_id: int, session: AsyncSession = Depends(get_session)
):
    """이 데이터 소스를 사용하는 데이터셋 수를 조회한다."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    count = await service.get_datasource_dataset_count(session, datasource_id)
    return {"datasource_id": datasource_id, "dataset_count": count}


@router.get("/datasources/{datasource_id}/delete-impact")
async def get_datasource_delete_impact(
    datasource_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터 소스 삭제 영향(데이터셋 수, 타 데이터 소스 연결 리니지 수)."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return await service.get_datasource_delete_impact(session, datasource_id)


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(_guard: AdminUser,
    datasource_id: int,
    force: bool = Query(False, description="true 면 소속 데이터셋과 하위를 함께 삭제(cascade)"),
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스 삭제. 데이터셋이 있으면 기본 차단(409); force=true 면 cascade 삭제."""
    count = await service.get_datasource_dataset_count(session, datasource_id)
    if count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=f"데이터 소스를 삭제할 수 없습니다: {count}개의 데이터셋이 이 데이터 소스를 사용하고 있습니다. "
            f"함께 삭제하려면 ?force=true 를 사용하십시오.",
        )
    if not await service.delete_datasource(session, datasource_id, force=force):
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Datasource deleted"}


@router.post("/datasources/{datasource_id}/sync")
async def sync_datasource(_guard: AdminUser,
    request: Request,
    datasource_id: int,
    database: str | None = Query(None, description="Specific database to sync (optional)"),
    session: AsyncSession = Depends(get_session),
):
    """외부 데이터 소스의 메타데이터를 카탈로그로 동기화한다."""
    datasource = await service.get_datasource(session, datasource_id)
    if not datasource:
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")

    logger.info("동기화 요청: datasource=%s (id=%d), database=%s",
                datasource.datasource_id, datasource_id, database or "all")

    # 들어온 요청에서 카탈로그 base URL 을 도출
    catalog_url = f"{request.url.scheme}://{request.url.netloc}"

    from app.catalog.sync import sync_datasource_metadata
    result = await sync_datasource_metadata(
        session, datasource.datasource_id, database, catalog_url=catalog_url,
    )

    if result.errors:
        logger.warning("동기화 실패: datasource=%s, error=%s", datasource.datasource_id, result.errors[0])
        raise HTTPException(status_code=400, detail=result.errors[0])

    logger.info("동기화 완료: datasource=%s, 생성=%d, 갱신=%d, 제거=%d, 샘플=%d",
                result.datasource_id, result.tables_created, result.tables_updated,
                result.tables_removed, result.samples_uploaded)

    return {
        "status": "ok",
        "datasource_id": result.datasource_id,
        "databases_scanned": result.databases_scanned,
        "tables_created": result.tables_created,
        "tables_updated": result.tables_updated,
        "tables_removed": result.tables_removed,
        "tables_total": result.tables_total,
        "samples_uploaded": result.samples_uploaded,
    }


# ---------------------------------------------------------------------------
# 태그 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/tags", response_model=list[TagResponse])
async def list_tags(session: AsyncSession = Depends(get_session)):
    """전체 태그 목록 조회."""
    return await service.list_tags(session)


@router.post("/tags", response_model=TagResponse)
async def create_tag(req: TagCreate, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """태그 생성. 관리자만 호출 가능."""
    result = await service.create_tag(session, req)
    logger.info("태그 생성: id=%d, name=%s", result.id, result.name)
    return result


@router.get("/tags/{tag_id}/usage", response_model=TagUsage)
async def get_tag_usage(tag_id: int, session: AsyncSession = Depends(get_session)):
    """태그가 적용된 데이터셋 목록과 개수 조회. 태그 삭제 전 영향 범위 확인용."""
    usage = await service.get_tag_usage(session, tag_id)
    if not usage:
        logger.warning("태그 사용 현황 조회 실패(없음): tag_id=%d", tag_id)
        raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다.")
    return usage


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """태그 삭제. 관리자만 호출 가능. 데이터셋 매핑은 CASCADE 로 함께 제거된다."""
    if not await service.delete_tag(session, tag_id):
        logger.warning("태그 삭제 실패(없음): tag_id=%d", tag_id)
        raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다.")
    logger.info("태그 삭제: id=%d", tag_id)
    return {"status": "ok", "message": "Tag deleted"}


# ---------------------------------------------------------------------------
# 용어집 항목 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/glossary", response_model=list[GlossaryTermResponse])
async def list_glossary_terms(session: AsyncSession = Depends(get_session)):
    """전체 용어집 항목 조회. CATEGORY/TERM 모두 포함, 계층 구조는 ``parent_id`` 로 표현."""
    return await service.list_glossary_terms(session)


@router.post("/glossary", response_model=GlossaryTermResponse)
async def create_glossary_term(
    req: GlossaryTermCreate, _admin: AdminUser, session: AsyncSession = Depends(get_session),
):
    """용어집 항목 생성. 관리자만 호출 가능. ``term_type`` 으로 분류/용어를 구분."""
    return await service.create_glossary_term(session, req)


@router.put("/glossary/{term_id}", response_model=GlossaryTermResponse)
async def update_glossary_term(
    term_id: int, req: GlossaryTermUpdate, _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """용어집 항목 부분 갱신. 관리자만 호출 가능. ``parent_id`` 변경으로 다른 분류로 이동 가능."""
    result = await service.update_glossary_term(session, term_id, req.model_dump(exclude_unset=True))
    if not result:
        logger.warning("용어집 항목 수정 실패(없음): term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    return result


@router.delete("/glossary/{term_id}")
async def delete_glossary_term(term_id: int, _admin: AdminUser, session: AsyncSession = Depends(get_session)):
    """용어집 항목 삭제. 관리자만 호출 가능.

    데이터셋 매핑은 CASCADE 로 정리되지만, 하위 용어(parent_id)가 있으면 FK 로 차단되므로
    먼저 막아 409 를 반환한다(분류·조직 삭제와 동일).
    """
    children = await service.count_glossary_term_children(session, term_id)
    if children > 0:
        raise HTTPException(
            status_code=409,
            detail=f"용어를 삭제할 수 없습니다: 하위 용어 {children}개가 있습니다.",
        )
    if not await service.delete_glossary_term(session, term_id):
        logger.warning("용어집 항목 삭제 실패(없음): term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    return {"status": "ok", "message": "Glossary term deleted"}


# ---------------------------------------------------------------------------
# 데이터셋 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/datasets", response_model=PaginatedDatasets)
async def list_datasets(
    search: str | None = Query(None, description="Search in name, description, URN"),
    datasource: str | None = Query(None, description="Filter by datasource_id"),
    origin: str | None = Query(None, description="Filter by origin (PROD/DEV/STAGING)"),
    tag: str | None = Query(None, description="Filter by tag name"),
    status: str | None = Query(None, description="Filter by status"),
    org_id: int | None = Query(None, description="Filter by organization (subtree)"),
    system_id: int | None = Query(None, description="Filter by system"),
    category_id: int | None = Query(None, description="Filter by taxonomy category (subtree)"),
    taxonomy_id: int | None = Query(None, description="With uncategorized=true: datasets unmapped in this taxonomy"),
    uncategorized: bool = Query(False, description="taxonomy_id 의 미분류 데이터셋만"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: AsyncSession = Depends(get_session),
):
    """필터·페이지네이션 옵션으로 데이터셋 목록을 조회한다."""
    return await service.list_datasets(
        session, search=search, datasource=datasource, origin=origin,
        tag=tag, status=status, org_id=org_id, system_id=system_id,
        category_id=category_id, taxonomy_id=taxonomy_id, uncategorized=uncategorized,
        page=page, page_size=page_size,
    )


@router.post("/datasets", response_model=DatasetResponse)
async def create_dataset(req: DatasetCreate, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """데이터셋 등록 — 로그인 사용자 누구나. 생성자(created_by)가 소유자가 된다."""
    try:
        return await service.create_dataset(session, req, created_by=current.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터셋 상세를 조회한다."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return dataset


@router.get("/datasets/urn/{urn:path}", response_model=DatasetResponse)
async def get_dataset_by_urn(urn: str, session: AsyncSession = Depends(get_session)):
    """URN 으로 데이터셋 상세를 조회한다."""
    dataset = await service.get_dataset_by_urn(session, urn)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return dataset


@router.put("/datasets/{dataset_id}", response_model=DatasetResponse)
async def update_dataset(
    dataset_id: int, req: DatasetUpdate, current: CurrentUser, session: AsyncSession = Depends(get_session),
):
    """데이터셋 수정 — 소유자(생성자) 또는 관리자만."""
    created_by = await service.get_dataset_created_by(session, dataset_id)
    if created_by is None and not await service.get_dataset(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    assert_owner_or_admin(current, created_by, "데이터셋")
    dataset = await service.update_dataset(session, dataset_id, req)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return dataset


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """데이터셋 삭제 — 소유자(생성자) 또는 관리자만."""
    created_by = await service.get_dataset_created_by(session, dataset_id)
    if created_by is None and not await service.get_dataset(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    assert_owner_or_admin(current, created_by, "데이터셋")
    if not await service.delete_dataset(session, dataset_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return {"status": "ok", "message": "Dataset deleted"}


# ---------------------------------------------------------------------------
# 리니지
# ---------------------------------------------------------------------------

@router.get("/datasources/{datasource_id}/erd")
async def get_datasource_erd(datasource_id: int, session: AsyncSession = Depends(get_session)):
    """데이터 소스 전체 ER 다이어그램 — 모든 테이블 + FK 관계."""
    from app.catalog.erd import build_datasource_erd
    return await build_datasource_erd(session, datasource_id)


@router.get("/datasets/{dataset_id}/erd")
async def get_dataset_erd(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """ER 다이어그램 — 중심 데이터셋 + FK 직접 연결 테이블 (DDL 파싱 + 리니지 폴백)."""
    from app.catalog.erd import build_erd
    return await build_erd(session, dataset_id)


# ---------------------------------------------------------------------------
# Column relationships (사용 기반 — 쿼리 JOIN 키 집계)
# ---------------------------------------------------------------------------

class JoinKeyPair(BaseModel):
    a_table: str
    a_col: str
    b_table: str
    b_col: str
    kind: str = "explicit"  # explicit(JOIN ON) | implicit(WHERE 등치)


class RelationshipIngest(BaseModel):
    datasource_id: str
    pairs: list[JoinKeyPair]


@router.post("/relationships/ingest")
async def ingest_relationships(
    req: RelationshipIngest, current: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    """확장(metadata-sync)이 추출한 JOIN 키 컬럼쌍을 해석·집계해 적재한다."""
    from app.catalog import relationship_service
    return await relationship_service.ingest_join_keys(
        session, req.datasource_id, [p.model_dump() for p in req.pairs],
        username=getattr(current, "username", None),
    )


@router.post("/relationships/reset")
async def reset_relationships(
    _guard: AdminUser,
    datasource_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """관계 전체(또는 datasource 범위) 삭제 — 전체 재계산 전에 호출(관리자)."""
    from app.catalog import relationship_service
    deleted = await relationship_service.reset_relationships(session, datasource_id)
    return {"deleted": deleted}


@router.post("/relationships/recompute")
async def recompute_relationships(_guard: AdminUser, reset: bool = Query(default=True)):
    """metadata-sync 의 전체 재계산을 트리거(프록시)한다(관리자).

    파서(JOIN 키 추출)는 metadata-sync 가 소유하므로, 카탈로그는 sync 의 recompute 를
    호출해 결과를 그대로 반환한다.
    """
    import httpx
    url = f"{settings.metadata_sync_base_url.rstrip('/')}/collector/relationships/recompute"
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, params={"reset": str(reset).lower()})
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.warning("metadata-sync recompute 호출 실패: %s", e)
        raise HTTPException(
            status_code=502, detail=f"metadata-sync recompute 호출 실패: {e}",
        )


@router.get("/datasets/{dataset_id}/relationships/graph")
async def get_dataset_relationship_graph(
    dataset_id: int,
    depth: int = Query(default=2, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
):
    """관계 그래프(무방향, depth 홉) — React Flow 시각화용 nodes/edges."""
    from app.catalog import relationship_service
    return await relationship_service.get_relationship_graph(session, dataset_id, depth)


@router.get("/datasets/{dataset_id}/relationships")
async def get_dataset_relationships(
    dataset_id: int, session: AsyncSession = Depends(get_session),
):
    """데이터셋의 사용 기반 컬럼 관계(자주 함께 조인된 컬럼/데이터셋)."""
    from app.catalog import relationship_service
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return await relationship_service.get_relationships(session, dataset_id)


@router.post("/lineage/resolve")
async def resolve_query_lineage(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """argus_query_lineage 의 NULL dataset_id 를 이름→데이터셋 매칭으로 채운다(관리자 백필).

    확장은 테이블명만 적재하므로, 이 해석을 거쳐야 쿼리 기반 lineage 가 카탈로그에 노출된다.
    (데이터셋 생성 시 자동으로도 점증 해석되지만, 과거 이력 일괄 반영용.)
    """
    from app.catalog import lineage_resolve
    return await lineage_resolve.resolve_all_query_lineage(session)


@router.get("/datasets/{dataset_id}/lineage")
async def get_dataset_lineage(dataset_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋의 업스트림·다운스트림 리니지를 조회한다.

    UI 에서 리니지 DAG 를 렌더링하기에 적합한 노드(데이터셋)와 엣지(source→target 관계)를
    반환한다. 쿼리 기반 리니지(argus_query_lineage)와 데이터셋 단위 리니지
    (argus_dataset_lineage)를 병합해 타 데이터 소스 간 관계까지 포함한다.
    """
    # 데이터셋 존재 여부 확인
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    # ------------------------------------------------------------------
    # 1. 두 리니지 출처 모두에서 관련 ID 를 수집
    # ------------------------------------------------------------------
    related_ids: set[int] = {dataset_id}
    edge_set: set[tuple] = set()  # (source_id, target_id)
    edge_meta: dict[tuple, dict] = {}  # 엣지별 부가 정보

    # 1a. 쿼리 기반 리니지 (같은 데이터 소스, 자동 수집)
    upstream_rows = (await session.execute(text("""
        SELECT DISTINCT source_table, source_dataset_id
        FROM argus_query_lineage
        WHERE target_dataset_id = :ds_id AND source_dataset_id IS NOT NULL
    """), {"ds_id": dataset_id})).fetchall()

    downstream_rows = (await session.execute(text("""
        SELECT DISTINCT target_table, target_dataset_id
        FROM argus_query_lineage
        WHERE source_dataset_id = :ds_id AND target_dataset_id IS NOT NULL
    """), {"ds_id": dataset_id})).fetchall()

    for row in upstream_rows:
        related_ids.add(row[1])
        key = (row[1], dataset_id)
        edge_set.add(key)
        edge_meta.setdefault(key, {
            "sourceTable": row[0], "targetTable": "",
            "lineageSource": "QUERY_AGGREGATED", "lineageId": None,
        })

    for row in downstream_rows:
        related_ids.add(row[1])
        key = (dataset_id, row[1])
        edge_set.add(key)
        edge_meta.setdefault(key, {
            "sourceTable": "", "targetTable": row[0],
            "lineageSource": "QUERY_AGGREGATED", "lineageId": None,
        })

    # 1b. 데이터셋 단위 리니지 (타 데이터 소스 간, 수동/파이프라인)
    # FK(외래키)는 데이터 흐름이 아니라 구조적 관계라 lineage 에서 제외한다(관계는 별도).
    dl_rows = (await session.execute(text("""
        SELECT id, source_dataset_id, target_dataset_id, lineage_source, relation_type
        FROM argus_dataset_lineage
        WHERE (source_dataset_id = :ds_id OR target_dataset_id = :ds_id)
          AND lineage_source <> 'FK'
    """), {"ds_id": dataset_id})).fetchall()

    for row in dl_rows:
        related_ids.add(row[1])
        related_ids.add(row[2])
        key = (row[1], row[2])
        edge_set.add(key)
        edge_meta[key] = {
            "sourceTable": "", "targetTable": "",
            "lineageSource": row[3], "lineageId": row[0],
            "relationType": row[4],
        }

    # 2. 두 출처에서 2차 연결(2-hop)을 조회
    first_level_ids = related_ids - {dataset_id}
    if first_level_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(first_level_ids)))
        params = {f"id{i}": fid for i, fid in enumerate(first_level_ids)}

        # 쿼리 리니지 2-hop
        for direction_col, filter_col in [
            ("source_dataset_id", "target_dataset_id"),
            ("target_dataset_id", "source_dataset_id"),
        ]:
            rows2 = (await session.execute(text(f"""
                SELECT DISTINCT {direction_col}
                FROM argus_query_lineage
                WHERE {filter_col} IN ({placeholders}) AND {direction_col} IS NOT NULL
            """), params)).fetchall()
            for r in rows2:
                related_ids.add(r[0])

        # 데이터셋 리니지 2-hop
        rows2 = (await session.execute(text(f"""
            SELECT DISTINCT source_dataset_id, target_dataset_id,
                   id, lineage_source, relation_type
            FROM argus_dataset_lineage
            WHERE (source_dataset_id IN ({placeholders})
               OR target_dataset_id IN ({placeholders}))
              AND lineage_source <> 'FK'
        """), params)).fetchall()
        for r in rows2:
            related_ids.add(r[0])
            related_ids.add(r[1])
            key = (r[0], r[1])
            edge_set.add(key)
            edge_meta.setdefault(key, {
                "sourceTable": "", "targetTable": "",
                "lineageSource": r[3], "lineageId": r[2],
                "relationType": r[4],
            })

    # 3. 노드 구성
    nodes = []
    if related_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(related_ids)))
        params = {f"id{i}": rid for i, rid in enumerate(related_ids)}
        ds_rows = (await session.execute(text(f"""
            SELECT d.id, d.name, d.urn, p.type AS datasource_type, p.name AS datasource_name,
                   d.quality_status
            FROM catalog_datasets d
            JOIN catalog_datasources p ON d.datasource_id = p.id
            WHERE d.id IN ({placeholders})
        """), params)).fetchall()
        for row in ds_rows:
            nodes.append({
                "id": row[0],
                "name": row[1],
                "urn": row[2],
                "datasourceType": row[3],
                "datasourceName": row[4],
                "isCurrent": row[0] == dataset_id,
                "qualityStatus": row[5],
            })

    # 4. 엣지 구성 (쿼리 기반)
    edges = []
    if related_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(related_ids)))
        params = {f"id{i}": rid for i, rid in enumerate(related_ids)}
        edge_rows = (await session.execute(text(f"""
            SELECT DISTINCT source_dataset_id, target_dataset_id, source_table, target_table
            FROM argus_query_lineage
            WHERE source_dataset_id IN ({placeholders})
              AND target_dataset_id IN ({placeholders})
              AND source_dataset_id IS NOT NULL
              AND target_dataset_id IS NOT NULL
        """), params)).fetchall()
        # edge dedup 은 (source, target, lineage_source) 까지 봐야 multi-source 가 안 가려진다.
        # FK 와 MANUAL 이 같은 쌍에 양립하거나 QUERY 와 FK 가 같은 쌍에 동시에 있을 때, 한
        # 쌍에 대해 한 source 만 표시되는 게 아니라 모두 응답에 노출.
        seen_edges: set[tuple[int, int, str]] = set()
        for row in edge_rows:
            key = (row[0], row[1], "QUERY_AGGREGATED")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "source": row[0],
                "target": row[1],
                "sourceTable": row[2],
                "targetTable": row[3],
                "lineageSource": "QUERY_AGGREGATED",
                "lineageId": None,
            })

        # 데이터셋 단위 리니지 엣지 추가 (타 데이터 소스 간)
        dl_edge_rows = (await session.execute(text(f"""
            SELECT id, source_dataset_id, target_dataset_id,
                   lineage_source, relation_type, description
            FROM argus_dataset_lineage
            WHERE source_dataset_id IN ({placeholders})
              AND target_dataset_id IN ({placeholders})
              AND lineage_source <> 'FK'
        """), params)).fetchall()
        for row in dl_edge_rows:
            key = (row[1], row[2], row[3])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "source": row[1],
                "target": row[2],
                "sourceTable": "",
                "targetTable": "",
                "lineageSource": row[3],
                "lineageId": row[0],
                "relationType": row[4],
                "description": row[5],
            })

    # 5. 컬럼 리니지 (쿼리 기반 + 데이터셋 단위 병합)
    # related_ids 는 그래프에 그려지는 모든 노드(2-hop 포함). columnLineage 가 graph 의
    # edge 와 일치하지 않으면 UI 가 edge click 시 매핑 정보를 못 찾으니, 1-hop 만이
    # 아니라 전체 related_ids 를 대상으로 column mapping 을 모은다.
    column_lineage = []
    direct_ids = list(related_ids)

    if direct_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(direct_ids)))
        params = {f"id{i}": did for i, did in enumerate(direct_ids)}

        # Query-based column lineage — lineageSource 는 항상 QUERY_AGGREGATED.
        cl_rows = (await session.execute(text(f"""
            SELECT ql.source_dataset_id, ql.target_dataset_id,
                   cl.source_column, cl.target_column, cl.transform_type
            FROM argus_column_lineage cl
            JOIN argus_query_lineage ql ON cl.query_lineage_id = ql.id
            WHERE ql.source_dataset_id IN ({placeholders})
              AND ql.target_dataset_id IN ({placeholders})
              AND ql.source_dataset_id IS NOT NULL
              AND ql.target_dataset_id IS NOT NULL
        """), params)).fetchall()
        for row in cl_rows:
            column_lineage.append({
                "sourceDatasetId": row[0],
                "targetDatasetId": row[1],
                "sourceColumn": row[2],
                "targetColumn": row[3],
                "transformType": row[4],
                "lineageSource": "QUERY_AGGREGATED",
            })

        # Dataset-level column mappings — MANUAL/PIPELINE 만(FK 는 데이터 흐름 아님 → 제외).
        dcm_rows = (await session.execute(text(f"""
            SELECT dl.source_dataset_id, dl.target_dataset_id,
                   cm.source_column, cm.target_column, cm.transform_type,
                   dl.lineage_source
            FROM argus_dataset_column_mapping cm
            JOIN argus_dataset_lineage dl ON cm.dataset_lineage_id = dl.id
            WHERE dl.source_dataset_id IN ({placeholders})
              AND dl.target_dataset_id IN ({placeholders})
              AND dl.lineage_source <> 'FK'
        """), params)).fetchall()
        for row in dcm_rows:
            column_lineage.append({
                "sourceDatasetId": row[0],
                "targetDatasetId": row[1],
                "sourceColumn": row[2],
                "targetColumn": row[3],
                "transformType": row[4],
                "lineageSource": row[5],
            })

    return {
        "datasetId": dataset_id,
        "nodes": nodes,
        "edges": edges,
        "columnLineage": column_lineage,
    }


# ---------------------------------------------------------------------------
# 데이터셋 관계 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/tags/{tag_id}")
async def add_dataset_tag(_guard: AdminUser,
    dataset_id: int, tag_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 태그 부착."""
    if not await service.add_dataset_tag(session, dataset_id, tag_id):
        logger.warning("데이터셋 태그 부착 실패(데이터셋 또는 태그 없음): dataset_id=%d, tag_id=%d", dataset_id, tag_id)
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    logger.info("데이터셋 태그 부착: dataset_id=%d, tag_id=%d", dataset_id, tag_id)
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/tags/{tag_id}")
async def remove_dataset_tag(_guard: AdminUser,
    dataset_id: int, tag_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 태그 분리."""
    if not await service.remove_dataset_tag(session, dataset_id, tag_id):
        logger.warning("데이터셋 태그 분리 실패(연결 없음): dataset_id=%d, tag_id=%d", dataset_id, tag_id)
        raise HTTPException(status_code=404, detail="태그 연결을 찾을 수 없습니다.")
    logger.info("데이터셋 태그 분리: dataset_id=%d, tag_id=%d", dataset_id, tag_id)
    return {"status": "ok"}


@router.post("/datasets/{dataset_id}/owners", response_model=OwnerResponse)
async def add_dataset_owner(_guard: AdminUser,
    dataset_id: int, req: OwnerCreate, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 소유자를 추가한다."""
    owner = await service.add_dataset_owner(session, dataset_id, req)
    if not owner:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return owner


@router.delete("/datasets/{dataset_id}/owners/{owner_id}")
async def remove_dataset_owner(_guard: AdminUser,
    dataset_id: int, owner_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 소유자를 제거한다."""
    if not await service.remove_dataset_owner(session, owner_id):
        raise HTTPException(status_code=404, detail="소유자를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.post("/datasets/{dataset_id}/glossary/{term_id}")
async def add_dataset_glossary_term(_guard: AdminUser,
    dataset_id: int, term_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에 용어집 용어를 연결한다."""
    if not await service.add_dataset_glossary_term(session, dataset_id, term_id):
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/datasets/{dataset_id}/glossary/{term_id}")
async def remove_dataset_glossary_term(_guard: AdminUser,
    dataset_id: int, term_id: int, session: AsyncSession = Depends(get_session)
):
    """데이터셋에서 용어집 용어를 분리한다."""
    if not await service.remove_dataset_glossary_term(session, dataset_id, term_id):
        raise HTTPException(status_code=404, detail="용어 연결을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.put("/datasets/{dataset_id}/schema", response_model=list[SchemaFieldResponse])
async def update_schema_fields(
    dataset_id: int,
    fields: list[SchemaFieldCreate],
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 스키마 필드 전체를 교체한다. 관리자 권한 필요."""
    return await service.update_schema_fields(
        session, dataset_id,
        [f.model_dump() for f in fields],
    )


@router.put("/datasets/{dataset_id}/properties", response_model=list[DatasetPropertyResponse])
async def update_dataset_properties(
    dataset_id: int,
    properties: list[DatasetPropertyCreate],
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 속성 전체를 교체한다. 관리자 권한 필요.

    metadata-sync 어댑터가 매 sync 마다 datasource-specific 메타를 갱신하기 위해 사용
    (예: Iceberg 의 current_snapshot_id / last_updated_ms).
    """
    return await service.update_dataset_properties(
        session, dataset_id,
        [p.model_dump() for p in properties],
    )


@router.get("/datasets/{dataset_id}/schema/history")
async def get_schema_history(
    dataset_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 스키마 변경 이력 스냅샷을 조회한다."""
    return await service.get_schema_history(session, dataset_id, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 샘플 데이터 엔드포인트
# ---------------------------------------------------------------------------

def _sample_dir(qualified_name: str):
    """data_dir/samples/{qualified_name}/ 아래의 샘플 디렉터리 경로를 구한다."""
    safe_name = qualified_name.replace("/", "_").replace("\\", "_")
    return settings.data_dir / "samples" / safe_name


def _sample_dir_by_datasource(datasource_id: str, name: str):
    """샘플 디렉터리 경로를 구한다: data_dir/samples/{datasource_id}/{name}/."""
    return settings.data_dir / "samples" / datasource_id / name


def _sample_path(qualified_name: str):
    """data_dir/samples/{qualified_name}/sample.csv 의 샘플 CSV 경로를 구한다."""
    return _sample_dir(qualified_name) / "sample.csv"


@router.post("/datasets/{dataset_id}/sample")
async def upload_sample_data(
    dataset_id: int,
    file: UploadFile,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 CSV 샘플 데이터 파일을 업로드한다. 관리자 권한 필요."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    dest = _sample_path(qn)
    dest.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if len(content) > 100 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"파일 크기({len(content) / 1024:.1f} KB)가 100 KB 제한을 초과합니다.",
        )
    dest.write_bytes(content)
    logger.info("샘플 데이터 업로드 완료: %s (%d bytes)", dest, len(content))
    return {"status": "ok", "path": str(dest), "size": len(content)}


@router.get("/datasets/{dataset_id}/sample")
async def get_sample_data(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
    # 실데이터 노출 — 기능 권한 매트릭스(datasets.sample-view)로 통제
    _perm=Depends(require_feature("datasets.sample-view")),
):
    """데이터셋의 샘플 데이터를 내려받는다.

    parquet 은 JSON {format, columns, rows} 로, 레거시 파일은 원본 CSV 로 반환한다.
    """
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    # parquet 경로를 먼저 시도
    parquet_path = _sample_dir_by_datasource(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if parquet_path.is_file():
        import pyarrow.parquet as pq
        table = pq.read_table(parquet_path)
        columns = table.column_names
        rows = []
        for i in range(table.num_rows):
            rows.append([
                str(v) if v is not None else None
                for v in (table.column(c)[i].as_py() for c in range(table.num_columns))
            ])
        return JSONResponse(content={
            "format": "parquet",
            "columns": columns,
            "rows": rows,
        })

    # 레거시 CSV 경로로 폴백
    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if csv_path.is_file():
        return FileResponse(csv_path, media_type="text/csv", filename="sample.csv")

    raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")


@router.post("/datasets/{dataset_id}/sample/convert-to-parquet")
async def convert_sample_to_parquet(
    dataset_id: int,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """기존 CSV 샘플을 parquet 포맷으로 변환한다. 관리자 권한 필요."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail="변환할 CSV 샘플 데이터가 없습니다.")

    import csv as csv_mod
    import io

    import pyarrow as pa
    import pyarrow.parquet as _pq

    text = csv_path.read_text(encoding="utf-8", errors="replace")
    reader = csv_mod.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        raise HTTPException(status_code=400, detail="CSV 파일이 비어 있습니다.")

    header = all_rows[0]
    data_rows = all_rows[1:] if len(all_rows) > 1 else []

    columns = {}
    for ci, col_name in enumerate(header):
        columns[col_name or f"col_{ci}"] = [
            row[ci] if ci < len(row) else None for row in data_rows
        ]

    arrow_table = pa.table(columns)
    dest_dir = _sample_dir_by_datasource(dataset.datasource.datasource_id, dataset.name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "sample.parquet"
    _pq.write_table(arrow_table, dest)

    # 기존 CSV 와 구분자 설정 제거
    csv_path.unlink()
    delim_path = csv_path.parent / "delimiter.json"
    if delim_path.is_file():
        delim_path.unlink()
    try:
        csv_path.parent.rmdir()
    except OSError:
        pass

    logger.info("CSV → parquet 변환 완료: %s (%d rows)", dest, len(data_rows))
    return {"status": "ok", "rows": len(data_rows), "columns": len(header)}


@router.delete("/datasets/{dataset_id}/sample")
async def delete_sample_data(
    dataset_id: int,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 샘플 데이터(parquet 또는 CSV)를 삭제한다. 관리자 권한 필요."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    deleted = False

    # parquet 경로 시도
    parquet_path = _sample_dir_by_datasource(
        dataset.datasource.datasource_id, dataset.name,
    ) / "sample.parquet"
    if parquet_path.is_file():
        parquet_path.unlink()
        try:
            parquet_path.parent.rmdir()
        except OSError:
            pass
        deleted = True

    # 레거시 CSV 경로 시도
    qn = dataset.qualified_name or dataset.name
    csv_path = _sample_path(qn)
    if csv_path.is_file():
        csv_path.unlink()
        delim_path = csv_path.parent / "delimiter.json"
        if delim_path.is_file():
            delim_path.unlink()
        try:
            csv_path.parent.rmdir()
        except OSError:
            pass
        deleted = True

    if deleted:
        return {"status": "ok", "message": "Sample data deleted"}
    raise HTTPException(status_code=404, detail="사용 가능한 샘플 데이터가 없습니다.")


class DelimiterConfig(BaseModel):
    """샘플 CSV 와 함께 저장되는 구분자/파싱 설정."""
    encoding: str = "UTF-8"
    line_delimiter: str = "\n"
    delimiter: str = ","
    delimiter_mode: str | None = None
    delimiter_input: str = ""
    has_header: bool = True
    quote_char: str = "__none__"
    custom_quote_char: str = ""
    is_custom_quote: bool = False


@router.put("/datasets/{dataset_id}/sample/delimiter")
async def save_delimiter_config(
    dataset_id: int,
    config: DelimiterConfig,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 샘플 데이터의 구분자/파싱 설정을 저장한다. 관리자 권한 필요."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    sample_dir = _sample_dir(qn)
    sample_dir.mkdir(parents=True, exist_ok=True)

    dest = sample_dir / "delimiter.json"
    dest.write_text(json.dumps(config.model_dump(), ensure_ascii=False), encoding="utf-8")
    logger.info("구분자 설정 저장 완료: %s", dest)
    return {"status": "ok"}


@router.get("/datasets/{dataset_id}/sample/delimiter")
async def get_delimiter_config(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 샘플 데이터의 구분자/파싱 설정을 조회한다."""
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")

    qn = dataset.qualified_name or dataset.name
    path = _sample_dir(qn) / "delimiter.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="사용 가능한 구분자 설정이 없습니다.")

    data = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


# ---------------------------------------------------------------------------
# 샘플 데이터 수집 (동기화 업로드)
# ---------------------------------------------------------------------------

@router.post("/samples/upload")
async def upload_sample_parquet(_guard: AdminUser,
    request: Request,
    x_datasource_id: str = Header(..., alias="X-Datasource-Id"),
    x_dataset_name: str = Header(..., alias="X-Dataset-Name"),
):
    """동기화 프로세스로부터 parquet 샘플 파일을 수신한다.

    헤더:
        X-Datasource-Id: datasource_id (예: mysql-19d0bfe954e2cfdaa)
        X-Dataset-Name: database.table (예: sakila.country)
    본문:
        원본 parquet 바이트.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="요청 본문이 비어 있습니다.")

    dest_dir = _sample_dir_by_datasource(x_datasource_id, x_dataset_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "sample.parquet"
    dest.write_bytes(body)
    logger.info("샘플 parquet 업로드 완료: %s (%d bytes)", dest, len(body))
    return {"status": "ok", "path": str(dest), "size": len(body)}


# ---------------------------------------------------------------------------
# 데이터 파이프라인 CRUD — ETL/CDC/파일 내보내기 파이프라인 레지스트리
# ---------------------------------------------------------------------------

@router.post("/pipelines", response_model=PipelineResponse, status_code=201)
async def create_pipeline(_guard: AdminUser, data: PipelineCreate, session: AsyncSession = Depends(get_session)):
    """데이터 파이프라인(ETL, CDC, 파일 내보내기 등)을 등록한다."""
    result = await service.create_pipeline(session, data)
    await session.commit()
    return result


@router.get("/pipelines", response_model=list[PipelineResponse])
async def list_pipelines(session: AsyncSession = Depends(get_session)):
    """등록된 모든 데이터 파이프라인을 조회한다."""
    return await service.list_pipelines(session)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터 파이프라인을 조회한다."""
    pipeline = await service.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    return PipelineResponse.model_validate(pipeline)


@router.put("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(_guard: AdminUser,
    pipeline_id: int, data: PipelineUpdate, session: AsyncSession = Depends(get_session),
):
    """데이터 파이프라인을 수정한다."""
    result = await service.update_pipeline(session, pipeline_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(_guard: AdminUser, pipeline_id: int, session: AsyncSession = Depends(get_session)):
    """데이터 파이프라인을 삭제한다."""
    deleted = await service.delete_pipeline(session, pipeline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 타 데이터 소스 간 데이터셋 리니지 — 시스템 간 명시적 데이터 흐름
# ---------------------------------------------------------------------------

@router.post("/lineage", response_model=DatasetLineageResponse, status_code=201)
async def create_lineage(_guard: AdminUser,
    data: DatasetLineageCreate, session: AsyncSession = Depends(get_session),
):
    """타 데이터 소스 간 데이터셋 리니지 관계를 등록한다."""
    # 소스/대상 데이터셋 존재 여부 검증
    src = await service.get_dataset(session, data.source_dataset_id)
    if not src:
        raise HTTPException(status_code=404, detail="소스 데이터셋을 찾을 수 없습니다.")
    tgt = await service.get_dataset(session, data.target_dataset_id)
    if not tgt:
        raise HTTPException(status_code=404, detail="대상 데이터셋을 찾을 수 없습니다.")
    if data.source_dataset_id == data.target_dataset_id:
        raise HTTPException(status_code=400, detail="소스와 대상은 서로 달라야 합니다.")
    if data.pipeline_id:
        pipeline = await service.get_pipeline(session, data.pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="파이프라인을 찾을 수 없습니다.")

    result = await service.create_dataset_lineage(session, data)
    await session.commit()
    return result


@router.get("/lineage", response_model=list[DatasetLineageResponse])
async def list_lineages(
    dataset_id: int | None = Query(None, description="Filter by dataset ID"),
    lineage_source: str | None = Query(None, description="Filter: MANUAL, PIPELINE, etc."),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 리니지 관계 목록을 조회한다."""
    return await service.list_dataset_lineages(session, dataset_id, lineage_source)


@router.get("/lineage/{lineage_id}", response_model=DatasetLineageResponse)
async def get_lineage(lineage_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 데이터셋 리니지를 컬럼 매핑과 함께 조회한다."""
    lineage = await service.get_dataset_lineage(session, lineage_id)
    if not lineage:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    return await service._build_lineage_response(session, lineage_id)


@router.put("/lineage/{lineage_id}/column-mappings", response_model=DatasetLineageResponse)
async def update_lineage_column_mappings(_guard: AdminUser,
    lineage_id: int,
    column_mappings: list[ColumnMappingCreate],
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 리니지의 컬럼 매핑을 교체한다."""
    result = await service.update_dataset_lineage_column_mappings(
        session, lineage_id, column_mappings,
    )
    if not result:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/lineage/{lineage_id}", status_code=204)
async def delete_lineage(_guard: AdminUser, lineage_id: int, session: AsyncSession = Depends(get_session)):
    """데이터셋 리니지 관계를 삭제한다."""
    deleted = await service.delete_dataset_lineage(session, lineage_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    await session.commit()


@router.put("/datasets/{dataset_id}/lineage/fk")
async def replace_fk_lineage(
    dataset_id: int,
    req: FKLineageReplaceRequest,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """``lineage_source=FK`` lineage 를 통째 교체. metadata-sync 어댑터가 매 sync 마다
    이 dataset 의 FK 제약을 통째 다시 보내는 멱등 엔드포인트. 다른 출처
    (MANUAL/PIPELINE/QUERY_AGGREGATED) 의 lineage 는 건드리지 않는다.
    """
    dataset = await service.get_dataset(session, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    result = await service.replace_fk_lineage_for_target(
        session, dataset_id, req.entries,
    )
    await session.commit()
    return result
