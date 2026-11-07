# SPDX-License-Identifier: Apache-2.0
"""AI Agent 카탈로그 라우터.

``/api/v1/ai-agents`` 경로에서 에이전트의 CRUD, 대시보드 통계, 상세,
도구/MCP/리니지/버전 서브리소스를 제공한다. 그 밖에 정의 파일 일괄 반영(apply/prune),
에이전트 카드 가져오기, 일괄 변경, 저장된 보기, 미등록 에이전트 발견, 평가·미터링·A2A 카드,
실행 계층 연동(정책 번들 pull·훅 이벤트 push)을 제공한다. 라우터는 서비스 함수만
호출하고 HTTP 변환(상태 코드/예외)만 담당한다.

고정 경로(``/stats``·``/apply``·``/bulk``·``/views``·``/discoveries`` 등)는 ``/{name}`` 보다 먼저 선언해야
경로 매칭에서 가로채이지 않는다. 쓰기 권한은 ``_assert_agent_editor`` (관리자·등록자·소유자) 기준이다.
"""

import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import bulk, card_import, discovery, manifest, service, views
from app.agents.models import AIAgent
from app.agents.schemas import (
    AIAgentCard,
    AIAgentCardImportRequest,
    AIAgentCardImportResponse,
    AIAgentCreate,
    AIAgentDetailResponse,
    AIAgentDiscoveryRegister,
    AIAgentDiscoveryRegisterResponse,
    AIAgentDiscoveryResponse,
    AIAgentEvalCreate,
    AIAgentEvalResponse,
    AIAgentInvocationIngest,
    AIAgentLineageCreate,
    AIAgentLineageResponse,
    AIAgentMcpServerCreate,
    AIAgentMcpServerResponse,
    AIAgentMcpServerUpdate,
    AIAgentMeteringResponse,
    AIAgentPolicyBundle,
    AIAgentStats,
    AIAgentStatusHistoryResponse,
    AIAgentSummary,
    AIAgentToolCreate,
    AIAgentToolResponse,
    AIAgentUpdate,
    AIAgentVersionCreate,
    AIAgentVersionResponse,
    HookEventIngest,
    HookEventResponse,
    PaginatedAIAgentDiscoveries,
    PaginatedAIAgents,
)
from app.core.auth import AdminUser, CurrentUser, OptionalUser, TokenUser
from app.core.database import get_session
from app.permissions.router import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-agents", tags=["ai-agents"])

_NOT_FOUND = "AI Agent '{name}'을(를) 찾을 수 없습니다."


async def _assert_agent_editor(session: AsyncSession, name: str, current: TokenUser):
    """에이전트 쓰기 가드 — 관리자·등록자·소유자(owner_email 이 로그인 이메일과 같음)면 통과.

    정보 수정·삭제와 도구·MCP·리니지·버전·평가 같은 하위 항목 변경이 모두 같은 기준을 쓴다
    (일괄 처리·정의 파일 반영과도 같다 — ``manifest._can_edit``). 없으면 404, 권한이 없으면 403.
    """
    agent = (await session.execute(select(AIAgent).where(AIAgent.name == name))).scalars().first()
    if agent is None:
        logger.warning("AI Agent 없음(쓰기): %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    if not manifest._can_edit(agent, current):
        logger.warning("AI Agent 쓰기 거부: %s (요청자 %s)", name, current.username)
        raise HTTPException(status_code=403, detail="에이전트의 등록자·소유자 또는 관리자만 수행할 수 있습니다.")
    return agent


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AIAgentStats)
async def get_ai_agent_stats(session: AsyncSession = Depends(get_session)):
    """대시보드 통계(상태/프레임워크/카테고리 분포, 멀티에이전트/HITL 건수)."""
    logger.info("GET /ai-agents/stats")
    return await service.get_stats(session)


