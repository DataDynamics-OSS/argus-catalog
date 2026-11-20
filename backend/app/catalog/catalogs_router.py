# SPDX-License-Identifier: Apache-2.0
"""카탈로그 엔티티 API (design/catalog-entity-design.md, design/catalog-registration-design.md).

``/api/v1/catalog`` prefix 로 마운트된다. 조회는 로그인 사용자, 생성·수정·삭제·연결 관리와 수동 데이터셋
원천 매핑은 카탈로그 관리 권한(``catalogs.manage``) 또는 데이터 소스 관리 권한(``datasources.manage``)이 필요하다.
엔진 카탈로그 탐색은 외부에 접속하므로 데이터 소스 관리 권한만.

- 카탈로그: 목록·단건·생성·수정(큐레이션 필드)·삭제
- 연결: 카탈로그에 데이터 소스를 SYNC(수집) / QUERY(엔진 조회)로 연결·해제
- 데이터 소스 기준: 연결된 카탈로그 목록, 엔진 카탈로그 탐색(Trino), 원천 매핑된 경로
- 수동 데이터셋 원천 매핑(TODO-CAT-001): 미리보기 · 저장 · 해제 · 다시 확인 · 원천 스키마 가져오기

변경은 감사 로그(``catalogs`` 분류)에 남긴다.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record as audit_record
from app.catalog import catalogs_service as svc
from app.catalog import source_maps
from app.catalog.router import DatasourceManager
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogCreate,
    CatalogResponse,
    CatalogUpdate,
    DatasetSourceMapRequest,
    DatasetSourceMapResponse,
    DatasourceSourceMapEntry,
    EngineCatalogsResponse,
    SourceMapPreview,
)
from app.core.auth import CurrentUser, TokenUser
from app.core.database import get_session
from app.permissions.router import require_any_feature_granted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-catalogs"])

# 카탈로그 관리 — catalogs.manage(TODO-CAT-001) 또는 기존 datasources.manage(데이터 소스 화면의 카탈로그 연결)
CatalogManager = Annotated[TokenUser, Depends(require_any_feature_granted("catalogs.manage", "datasources.manage"))]


def _http(e: Exception) -> HTTPException:
    if isinstance(e, svc.CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, source_maps.SourceMapConflictError):
        return HTTPException(status_code=409, detail={
            "code": "SOURCE_MAP_CONFLICT", "message": str(e), "conflict": e.conflict.model_dump()})
    return HTTPException(status_code=409, detail=str(e))


async def _audit(session: AsyncSession, actor, action: str, target: str, summary: str, detail=None) -> None:
    await audit_record(session, actor, "catalogs", action, target=target, summary=summary, detail=detail)


# ---------------------------------------------------------------------------
# 카탈로그
# ---------------------------------------------------------------------------

@router.get("/catalogs", response_model=list[CatalogResponse])
async def list_catalogs(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """카탈로그 목록 — 연결된 데이터 소스(역할 포함)와 활성 데이터셋 수."""
    return await svc.list_catalogs(session)


@router.get("/catalogs/{catalog_id}", response_model=CatalogResponse)
async def get_catalog(catalog_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """카탈로그 하나. 없으면 404."""
    resp = await svc.get_catalog(session, catalog_id)
    if resp is None:
        raise HTTPException(status_code=404, detail="Catalog not found")
    return resp


@router.post("/catalogs", response_model=CatalogResponse, status_code=201)
async def create_catalog(user: CatalogManager, req: CatalogCreate,
                         session: AsyncSession = Depends(get_session)):
    """카탈로그 생성 — 화면에서 직접 등록(ARGUS) 또는 엔진을 통해서만 닿는 카탈로그(EXTERNAL)."""
    try:
        resp = await svc.create_catalog(session, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "catalog.create", resp.catalog_key, f"카탈로그 등록: {resp.name}",
                 {"name": resp.name, "kind": resp.kind, "source_type": resp.source_type, "system": resp.system_name})
    return resp


@router.put("/catalogs/{catalog_id}", response_model=CatalogResponse)
async def update_catalog(user: CatalogManager, catalog_id: int, req: CatalogUpdate,
                         session: AsyncSession = Depends(get_session)):
    """큐레이션 필드(이름·요약·설명·담당자)와 직접 등록 카탈로그의 소속 시스템 수정.
    자동 생성 카탈로그 이름 변경 · 수집 카탈로그 시스템 지정은 409, 없는 시스템은 404."""
    before = await svc.get_catalog(session, catalog_id)
    try:
        resp = await svc.update_catalog(session, catalog_id, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    detail = req.model_dump(exclude_none=True)
    if "system_id" in req.model_fields_set:
        detail["system"] = {"before": before.system_name if before else None, "after": resp.system_name}
    await _audit(session, user, "catalog.modify", resp.catalog_key, f"카탈로그 수정: {resp.name}", detail)
    return resp


@router.delete("/catalogs/{catalog_id}", status_code=204)
async def delete_catalog(user: CatalogManager, catalog_id: int,
                         session: AsyncSession = Depends(get_session)):
    """수동 생성 카탈로그 삭제. 자동 생성분이거나 데이터셋이 있으면 409. 수동 등록 데이터 소스도 함께 지운다."""
    before = await svc.get_catalog(session, catalog_id)
    try:
        await svc.delete_catalog(session, catalog_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    if before is not None:
        await _audit(session, user, "catalog.delete", before.catalog_key, f"카탈로그 삭제: {before.name}")


# ---------------------------------------------------------------------------
# 연결
# ---------------------------------------------------------------------------

@router.post("/catalogs/{catalog_id}/connections", response_model=CatalogResponse, status_code=201)
async def add_connection(user: CatalogManager, catalog_id: int, req: CatalogConnectionCreate,
                         session: AsyncSession = Depends(get_session)):
    """카탈로그에 데이터 소스 연결 (QUERY: 엔진 조회, SYNC: 수집 — 카탈로그·데이터 소스당 하나).

    직접 등록한(ARGUS) 카탈로그에는 SYNC 연결을 할 수 없다(409) — 데이터셋마다 원천 매핑을 쓴다.
    """
    try:
        resp = await svc.add_connection(session, catalog_id, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "catalog.connect", resp.catalog_key,
                 f"카탈로그 연결: {resp.name} ← 데이터 소스 id={req.datasource_id} ({req.role})",
                 req.model_dump())
    return resp


@router.delete("/catalogs/{catalog_id}/connections/{datasource_id}", response_model=CatalogResponse)
async def remove_connection(user: CatalogManager, catalog_id: int, datasource_id: int,
                            session: AsyncSession = Depends(get_session)):
    """연결 해제. 자동 생성 카탈로그 · 수동 등록 데이터 소스의 SYNC 연결은 409."""
    try:
        resp = await svc.remove_connection(session, catalog_id, datasource_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "catalog.disconnect", resp.catalog_key,
                 f"카탈로그 연결 해제: {resp.name} ← 데이터 소스 id={datasource_id}")
    return resp


# ---------------------------------------------------------------------------
# 데이터 소스 기준
# ---------------------------------------------------------------------------

@router.get("/datasources/{datasource_id}/catalogs", response_model=list[CatalogResponse])
async def list_datasource_catalogs(datasource_id: int, _user: CurrentUser,
                                   session: AsyncSession = Depends(get_session)):
    """데이터 소스가 연결된 카탈로그들."""
    try:
        return await svc.list_datasource_catalogs(session, datasource_id)
    except svc.CatalogNotFoundError as e:
        raise _http(e)


@router.get("/datasources/{datasource_id}/engine-catalogs", response_model=EngineCatalogsResponse)
async def discover_engine_catalogs(_guard: DatasourceManager, datasource_id: int,
                                   session: AsyncSession = Depends(get_session)):
    """엔진이 노출하는 카탈로그와 연결 후보(Trino). 외부 접속을 하므로 관리 권한이 필요하다."""
    try:
        return await svc.discover_engine_catalogs(session, datasource_id)
    except svc.CatalogNotFoundError as e:
        raise _http(e)


@router.get("/datasources/{datasource_id}/source-maps", response_model=list[DatasourceSourceMapEntry])
async def list_datasource_source_maps(datasource_id: int, _user: CurrentUser,
                                      session: AsyncSession = Depends(get_session)):
    """이 데이터 소스에 원천 매핑된 경로(수동 데이터셋) — 수집이 건너뛸 테이블."""
    try:
        return await source_maps.list_datasource_maps(session, datasource_id)
    except svc.CatalogNotFoundError as e:
        raise _http(e)


# ---------------------------------------------------------------------------
# 수동 데이터셋 원천 매핑 (TODO-CAT-001)
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/source-map", response_model=DatasetSourceMapResponse | None)
async def get_source_map(dataset_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """수동 데이터셋의 원천 매핑. 없으면 null."""
    return await source_maps.get_map(session, dataset_id)


@router.post("/datasets/{dataset_id}/source-map/check", response_model=SourceMapPreview)
async def check_source_map(_guard: CatalogManager, dataset_id: int, req: DatasetSourceMapRequest,
                           session: AsyncSession = Depends(get_session)):
    """매핑 미리보기(저장 없음) — ① 중복이면 conflict, ② 존재 · ③ 스키마 비교는 check."""
    try:
        return await source_maps.preview(session, dataset_id, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


@router.put("/datasets/{dataset_id}/source-map", response_model=DatasetSourceMapResponse)
async def save_source_map(user: CatalogManager, dataset_id: int, req: DatasetSourceMapRequest,
                          session: AsyncSession = Depends(get_session)):
    """매핑 저장(새로 · 변경). ① 중복이면 409 ``SOURCE_MAP_CONFLICT``. ②③ 결과는 기록만 한다."""
    try:
        resp, before = await source_maps.save(session, dataset_id, req, getattr(user, "username", None))
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "source-map.modify" if before else "source-map.create", f"dataset:{dataset_id}",
                 f"원천 매핑: 데이터셋 {dataset_id} → {resp.datasource_name} · {resp.remote_path}",
                 {"before": before, "after": {"datasource_id": resp.datasource_id, "remote_path": resp.remote_path},
                  "check_status": resp.check_status})
    return resp


@router.delete("/datasets/{dataset_id}/source-map", status_code=204)
async def delete_source_map(user: CatalogManager, dataset_id: int, session: AsyncSession = Depends(get_session)):
    """매핑 해제."""
    try:
        removed = await source_maps.delete(session, dataset_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "source-map.delete", f"dataset:{dataset_id}",
                 f"원천 매핑 해제: 데이터셋 {dataset_id}", {"before": removed})


@router.post("/datasets/{dataset_id}/source-map/recheck", response_model=DatasetSourceMapResponse)
async def recheck_source_map(_guard: CatalogManager, dataset_id: int, session: AsyncSession = Depends(get_session)):
    """저장된 매핑의 원천 존재 · 스키마 비교를 다시 한다."""
    try:
        return await source_maps.recheck(session, dataset_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


@router.post("/datasets/{dataset_id}/source-map/import-schema")
async def import_source_schema(user: CatalogManager, dataset_id: int, session: AsyncSession = Depends(get_session)):
    """원천 스키마 가져오기 — 원천 컬럼을 더하고 타입을 맞춘다(큐레이션 값 · 수동에만 있는 컬럼은 유지)."""
    try:
        result = await source_maps.import_schema(session, dataset_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)
    await _audit(session, user, "source-map.import-schema", f"dataset:{dataset_id}",
                 f"원천 스키마 가져오기: 데이터셋 {dataset_id} (추가 {result['added']}, 타입 변경 {result['updated']})",
                 result)
    return result
