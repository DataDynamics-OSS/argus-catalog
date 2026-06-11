"""AI Agent 카탈로그 라우터.

``/api/v1/ai-agents`` 경로에서 에이전트의 CRUD, 대시보드 통계, 상세,
도구/MCP/리니지/버전 서브리소스를 제공한다. 라우터는 서비스 함수만
호출하고 HTTP 변환(상태 코드/예외)만 담당한다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service
from app.core.auth import AdminUser, CurrentUser, OptionalUser, assert_owner_or_admin
from app.agents.schemas import (
    AIAgentCard,
    AIAgentCreate,
    AIAgentDetailResponse,
    AIAgentEvalCreate,
    AIAgentEvalResponse,
    AIAgentInvocationIngest,
    AIAgentLineageCreate,
    AIAgentLineageResponse,
    AIAgentMcpServerCreate,
    AIAgentMcpServerResponse,
    AIAgentMeteringResponse,
    AIAgentPolicyBundle,
    AIAgentStats,
    AIAgentSummary,
    AIAgentToolCreate,
    AIAgentToolResponse,
    AIAgentUpdate,
    AIAgentVersionCreate,
    AIAgentStatusHistoryResponse,
    AIAgentVersionResponse,
    HookEventIngest,
    HookEventResponse,
    PaginatedAIAgents,
)
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-agents", tags=["ai-agents"])

_NOT_FOUND = "AI Agent '{name}'을(를) 찾을 수 없습니다."


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
    session: AsyncSession = Depends(get_session),
):
    """새 에이전트를 등록한다(이름 중복 시 409)."""
    logger.info("POST /ai-agents: name=%s", req.name)
    try:
        result = await service.create_agent(session, req, created_by=current.username)
        logger.info("AI Agent 생성됨: %s (id=%d)", result.name, result.id)
        return result
    except ValueError as e:
        logger.warning("POST /ai-agents 충돌: %s", e)
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=PaginatedAIAgents)
async def list_ai_agents(
    search: str | None = Query(None, description="이름/표시명/설명 검색"),
    status: str | None = Query(None, description="상태 필터"),
    framework: str | None = Query(None, description="프레임워크 필터"),
    category: str | None = Query(None, description="카테고리 필터"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """에이전트 목록 페이징 조회."""
    logger.info("GET /ai-agents: search=%s, status=%s, page=%d", search, status, page)
    return await service.list_agents(
        session,
        search=search,
        status=status,
        framework=framework,
        category=category,
        page=page,
        page_size=page_size,
    )


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
    """에이전트 메타데이터 부분 갱신."""
    logger.info("PATCH /ai-agents/%s", name)
    changed_by = (user.username or user.email) if user else None
    assert_owner_or_admin(current, await service.get_agent_created_by(session, name), "에이전트")
    agent = await service.update_agent(session, name, req, changed_by=changed_by)
    if not agent:
        logger.warning("AI Agent 없음(수정): %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return agent


@router.delete("/{name}")
async def delete_ai_agent(name: str, current: CurrentUser, session: AsyncSession = Depends(get_session)):
    """에이전트 삭제."""
    logger.info("DELETE /ai-agents/%s", name)
    assert_owner_or_admin(current, await service.get_agent_created_by(session, name), "에이전트")
    if not await service.delete_agent(session, name):
        logger.warning("AI Agent 없음(삭제): %s", name)
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return {"status": "ok", "message": f"AI Agent '{name}' deleted"}


# ---------------------------------------------------------------------------
# 서브리소스
# ---------------------------------------------------------------------------


@router.post("/{name}/tools", response_model=AIAgentToolResponse)
async def add_ai_agent_tool(_guard: AdminUser, 
    name: str,
    req: AIAgentToolCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트에 도구를 등록한다."""
    logger.info("POST /ai-agents/%s/tools: %s", name, req.name)
    tool = await service.add_tool(session, name, req)
    if not tool:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return tool


