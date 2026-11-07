# SPDX-License-Identifier: Apache-2.0
"""MCP Server 카탈로그 라우터.

- ``/api/v1/mcp-servers``     : 서버 정의 (도구 그룹·도구·리소스·프롬프트, 가져오기/내보내기, Forge 시드)
- ``/api/v1/mcp-deployments`` : 배포 인스턴스 (자격증명, 수집, 헬스, 설정 가져오기, 실효 상태, 리소스 범위)
  Phase 2: 승인 정책·finding·감사 이벤트·수집 토큰·manifest 가져오기

조회는 로그인 사용자 누구나(API 로그인 게이트 ``app.core.auth_gate`` 와 메뉴 권한 ``mcp-servers`` 가 공통 적용),
쓰기는 로그인 사용자, 수정·삭제는 소유자 또는 관리자, Forge 시드·미등록 MCP 연결은 관리자만.

승인 정책 변경은 배포 소유자·관리자 + 기능 권한 ``mcp.policy`` — 권한 매트릭스에서 해제한 역할은
자기 배포여도 불가, 설정이 없으면 배포 소유자·관리자 (매트릭스로 소유자 밖으로 넓어지지 않는다).
감사 이벤트 수집(POST)은 배포별 수집 토큰(``mcpi_…``) 또는 소유자·관리자 로그인.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser, assert_owner_or_admin, get_current_user
from app.core.database import get_session
from app.mcp_servers import audit, policy, service, versions
from app.mcp_servers import bundle as bundle_mod
from app.mcp_servers.origins import field_origins
from app.mcp_servers.schemas import (
    AdoptObservedRequest,
    AgentScopesUpdate,
    AuditIngestRequest,
    AuditIngestResult,
    CollectRequest,
    CollectResult,
    ConfigImportRequest,
    ControlOverview,
    CredentialPut,
    CredentialResponse,
    DeploymentAgentLink,
    DeploymentCreate,
    DeploymentDetail,
    DeploymentPolicy,
    DeploymentSummary,
    DeploymentUpdate,
    EffectiveState,
    FindingResponse,
    FindingUpdate,
    ForgeSeedResult,
    ImportRequest,
    ImportResult,
    IngestTokenResponse,
    ManifestImportRequest,
    McpServerCreate,
    McpServerDetail,
    McpServerSummary,
    McpServerUpdate,
    McpStats,
    PaginatedAuditEvents,
    PaginatedDeployments,
    PaginatedFindings,
    PaginatedMcpServers,
    PolicyHistoryResponse,
    PolicyUpdate,
    PromptCreate,
    PromptResponse,
    ResourceCreate,
    ResourceResponse,
    ResourceScopeCreate,
    ResourceScopeResponse,
    ServerVersionDetail,
    ServerVersionResponse,
    SimulateRequest,
    SimulateResult,
    StatusHistoryResponse,
    SyncLogResponse,
    ToolCreate,
    ToolResponse,
    ToolUpdate,
    UnregisteredMcp,
)
from app.permissions.router import require_feature, require_feature_granted

logger = logging.getLogger(__name__)

servers_router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])
deployments_router = APIRouter(prefix="/mcp-deployments", tags=["mcp-servers"])

_SERVER_404 = "MCP 서버 '{name}'을(를) 찾을 수 없습니다."
_DEP_404 = "MCP 배포 '{name}'을(를) 찾을 수 없습니다."


def _404(msg: str):
    """404 응답을 던진다(반환하지 않음)."""
    logger.warning("MCP API 대상 없음(404): %s", msg)
    raise HTTPException(status_code=404, detail=msg)


def _who(user) -> str:
    """감사·이력에 남길 사용자 식별자(username, 없으면 email)."""
    return user.username or user.email


async def _guard_server(current, session, name: str) -> None:
    """서버 정의가 없으면 404, 있으면 소유자(created_by)·관리자만 통과(아니면 403)."""
    created_by = await service.get_server_created_by(session, name)
    if created_by is None and not await service.server_exists(session, name):
        _404(_SERVER_404.format(name=name))
    assert_owner_or_admin(current, created_by, "MCP 서버")


async def _guard_deployment(current, session, name: str) -> None:
    """배포가 없으면 404, 있으면 소유자(created_by)·관리자만 통과(아니면 403)."""
    created_by = await service.get_deployment_created_by(session, name)
    if created_by is None and not await service.deployment_exists(session, name):
        _404(_DEP_404.format(name=name))
    assert_owner_or_admin(current, created_by, "MCP 배포")


POLICY_FEATURE = "mcp.policy"


async def _guard_policy(current, session, name: str) -> None:
    """승인 정책 변경 권한 — 배포 소유자·관리자이면서 기능 권한 ``mcp.policy`` 가 있어야 한다.

    기능 권한은 다른 기능과 같은 "잠그는" 의미다(미설정 = 모든 역할 허용, 해제한 역할은 자기 배포여도 불가).
    예전에는 설정이 있으면 소유자 확인 없이 역할만 봐서, 한 역할만 해제해 저장해도 남은 역할 전원이
    남의 배포 정책까지 바꿀 수 있게 되는(권한이 오히려 넓어지는) 문제가 있었다.
    """
    from app.permissions.router import check_feature

    await _guard_deployment(current, session, name)  # 404 / 소유자·관리자
    if not await check_feature(session, current, POLICY_FEATURE):
        logger.warning("MCP 승인 정책 변경 거부(기능 권한 없음): deployment=%s user=%s", name, _who(current))
        raise HTTPException(status_code=403, detail=f"기능 권한이 없습니다: {POLICY_FEATURE}")


# ===========================================================================
# 서버 정의
# ===========================================================================


@servers_router.get("/stats", response_model=McpStats)
async def get_stats(session: AsyncSession = Depends(get_session)):
    """대시보드 통계."""
    return await service.get_stats(session)


@servers_router.get("/field-origins")
async def get_field_origins():
    """필드 출처(MCP 표준 / Registry / 서버 설정 / Argus 전용) 레지스트리."""
    return field_origins()


@servers_router.get("/control-overview", response_model=ControlOverview)
async def control_overview(session: AsyncSession = Depends(get_session)):
    """통제 현황: 환경×서버 매트릭스, 위험 배포, finding 분포·추이, 최근 변경 호출, 정책 커버리지."""
    return await service.get_control_overview(session)


@servers_router.get("/findings", response_model=PaginatedFindings)
async def list_all_findings(
    status: str | None = Query("unresolved", description="open|acknowledged|resolved|unresolved|all"),
    severity: str | None = Query(None),
    kind: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """전체 배포의 finding (기본: 미해소)."""
    return await policy.list_findings(session, status=status, severity=severity, kind=kind, page=page,
                                      page_size=page_size)


@servers_router.get("/audit-events", response_model=PaginatedAuditEvents)
async def list_all_audit_events(
    current: CurrentUser,
    tool: str | None = Query(None),
    user: str | None = Query(None),
    decision: str | None = Query(None, description="allow|deny|blocked|unknown|violation"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """전체 배포의 감사 이벤트 (로그인 필요 — 호출 인자가 포함됨)."""
    return await audit.list_events(session, None, tool=tool, user=user, decision=decision, page=page,
                                   page_size=page_size)


@servers_router.get("/unregistered", response_model=list[UnregisteredMcp])
async def list_unregistered(session: AsyncSession = Depends(get_session)):
    """에이전트에 직접 입력된(카탈로그 미등록) MCP 연결 목록."""
    return await service.list_unregistered(session)


@servers_router.post("/import", response_model=ImportResult)
async def import_documents(current: CurrentUser, req: ImportRequest, _perm=Depends(require_feature("mcp.register")), session: AsyncSession = Depends(get_session)):
    """MCP Registry ``server.json`` 또는 클라이언트 ``mcp.json`` 가져오기."""
    logger.info("POST /mcp-servers/import: format=%s dry_run=%s", req.format, req.dry_run)
    try:
        return await service.import_documents(session, req, by=_who(current))
    except ValueError as e:
        logger.warning("MCP 가져오기 입력 오류(400): format=%s error=%s", req.format, e)
        raise HTTPException(status_code=400, detail=str(e))


@servers_router.post("/seed-forge", response_model=ForgeSeedResult)
async def seed_forge(current: AdminUser, session: AsyncSession = Depends(get_session)):
    """Argus Forge MCP 서버 정의 일괄 등록(관리자)."""
    logger.info("POST /mcp-servers/seed-forge")
    return await service.seed_forge(session, by=_who(current))


@servers_router.post("", response_model=McpServerSummary)
async def create_server(current: CurrentUser, req: McpServerCreate, _perm=Depends(require_feature("mcp.register")), session: AsyncSession = Depends(get_session)):
    logger.info("POST /mcp-servers: %s", req.name)
    try:
        return await service.create_server(session, req, created_by=_who(current))
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@servers_router.get("", response_model=PaginatedMcpServers)
async def list_servers(
    search: str | None = Query(None),
    status: str | None = Query(None),
    provider_type: str | None = Query(None),
    framework: str | None = Query(None),
    target_platform: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """서버 정의 목록(이름순) — 검색어는 이름·표시명·제목·설명·Registry 이름 부분 일치."""
    logger.debug("GET /mcp-servers: search=%s status=%s framework=%s page=%d", search, status, framework, page)
    return await service.list_servers(session, search=search, status=status, provider_type=provider_type,
                                      framework=framework, target_platform=target_platform, page=page,
                                      page_size=page_size)


@servers_router.get("/{name}", response_model=McpServerDetail)
async def get_server(name: str, session: AsyncSession = Depends(get_session)):
    detail = await service.get_server_detail(session, name)
    if not detail:
        _404(_SERVER_404.format(name=name))
    return detail


@servers_router.patch("/{name}", response_model=McpServerSummary)
async def update_server(current: CurrentUser, name: str, req: McpServerUpdate,
                        session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    result = await service.update_server(session, name, req, changed_by=_who(current))
    if not result:
        _404(_SERVER_404.format(name=name))
    return result


@servers_router.delete("/{name}")
async def delete_server(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """소프트 삭제(status=retired). retired 가 아닌 배포가 있으면 409."""
    await _guard_server(current, session, name)
    try:
        if not await service.retire_server(session, name, by=_who(current)):
            _404(_SERVER_404.format(name=name))
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "retired", "name": name}


@servers_router.get("/{name}/status-history", response_model=list[StatusHistoryResponse])
async def server_status_history(name: str, session: AsyncSession = Depends(get_session)):
    rows = await service.list_server_status_history(session, name)
    if rows is None:
        _404(_SERVER_404.format(name=name))
    return rows


@servers_router.get("/{name}/sync-log", response_model=list[SyncLogResponse])
async def server_sync_log(name: str, limit: int = Query(50, ge=1, le=500),
                          session: AsyncSession = Depends(get_session)):
    rows = await service.list_sync_log(session, server_name=name, limit=limit)
    if rows is None:
        _404(_SERVER_404.format(name=name))
    return rows


@servers_router.post("/{name}/manifest-import", response_model=CollectResult)
async def server_manifest_import(current: CurrentUser, name: str, req: ManifestImportRequest,
                                 session: AsyncSession = Depends(get_session)):
    """정적 manifest(릴리즈 산출물) 가져오기 — 정의(도구·그룹)만 갱신."""
    await _guard_server(current, session, name)
    try:
        result = await service.import_manifest(session, server_name=name, req=req, by=_who(current))
    except ValueError as e:
        logger.warning("MCP 서버 manifest 가져오기 입력 오류(400): server=%s error=%s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404(_SERVER_404.format(name=name))
    return result


@servers_router.get("/{name}/versions", response_model=list[ServerVersionResponse])
async def server_versions(name: str, session: AsyncSession = Depends(get_session)):
    """서버 정의(capability) 버전 이력 — 도구 추가·제거·설명 변경."""
    server = await service._get_server(session, name)
    if not server:
        _404(_SERVER_404.format(name=name))
    return await versions.list_versions(session, server.id)


@servers_router.get("/{name}/versions/{version_id}", response_model=ServerVersionDetail)
async def server_version(name: str, version_id: int, session: AsyncSession = Depends(get_session)):
    server = await service._get_server(session, name)
    if not server:
        _404(_SERVER_404.format(name=name))
    v = await versions.get_version(session, server.id, version_id)
    if not v:
        _404("버전을 찾을 수 없습니다.")
    return v


@servers_router.get("/{name}/server-json")
async def export_server_json(name: str, session: AsyncSession = Depends(get_session)):
    """MCP Registry ``server.json`` 내보내기 (Argus 전용 정보는 ``_meta`` 네임스페이스)."""
    doc = await service.export_server_json(session, name)
    if doc is None:
        _404(_SERVER_404.format(name=name))
    return doc


@servers_router.post("/{name}/tools", response_model=ToolResponse)
async def add_tool(current: CurrentUser, name: str, req: ToolCreate, session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    try:
        tool = await service.add_tool(session, name, req)
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not tool:
        _404(_SERVER_404.format(name=name))
    return tool


@servers_router.patch("/{name}/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(current: CurrentUser, name: str, tool_id: int, req: ToolUpdate,
                      session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    try:
        tool = await service.update_tool(session, name, tool_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not tool:
        _404("도구를 찾을 수 없습니다.")
    return tool


@servers_router.delete("/{name}/tools/{tool_id}")
async def delete_tool(current: CurrentUser, name: str, tool_id: int, session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    if not await service.delete_tool(session, name, tool_id):
        _404("도구를 찾을 수 없습니다.")
    return {"status": "deleted"}


@servers_router.post("/{name}/resources", response_model=ResourceResponse)
async def add_resource(current: CurrentUser, name: str, req: ResourceCreate,
                       session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    row = await service.add_resource(session, name, req)
    if not row:
        _404(_SERVER_404.format(name=name))
    return row


@servers_router.delete("/{name}/resources/{resource_id}")
async def delete_resource(current: CurrentUser, name: str, resource_id: int,
                          session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    if not await service.delete_resource(session, name, resource_id):
        _404("리소스를 찾을 수 없습니다.")
    return {"status": "deleted"}


@servers_router.post("/{name}/prompts", response_model=PromptResponse)
async def add_prompt(current: CurrentUser, name: str, req: PromptCreate, session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    row = await service.add_prompt(session, name, req)
    if not row:
        _404(_SERVER_404.format(name=name))
    return row


@servers_router.delete("/{name}/prompts/{prompt_id}")
async def delete_prompt(current: CurrentUser, name: str, prompt_id: int,
                        session: AsyncSession = Depends(get_session)):
    await _guard_server(current, session, name)
    if not await service.delete_prompt(session, name, prompt_id):
        _404("프롬프트를 찾을 수 없습니다.")
    return {"status": "deleted"}


# ===========================================================================
# 배포
# ===========================================================================


@deployments_router.post("", response_model=DeploymentSummary)
async def create_deployment(current: CurrentUser, req: DeploymentCreate, _perm=Depends(require_feature("mcp.register")), session: AsyncSession = Depends(get_session)):
    logger.info("POST /mcp-deployments: %s (server=%s)", req.name, req.server_name)
    try:
        return await service.create_deployment(session, req, created_by=_who(current))
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@deployments_router.get("", response_model=PaginatedDeployments)
async def list_deployments(
    search: str | None = Query(None),
    server: str | None = Query(None, description="서버 정의 이름"),
    environment: str | None = Query(None),
    status: str | None = Query(None),
    health_status: str | None = Query(None),
    mutating_enabled: bool | None = Query(None, description="변경 도구 그룹이 켜진 배포만(true)/제외(false)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_deployments(session, search=search, server=server, environment=environment,
                                          status=status, health_status=health_status,
                                          mutating_enabled=mutating_enabled, page=page, page_size=page_size)


@deployments_router.get("/{name}", response_model=DeploymentDetail)
async def get_deployment(name: str, session: AsyncSession = Depends(get_session)):
    detail = await service.get_deployment_detail(session, name)
    if not detail:
        _404(_DEP_404.format(name=name))
    return detail


@deployments_router.patch("/{name}", response_model=DeploymentSummary)
async def update_deployment(current: CurrentUser, name: str, req: DeploymentUpdate,
                            session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    try:
        result = await service.update_deployment(session, name, req, changed_by=_who(current))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.delete("/{name}")
async def delete_deployment(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """소프트 삭제(status=retired). 사용 중인 에이전트가 있으면 409."""
    await _guard_deployment(current, session, name)
    try:
        if not await service.retire_deployment(session, name, by=_who(current)):
            _404(_DEP_404.format(name=name))
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "retired", "name": name}


@deployments_router.get("/{name}/status-history", response_model=list[StatusHistoryResponse])
async def deployment_status_history(name: str, session: AsyncSession = Depends(get_session)):
    rows = await service.list_deployment_status_history(session, name)
    if rows is None:
        _404(_DEP_404.format(name=name))
    return rows


@deployments_router.get("/{name}/credential", response_model=CredentialResponse | None)
async def get_credential(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """자격증명 메타데이터(값은 마스킹)."""
    await _guard_deployment(current, session, name)
    return await service.get_credential(session, name)


@deployments_router.put("/{name}/credential", response_model=CredentialResponse)
async def put_credential(current: CurrentUser, name: str, req: CredentialPut,
                         _perm=Depends(require_feature_granted("mcp.credential")), session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    result = await service.put_credential(session, name, req, by=_who(current))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.delete("/{name}/credential")
async def delete_credential(current: CurrentUser, name: str, _perm=Depends(require_feature_granted("mcp.credential")), session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    if not await service.delete_credential(session, name):
        _404("자격증명이 없습니다.")
    return {"status": "deleted"}


@deployments_router.post("/{name}/collect", response_model=CollectResult)
async def collect(current: CurrentUser, name: str, req: CollectRequest = Body(default_factory=CollectRequest),
                  session: AsyncSession = Depends(get_session)):
    """도구·리소스·프롬프트 수집 (source=auto 면 Forge 는 manifest → 없으면 도구 REST, 그 밖은 MCP 프로토콜)."""
    await _guard_deployment(current, session, name)
    logger.info("POST /mcp-deployments/%s/collect: source=%s dry_run=%s", name, req.source, req.dry_run)
    result = await service.collect_deployment(session, name, req, by=_who(current))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/health-check", response_model=DeploymentSummary)
async def health_check(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    result = await service.health_check(session, name, by=_who(current))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/config-import", response_model=CollectResult)
async def config_import(current: CurrentUser, name: str, req: ConfigImportRequest,
                        session: AsyncSession = Depends(get_session)):
    """Forge 서버 config.yaml 가져오기 (그룹 on/off·리소스 범위·limits·실행 계정)."""
    await _guard_deployment(current, session, name)
    try:
        result = await service.import_config(session, name, req, by=_who(current))
    except ValueError as e:
        logger.warning("MCP 설정 가져오기 입력 오류(400): deployment=%s error=%s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/manifest-import", response_model=CollectResult)
async def deployment_manifest_import(current: CurrentUser, name: str, req: ManifestImportRequest,
                                     session: AsyncSession = Depends(get_session)):
    """배포 manifest 가져오기 — 정의 + 노출·그룹·범위·limits·보안 경고 관측값."""
    await _guard_deployment(current, session, name)
    try:
        result = await service.import_manifest(session, deployment_name=name, req=req, by=_who(current))
    except ValueError as e:
        logger.warning("MCP 배포 manifest 가져오기 입력 오류(400): deployment=%s error=%s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.get("/{name}/sync-log", response_model=list[SyncLogResponse])
async def deployment_sync_log(name: str, limit: int = Query(50, ge=1, le=500),
                              session: AsyncSession = Depends(get_session)):
    rows = await service.list_sync_log(session, deployment_name=name, limit=limit)
    if rows is None:
        _404(_DEP_404.format(name=name))
    return rows


@deployments_router.get("/{name}/effective", response_model=EffectiveState)
async def effective(name: str, session: AsyncSession = Depends(get_session)):
    """실효 상태: 그룹 on/off, 도구 노출, 리소스 범위, limits, 실행 계정."""
    result = await service.get_effective(session, name)
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/scopes", response_model=ResourceScopeResponse)
async def add_scope(current: CurrentUser, name: str, req: ResourceScopeCreate,
                    session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    try:
        row = await service.add_scope(session, name, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not row:
        _404(_DEP_404.format(name=name))
    return row


@deployments_router.delete("/{name}/scopes/{scope_id}")
async def delete_scope(current: CurrentUser, name: str, scope_id: int, session: AsyncSession = Depends(get_session)):
    await _guard_deployment(current, session, name)
    try:
        deleted = await service.delete_scope(session, name, scope_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        _404("리소스 범위 규칙을 찾을 수 없습니다.")
    return {"status": "deleted"}


@deployments_router.post("/{name}/simulate", response_model=SimulateResult)
async def simulate(name: str, req: SimulateRequest, session: AsyncSession = Depends(get_session)):
    """리소스 접근 시뮬레이터 — layer: observed(서버 설정) / approved(승인 정책) / combined(둘 다)."""
    result = await service.simulate(session, name, req)
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.get("/{name}/agents", response_model=list[DeploymentAgentLink])
async def deployment_agents(name: str, session: AsyncSession = Depends(get_session)):
    rows = await service.list_deployment_agents(session, name)
    if rows is None:
        _404(_DEP_404.format(name=name))
    return rows


@deployments_router.put("/{name}/agents/{link_id}/scopes", response_model=list[ResourceScopeResponse])
async def put_agent_scopes(current: CurrentUser, name: str, link_id: int, req: AgentScopesUpdate,
                           session: AsyncSession = Depends(get_session)):
    """에이전트별 리소스 범위 저장(목록 전체 교체) — 승인 정책과 같은 권한. 정책 번들 agents.*.resource_scopes 로 실린다."""
    await _guard_policy(current, session, name)
    logger.info("PUT /mcp-deployments/%s/agents/%s/scopes by=%s", name, link_id, _who(current))
    try:
        rows = await service.put_agent_scopes(session, name, link_id, req.scopes, by=_who(current))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rows is None:
        _404("이 배포에 연결된 에이전트를 찾을 수 없습니다.")
    return rows


@deployments_router.post("/{name}/link-unregistered")
async def link_unregistered(current: AdminUser, name: str, link_ids: list[int] = Body(..., embed=True),
                            session: AsyncSession = Depends(get_session)):
    """미등록 MCP 연결(에이전트 직접 입력)을 이 배포로 연결(관리자)."""
    count = await service.link_unregistered(session, name, link_ids)
    if count is None:
        _404(_DEP_404.format(name=name))
    return {"linked": count}


# ===========================================================================
# Phase 2 — 승인 정책
# ===========================================================================


@deployments_router.get("/{name}/policy", response_model=DeploymentPolicy)
async def get_policy(name: str, session: AsyncSession = Depends(get_session)):
    """승인 정책 + 관측값 (그룹·도구·리소스 범위·limits·버전)."""
    result = await policy.get_policy(session, name)
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.put("/{name}/policy", response_model=DeploymentPolicy)
async def put_policy(current: CurrentUser, name: str, req: PolicyUpdate, session: AsyncSession = Depends(get_session)):
    """승인 정책 저장 — 보낸 항목만 교체(scopes 는 목록 전체 교체). 새 policy_version 과 이력이 남는다."""
    await _guard_policy(current, session, name)
    logger.info("PUT /mcp-deployments/%s/policy by=%s", name, _who(current))
    try:
        result = await policy.save_policy(session, name, req, by=_who(current))
    except ValueError as e:
        logger.warning("MCP 승인 정책 저장 입력 오류(400): deployment=%s error=%s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/policy/adopt-observed", response_model=DeploymentPolicy)
async def adopt_observed(current: CurrentUser, name: str,
                         req: AdoptObservedRequest = Body(default_factory=AdoptObservedRequest),
                         session: AsyncSession = Depends(get_session)):
    """현재 관측값을 승인 기준선으로 채택."""
    await _guard_policy(current, session, name)
    logger.info("POST /mcp-deployments/%s/policy/adopt-observed by=%s", name, _who(current))
    result = await policy.adopt_observed(session, name, req, by=_who(current))
    if not result:
        _404(_DEP_404.format(name=name))
    return result


def _service_token(request: Request) -> str | None:
    """배포 서비스 토큰 (``X-Argus-Ingest-Token`` 또는 ``Authorization: Bearer mcpi_…``).

    Bearer 값은 ``mcpi_`` 로 시작할 때만 서비스 토큰으로 본다(사용자 JWT 와 구분). 없으면 None.
    """
    header_token = request.headers.get("X-Argus-Ingest-Token")
    auth = request.headers.get("Authorization") or ""
    bearer = auth.split(None, 1)[1].strip() if auth.lower().startswith("bearer ") else None
    return header_token or (bearer if bearer and bearer.startswith(audit.TOKEN_PREFIX) else None)


@deployments_router.get("/{name}/policy-bundle")
async def get_policy_bundle(name: str, request: Request, session: AsyncSession = Depends(get_session)):
    """정책 번들 (Phase 3) — MCP 서버가 서비스 토큰으로 주기적으로 pull 해 집행한다.

    ``If-None-Match`` 가 현재 ETag 와 같으면 304. 서버는 ``X-Argus-Instance-Id`` 로 자기 instance_id 를 알린다.
    서비스 토큰 없이 부르면 소유자·관리자 로그인으로 미리보기만 하고 pull 기록은 남기지 않는다.
    """
    dep = await service._get_deployment(session, name)
    token = _service_token(request)
    if token:
        if not dep or not audit.verify_token(dep, token):
            logger.warning("정책 번들 요청 거부(토큰 불일치): %s", name)
            raise HTTPException(status_code=401, detail="서비스 토큰이 올바르지 않습니다.")
    else:
        current = await get_current_user(request)
        await _guard_deployment(current, session, name)
    if not dep:
        _404(_DEP_404.format(name=name))
    body = await bundle_mod.build_bundle(session, dep)
    etag = bundle_mod.bundle_etag(body)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if token:
        before = bundle_mod.pull_state(dep, etag)
        bundle_mod.record_pull(dep, etag=etag, instance_id=request.headers.get("X-Argus-Instance-Id"))
        await session.flush()
        if dep.policy_mode == "enforce" and before != "applied":
            # policy_not_applied finding 자동 해소
            await policy.evaluate_findings(session, dep)
        await session.commit()
    if bundle_mod.etag_matches(request.headers.get("If-None-Match"), etag):
        logger.debug("정책 번들 변경 없음(304): deployment=%s etag=%s", name, etag)
        return Response(status_code=304, headers=headers)
    logger.debug("정책 번들 응답: deployment=%s etag=%s via=%s", name, etag, "token" if token else "preview")
    return JSONResponse(body, headers=headers)


@deployments_router.get("/{name}/policy/history", response_model=list[PolicyHistoryResponse])
async def policy_history(name: str, session: AsyncSession = Depends(get_session)):
    rows = await policy.list_policy_history(session, name)
    if rows is None:
        _404(_DEP_404.format(name=name))
    return rows


# ===========================================================================
# Phase 2 — finding
# ===========================================================================


@deployments_router.get("/{name}/findings", response_model=PaginatedFindings)
async def list_findings(
    name: str,
    status: str | None = Query(None, description="open|acknowledged|resolved|unresolved"),
    severity: str | None = Query(None),
    kind: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    result = await policy.list_findings(session, deployment_name=name, status=status, severity=severity,
                                        kind=kind, page=page, page_size=page_size)
    if result is None:
        _404(_DEP_404.format(name=name))
    return result


@deployments_router.post("/{name}/findings/evaluate", response_model=PaginatedFindings)
async def evaluate_findings(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """finding 즉시 재계산."""
    await _guard_deployment(current, session, name)
    logger.info("POST /mcp-deployments/%s/findings/evaluate by=%s", name, _who(current))
    dep = await service._get_deployment(session, name)
    await policy.evaluate_findings(session, dep)
    await session.commit()
    return await policy.list_findings(session, deployment_name=name, status="unresolved", page_size=200)


@deployments_router.patch("/{name}/findings/{finding_id}", response_model=FindingResponse)
async def update_finding(current: CurrentUser, name: str, finding_id: int, req: FindingUpdate,
                         session: AsyncSession = Depends(get_session)):
    """finding 확인(acknowledged) / 다시 열기(open). 해소는 조건이 사라지면 자동."""
    await _guard_deployment(current, session, name)
    try:
        result = await policy.update_finding(session, name, finding_id, req, by=_who(current))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        _404("finding 을 찾을 수 없습니다.")
    return result


# ===========================================================================
# Phase 2 — 감사 이벤트 / 수집 토큰
# ===========================================================================


@deployments_router.post("/{name}/audit-events", response_model=AuditIngestResult)
async def ingest_audit_events(name: str, req: AuditIngestRequest, request: Request,
                              session: AsyncSession = Depends(get_session)):
    """감사 이벤트 수집 — 서버 shipper 는 수집 토큰, 화면 업로드는 소유자·관리자 로그인."""
    dep = await service._get_deployment(session, name)
    token = _service_token(request)
    if token:
        if not dep or not audit.verify_token(dep, token):
            logger.warning("감사 이벤트 수집 거부(토큰 불일치): %s", name)
            raise HTTPException(status_code=401, detail="수집 토큰이 올바르지 않습니다.")
        via, by = "token", None
    else:
        current = await get_current_user(request)
        await _guard_deployment(current, session, name)
        dep = dep or await service._get_deployment(session, name)
        via, by = "upload", _who(current)
    try:
        result = await audit.ingest(session, dep, req, via=via)
    except ValueError as e:
        logger.warning("감사 이벤트 수집 입력 오류(400): deployment=%s via=%s error=%s", name, via, e)
        raise HTTPException(status_code=400, detail=str(e))
    if by:
        logger.info("감사 이벤트 업로드: %s by=%s inserted=%d", name, by, result.inserted)
    return result


@deployments_router.get("/{name}/audit-events", response_model=PaginatedAuditEvents)
async def list_audit_events(
    current: CurrentUser,
    name: str,
    tool: str | None = Query(None),
    user: str | None = Query(None),
    ok: bool | None = Query(None),
    decision: str | None = Query(None, description="allow|deny|blocked|unknown|violation"),
    resource: str | None = Query(None),
    since: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """감사 이벤트 조회 (로그인 필요 — 호출 인자가 포함됨)."""
    dep = await service._get_deployment(session, name)
    if not dep:
        _404(_DEP_404.format(name=name))
    return await audit.list_events(session, dep, tool=tool, user=user, ok=ok, decision=decision,
                                   resource=resource, since=since, page=page, page_size=page_size)


@deployments_router.get("/{name}/ingest-token", response_model=IngestTokenResponse)
async def get_ingest_token(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """수집 토큰 메타데이터 (원문은 발급 시에만)."""
    await _guard_deployment(current, session, name)
    dep = await service._get_deployment(session, name)
    return IngestTokenResponse(hint=dep.ingest_token_hint, created_at=dep.ingest_token_created_at,
                               ingest_url=f"/api/v1/mcp-deployments/{dep.name}/audit-events")


@deployments_router.post("/{name}/ingest-token", response_model=IngestTokenResponse)
async def issue_ingest_token(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """수집 토큰 발급(또는 재발급 — 이전 토큰은 즉시 무효)."""
    await _guard_deployment(current, session, name)
    dep = await service._get_deployment(session, name)
    return await audit.issue_token(session, dep, by=_who(current))


@deployments_router.delete("/{name}/ingest-token")
async def revoke_ingest_token(current: CurrentUser, name: str, session: AsyncSession = Depends(get_session)):
    """수집 토큰 폐기 — 이후 이 토큰으로 수집·번들 요청은 401."""
    await _guard_deployment(current, session, name)
    dep = await service._get_deployment(session, name)
    await audit.revoke_token(session, dep)
    return {"status": "revoked"}