# ---------------------------------------------------------------------------
# AIAgent CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=AIAgentSummary)
async def create_ai_agent(current: CurrentUser,
    req: AIAgentCreate,
    _perm=Depends(require_feature("agents.register")), session: AsyncSession = Depends(get_session),
):
    """새 에이전트를 등록한다(이름 중복 시 409). 소유자 이메일·부서·분류는 필수(없으면 422)."""
    logger.info("POST /ai-agents: name=%s", req.name)
    problem = discovery.missing_required(req.owner_email, req.department, req.category)
    if problem:
        logger.warning("POST /ai-agents 필수 항목 누락: name=%s reason=%s", req.name, problem)
        raise HTTPException(status_code=422, detail=problem)
    try:
        result = await service.create_agent(session, req, created_by=current.username)
        logger.info("AI Agent 생성됨: %s (id=%d)", result.name, result.id)
        return result
    except ValueError as e:
        logger.warning("POST /ai-agents 충돌: %s", e)
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# 정의 파일 일괄 반영 — design/ai-agent-bulk-registration-design.md
# ---------------------------------------------------------------------------

_MAX_APPLY_BYTES = 5 * 1024 * 1024


@router.post("/apply")
async def apply_ai_agent_manifests(
    current: CurrentUser,
    request: Request,
    dry_run: bool = Query(False, description="true 면 저장하지 않고 바뀔 내용만 반환"),
    source_ref: str | None = Query(None, max_length=1000, description="정의 파일 위치(저장소·경로)"),
    _perm=Depends(require_feature("agents.register")), session: AsyncSession = Depends(get_session),
):
    """정의 파일(agent.yaml) 여러 개를 한 번에 생성·갱신한다(멱등).

    본문: YAML 원문(``---`` 로 여러 문서) 또는 JSON ``{"agents": [...]}``. 최대 200개.
    에이전트마다 독립 처리되며, 실패한 것은 결과에 사유와 함께 남는다(HTTP 200).
    """
    raw = await request.body()
    if len(raw) > _MAX_APPLY_BYTES:
        logger.warning("POST /ai-agents/apply 거부(본문 초과): size=%d by=%s", len(raw), current.username)
        raise HTTPException(status_code=413, detail="요청 본문이 너무 큽니다(최대 5MB).")
    # Content-Type 이 application/json 이면 JSON, 그 밖(YAML·text 등)은 YAML 원문으로 해석한다
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype == "application/json":
            try:
                body = json.loads(raw or b"null")
            except ValueError as e:
                raise manifest.ManifestError(f"JSON 을 해석할 수 없습니다: {e}") from e
            docs = manifest.parse_documents(body)
        else:
            docs = manifest.parse_documents(raw)
    except manifest.ManifestError as e:
        logger.warning("POST /ai-agents/apply 형식 오류: %s (by=%s)", e, current.username)
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("POST /ai-agents/apply: %d건 dry_run=%s source=%s", len(docs), dry_run, source_ref)
    return await manifest.apply_manifests(session, docs, current, dry_run=dry_run, source_ref=source_ref)


@router.post("/prune")
async def prune_ai_agent_manifests(
    current: CurrentUser,
    source_prefix: str = Body(..., max_length=1000),
    keep: list[str] = Body(default_factory=list),
    dry_run: bool = Body(False),
    session: AsyncSession = Depends(get_session),
):
    """정의 파일에서 빠진 에이전트를 사용 중단(deprecated)으로 바꾼다(삭제하지 않음).

    ``source_ref`` 가 ``source_prefix`` 로 시작하는 정의 파일 관리 에이전트 중 ``keep`` 에 없는 것이 대상.
    """
    logger.info("POST /ai-agents/prune: prefix=%s keep=%d dry_run=%s", source_prefix, len(keep), dry_run)
    try:
        return await manifest.prune_manifests(session, source_prefix, keep, current, dry_run=dry_run)
    except manifest.ManifestError as e:
        logger.warning("POST /ai-agents/prune 거부: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import/card", response_model=AIAgentCardImportResponse)
