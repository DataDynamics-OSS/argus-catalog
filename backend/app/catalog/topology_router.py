# SPDX-License-Identifier: Apache-2.0
"""조직 · 시스템 · 토폴로지 API.

사이드바 데이터 소스 메뉴의 ``조직(트리) → 시스템 → 데이터 소스 → 데이터셋`` 계층을 다룬다.
catalog_router 와 동일하게 ``/api/v1/catalog`` prefix 로 마운트된다.
조회는 로그인 사용자, 생성·수정·삭제·배정은 데이터 소스 관리 권한(DatasourceManager)이 필요하다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import topology_service as svc
from app.catalog.router import DatasourceManager
from app.catalog.schemas import (
    DatasourceResponse,
    DatasourceSystemAssign,
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
    SystemCreate,
    SystemResponse,
    SystemUpdate,
    TopologyResponse,
)
from app.core.auth import CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-topology"])


# ---------------------------------------------------------------------------
# 토폴로지 (사이드바 트리)
# ---------------------------------------------------------------------------

@router.get("/topology", response_model=TopologyResponse)
async def get_topology(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """조직 트리 → 시스템 → 데이터 소스(+데이터셋 수). 미분류 데이터 소스는 unassigned 버킷."""
    logger.debug("GET /catalog/topology")
    return await svc.get_topology(session)


# ---------------------------------------------------------------------------
# 조직 (Organization)
# ---------------------------------------------------------------------------

@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_organizations(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """조직 평면 목록(sort_order, 이름 순)."""
    return await svc.list_organizations(session)


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    _admin: DatasourceManager,
    req: OrganizationCreate, session: AsyncSession = Depends(get_session)
):
    """조직 생성 — 상위 조직이 없으면 400."""
    try:
        return await svc.create_organization(session, req)
    except ValueError as e:
        logger.warning("조직 생성 거부: name=%s reason=%s", req.name, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/organizations/{org_id}", response_model=OrganizationResponse)
async def get_organization(org_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """조직 단건 조회. 없으면 404."""
    org = await svc.get_organization(session, org_id)
    if org is None:
        logger.debug("조직 없음: org_id=%d", org_id)
        raise HTTPException(status_code=404, detail="조직을 찾을 수 없습니다.")
    return OrganizationResponse.model_validate(org)


@router.put("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    _admin: DatasourceManager,
    org_id: int, req: OrganizationUpdate, session: AsyncSession = Depends(get_session)
):
    """조직 수정·이동 — 순환·상위 없음은 400, 조직이 없으면 404."""
    try:
        org = await svc.update_organization(session, org_id, req)
    except ValueError as e:
        logger.warning("조직 수정 거부: org_id=%d reason=%s", org_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if org is None:
        logger.warning("수정 대상 조직 없음: org_id=%d", org_id)
        raise HTTPException(status_code=404, detail="조직을 찾을 수 없습니다.")
    return org


@router.delete("/organizations/{org_id}")
async def delete_organization(_admin: DatasourceManager, org_id: int, session: AsyncSession = Depends(get_session)):
    """조직 삭제 — 직속 하위 조직이나 시스템이 있으면 409, 없으면 404."""
    child_orgs, systems = await svc.count_org_children(session, org_id)
    if child_orgs or systems:
        logger.warning("조직 삭제 거부 — 연결 존재: org_id=%d child_orgs=%d systems=%d", org_id, child_orgs, systems)
        raise HTTPException(
            status_code=409,
            detail=f"조직을 삭제할 수 없습니다: 하위 조직 {child_orgs}개, 시스템 {systems}개가 연결되어 있습니다.",
        )
    if not await svc.delete_organization(session, org_id):
        logger.warning("삭제 대상 조직 없음: org_id=%d", org_id)
        raise HTTPException(status_code=404, detail="조직을 찾을 수 없습니다.")
    return {"status": "ok", "message": "Organization deleted"}


# ---------------------------------------------------------------------------
# 시스템 (System)
# ---------------------------------------------------------------------------

@router.get("/systems", response_model=list[SystemResponse])
async def list_systems(
    _user: CurrentUser, org_id: int | None = Query(None), session: AsyncSession = Depends(get_session)
):
    """시스템 목록 — org_id 를 주면 그 조직 소속만."""
    return await svc.list_systems(session, org_id=org_id)


@router.post("/systems", response_model=SystemResponse, status_code=201)
async def create_system(_admin: DatasourceManager, req: SystemCreate, session: AsyncSession = Depends(get_session)):
    """시스템 생성 — 조직이 없으면 400."""
    try:
        return await svc.create_system(session, req)
    except ValueError as e:
        logger.warning("시스템 생성 거부: name=%s reason=%s", req.name, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/systems/{system_id}", response_model=SystemResponse)
async def get_system(system_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """시스템 단건 조회. 없으면 404."""
    system = await svc.get_system(session, system_id)
    if system is None:
        logger.debug("시스템 없음: system_id=%d", system_id)
        raise HTTPException(status_code=404, detail="시스템을 찾을 수 없습니다.")
    return SystemResponse.model_validate(system)


@router.put("/systems/{system_id}", response_model=SystemResponse)
async def update_system(
    _admin: DatasourceManager,
    system_id: int, req: SystemUpdate, session: AsyncSession = Depends(get_session)
):
    """시스템 수정 — 조직이 없으면 400, 시스템이 없으면 404."""
    try:
        system = await svc.update_system(session, system_id, req)
    except ValueError as e:
        logger.warning("시스템 수정 거부: system_id=%d reason=%s", system_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if system is None:
        logger.warning("수정 대상 시스템 없음: system_id=%d", system_id)
        raise HTTPException(status_code=404, detail="시스템을 찾을 수 없습니다.")
    return system


@router.delete("/systems/{system_id}")
async def delete_system(
    _admin: DatasourceManager,
    system_id: int,
    force: bool = Query(False, description="true 면 소속 데이터 소스를 미분류로 두고 삭제"),
    session: AsyncSession = Depends(get_session),
):
    """시스템 삭제 — 소속 데이터 소스가 있으면 ``force`` 없이는 409. 응답에 미분류로 떨어진 수를 담는다."""
    count = await svc.count_system_datasources(session, system_id)
    if count and not force:
        logger.warning("시스템 삭제 거부 — 데이터 소스 연결: system_id=%d datasources=%d", system_id, count)
        raise HTTPException(
            status_code=409,
            detail=f"시스템을 삭제할 수 없습니다: 데이터 소스 {count}개가 연결되어 있습니다. "
            "데이터 소스 배정을 해제하고 삭제하도록 선택해 주세요.",
        )
    if not await svc.delete_system(session, system_id):
        logger.warning("삭제 대상 시스템 없음: system_id=%d", system_id)
        raise HTTPException(status_code=404, detail="시스템을 찾을 수 없습니다.")
    return {"status": "ok", "message": "System deleted", "unassigned_datasources": count}


# ---------------------------------------------------------------------------
# 데이터 소스 배정
# ---------------------------------------------------------------------------

@router.put("/datasources/{datasource_id}/system", response_model=DatasourceResponse)
async def assign_datasource_system(
    _admin: DatasourceManager,
    datasource_id: int,
    req: DatasourceSystemAssign,
    session: AsyncSession = Depends(get_session),
):
    """데이터 소스를 시스템에 배정/이동하거나 해제(system_id=null)한다. 시스템 없음 400, 데이터 소스 없음 404."""
    try:
        datasource = await svc.assign_datasource_system(session, datasource_id, req.system_id)
    except ValueError as e:
        logger.warning("데이터 소스 배정 거부: datasource_id=%d system_id=%s reason=%s", datasource_id, req.system_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if datasource is None:
        logger.warning("배정 대상 데이터 소스 없음: datasource_id=%d", datasource_id)
        raise HTTPException(status_code=404, detail="데이터 소스를 찾을 수 없습니다.")
    # 응답에 소속 시스템 이름을 채운다(예전에는 이 경로만 system_name 이 항상 null)
    from app.catalog.service import _datasource_response

    return await _datasource_response(session, datasource)