@router.post("/{name}/mcp-servers", response_model=AIAgentMcpServerResponse)
async def add_ai_agent_mcp_server(_guard: AdminUser, 
    name: str,
    req: AIAgentMcpServerCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트에 MCP 서버를 연결한다."""
    logger.info("POST /ai-agents/%s/mcp-servers: %s", name, req.name)
    mcp = await service.add_mcp_server(session, name, req)
    if not mcp:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return mcp


@router.post("/{name}/lineage", response_model=AIAgentLineageResponse)
async def add_ai_agent_lineage(_guard: AdminUser, 
    name: str,
    req: AIAgentLineageCreate,
    session: AsyncSession = Depends(get_session),
):
    """에이전트 의존성(depends_on/consumed_by/related)을 등록한다."""
    logger.info("POST /ai-agents/%s/lineage -> %s", name, req.target_ref)
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
async def create_ai_agent_version(_guard: AdminUser, 
    name: str,
    req: AIAgentVersionCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 사양 버전을 생성한다(버전 중복 시 409)."""
    logger.info("POST /ai-agents/%s/versions: %s", name, req.version)
    try:
        ver = await service.create_version(session, name, req)
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
async def delete_ai_agent_tool(_guard: AdminUser, 
    name: str, tool_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 도구를 삭제한다."""
    logger.info("DELETE /ai-agents/%s/tools/%d", name, tool_id)
    if not await service.delete_tool(session, name, tool_id):
        raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/{name}/mcp-servers/{mcp_id}")
async def delete_ai_agent_mcp_server(_guard: AdminUser, 
    name: str, mcp_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 MCP 서버 연결을 해제한다."""
    logger.info("DELETE /ai-agents/%s/mcp-servers/%d", name, mcp_id)
    if not await service.delete_mcp_server(session, name, mcp_id):
        raise HTTPException(status_code=404, detail="MCP 서버를 찾을 수 없습니다.")
    return {"status": "ok"}


@router.delete("/{name}/lineage/{lineage_id}")
async def delete_ai_agent_lineage(_guard: AdminUser, 
    name: str, lineage_id: int, session: AsyncSession = Depends(get_session)
):
    """에이전트 리니지를 삭제한다."""
    logger.info("DELETE /ai-agents/%s/lineage/%d", name, lineage_id)
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
async def add_ai_agent_eval(_guard: AdminUser, 
    name: str, req: AIAgentEvalCreate, session: AsyncSession = Depends(get_session)
):
    """평가 결과를 기록한다(평판 점수 자동 갱신)."""
    logger.info("POST /ai-agents/%s/evals: %s/%s", name, req.eval_type, req.metric_key)
    ev = await service.add_eval(session, name, req)
    if not ev:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return ev


# ---------------------------------------------------------------------------
# 미터링 (Phase 2) — 외부 런타임 텔레메트리 ingest + 집계 조회
# ---------------------------------------------------------------------------


@router.post("/{name}/invocations")
async def ingest_ai_agent_invocation(
    name: str, req: AIAgentInvocationIngest, session: AsyncSession = Depends(get_session)
):
    """외부 런타임이 호출 텔레메트리를 push 한다(관측 캐시/평판 갱신)."""
    logger.info("POST /ai-agents/%s/invocations: status=%s", name, req.status)
    if not await service.ingest_invocation(session, name, req):
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


@router.get("/{name}/policy", response_model=AIAgentPolicyBundle)
async def get_ai_agent_policy(name: str, session: AsyncSession = Depends(get_session)):
    """외부 Execution Plane이 런타임 집행을 위해 pull 하는 정책 번들(읽기 전용).

    ``policy_version`` 으로 변경 감지(폴링/ETag)에 사용한다.
    """
    logger.info("GET /ai-agents/%s/policy", name)
    bundle = await service.get_policy_bundle(session, name)
    if not bundle:
        raise HTTPException(status_code=404, detail=_NOT_FOUND.format(name=name))
    return bundle


@router.post("/{name}/hook-events", response_model=HookEventResponse)
async def ingest_ai_agent_hook_event(
    name: str, req: HookEventIngest, session: AsyncSession = Depends(get_session)
):
    """외부 런타임의 인라인 훅이 집행 결과(허용/차단/마스킹/HITL 등)를 보고한다."""
    logger.info("POST /ai-agents/%s/hook-events: %s/%s", name, req.stage, req.decision)
    ev = await service.ingest_hook_event(session, name, req)
    if not ev:
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
