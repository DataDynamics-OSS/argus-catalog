"""API Catalog 라우터 — /api/v1/apis."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis import service
from app.apis.openapi_parser import SpecParseError
from app.apis.schemas import (
    ApiAlertResponse,
    ApiCreate,
    ApiDetailResponse,
    ApiEndpointResponse,
    ApiInvocationResponse,
    ApiLintResponse,
    ApiStats,
    ApiStatusHistoryResponse,
    ApiSummary,
    ApiUpdate,
    ApiUsageResponse,
    CredentialCreate,
    CredentialResponse,
    EndpointCreate,
    FavoriteCreate,
    FavoriteResponse,
    InvokeRequest,
    InvokeResponse,
    LineageCreate,
    LineageResponse,
    PaginatedApis,
    SpecDiffResponse,
    SpecUpload,
)
from app.core.auth import AdminUser, CurrentUser, OptionalUser, assert_owner_or_admin
from app.core.database import get_session
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apis", tags=["apis"])


def _user_key(user) -> str:
    """즐겨찾기·이력 식별용 사용자 키. 인증 없으면 'anonymous'."""
    return (user.username or user.email) if user else "anonymous"

_NOT_FOUND = "API 를 찾을 수 없습니다: {name}"


@router.get("/stats", response_model=ApiStats)
async def stats(session: AsyncSession = Depends(get_session)):
    return await service.get_stats(session)


@router.get("/favorites", response_model=list[FavoriteResponse])
async def all_favorites(user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """현재 사용자의 전체 즐겨찾기 엔드포인트(즐겨찾기 화면용)."""
    return await service.list_all_favorites(session, _user_key(user))


@router.post("", response_model=ApiSummary)
async def create_api(req: ApiCreate, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    logger.info("POST /apis: name=%s", req.name)
    try:
        return await service.create_api(session, req, created_by=current.username)
    except SpecParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=PaginatedApis)
async def list_apis(
    search: str | None = Query(None),
    status: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_apis(session, search=search, status=status, category=category, page=page, page_size=page_size)


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(_guard: CurrentUser, req: InvokeRequest, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """Try-it 프록시 호출(브라우저 CORS 우회 + 저장 자격증명 주입). 알려진 API 는 사용량 로깅."""
    return await service.invoke(session, req, called_by=_user_key(user))


@router.get("/{name}", response_model=ApiDetailResponse)
async def get_api(name: str, session: AsyncSession = Depends(get_session)):
    detail = await service.get_detail(session, name)
    if not detail:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return detail


@router.patch("/{name}", response_model=ApiSummary)
async def update_api(name: str, req: ApiUpdate, current: CurrentUser, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    changed_by = (user.username or user.email) if user else None
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    res = await service.update_api(session, name, req, changed_by=changed_by)
    if not res:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.delete("/{name}")
async def delete_api(name: str, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    if not await service.delete_api(session, name):
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok"}


@router.post("/{name}/specs", response_model=ApiSummary)
async def upload_spec(_guard: AdminUser, name: str, req: SpecUpload, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    try:
        res = await service.add_spec(session, name, req, created_by=(user.username or user.email) if user else None)
    except SpecParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not res:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/endpoints", response_model=ApiEndpointResponse)
async def add_endpoint(name: str, req: EndpointCreate, _user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """수동(manual) API 에 엔드포인트 추가."""
    try:
        res = await service.add_endpoint(session, name, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.patch("/{name}/endpoints/{ep_id}", response_model=ApiEndpointResponse)
async def update_endpoint(name: str, ep_id: int, req: EndpointCreate, _user: OptionalUser, session: AsyncSession = Depends(get_session)):
    try:
        res = await service.update_endpoint(session, name, ep_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail="엔드포인트를 찾을 수 없습니다.")
    return res


@router.delete("/{name}/endpoints/{ep_id}")
async def delete_endpoint(_guard: AdminUser, name: str, ep_id: int, session: AsyncSession = Depends(get_session)):
    try:
        ok = await service.delete_endpoint(session, name, ep_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if ok is None or ok is False:
        raise HTTPException(status_code=404, detail="엔드포인트를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.get("/{name}/diff", response_model=SpecDiffResponse)
async def diff_spec(
    name: str,
    from_id: int | None = Query(None, alias="from"),
    to_id: int | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
):
    """스펙 버전 간 변경 비교(Breaking change 감지). 미지정 시 직전→최신."""
    res = await service.diff_specs(session, name, from_id, to_id)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/alerts", response_model=list[ApiAlertResponse])
async def list_alerts(name: str, status: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    """스펙 Breaking 변경 알림 목록. status=OPEN/ACKNOWLEDGED 로 필터."""
    res = await service.list_api_alerts(session, name, status=status)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/alerts/{alert_id}/ack")
async def acknowledge_alert(_guard: CurrentUser, name: str, alert_id: int, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """Breaking 알림 확인 처리(OPEN → ACKNOWLEDGED)."""
    ok = await service.acknowledge_api_alert(session, name, alert_id, user=(user.username or user.email) if user else None)
    if not ok:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.get("/{name}/lint", response_model=ApiLintResponse)
async def lint(name: str, spec_id: int | None = Query(None), session: AsyncSession = Depends(get_session)):
    """스펙 품질 린팅(Spectral 스타일). spec_id 미지정 시 현재 스펙."""
    res = await service.lint_api(session, name, spec_id=spec_id)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/usage", response_model=ApiUsageResponse)
async def usage(name: str, days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_session)):
    """API 호출 사용량 집계(최근 N일)."""
    res = await service.get_api_usage(session, name, days=days)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/invocations", response_model=list[ApiInvocationResponse])
async def endpoint_invocations(
    name: str, method: str = Query(...), path: str = Query(...),
    mine: bool = Query(True), limit: int = Query(20, ge=1, le=100),
    user: OptionalUser = None, session: AsyncSession = Depends(get_session),
):
    """특정 엔드포인트의 최근 호출 이력(입력 파라미터 포함)."""
    res = await service.list_endpoint_invocations(session, name, method, path, user=_user_key(user), mine=mine, limit=limit)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/favorites")
async def list_favorites(name: str, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """특정 API 의 (사용자) 즐겨찾기 — 별 표시용 {method, path} 목록."""
    res = await service.list_api_favorites(session, name, _user_key(user))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/favorites")
async def add_favorite(_guard: CurrentUser, name: str, req: FavoriteCreate, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    res = await service.add_api_favorite(session, name, req, _user_key(user))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok"}


@router.delete("/{name}/favorites")
async def remove_favorite(_guard: CurrentUser, 
    name: str, method: str = Query(...), path: str = Query(...),
    user: OptionalUser = None, session: AsyncSession = Depends(get_session),
):
    res = await service.remove_api_favorite(session, name, method, path, _user_key(user))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok"}


@router.get("/{name}/lineage", response_model=list[LineageResponse])
async def list_lineage(name: str, session: AsyncSession = Depends(get_session)):
    """API 의 provides/consumes 리니지 엣지 목록."""
    res = await service.list_api_lineage(session, name)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/lineage", response_model=LineageResponse)
async def add_lineage(_guard: AdminUser, name: str, req: LineageCreate, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    try:
        res = await service.add_api_lineage(session, name, req, created_by=(user.username or user.email) if user else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.delete("/{name}/lineage/{edge_id}")
async def delete_lineage(_guard: AdminUser, name: str, edge_id: int, session: AsyncSession = Depends(get_session)):
    if not await service.delete_api_lineage(session, name, edge_id):
        raise HTTPException(status_code=404, detail="리니지 관계를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.get("/{name}/credentials", response_model=list[CredentialResponse])
async def list_credentials(name: str, session: AsyncSession = Depends(get_session),
                           # API 자격증명 — 기능 권한(apis.credentials)으로 통제
                           _perm=Depends(require_feature("apis.credentials"))):
    res = await service.list_credentials(session, name)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/credentials", response_model=CredentialResponse)
async def add_credential(_guard: AdminUser, name: str, req: CredentialCreate, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    res = await service.add_credential(session, name, req, created_by=(user.username or user.email) if user else None)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.delete("/{name}/credentials/{cred_id}")
async def delete_credential(_guard: AdminUser, name: str, cred_id: int, session: AsyncSession = Depends(get_session)):
    if not await service.delete_credential(session, name, cred_id):
        raise HTTPException(status_code=404, detail="자격증명을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.get("/{name}/status-history", response_model=list[ApiStatusHistoryResponse])
async def status_history(name: str, session: AsyncSession = Depends(get_session)):
    res = await service.list_status_history(session, name)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res
