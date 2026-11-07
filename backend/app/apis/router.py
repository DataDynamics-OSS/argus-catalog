# SPDX-License-Identifier: Apache-2.0
"""API Catalog 라우터 — /api/v1/apis.

서비스 함수를 호출하고 HTTP 변환만 담당한다. 서비스가 None 을 돌려주면 404, ``SpecParseError``·
``ApiInputError``·자격증명 오류는 400, 이름 중복 등 ``ValueError`` 는 409(등록) 또는 400(그 밖)으로 바꾼다.

권한: 라우터 전체에 메뉴 권한(main.py 의 ``require_menu("apis")``)이 걸린다. 등록은 ``apis.register``,
자격증명 목록·저장 자격증명 호출은 ``apis.credentials`` 기능 권한이 필요하다. 수정·삭제·스펙/엔드포인트 변경·
알림 확인과 다른 사람 호출 이력 조회는 API 생성자 본인 또는 관리자(``assert_owner_or_admin``),
리니지·자격증명 추가/삭제는 관리자 전용이다.
"""

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
    ApiPermissions,
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
    IncomingLineageResponse,
    InvokeRequest,
    InvokeResponse,
    LineageCreate,
    LineageResponse,
    LineageTargetOption,
    PaginatedApis,
    SpecDiffResponse,
    SpecPreviewRequest,
    SpecPreviewResponse,
    SpecUpload,
)
from app.core.auth import AdminUser, CurrentUser, OptionalUser, assert_owner_or_admin
from app.core.database import get_session
from app.core.outbound import OutboundBlockedError
from app.permissions.router import check_feature, require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apis", tags=["apis"])


def _user_key(user) -> str:
    """즐겨찾기·이력 식별용 사용자 키. 인증 없으면 'anonymous'."""
    return (user.username or user.email) if user else "anonymous"

_NOT_FOUND = "API 를 찾을 수 없습니다: {name}"


@router.get("/name-available")
async def name_available(name: str = Query(..., min_length=1, max_length=255),
                         session: AsyncSession = Depends(get_session)):
    """등록 다이얼로그의 식별자 중복 사전 확인. 최종 판단은 등록 시점(409)."""
    return {"name": name.strip(), "available": await service.is_name_available(session, name)}


@router.get("/lineage-targets", response_model=list[LineageTargetOption])
async def lineage_targets(type: str = Query(..., max_length=20), q: str = Query("", max_length=100),
                          session: AsyncSession = Depends(get_session)):
    """리니지 대상 자동완성 — api / dataset / model / agent 이름 검색(최대 10건)."""
    if not q.strip():
        return []
    return await service.search_lineage_targets(session, type, q)


@router.get("/stats", response_model=ApiStats)
async def stats(session: AsyncSession = Depends(get_session)):
    """목록 화면 상단 지표·필터 옵션용 통계."""
    return await service.get_stats(session)


