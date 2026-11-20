# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 수집 실행 API (design/datasource-sync-scope-design.md 1단계).

``/api/v1/catalog`` prefix 로 마운트된다. 조회는 로그인 사용자, 범위 저장·실행·취소·재시도와
metadata-sync 진행 보고(``PATCH /internal/sync-runs/{id}``)는 수집 권한(datasources.sync)이 필요하다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import sync_runs_service as svc
from app.catalog.router import DatasourceSyncer, NotManual
from app.catalog.schemas import (
    SyncBrowseResponse,
    SyncRunItemResponse,
    SyncRunReport,
    SyncRunResponse,
    SyncRunStart,
    SyncRunSummary,
    SyncScheduleUpdate,
    SyncScopePreview,
    SyncScopePreviewRequest,
    SyncServiceStatus,
    SyncSettingsResponse,
    SyncSettingsUpdate,
)
from app.core.auth import CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog-sync-runs"])


def _http(e: svc.SyncRunError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/datasources/{datasource_id}/sync-scope", response_model=SyncSettingsResponse)
async def get_sync_scope(datasource_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.get_settings(session, datasource_id)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.put("/datasources/{datasource_id}/sync-scope", response_model=SyncSettingsResponse, dependencies=[NotManual])
async def put_sync_scope(datasource_id: int, req: SyncSettingsUpdate, user: DatasourceSyncer,
                         session: AsyncSession = Depends(get_session)):
    try:
        return await svc.save_settings(session, datasource_id, req, user.username)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.put("/datasources/{datasource_id}/sync-schedule", response_model=SyncSettingsResponse, dependencies=[NotManual])
async def put_sync_schedule(datasource_id: int, req: SyncScheduleUpdate, user: DatasourceSyncer,
                            session: AsyncSession = Depends(get_session)):
    """예약 수집 켜기·끄기·주기(분). 저장된 수집 범위로 돈다."""
    try:
        return await svc.save_schedule(session, datasource_id, req, user.username)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.post("/datasources/{datasource_id}/sync-scope/preview", response_model=SyncScopePreview, dependencies=[NotManual])
async def preview_sync_scope(datasource_id: int, req: SyncScopePreviewRequest, _user: DatasourceSyncer,
                             session: AsyncSession = Depends(get_session)):
    """저장 전 확인 — 새 범위 밖에 남는 수집 데이터셋 수."""
    try:
        return await svc.preview_scope(session, datasource_id, req.scope)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.get("/datasources/{datasource_id}/browse", response_model=SyncBrowseResponse, dependencies=[NotManual])
async def browse_datasource(datasource_id: int, _user: DatasourceSyncer,
                            path: list[str] = Query(default_factory=list, max_length=10),
                            q: str | None = Query(None, max_length=200),
                            cursor: int = Query(0, ge=0),
                            session: AsyncSession = Depends(get_session)):
    """원본을 열어 ``path`` 바로 아래 컨테이너 목록(+이미 수집된 데이터셋 수). 접속 정보가 쓰이므로 수집 권한."""
    try:
        return await svc.browse(session, datasource_id, path, q, cursor)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.post("/datasources/{datasource_id}/sync-runs", response_model=SyncRunResponse, status_code=202, dependencies=[NotManual])
async def start_sync_run(datasource_id: int, user: DatasourceSyncer, req: SyncRunStart | None = None,
                         session: AsyncSession = Depends(get_session)):
    try:
        return await svc.start_run(session, datasource_id, req.scope if req else None, user.username)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.get("/datasources/{datasource_id}/sync-runs", response_model=list[SyncRunResponse])
async def list_datasource_sync_runs(datasource_id: int, _user: CurrentUser,
                                    limit: int = Query(20, ge=1, le=200),
                                    session: AsyncSession = Depends(get_session)):
    return await svc.list_runs(session, datasource_id=datasource_id, limit=limit)


@router.get("/sync-runs", response_model=list[SyncRunResponse])
async def list_sync_runs(_user: CurrentUser, status: str | None = Query(None, max_length=20),
                         datasource_id: int | None = Query(None),
                         since: datetime | None = Query(None),
                         cursor: int = Query(0, ge=0),
                         limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)):
    """전체 수집 실행 — ``status``: 상태 하나 / ``ACTIVE``(대기·실행 중) / ``PROBLEM``(실패·일부 실패)."""
    return await svc.list_runs(session, datasource_id=datasource_id, status=status, limit=limit,
                               since=since, cursor=cursor)


@router.get("/sync-runs/summary", response_model=SyncRunSummary)
async def sync_runs_summary(_user: CurrentUser, session: AsyncSession = Depends(get_session)):
    """대시보드 카드 — 실행 중·대기, 최근 24시간 실패·성공 수."""
    return await svc.run_summary(session)


@router.get("/sync-runs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(run_id: int, _user: CurrentUser, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.get_run(session, run_id)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.get("/sync-runs/{run_id}/items", response_model=list[SyncRunItemResponse])
async def list_sync_run_items(run_id: int, _user: CurrentUser, limit: int = Query(200, ge=1, le=1000),
                              level: str | None = Query(None, pattern="^(ERROR|WARNING)$"),
                              q: str | None = Query(None, max_length=200),
                              session: AsyncSession = Depends(get_session)):
    try:
        return await svc.list_items(session, run_id, limit, level, q)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.post("/sync-runs/{run_id}/cancel", response_model=SyncRunResponse)
async def cancel_sync_run(run_id: int, user: DatasourceSyncer, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.cancel_run(session, run_id, user.username)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.post("/sync-runs/{run_id}/retry", response_model=SyncRunResponse, status_code=202)
async def retry_sync_run(run_id: int, user: DatasourceSyncer, session: AsyncSession = Depends(get_session)):
    """끝난 실행을 같은 범위로 다시 실행한다."""
    try:
        prev = await svc.get_run(session, run_id)
        if prev.status not in svc.TERMINAL:
            raise svc.SyncRunError("아직 끝나지 않은 실행입니다.", 409)
        return await svc.start_run(session, prev.datasource_id, prev.scope, user.username)
    except svc.SyncRunError as e:
        raise _http(e) from None


@router.get("/sync-service/status", response_model=SyncServiceStatus)
async def sync_service_status(_user: CurrentUser):
    return await svc.sync_service_status()


@router.patch("/internal/sync-runs/{run_id}", response_model=SyncRunResponse)
async def report_sync_run(run_id: int, report: SyncRunReport, _guard: DatasourceSyncer,
                          session: AsyncSession = Depends(get_session)):
    """metadata-sync 진행·결과 보고."""
    try:
        return await svc.apply_report(session, run_id, report)
    except svc.SyncRunError as e:
        raise _http(e) from None
