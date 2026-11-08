# SPDX-License-Identifier: Apache-2.0
"""카탈로그 엔티티 API (design/catalog-entity-design.md).

``/api/v1/catalog`` prefix 로 마운트된다. 조회는 로그인 사용자, 생성·수정·삭제·연결 관리는
데이터 소스 관리 권한(DatasourceManager)이 필요하다.

- 카탈로그: 목록·단건·생성·수정(큐레이션 필드)·삭제
- 연결: 카탈로그에 데이터 소스를 SYNC(수집) / QUERY(엔진 조회)로 연결·해제
- 데이터 소스 기준: 연결된 카탈로그 목록, 엔진 카탈로그 탐색(Trino)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import catalogs_service as svc
from app.catalog.router import DatasourceManager
from app.catalog.schemas import (
    CatalogConnectionCreate,
    CatalogCreate,
    CatalogResponse,
    CatalogUpdate,
    EngineCatalogsResponse,
)
from app.core.auth import CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-catalogs"])


def _http(e: Exception) -> HTTPException:
    if isinstance(e, svc.CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=409, detail=str(e))


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
async def create_catalog(_guard: DatasourceManager, req: CatalogCreate,
                         session: AsyncSession = Depends(get_session)):
    """카탈로그 수동 생성 — 엔진을 통해서만 닿는 카탈로그 등."""
    return await svc.create_catalog(session, req)


@router.put("/catalogs/{catalog_id}", response_model=CatalogResponse)
async def update_catalog(_guard: DatasourceManager, catalog_id: int, req: CatalogUpdate,
                         session: AsyncSession = Depends(get_session)):
    """큐레이션 필드(이름·요약·설명·담당자) 수정. 자동 생성 카탈로그 이름 변경은 409."""
    try:
        return await svc.update_catalog(session, catalog_id, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


@router.delete("/catalogs/{catalog_id}", status_code=204)
async def delete_catalog(_guard: DatasourceManager, catalog_id: int,
                         session: AsyncSession = Depends(get_session)):
    """수동 생성 카탈로그 삭제. 자동 생성분이거나 데이터셋이 있으면 409."""
    try:
        await svc.delete_catalog(session, catalog_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


# ---------------------------------------------------------------------------
# 연결
# ---------------------------------------------------------------------------

@router.post("/catalogs/{catalog_id}/connections", response_model=CatalogResponse, status_code=201)
async def add_connection(_guard: DatasourceManager, catalog_id: int, req: CatalogConnectionCreate,
                         session: AsyncSession = Depends(get_session)):
    """카탈로그에 데이터 소스 연결 (QUERY: 엔진 조회, SYNC: 수집 — 카탈로그·데이터 소스당 하나)."""
    try:
        return await svc.add_connection(session, catalog_id, req)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


@router.delete("/catalogs/{catalog_id}/connections/{datasource_id}", response_model=CatalogResponse)
async def remove_connection(_guard: DatasourceManager, catalog_id: int, datasource_id: int,
                            session: AsyncSession = Depends(get_session)):
    """연결 해제. 자동 생성 카탈로그의 SYNC 연결은 409."""
    try:
        return await svc.remove_connection(session, catalog_id, datasource_id)
    except (svc.CatalogNotFoundError, svc.CatalogConflictError) as e:
        raise _http(e)


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