@router.get("/favorites", response_model=list[FavoriteResponse])
async def all_favorites(user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """현재 사용자의 전체 즐겨찾기 엔드포인트(즐겨찾기 화면용)."""
    return await service.list_all_favorites(session, _user_key(user))


@router.post("", response_model=ApiSummary)
async def create_api(req: ApiCreate, current: CurrentUser, _perm=Depends(require_feature("apis.register")), session: AsyncSession = Depends(get_session)):
    logger.info("POST /apis: name=%s", req.name)
    try:
        return await service.create_api(session, req, created_by=current.username)
    except (SpecParseError, service.ApiInputError) as e:
        logger.warning("POST /apis 입력 오류: name=%s reason=%s", req.name, e)
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        logger.warning("POST /apis 충돌: name=%s reason=%s", req.name, e)
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/preview", response_model=SpecPreviewResponse)
async def preview_spec(req: SpecPreviewRequest, _current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """등록 다이얼로그의 스펙 미리보기 — 파싱만 하고 저장하지 않는다. 파싱·URL 조회 실패는 400."""
    if not (req.spec_text or req.spec_url):
        raise HTTPException(status_code=400, detail="스펙 원문 또는 URL을 입력하세요.")
    try:
        return await service.preview_spec(session, req)
    except SpecParseError as e:
        logger.warning("POST /apis/preview 파싱 실패: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=PaginatedApis)
async def list_apis(
    search: str | None = Query(None, description="이름·표시명·설명·카테고리·태그·소유자(이메일·이름·username) 부분 일치"),
    status: str | None = Query(None),
    category: str | None = Query(None),
    protocol: str | None = Query(None),
    certification: str | None = Query(None),
    owner: str | None = Query(None, pattern="^unowned$", description="unowned: 소유자 미지정만"),
    has_open_alert: bool = Query(False, description="확인되지 않은 Breaking 변경 알림이 있는 API 만"),
    sort: str = Query("updated", pattern=f"^({'|'.join(service.LIST_SORT_KEYS)})$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_apis(
        session, search=search, status=status, category=category, page=page, page_size=page_size,
        protocol=protocol, certification=certification, owner=owner, has_open_alert=has_open_alert, sort=sort,
    )


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(_guard: CurrentUser, req: InvokeRequest, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """Try-it 프록시 호출(브라우저 CORS 우회 + 저장 자격증명 주입). 알려진 API 는 사용량 로깅.

    내부 인프라·루프백·메타데이터 등 SSRF 정책상 차단 대상이면 400 (app.core.outbound).
    """
    # 저장 자격증명 사용은 자격증명 기능 권한(apis.credentials)이 있어야 한다(목록 조회와 동일 기준)
    if req.credential_id and not await check_feature(session, _guard, "apis.credentials"):
        logger.warning("POST /apis/invoke 거부(apis.credentials 권한 없음): user=%s api=%s", _guard.username, req.api_name)
        raise HTTPException(status_code=403, detail="기능 권한이 없습니다: apis.credentials")
    try:
        return await service.invoke(session, req, called_by=_user_key(user))
    except OutboundBlockedError as e:
        raise HTTPException(status_code=400, detail=f"보안 정책으로 차단된 호출입니다: {e}")
    except (service.CredentialTargetError, service.CredentialDecryptError) as e:
        logger.warning("POST /apis/invoke 자격증명 오류: api=%s error=%s", req.api_name, type(e).__name__)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{name}", response_model=ApiDetailResponse)
async def get_api(name: str, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """API 상세. 로그인 사용자에게는 화면 버튼 노출용 ``permissions`` 를 함께 채운다."""
    logger.debug("GET /apis/%s", name)
    detail = await service.get_detail(session, name)
    if not detail:
        logger.warning("API 없음: %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    # 화면 버튼 노출용 권한 — 판단 규칙은 assert_owner_or_admin / AdminUser 가드와 동일
    if user:
        created_by = await service.get_api_created_by(session, name)
        detail.permissions = ApiPermissions(
            can_edit=user.is_admin or bool(created_by and created_by == user.username),
            can_admin=user.is_admin,
        )
    return detail


@router.patch("/{name}", response_model=ApiSummary)
async def update_api(name: str, req: ApiUpdate, current: CurrentUser, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """API 메타데이터 부분 갱신 — API 생성자 본인 또는 관리자."""
    logger.info("PATCH /apis/%s by=%s", name, current.username)
    changed_by = (user.username or user.email) if user else None
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    try:
        res = await service.update_api(session, name, req, changed_by=changed_by)
    except service.ApiInputError as e:
        logger.warning("PATCH /apis/%s 입력 오류: %s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not res:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.delete("/{name}")
async def delete_api(name: str, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """API 삭제 — API 생성자 본인 또는 관리자."""
    logger.info("DELETE /apis/%s by=%s", name, current.username)
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    if not await service.delete_api(session, name):
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok"}


@router.post("/{name}/specs", response_model=ApiSummary)
async def upload_spec(name: str, req: SpecUpload, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """스펙 새 버전 등록 — 개요 편집과 같은 기준(생성자 본인 또는 관리자)."""
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    if not (req.spec_text or req.spec_url):
        raise HTTPException(status_code=400, detail="스펙 원문 또는 URL을 입력하세요.")
    logger.info("POST /apis/%s/specs by=%s", name, current.username)
    try:
        res = await service.add_spec(session, name, req, created_by=current.username or current.email)
    except (SpecParseError, ValueError) as e:
        logger.warning("POST /apis/%s/specs 거부: %s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not res:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/endpoints", response_model=ApiEndpointResponse)
async def add_endpoint(name: str, req: EndpointCreate, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """수동(manual) API 에 엔드포인트 추가."""
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    try:
        res = await service.add_endpoint(session, name, req)
    except ValueError as e:
        logger.warning("POST /apis/%s/endpoints 거부: %s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.patch("/{name}/endpoints/{ep_id}", response_model=ApiEndpointResponse)
async def update_endpoint(name: str, ep_id: int, req: EndpointCreate, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """수동(manual) API 의 엔드포인트 수정."""
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    try:
        res = await service.update_endpoint(session, name, ep_id, req)
    except ValueError as e:
        logger.warning("PATCH /apis/%s/endpoints/%d 거부: %s", name, ep_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail="엔드포인트를 찾을 수 없습니다.")
    return res


@router.delete("/{name}/endpoints/{ep_id}")
async def delete_endpoint(name: str, ep_id: int, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    # 추가·수정과 동일하게 API 생성자 본인 또는 관리자
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    try:
        ok = await service.delete_endpoint(session, name, ep_id)
    except ValueError as e:
        logger.warning("DELETE /apis/%s/endpoints/%d 거부: %s", name, ep_id, e)
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
async def acknowledge_alert(current: CurrentUser, name: str, alert_id: int, session: AsyncSession = Depends(get_session)):
    """Breaking 알림 확인 처리(OPEN → ACKNOWLEDGED) — API 생성자 본인 또는 관리자."""
    assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    ok = await service.acknowledge_api_alert(session, name, alert_id, user=current.username or current.email)
    if not ok:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.get("/{name}/lint", response_model=ApiLintResponse)
async def lint(name: str, spec_id: int | None = Query(None), session: AsyncSession = Depends(get_session)):
    """스펙 품질 린팅(Spectral 스타일). spec_id 미지정 시 현재 스펙."""
    try:
        res = await service.lint_api(session, name, spec_id=spec_id)
    except service.SpecVersionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/usage", response_model=ApiUsageResponse)
async def usage(
    name: str, current: CurrentUser,
    days: int = Query(30, ge=1, le=365),
    mine: bool = Query(False, description="내 호출만"),
    endpoint_method: str | None = Query(None, max_length=20),
    endpoint_path: str | None = Query(None, max_length=2000),
    tz_offset: int = Query(0, ge=-840, le=840, description="일별 집계 시간대 — UTC 기준 분(KST=540)"),
    session: AsyncSession = Depends(get_session),
):
    """API 호출 사용량 집계(최근 N일) — 호출 URL·호출자가 담기므로 로그인 필요.
    다른 사람의 호출까지 보는 전체 집계(mine=false)는 /invocations 와 같이 API 생성자 본인 또는 관리자만."""
    if not mine:
        assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    res = await service.get_api_usage(
        session, name, days=days, caller=_user_key(current) if mine else None,
        endpoint_method=endpoint_method, endpoint_path=endpoint_path, tz_offset_min=tz_offset,
    )
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.get("/{name}/invocations", response_model=list[ApiInvocationResponse])
async def endpoint_invocations(
    name: str, current: CurrentUser, method: str = Query(...), path: str = Query(...),
    mine: bool = Query(True), limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """특정 엔드포인트의 최근 호출 이력(입력 파라미터 포함) — 로그인 필요.
    다른 사람의 호출(mine=false)은 입력값이 담기므로 API 생성자 본인 또는 관리자만."""
    if not mine:
        assert_owner_or_admin(current, await service.get_api_created_by(session, name), "API")
    res = await service.list_endpoint_invocations(session, name, method, path, user=_user_key(current), mine=mine, limit=limit)
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


@router.get("/{name}/lineage/incoming", response_model=list[IncomingLineageResponse])
async def list_incoming_lineage(name: str, session: AsyncSession = Depends(get_session)):
    """다른 API 가 이 API 를 대상으로 등록한 리니지 엣지(들어오는 관계)."""
    res = await service.list_api_incoming_lineage(session, name)
    if res is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return res


@router.post("/{name}/lineage", response_model=LineageResponse)
async def add_lineage(_guard: AdminUser, name: str, req: LineageCreate, user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """리니지 엣지 추가(관리자). 유형 오류·중복은 400."""
    try:
        res = await service.add_api_lineage(session, name, req, created_by=(user.username or user.email) if user else None)
    except ValueError as e:
        logger.warning("POST /apis/%s/lineage 거부: %s", name, e)
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
    """자격증명 저장(관리자). 시크릿은 서버에서 암호화하며 응답에 포함하지 않는다."""
    try:
        res = await service.add_credential(session, name, req, created_by=(user.username or user.email) if user else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