async def import_ai_agent_card(
    current: CurrentUser,
    body: AIAgentCardImportRequest,
    _perm=Depends(require_feature("agents.register")), session: AsyncSession = Depends(get_session),
):
    """외부 에이전트의 소개 정보(에이전트 카드)를 읽어 정의 내용 미리 보기를 돌려준다(저장하지 않음).

    ``<url>/.well-known/agent-card.json``·``agent.json`` (또는 ``.json`` 주소 그대로)을 서버가 읽는다.
    공용 외부 호출 정책(루프백·메타데이터·내부 인프라 차단, ``api_outbound.*``)과 크기·시간 제한을 따른다.
    차단·입력 오류 400, 상대 서버 문제(없음·오류·형식) 502.
    """
    logger.info("POST /ai-agents/import/card: url=%s by=%s", body.url, current.username)
    try:
        return await card_import.import_card(
            session, body.url, name=body.name, owner=body.owner, department=body.department, category=body.category,
        )
    except card_import.CardImportError as e:
        logger.warning("POST /ai-agents/import/card 실패: status=%d reason=%s", e.status, e)
        raise HTTPException(status_code=e.status, detail=str(e))


# ---------------------------------------------------------------------------
# 분류와 책임 — 일괄 변경 · 저장된 보기 ('/{name}' 경로보다 먼저 선언)
# 설계: design/ai-agent-ownership-design.md
# ---------------------------------------------------------------------------


@router.post("/bulk")
async def bulk_update_ai_agents(current: CurrentUser, req: bulk.BulkRequest,
                                session: AsyncSession = Depends(get_session)):
    """여러 에이전트(최대 200개)의 상태·소유자·부서·분류·태그를 한 번에 바꾼다. 에이전트마다 독립 처리(HTTP 200)."""
    logger.info("POST /ai-agents/bulk: %d건 dry_run=%s by=%s", len(req.names), req.dry_run, current.username)
    return await bulk.apply_bulk(session, req, current)


@router.get("/views", response_model=list[views.ViewResponse])
async def list_ai_agent_views(user: OptionalUser, session: AsyncSession = Depends(get_session)):
    """저장된 보기 — 기본 보기, 내 보기, 다른 사람의 공유 보기."""
    logger.debug("GET /ai-agents/views")
    return await views.list_views(session, user)


@router.post("/views", response_model=views.ViewResponse, status_code=201)
async def create_ai_agent_view(current: CurrentUser, req: views.ViewSave,
                               session: AsyncSession = Depends(get_session)):
    """저장된 보기 만들기(한 사람당 최대 50개, 초과 시 400)."""
    logger.info("POST /ai-agents/views: %s shared=%s by=%s", req.name, req.shared, current.username)
    try:
        return await views.create_view(session, req, current)
    except views.ViewError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.put("/views/{view_id}", response_model=views.ViewResponse)
async def update_ai_agent_view(current: CurrentUser, view_id: int, req: views.ViewSave,
                               session: AsyncSession = Depends(get_session)):
    """저장된 보기 수정 — 만든 사람, 또는 공유 보기면 관리자도(남의 개인 보기는 404)."""
    logger.info("PUT /ai-agents/views/%d by=%s", view_id, current.username)
    try:
        return await views.update_view(session, view_id, req, current)
    except views.ViewError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.delete("/views/{view_id}", status_code=204)
async def delete_ai_agent_view(current: CurrentUser, view_id: int, session: AsyncSession = Depends(get_session)):
    """저장된 보기 삭제 — 수정과 같은 권한 기준."""
    logger.info("DELETE /ai-agents/views/%d by=%s", view_id, current.username)
    try:
        await views.delete_view(session, view_id, current)
    except views.ViewError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 미등록 에이전트 발견 — 실행 기록에 나왔지만 카탈로그에 없는 에이전트
# ('/{name}' 경로보다 먼저 선언해야 한다)
# ---------------------------------------------------------------------------


@router.get("/discoveries", response_model=PaginatedAIAgentDiscoveries)
async def list_ai_agent_discoveries(
    _current: CurrentUser,
    status: str | None = Query("new", description="new / ignored / registered (빈 값이면 전체)"),
    search: str | None = Query(None, description="이름 검색"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """미등록 에이전트 목록(최근 발견 순)."""
    if status and status not in discovery.STATUSES:
        logger.warning("GET /ai-agents/discoveries 잘못된 status: %s", status)
        raise HTTPException(status_code=400, detail=f"status 는 {', '.join(discovery.STATUSES)} 중 하나여야 합니다.")
    logger.debug("GET /ai-agents/discoveries: status=%s search=%s page=%d", status, search, page)
    return await discovery.list_discoveries(session, status or None, search, page, page_size)


@router.post("/discoveries/{discovery_id}/register", response_model=AIAgentDiscoveryRegisterResponse)
async def register_ai_agent_discovery(
    current: CurrentUser, discovery_id: int, req: AIAgentDiscoveryRegister | None = None,
    _perm=Depends(require_feature("agents.register")), session: AsyncSession = Depends(get_session),
):
    """발견된 이름으로 초안(draft) 에이전트를 만든다. 같은 이름이 이미 있으면 연결만 한다.

    새로 만들 때 소유자 이메일·부서·분류가 필수(화면 등록과 같음, 없으면 422). 소유자·부서는 비우면 요청자 값.
    """
    logger.info("POST /ai-agents/discoveries/%d/register", discovery_id)
    try:
        result = await discovery.register(session, discovery_id, current, req)
    except discovery.DiscoveryStateError as e:
        logger.warning("발견 기록 등록 충돌: id=%d reason=%s", discovery_id, e)
        raise HTTPException(status_code=409, detail=str(e))
    except discovery.RequiredFieldsError as e:
        logger.warning("발견 기록 등록 필수 항목 누락: id=%d reason=%s", discovery_id, e)
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="발견 기록을 찾을 수 없습니다.")
    row, agent = result
    item = AIAgentDiscoveryResponse.model_validate(row)
    item.agent_name = agent.name
    return AIAgentDiscoveryRegisterResponse(discovery=item, agent=agent)


async def _set_discovery_status(session, discovery_id: int, status: str, user) -> AIAgentDiscoveryResponse:
    """발견 기록 상태 변경 공통 처리 — 없으면 404, 이미 등록된 기록이면 409."""
    try:
        row = await discovery.set_status(session, discovery_id, status, user)
    except discovery.DiscoveryStateError as e:
        logger.warning("발견 기록 상태 변경 충돌: id=%d status=%s reason=%s", discovery_id, status, e)
        raise HTTPException(status_code=409, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="발견 기록을 찾을 수 없습니다.")
    return AIAgentDiscoveryResponse.model_validate(row)


@router.post("/discoveries/{discovery_id}/ignore", response_model=AIAgentDiscoveryResponse)
async def ignore_ai_agent_discovery(current: AdminUser, discovery_id: int, session: AsyncSession = Depends(get_session)):
    """무시(관리자) — 목록 기본 보기에서 빠진다. 같은 이름이 다시 들어와도 무시 상태를 유지한다."""
    logger.info("POST /ai-agents/discoveries/%d/ignore", discovery_id)
    return await _set_discovery_status(session, discovery_id, "ignored", current)


@router.post("/discoveries/{discovery_id}/reopen", response_model=AIAgentDiscoveryResponse)
async def reopen_ai_agent_discovery(current: AdminUser, discovery_id: int, session: AsyncSession = Depends(get_session)):
    """무시를 되돌린다(관리자)."""
    logger.info("POST /ai-agents/discoveries/%d/reopen", discovery_id)
    return await _set_discovery_status(session, discovery_id, "new", current)


@router.get("", response_model=PaginatedAIAgents)
async def list_ai_agents(
    user: OptionalUser,
    search: str | None = Query(None, description="이름/표시명/설명 검색"),
    status: str | None = Query(None, description="상태 필터"),
    framework: str | None = Query(None, description="프레임워크 필터"),
    category: str | None = Query(None, description="카테고리 필터"),
    department: str | None = Query(None, description="부서(여러 개는 쉼표)"),
    owner: str | None = Query(None, description="소유자 이메일. 'me' 는 로그인 사용자"),
    tag: str | None = Query(None, description="태그(여러 개는 쉼표, 모두 포함)"),
    risk: Literal["low", "medium", "high", "critical"] | None = Query(None, description="최고 도구 위험도 이 값 이상"),
    managed_by: str | None = Query(None, description="관리 방식 ui/manifest/discovered(쉼표)"),
    owner_state: str | None = Query(
        None, pattern=r"^(attention|(ok|missing|unknown|inactive)(,(ok|missing|unknown|inactive))*)$",
        description="책임자 상태 attention(확인 필요) 또는 ok/missing/unknown/inactive(쉼표)",
    ),
    incomplete: bool = Query(False, description="정보 완성도 100% 미만만"),
    sort: str | None = Query(
        None, pattern=r"^-?(updated|name|usage|last_invoked|completeness|risk)$",
        description="정렬 updated(기본)/name/usage/last_invoked/completeness/risk, '-' 는 반대 방향",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """에이전트 목록 — 검색·필터·정렬·페이지. 항목마다 책임자 상태·정보 완성도·최고 위험도를 함께 돌려준다."""
    # owner=me 는 로그인 사용자의 이메일로 바꿔 서비스에 넘긴다
    if owner and owner.lower() == "me":
        if user is None:
            raise HTTPException(status_code=401, detail="owner=me 는 로그인이 필요합니다.")
        if not user.email:
            raise HTTPException(status_code=400, detail="로그인 계정에 이메일이 없어 owner=me 를 쓸 수 없습니다.")
        owner = user.email
    logger.info("GET /ai-agents: search=%s status=%s dept=%s owner=%s tag=%s risk=%s owner_state=%s sort=%s page=%d",
                search, status, department, owner, tag, risk, owner_state, sort, page)
    return await service.list_agents(
        session, search=search, status=status, framework=framework, category=category, page=page,
        page_size=page_size, department=department, owner=owner, tag=tag, risk=risk, managed_by=managed_by,
        owner_state=owner_state, incomplete=incomplete, sort=sort,
    )


@router.get("/{name}/manifest")
async def export_ai_agent_manifest(name: str, session: AsyncSession = Depends(get_session)):
    """현재 내용을 정의 파일(agent.yaml) 형식으로 내보낸다. 그대로 반영하면 '변경 없음'."""
    logger.info("GET /ai-agents/%s/manifest", name)
    doc = await manifest.export_manifest(session, name)
    if doc is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return PlainTextResponse(manifest.dump_yaml(doc), media_type="application/yaml; charset=utf-8")


@router.get("/{name}", response_model=AIAgentDetailResponse)
async def get_ai_agent(name: str, session: AsyncSession = Depends(get_session)):
    """에이전트 상세(전체 메타데이터 + 도구/MCP/리니지/버전)."""
    logger.info("GET /ai-agents/%s", name)
    detail = await service.get_agent_detail(session, name)
    if not detail:
        logger.warning("AI Agent 없음: %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return detail


@router.patch("/{name}", response_model=AIAgentSummary)
async def update_ai_agent(current: CurrentUser,
    name: str,
    req: AIAgentUpdate,
    user: OptionalUser,
    session: AsyncSession = Depends(get_session),
):
    """에이전트 메타데이터 부분 갱신 — 관리자·등록자·소유자. ``blocked`` 설정·해제는 관리자만(403)."""
    logger.info("PATCH /ai-agents/%s", name)
    changed_by = (user.username or user.email) if user else None
    agent = await _assert_agent_editor(session, name, current)
    want = req.status.value if req.status else None
    if want and "blocked" in (want, agent.status) and want != agent.status and not current.is_admin:
        logger.warning("AI Agent 차단 상태 변경 거부(관리자 아님): %s %s -> %s (요청자 %s)",
                       name, agent.status, want, current.username)
        raise HTTPException(status_code=403, detail="차단(blocked) 설정·해제는 관리자만 할 수 있습니다.")
    agent = await service.update_agent(session, name, req, changed_by=changed_by)
    if not agent:
        logger.warning("AI Agent 없음(수정): %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return agent


@router.delete("/{name}")
async def delete_ai_agent(name: str, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """에이전트 삭제(하드 삭제) — 관리자·등록자·소유자."""
    logger.info("DELETE /ai-agents/%s", name)
    await _assert_agent_editor(session, name, current)
    if not await service.delete_agent(session, name):
        logger.warning("AI Agent 없음(삭제): %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok", "message": f"AI Agent '{name}' deleted"}


# ---------------------------------------------------------------------------
# 서브리소스
# ---------------------------------------------------------------------------


@router.post("/{name}/tools", response_model=AIAgentToolResponse)
async def add_ai_agent_tool(current: CurrentUser,
    name: str,
    req: AIAgentToolCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트에 도구를 등록한다."""
    logger.info("POST /ai-agents/%s/tools: %s", name, req.name)
    await _assert_agent_editor(session, name, current)
    tool = await service.add_tool(session, name, req)
    if not tool:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return tool


@router.post("/{name}/mcp-servers", response_model=AIAgentMcpServerResponse)
async def add_ai_agent_mcp_server(current: CurrentUser,
    name: str,
    req: AIAgentMcpServerCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트에 MCP 서버를 연결한다 (카탈로그 배포 선택 또는 직접 입력)."""
    logger.info("POST /ai-agents/%s/mcp-servers: %s (deployment=%s)", name, req.name, req.mcp_deployment_name)
    await _assert_agent_editor(session, name, current)
    try:
        mcp = await service.add_mcp_server(session, name, req)
    except ValueError as e:
        logger.warning("POST /ai-agents/%s/mcp-servers 거부: %s", name, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not mcp:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return mcp


@router.patch("/{name}/mcp-servers/{mcp_id}", response_model=AIAgentMcpServerResponse)
async def update_ai_agent_mcp_server(current: CurrentUser,
    name: str,
    mcp_id: int,
    req: AIAgentMcpServerUpdate,
    session: AsyncSession = Depends(get_session),
):
    """연결된 MCP 의 허용 그룹·도구 수정, 또는 미등록 MCP 를 카탈로그 배포로 연결한다."""
    logger.info("PATCH /ai-agents/%s/mcp-servers/%d", name, mcp_id)
    await _assert_agent_editor(session, name, current)
    try:
        mcp = await service.update_mcp_server(session, name, mcp_id, req)
    except ValueError as e:
        logger.warning("PATCH /ai-agents/%s/mcp-servers/%d 거부: %s", name, mcp_id, e)
        raise HTTPException(status_code=400, detail=str(e))
    if not mcp:
        raise HTTPException(status_code=404, detail="MCP 연결을 찾을 수 없습니다.")
    return mcp


@router.post("/{name}/lineage", response_model=AIAgentLineageResponse)
async def add_ai_agent_lineage(current: CurrentUser,
    name: str,
    req: AIAgentLineageCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트 의존성(depends_on/consumed_by/related)을 등록한다."""
    logger.info("POST /ai-agents/%s/lineage -> %s", name, req.target_ref)
    await _assert_agent_editor(session, name, current)
    line = await service.add_lineage(session, name, req)
    if not line:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return line


@router.get("/{name}/versions", response_model=list[AIAgentVersionResponse])
async def list_ai_agent_versions(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    """에이전트 사양 버전 이력 조회."""
    logger.info("GET /ai-agents/%s/versions", name)
    versions = await service.list_versions(session, name)
    if versions is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return versions


@router.get("/{name}/status-history", response_model=list[AIAgentStatusHistoryResponse])
async def list_ai_agent_status_history(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    """에이전트 상태 변경 이력 조회(최신순)."""
    logger.info("GET /ai-agents/%s/status-history", name)
    history = await service.list_status_history(session, name)
    if history is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return history


@router.post("/{name}/versions", response_model=AIAgentVersionResponse)
async def create_ai_agent_version(current: CurrentUser,
    name: str,
    req: AIAgentVersionCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 사양 버전을 생성한다(버전 중복 시 409)."""
    logger.info("POST /ai-agents/%s/versions: %s", name, req.version)
    await _assert_agent_editor(session, name, current)
    try:
        ver = await service.create_version(session, name, req, created_by=current.username)
    except ValueError as e:
        logger.warning("POST /ai-agents/%s/versions 충돌: %s", name, e)
        raise HTTPException(status_code=409, detail=str(e))
    if not ver:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return ver


# ---------------------------------------------------------------------------
# 서브리소스 삭제 (Phase 2)
# ---------------------------------------------------------------------------


@router.delete("/{name}/tools/{tool_id}")
async def delete_ai_agent_tool(current: CurrentUser,
    name: str, tool_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 도구를 삭제한다."""
    logger.info("DELETE /ai-agents/%s/tools/%d", name, tool_id)
    await _assert_agent_editor(session, name, current)
    if not await service.delete_tool(session, name, tool_id):
        raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/{name}/mcp-servers/{mcp_id}")
async def delete_ai_agent_mcp_server(current: CurrentUser,
    name: str, mcp_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 MCP 서버 연결을 해제한다."""
    logger.info("DELETE /ai-agents/%s/mcp-servers/%d", name, mcp_id)
    await _assert_agent_editor(session, name, current)
    if not await service.delete_mcp_server(session, name, mcp_id):
        raise HTTPException(status_code=404, detail="MCP 서버를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/{name}/lineage/{lineage_id}")
async def delete_ai_agent_lineage(current: CurrentUser,
    name: str, lineage_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 리니지를 삭제한다."""
    logger.info("DELETE /ai-agents/%s/lineage/%d", name, lineage_id)
    await _assert_agent_editor(session, name, current)
    if not await service.delete_lineage(session, name, lineage_id):
        raise HTTPException(status_code=404, detail="리니지를 찾을 수 없습니다.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 평가 (Phase 2)
# ---------------------------------------------------------------------------


@router.get("/{name}/evals", response_model=list[AIAgentEvalResponse])
async def list_ai_agent_evals(name: str, session: AsyncSession = Depends(get_session)):
    """에이전트 평가 결과 목록."""
    logger.info("GET /ai-agents/%s/evals", name)
    evals = await service.list_evals(session, name)
    if evals is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return evals


@router.post("/{name}/evals", response_model=AIAgentEvalResponse)
async def add_ai_agent_eval(current: CurrentUser,
    name: str, req: AIAgentEvalCreate, session: AsyncSession = Depends(get_session)
):
    """평가 결과를 기록한다(평판 점수 자동 갱신)."""
    logger.info("POST /ai-agents/%s/evals: %s/%s", name, req.eval_type, req.metric_key)
    await _assert_agent_editor(session, name, current)
    ev = await service.add_eval(session, name, req, created_by=current.username)
    if not ev:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return ev


# ---------------------------------------------------------------------------
# 미터링 (Phase 2) — 외부 런타임 텔레메트리 ingest + 집계 조회
# ---------------------------------------------------------------------------


@router.post("/{name}/invocations")
async def ingest_ai_agent_invocation(
    _caller: CurrentUser, name: str, req: AIAgentInvocationIngest, session: AsyncSession = Depends(get_session)
):
    """외부 런타임이 호출 텔레메트리를 push 한다(관측 캐시/평판 갱신)."""
    logger.info("POST /ai-agents/%s/invocations: status=%s", name, req.status)
    if not await service.ingest_invocation(session, name, req):
        # 카탈로그에 없는 에이전트 — 응답은 404 그대로, 이름은 '미등록 에이전트'로 기록
        await discovery.record(session, name, "invocation", _caller.username, req.consumer)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok"}


@router.get("/{name}/metering", response_model=AIAgentMeteringResponse)
async def get_ai_agent_metering(name: str, session: AsyncSession = Depends(get_session)):
    """집계된 미터링 지표(호출/토큰/비용/지연/소비자)."""
    logger.info("GET /ai-agents/%s/metering", name)
    metering = await service.get_metering(session, name)
    if metering is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return metering


# ---------------------------------------------------------------------------
# 에이전트 카드 (Phase 2) — A2A
# ---------------------------------------------------------------------------


@router.get("/{name}/card", response_model=AIAgentCard)
async def get_ai_agent_card(name: str, session: AsyncSession = Depends(get_session)):
    """A2A 스타일 에이전트 카드(상호 검색·협업용)."""
    logger.info("GET /ai-agents/%s/card", name)
    card = await service.get_agent_card(session, name)
    if not card:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return card


# ---------------------------------------------------------------------------
# Phase 3 연동 인터페이스 (Control ↔ Execution Plane)
# 카탈로그는 정책을 정의(pull)하고 집행 결과를 수신(push)만 한다.
# 실제 집행(격리 실행/인라인 훅/egress 강제)은 외부 런타임이 수행한다.
# 상세 계약: design/ai-agent-execution-plane-interface.md
# ---------------------------------------------------------------------------


def _etag_matches(header: str | None, etag: str) -> bool:
    """``If-None-Match`` 가 현재 ETag 와 맞는지(약한 비교, 여러 값·``*`` 허용)."""
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or any(t.removeprefix("W/") == etag for t in tags)


@router.get(
    "/{name}/policy",
    response_model=AIAgentPolicyBundle,
    responses={304: {"description": "If-None-Match 가 현재 ETag 와 같음 — 본문 없음"}},
)
async def get_ai_agent_policy(name: str, request: Request, session: AsyncSession = Depends(get_session)):
    """외부 Execution Plane이 런타임 집행을 위해 pull 하는 정책 번들(읽기 전용).

    응답 ``ETag`` 는 번들 내용의 해시다. 다음 요청에 ``If-None-Match`` 로 보내면 바뀌지 않았을 때 본문 없이 304 를 돌려준다.
    ``policy_version`` 폴링도 그대로 쓸 수 있다.
    """
    bundle = await service.get_policy_bundle(session, name)
    if not bundle:
        logger.info("GET /ai-agents/%s/policy: 404", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    # 응답 모델 직렬화와 같게 별칭(by_alias)으로 — 도구 스키마 키는 'schema'
    body = bundle.model_dump_json(by_alias=True)
    etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        logger.info("GET /ai-agents/%s/policy: 304", name)
        return Response(status_code=304, headers=headers)
    logger.info("GET /ai-agents/%s/policy: 200 version=%s", name, bundle.policy_version)
    return Response(content=body, media_type="application/json", headers=headers)


@router.post("/{name}/hook-events", response_model=HookEventResponse)
async def ingest_ai_agent_hook_event(
    _caller: CurrentUser, name: str, req: HookEventIngest, session: AsyncSession = Depends(get_session)
):
    """외부 런타임의 인라인 훅이 집행 결과(허용/차단/마스킹/HITL 등)를 보고한다."""
    logger.info("POST /ai-agents/%s/hook-events: %s/%s", name, req.stage, req.decision)
    ev = await service.ingest_hook_event(session, name, req)
    if not ev:
        await discovery.record(session, name, "hook_event", _caller.username, req.consumer)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return ev


@router.get("/{name}/hook-events", response_model=list[HookEventResponse])
async def list_ai_agent_hook_events(
    name: str,
    stage: str | None = Query(None, description="단계 필터 (access/pre_exec/post_exec)"),
    decision: str | None = Query(None, description="결정 필터 (allow/deny/mask/...)"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """집행 훅 이벤트 감사 로그 조회(최신순)."""
    logger.info("GET /ai-agents/%s/hook-events", name)
    events = await service.list_hook_events(
        session, name, stage=stage, decision=decision, limit=limit
    )
    if events is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return events
