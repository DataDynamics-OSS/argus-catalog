# SPDX-License-Identifier: Apache-2.0
"""AI Agent 카탈로그 서비스 레이어(비즈니스 로직).

라우터는 이 모듈의 함수만 호출하고 HTTP 변환만 수행한다. 데이터셋/모델
카탈로그와 동일한 페이지네이션·소프트필터·로깅 컨벤션을 따른다.

상태(``status``) 라이프사이클:
  draft → staging → active → blocked / deprecated / retired
목록 조회는 기본적으로 ``retired`` 를 제외하지 않고 그대로 노출하되
``status`` 필터로 좁힐 수 있다.

로깅 정책: 변경 동작(create/update/delete/sub-resource add)은 INFO 로
영향받은 에이전트명을 기록하고, 404/409 분기는 WARNING 으로 남긴다.
"""

import datetime as _dt
import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import discovery
from app.agents.models import (
    AIAgent,
    AIAgentEval,
    AIAgentHookEvent,
    AIAgentInvocationLog,
    AIAgentLineage,
    AIAgentMcpServer,
    AIAgentStatusHistory,
    AIAgentTool,
    AIAgentVersion,
)
from app.agents.schemas import (
    AgentCardProvider,
    AgentCardSkill,
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
    DataPoint,
    HookEventIngest,
    HookEventResponse,
    NameCount,
    PaginatedAIAgents,
    PolicyMcpServer,
    PolicyTool,
)
from app.core.sql import LIKE_ESCAPE, like_contains

logger = logging.getLogger(__name__)


def _generate_agent_urn(name: str) -> str:
    """에이전트 URN 을 ``{name}.agent`` 형식으로 생성한다."""
    return f"{name}.agent"


# ---------------------------------------------------------------------------
# AIAgent CRUD
# ---------------------------------------------------------------------------


async def create_agent(session: AsyncSession, req: AIAgentCreate, created_by: str | None = None) -> AIAgentSummary:
    """새 에이전트 등록. 이름 중복 시 ``ValueError``."""
    existing = await session.execute(select(AIAgent).where(AIAgent.name == req.name))
    if existing.scalars().first():
        logger.warning("AIAgent 생성 거부됨(이름 중복): %s", req.name)
        raise ValueError(f"AI Agent with name '{req.name}' already exists")

    data = req.model_dump(exclude={"name"})
    # Enum → 원시 문자열로 저장
    if data.get("status") is not None:
        data["status"] = (
            data["status"].value if hasattr(data["status"], "value") else data["status"]
        )

    data.setdefault("created_by", created_by)
    agent = AIAgent(name=req.name, urn=_generate_agent_urn(req.name), **data)
    session.add(agent)
    await session.flush()
    await discovery.mark_registered(session, agent.name, agent.id, created_by)
    await session.commit()
    await session.refresh(agent)
    logger.info("AIAgent 생성됨: %s (id=%d, urn=%s)", agent.name, agent.id, agent.urn)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 생성 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("ai_agent", agent.id)

    return AIAgentSummary.model_validate(agent)


async def _get_agent_by_name(session: AsyncSession, name: str) -> AIAgent | None:
    """이름으로 에이전트 조회. 없으면 None(라우터가 404 로 변환)."""
    result =await session.execute(select(AIAgent).where(AIAgent.name == name))
    return result.scalars().first()


async def list_agents(
    session: AsyncSession,
    search: str | None = None,
    status: str | None = None,
    framework: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
    *,
    department: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    risk: str | None = None,
    managed_by: str | None = None,
    owner_state: str | None = None,
    incomplete: bool = False,
    sort: str | None = None,
) -> PaginatedAIAgents:
    """에이전트 목록(검색·필터·정렬·페이지네이션).

    단순 조건은 SQL 로, 계산이 필요한 조건(태그·위험도·책임자 상태·정보 부족)과 정렬은 ``governance`` 에서 처리한다.
    ``owner`` 는 실제 이메일(라우터가 'me' 를 바꿔 넘김).
    """
    from app.agents import governance

    base = select(AIAgent)
    if search:
        like = like_contains(search)
        base = base.where(
            (AIAgent.name.ilike(like, escape=LIKE_ESCAPE))
            | (AIAgent.display_name.ilike(like, escape=LIKE_ESCAPE))
            | (AIAgent.description.ilike(like, escape=LIKE_ESCAPE))
        )
    if status:
        base = base.where(AIAgent.status == status)
    if framework:
        base = base.where(AIAgent.framework == framework)
    if category:
        base = base.where(AIAgent.category == category)
    base = governance.sql_filters(base, department=department, owner=owner, managed_by=managed_by)

    # 계산 조건·정렬을 위해 SQL 조건에 맞는 전체를 읽은 뒤 메모리에서 거르고 페이지를 자른다
    agents = list((await session.execute(base)).scalars().all())
    facts = await governance.facts_for(session, agents)
    rows = governance.filter_and_sort(agents, facts, tag=tag, risk=risk, owner_state=owner_state,
                                      incomplete=incomplete, sort=sort)
    logger.debug("AIAgent 목록: SQL 조건 일치 %d건 → 계산 조건 적용 후 %d건 (page=%d)", len(agents), len(rows), page)
    offset = (page - 1) * page_size
    items = []
    for a in rows[offset:offset + page_size]:
        item = AIAgentSummary.model_validate(a)
        f = facts[a.id]
        item.owner_state, item.completeness, item.missing_fields, item.max_risk = (
            f.owner_state, f.completeness, f.missing, f.max_risk)
        items.append(item)
    return PaginatedAIAgents(items=items, total=len(rows), page=page, page_size=page_size)


async def get_agent_detail(session: AsyncSession, name: str) -> AIAgentDetailResponse | None:
    """에이전트 상세(도구/MCP/리니지/버전 포함)."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None

    tools = (
        (
            await session.execute(
                select(AIAgentTool).where(AIAgentTool.agent_id == agent.id).order_by(AIAgentTool.id)
            )
        )
        .scalars()
        .all()
    )
    mcps = (
        (
            await session.execute(
                select(AIAgentMcpServer)
                .where(AIAgentMcpServer.agent_id == agent.id)
                .order_by(AIAgentMcpServer.id)
            )
        )
        .scalars()
        .all()
    )
    lineage = (
        (
            await session.execute(
                select(AIAgentLineage)
                .where(AIAgentLineage.agent_id == agent.id)
                .order_by(AIAgentLineage.id)
            )
        )
        .scalars()
        .all()
    )
    versions = (
        (
            await session.execute(
                select(AIAgentVersion)
                .where(AIAgentVersion.agent_id == agent.id)
                .order_by(AIAgentVersion.id.desc())
            )
        )
        .scalars()
        .all()
    )

    detail = AIAgentDetailResponse.model_validate(agent)
    detail.tools = [AIAgentToolResponse.model_validate(t) for t in tools]
    from app.mcp_servers.service import enrich_agent_links

    detail.mcp_servers = [AIAgentMcpServerResponse(**m) for m in await enrich_agent_links(session, list(mcps))]
    detail.lineage = [AIAgentLineageResponse.model_validate(line) for line in lineage]
    detail.versions = [AIAgentVersionResponse.model_validate(v) for v in versions]
    from app.agents import governance

    f = (await governance.facts_for(session, [agent]))[agent.id]
    detail.owner_state, detail.completeness, detail.missing_fields, detail.max_risk = (
        f.owner_state, f.completeness, f.missing, f.max_risk)
    return detail


async def update_agent(
    session: AsyncSession, name: str, req: AIAgentUpdate, changed_by: str | None = None
) -> AIAgentSummary | None:
    """에이전트 메타데이터 부분 갱신. status 변경 시 이력에 changed_by 기록."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None

    data = req.model_dump(exclude_unset=True)
    prev_status = agent.status
    if "status" in data and data["status"] is not None:
        data["status"] = (
            data["status"].value if hasattr(data["status"], "value") else data["status"]
        )
    for key, value in data.items():
        setattr(agent, key, value)

    # 상태가 실제로 바뀐 경우 이력 자동 기록
    if "status" in data and data["status"] is not None and data["status"] != prev_status:
        session.add(AIAgentStatusHistory(
            agent_id=agent.id,
            from_status=prev_status,
            to_status=data["status"],
            changed_by=changed_by,
        ))
        logger.info("AIAgent 상태 변경됨: %s %s -> %s", name, prev_status, data["status"])

    await session.commit()
    await session.refresh(agent)
    logger.info("AIAgent 수정됨: %s", name)

    from app.embedding.service import embed_entity_background
    await embed_entity_background("ai_agent", agent.id)

    return AIAgentSummary.model_validate(agent)


async def list_status_history(session: AsyncSession, name: str):
    """에이전트 상태 변경 이력(최신순)."""
    agent = await _get_agent_by_name(session, name)
    if agent is None:
        return None
    rows = (
        await session.execute(
            select(AIAgentStatusHistory)
            .where(AIAgentStatusHistory.agent_id == agent.id)
            .order_by(AIAgentStatusHistory.changed_at.desc(), AIAgentStatusHistory.id.desc())
        )
    ).scalars().all()
    return [AIAgentStatusHistoryResponse.model_validate(r) for r in rows]


async def delete_agent(session: AsyncSession, name: str) -> bool:
    """에이전트 삭제(하드 삭제, 관계 테이블은 CASCADE)."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return False

    # 임베딩 행 정리 (다형 테이블이라 FK CASCADE 가 없음)
    from app.embedding.service import delete_entity_embedding
    await delete_entity_embedding(session, "ai_agent", agent.id)

    await session.delete(agent)
    await session.commit()
    logger.info("AIAgent 삭제됨: %s", name)
    return True


# ---------------------------------------------------------------------------
# 서브리소스: tools / mcp / lineage / versions
# ---------------------------------------------------------------------------


def _bump_policy_version(agent: AIAgent) -> None:
    """정책 번들 구성요소(도구·MCP 서버)가 바뀌면 에이전트 ``updated_at`` 을 갱신한다.

    ``policy_version`` 은 ``updated_at`` 이므로, 하위 행만 추가/삭제하면 버전이 그대로라
    실행 계층(집행기)이 폴링으로 정책 변경을 감지하지 못한다. 호출한 쪽의 트랜잭션에서 함께 커밋된다.
    """
    agent.updated_at = _dt.datetime.now(_dt.timezone.utc)


async def add_tool(
    session: AsyncSession, name: str, req: AIAgentToolCreate
) -> AIAgentToolResponse | None:
    """도구 추가(정책 버전 갱신 + 재임베딩). 에이전트가 없으면 None."""
    agent =await _get_agent_by_name(session, name)
    if not agent:
        return None
    tool = AIAgentTool(agent_id=agent.id, **req.model_dump())
    session.add(tool)
    _bump_policy_version(agent)  # allowed_tools 변경
    await session.commit()
    await session.refresh(tool)
    logger.info("AIAgent 도구 추가됨: agent=%s tool=%s", name, req.name)

    # 도구는 임베딩 소스 텍스트에 포함되므로 재임베딩
    from app.embedding.service import embed_entity_background
    await embed_entity_background("ai_agent", agent.id)

    return AIAgentToolResponse.model_validate(tool)


async def add_mcp_server(
    session: AsyncSession, name: str, req: AIAgentMcpServerCreate
) -> AIAgentMcpServerResponse | None:
    """MCP 연결 추가. ``mcp_deployment_name`` 이 있으면 카탈로그 배포를 참조하고
    name/url/auth_method 를 배포 값으로 채운다. 없으면 직접 입력("미등록 MCP")."""
    from app.mcp_servers import service as mcp_service

    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    data = req.model_dump(exclude={"mcp_deployment_name"})
    if req.mcp_deployment_name:
        resolved = await mcp_service.resolve_deployment(session, req.mcp_deployment_name)
        if not resolved:
            raise ValueError(f"MCP 배포 '{req.mcp_deployment_name}' 을(를) 찾을 수 없습니다")
        dep, server = resolved
        await mcp_service.validate_agent_scope(session, dep, req.allowed_groups, req.allowed_tools)
        data.update(
            name=req.name or dep.display_name or dep.name,
            url=dep.endpoint,
            auth_method=dep.auth_method,
            description=req.description or dep.description or server.description,
            mcp_deployment_id=dep.id,
        )
    mcp = AIAgentMcpServer(agent_id=agent.id, **data)
    session.add(mcp)
    _bump_policy_version(agent)  # allowed_mcp_servers 변경
    await session.commit()
    await session.refresh(mcp)
    logger.info("AIAgent MCP 추가됨: agent=%s mcp=%s deployment=%s", name, mcp.name, req.mcp_deployment_name)
    return AIAgentMcpServerResponse(**(await mcp_service.enrich_agent_links(session, [mcp]))[0])


async def update_mcp_server(
    session: AsyncSession, name: str, mcp_id: int, req: AIAgentMcpServerUpdate
) -> AIAgentMcpServerResponse | None:
    """연결된 MCP 의 허용 그룹·도구 수정, 또는 미등록 MCP 를 카탈로그 배포로 연결."""
    from app.mcp_servers import service as mcp_service
    from app.mcp_servers.models import McpDeployment

    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    mcp = await session.get(AIAgentMcpServer, mcp_id)
    if not mcp or mcp.agent_id != agent.id:
        return None
    data = req.model_dump(exclude_unset=True)
    if data.get("mcp_deployment_name"):
        resolved = await mcp_service.resolve_deployment(session, data["mcp_deployment_name"])
        if not resolved:
            raise ValueError(f"MCP 배포 '{data['mcp_deployment_name']}' 을(를) 찾을 수 없습니다")
        dep, _ = resolved
        mcp.mcp_deployment_id = dep.id
        mcp.url = dep.endpoint
        mcp.auth_method = dep.auth_method
    if mcp.mcp_deployment_id and ("allowed_groups" in data or "allowed_tools" in data):
        dep = await session.get(McpDeployment, mcp.mcp_deployment_id)
        await mcp_service.validate_agent_scope(
            session, dep, data.get("allowed_groups", mcp.allowed_groups), data.get("allowed_tools", mcp.allowed_tools)
        )
    for key in ("allowed_groups", "allowed_tools", "description"):
        if key in data:
            setattr(mcp, key, data[key])
    if data.get("mcp_deployment_name") or "allowed_groups" in data or "allowed_tools" in data:
        _bump_policy_version(agent)  # allowed_mcp_servers(주소·허용 범위) 변경 — 집행기가 새 번들을 받도록
    await session.commit()
    await session.refresh(mcp)
    logger.info("AIAgent MCP 수정됨: agent=%s mcp_id=%d", name, mcp_id)
    return AIAgentMcpServerResponse(**(await mcp_service.enrich_agent_links(session, [mcp]))[0])


async def add_lineage(
    session: AsyncSession, name: str, req: AIAgentLineageCreate
) -> AIAgentLineageResponse | None:
    """리니지 추가(대상 존재 여부는 검사하지 않음 — 외부 대상도 자유 문자열). 에이전트가 없으면 None."""
    agent =await _get_agent_by_name(session, name)
    if not agent:
        return None
    line = AIAgentLineage(agent_id=agent.id, **req.model_dump())
    session.add(line)
    await session.commit()
    await session.refresh(line)
    logger.info("AIAgent 리니지 추가됨: agent=%s -> %s(%s)", name, req.target_ref, req.relation)
    return AIAgentLineageResponse.model_validate(line)


async def create_version(
    session: AsyncSession, name: str, req: AIAgentVersionCreate, created_by: str | None = None
) -> AIAgentVersionResponse | None:
    """새 사양 버전 생성. (agent, version) 중복 시 ``ValueError``."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    dup = await session.execute(
        select(AIAgentVersion).where(
            AIAgentVersion.agent_id == agent.id, AIAgentVersion.version == req.version
        )
    )
    if dup.scalars().first():
        logger.warning("AIAgent 버전 생성 거부됨(버전 중복): agent=%s version=%s", name, req.version)
        raise ValueError(f"Version '{req.version}' already exists for agent '{name}'")

    ver = AIAgentVersion(agent_id=agent.id, created_by=created_by, **req.model_dump())
    session.add(ver)
    # 메인 엔티티의 현재 버전도 최신으로 갱신
    agent.version = req.version
    await session.commit()
    await session.refresh(ver)
    logger.info("AIAgent 버전 생성됨: agent=%s version=%s", name, req.version)
    return AIAgentVersionResponse.model_validate(ver)


async def list_versions(session: AsyncSession, name: str) -> list[AIAgentVersionResponse] | None:
    """사양 버전 이력(최신 생성순). 에이전트가 없으면 None."""
    agent =await _get_agent_by_name(session, name)
    if not agent:
        return None
    rows = (
        (
            await session.execute(
                select(AIAgentVersion)
                .where(AIAgentVersion.agent_id == agent.id)
                .order_by(AIAgentVersion.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [AIAgentVersionResponse.model_validate(v) for v in rows]


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


async def _group_count(session: AsyncSession, column) -> list[NameCount]:
    """컬럼 값별 건수(NULL 값 그룹은 제외)."""
    rows =(await session.execute(select(column, func.count()).group_by(column))).all()
    return [NameCount(name=str(value), count=count) for value, count in rows if value is not None]


async def get_stats(session: AsyncSession) -> AIAgentStats:
    """대시보드 통계."""
    from app.agents import governance

    total = await session.scalar(select(func.count(AIAgent.id))) or 0
    active = (
        await session.scalar(select(func.count(AIAgent.id)).where(AIAgent.status == "active")) or 0
    )
    multi = (
        await session.scalar(select(func.count(AIAgent.id)).where(AIAgent.is_multi_agent.is_(True)))
        or 0
    )
    hitl = (
        await session.scalar(select(func.count(AIAgent.id)).where(AIAgent.hitl_required.is_(True)))
        or 0
    )
    invocations = await session.scalar(select(func.coalesce(func.sum(AIAgent.usage_count), 0))) or 0
    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
    inv_24h = await session.scalar(
        select(func.count(AIAgentInvocationLog.id)).where(AIAgentInvocationLog.invoked_at >= since)
    ) or 0
    err_24h = await session.scalar(
        select(func.count(AIAgentInvocationLog.id)).where(
            AIAgentInvocationLog.invoked_at >= since, AIAgentInvocationLog.status != "success",
        )
    ) or 0
    risky = await session.scalar(
        select(func.count(func.distinct(AIAgentTool.agent_id))).where(
            AIAgentTool.risk.in_(("high", "critical")), AIAgentTool.requires_approval.is_(False),
        )
    ) or 0
    unregistered = await session.scalar(
        select(func.count(AIAgentMcpServer.id)).where(AIAgentMcpServer.mcp_deployment_id.is_(None))
    ) or 0

    return AIAgentStats(
        total_agents=total,
        active_agents=active,
        by_status=await _group_count(session, AIAgent.status),
        by_framework=await _group_count(session, AIAgent.framework),
        by_category=await _group_count(session, AIAgent.category),
        multi_agent_count=multi,
        hitl_required_count=hitl,
        total_invocations=int(invocations),
        invocations_24h=inv_24h,
        errors_24h=err_24h,
        risky_tool_agents=risky,
        unregistered_mcp_count=unregistered,
        unregistered_agent_count=await discovery.count_new(session),
        **await governance.stats(session),
    )


# ---------------------------------------------------------------------------
# 서브리소스 삭제 (tools / mcp / lineage)
# ---------------------------------------------------------------------------


async def _delete_child(
    session: AsyncSession, model, name: str, child_id: int, *, affects_policy: bool = False
) -> bool:
    """에이전트 소속 서브리소스를 삭제한다(존재/소유 검증 포함).

    ``affects_policy`` 면 실제로 삭제된 경우 정책 버전(``updated_at``)도 갱신한다.
    """
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return False
    result = await session.execute(
        sa_delete(model).where(model.id == child_id, model.agent_id == agent.id)
    )
    deleted = result.rowcount and result.rowcount > 0
    if deleted and affects_policy:
        _bump_policy_version(agent)
    await session.commit()
    if deleted:
        logger.info("AIAgent 서브리소스 삭제됨: agent=%s %s=%d", name, model.__name__, child_id)
    else:
        # 없는 id 이거나 다른 에이전트 소속 — 라우터가 404 로 응답
        logger.warning("AIAgent 서브리소스 없음(삭제): agent=%s %s=%d", name, model.__name__, child_id)
    return bool(deleted)


async def delete_tool(session: AsyncSession, name: str, tool_id: int) -> bool:
    """도구 삭제(정책 버전 갱신 + 재임베딩). 삭제했으면 True."""
    deleted =await _delete_child(session, AIAgentTool, name, tool_id, affects_policy=True)
    if deleted:
        # 도구는 임베딩 소스 텍스트에 포함되므로 재임베딩
        agent = await _get_agent_by_name(session, name)
        if agent:
            from app.embedding.service import embed_entity_background
            await embed_entity_background("ai_agent", agent.id)
    return deleted


async def delete_mcp_server(session: AsyncSession, name: str, mcp_id: int) -> bool:
    return await _delete_child(session, AIAgentMcpServer, name, mcp_id, affects_policy=True)


async def delete_lineage(session: AsyncSession, name: str, lineage_id: int) -> bool:
    return await _delete_child(session, AIAgentLineage, name, lineage_id)


# ---------------------------------------------------------------------------
# Phase 2 — 평가 (Evaluation)
# ---------------------------------------------------------------------------


async def add_eval(
    session: AsyncSession, name: str, req: AIAgentEvalCreate, created_by: str | None = None
) -> AIAgentEvalResponse | None:
    """평가 결과 적재 후 평판 점수를 다시 계산한다. 에이전트가 없으면 None."""
    agent =await _get_agent_by_name(session, name)
    if not agent:
        return None
    ev = AIAgentEval(agent_id=agent.id, created_by=created_by, **req.model_dump())
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    # 평가가 들어오면 평판 점수를 갱신
    await _recompute_reputation(session, agent)
    await session.commit()
    logger.info("AIAgent 평가 추가됨: agent=%s type=%s key=%s", name, req.eval_type, req.metric_key)
    return AIAgentEvalResponse.model_validate(ev)


async def list_evals(session: AsyncSession, name: str) -> list[AIAgentEvalResponse] | None:
    """평가 결과 목록(최신 평가순). 에이전트가 없으면 None."""
    agent =await _get_agent_by_name(session, name)
    if not agent:
        return None
    rows = (
        (
            await session.execute(
                select(AIAgentEval)
                .where(AIAgentEval.agent_id == agent.id)
                .order_by(AIAgentEval.evaluated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [AIAgentEvalResponse.model_validate(e) for e in rows]


# 평판 산출에 사용하는 "성공형" 평가 유형 (0~1 정규화 가정)
_SUCCESS_EVAL_TYPES = {"accuracy", "task_success", "safety", "user_rating"}


async def _eval_quality_avg(session: AsyncSession, agent_id: int) -> float | None:
    """성공형 평가 점수의 평균(0~1)을 구한다. 없으면 None."""
    avg = await session.scalar(
        select(func.avg(AIAgentEval.metric_value)).where(
            AIAgentEval.agent_id == agent_id,
            AIAgentEval.eval_type.in_(_SUCCESS_EVAL_TYPES),
        )
    )
    return float(avg) if avg is not None else None


# ---------------------------------------------------------------------------
# Phase 2 — 미터링 (Metering / Invocation telemetry)
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[int], pct: float) -> int | None:
    """정렬된 리스트에서 백분위수(nearest-rank)를 구한다."""
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, round(pct / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[k]


async def _recompute_reputation(session: AsyncSession, agent: AIAgent) -> None:
    """호출 성공률과 평가 품질을 결합해 평판 점수(0~100)를 갱신한다.

    reputation = (0.5 * success_rate + 0.5 * eval_avg) * 100
    한쪽 신호만 있으면 그 신호만 사용한다.
    """
    total = (
        await session.scalar(
            select(func.count(AIAgentInvocationLog.id)).where(
                AIAgentInvocationLog.agent_id == agent.id
            )
        )
        or 0
    )
    success = (
        await session.scalar(
            select(func.count(AIAgentInvocationLog.id)).where(
                AIAgentInvocationLog.agent_id == agent.id,
                AIAgentInvocationLog.status == "success",
            )
        )
        or 0
    )
    success_rate = (success / total) if total else None
    eval_avg = await _eval_quality_avg(session, agent.id)

    signals = [s for s in (success_rate, eval_avg) if s is not None]
    if signals:
        agent.reputation_score = round(sum(signals) / len(signals) * 100, 2)
    logger.debug("평판 재계산: agent=%s success_rate=%s eval_avg=%s score=%s",
                 agent.name, success_rate, eval_avg, agent.reputation_score)


async def ingest_invocation(session: AsyncSession, name: str, req: AIAgentInvocationIngest) -> bool:
    """호출 텔레메트리를 적재하고 관측 캐시 컬럼/평판을 갱신한다.

    에이전트가 없으면 False(라우터가 발견 기록에 남기고 404). 집계는 매 호출마다 전체 로그 기준으로 다시 한다.
    """
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return False
    log = AIAgentInvocationLog(agent_id=agent.id, **req.model_dump())
    session.add(log)
    await session.flush()

    # 관측 캐시 컬럼 재계산 (전체 로그 기준)
    rows = (
        await session.execute(
            select(
                AIAgentInvocationLog.latency_ms,
                AIAgentInvocationLog.input_tokens,
                AIAgentInvocationLog.output_tokens,
                AIAgentInvocationLog.status,
            ).where(AIAgentInvocationLog.agent_id == agent.id)
        )
    ).all()
    latencies = sorted(r[0] for r in rows if r[0] is not None)
    tokens = [(r[1] or 0) + (r[2] or 0) for r in rows]
    # 오류율은 success 가 아닌 모든 상태(error·timeout 등) 기준 — 평판·실패 집계와 같은 규칙
    errors = sum(1 for r in rows if r[3] != "success")

    agent.usage_count = len(rows)
    agent.last_invoked_at = _dt.datetime.now(_dt.timezone.utc)
    agent.latency_p50 = _percentile(latencies, 50)
    agent.latency_p95 = _percentile(latencies, 95)
    agent.error_rate = round(errors / len(rows), 4) if rows else None
    agent.avg_token_usage = int(sum(tokens) / len(tokens)) if tokens else None
    await _recompute_reputation(session, agent)
    await session.commit()
    logger.info("AIAgent 호출 적재됨: agent=%s status=%s", name, req.status)
    return True


async def get_metering(session: AsyncSession, name: str) -> AIAgentMeteringResponse | None:
    """집계된 미터링 지표를 반환한다."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    rows = (
        await session.execute(
            select(
                AIAgentInvocationLog.invoked_at,
                AIAgentInvocationLog.status,
                AIAgentInvocationLog.latency_ms,
                AIAgentInvocationLog.input_tokens,
                AIAgentInvocationLog.output_tokens,
                AIAgentInvocationLog.cost,
                AIAgentInvocationLog.consumer,
            ).where(AIAgentInvocationLog.agent_id == agent.id)
        )
    ).all()

    total = len(rows)
    success = sum(1 for r in rows if r[1] == "success")
    errors = total - success
    latencies = sorted(r[2] for r in rows if r[2] is not None)
    total_in = sum(r[3] or 0 for r in rows)
    total_out = sum(r[4] or 0 for r in rows)
    total_cost = float(sum(r[5] or 0 for r in rows))
    avg_latency = (sum(latencies) / len(latencies)) if latencies else None

    daily: dict[str, int] = {}
    consumers: dict[str, int] = {}
    for r in rows:
        if r[0] is not None:
            day = r[0].date().isoformat()
            daily[day] = daily.get(day, 0) + 1
        if r[6]:
            consumers[r[6]] = consumers.get(r[6], 0) + 1

    return AIAgentMeteringResponse(
        total_invocations=total,
        success_count=success,
        error_count=errors,
        success_rate=round(success / total, 4) if total else 0.0,
        error_rate=round(errors / total, 4) if total else 0.0,
        avg_latency_ms=round(avg_latency, 2) if avg_latency is not None else None,
        latency_p50=_percentile(latencies, 50),
        latency_p95=_percentile(latencies, 95),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost=round(total_cost, 6),
        reputation_score=float(agent.reputation_score)
        if agent.reputation_score is not None
        else None,
        daily_invocations=[DataPoint(date=d, count=c) for d, c in sorted(daily.items())],
        by_consumer=[
            NameCount(name=n, count=c)
            for n, c in sorted(consumers.items(), key=lambda x: x[1], reverse=True)
        ],
    )


# ---------------------------------------------------------------------------
# Phase 2 — 에이전트 카드 (A2A)
# ---------------------------------------------------------------------------


async def get_agent_card(session: AsyncSession, name: str) -> AIAgentCard | None:
    """보유 메타데이터로부터 A2A 스타일 에이전트 카드를 조립한다(별도 저장 없음)."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    tools = (
        (
            await session.execute(
                select(AIAgentTool).where(AIAgentTool.agent_id == agent.id).order_by(AIAgentTool.id)
            )
        )
        .scalars()
        .all()
    )
    return AIAgentCard(
        name=agent.name,
        display_name=agent.display_name,
        description=agent.description,
        version=agent.version,
        url=agent.endpoint,
        protocol=agent.protocol,
        auth_method=agent.auth_method,
        provider=AgentCardProvider(organization=agent.department, contact=agent.owner_email),
        capabilities=list(agent.capabilities or []),
        supported_languages=list(agent.supported_languages or []),
        skills=[AgentCardSkill(name=t.name, description=t.description) for t in tools],
        streaming=bool(agent.streaming),
    )


# ---------------------------------------------------------------------------
# Phase 3 연동 인터페이스 — 정책 번들 (pull) + 집행 훅 이벤트 (push)
# ---------------------------------------------------------------------------


async def get_policy_bundle(session: AsyncSession, name: str) -> AIAgentPolicyBundle | None:
    """외부 Execution Plane이 런타임 집행을 위해 pull 하는 정책 번들을 조립한다.

    카탈로그가 보유한 거버넌스 메타데이터(허용 도구/MCP, egress, DLP, HITL,
    가드레일, 예산)를 집행기 친화적 형태로 묶어 읽기 전용으로 제공한다.
    """
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    tools = (
        (
            await session.execute(
                select(AIAgentTool).where(AIAgentTool.agent_id == agent.id).order_by(AIAgentTool.id)
            )
        )
        .scalars()
        .all()
    )
    mcps = (
        (
            await session.execute(
                select(AIAgentMcpServer)
                .where(AIAgentMcpServer.agent_id == agent.id)
                .order_by(AIAgentMcpServer.id)
            )
        )
        .scalars()
        .all()
    )
    # MCP Server 카탈로그 연계: 카탈로그 배포를 참조하는 연결은 실효 도구(노출 − 차단 ∩ 허용)를
    # 정책에 싣고 blocked/retired 배포는 제외한다. 직접 입력("미등록") 연결은 기존처럼 그대로 싣는다.
    from app.mcp_servers import service as mcp_service
    from app.mcp_servers.models import McpDeployment

    allowed_tools = [
        PolicyTool(name=t.name, description=t.description, schema=t.tool_schema,
                   risk=t.risk, requires_approval=bool(t.requires_approval))
        for t in tools
    ]
    allowed_mcp: list[PolicyMcpServer] = []
    # 변경 감지 토큰: 에이전트 자신과 연결된(유효한) 배포들의 updated_at 중 가장 최근 값
    stamps =[agent.updated_at] if agent.updated_at else []
    for m in mcps:
        if not m.mcp_deployment_id:
            allowed_mcp.append(PolicyMcpServer(name=m.name, url=m.url, auth_method=m.auth_method))
            continue
        dep = await session.get(McpDeployment, m.mcp_deployment_id)
        if not dep or dep.status in ("blocked", "retired"):
            logger.debug("정책 번들에서 MCP 연결 제외(배포 없음·차단·폐기): agent=%s mcp=%s", name, m.name)
            continue
        if dep.updated_at:
            stamps.append(dep.updated_at)
        effective = await mcp_service.effective_tools_for_link(session, m)
        allowed_mcp.append(PolicyMcpServer(
            name=m.name, url=dep.endpoint, auth_method=dep.auth_method, registered=True,
            deployment=dep.name, urn=dep.urn, allowed_tools=[e.name for e, _ in effective],
        ))
        allowed_tools.extend(
            PolicyTool(name=e.name, description=row.description, schema=row.input_schema,
                       mcp_deployment=dep.name, risk=e.risk, requires_approval=e.requires_approval)
            for e, row in effective
        )
    policy_version = max(stamps).isoformat() if stamps else agent.version
    logger.debug("정책 번들 조립: agent=%s tools=%d mcp=%d policy_version=%s",
                 name, len(allowed_tools), len(allowed_mcp), policy_version)
    return AIAgentPolicyBundle(
        name=agent.name,
        urn=agent.urn,
        version=agent.version,
        status=agent.status,
        policy_version=policy_version,
        max_steps=agent.max_steps,
        budget_limit=float(agent.budget_limit) if agent.budget_limit is not None else None,
        network_allowlist=list(agent.network_allowlist or []),
        allowed_tools=allowed_tools,
        allowed_mcp_servers=allowed_mcp,
        pii_handling=agent.pii_handling,
        data_residency=agent.data_residency,
        dlp_policies=list(agent.dlp_policies or []),
        hitl_required=agent.hitl_required,
        hitl_config=agent.hitl_config,
        guardrails=agent.guardrails,
        auth_method=agent.auth_method,
        protocol=agent.protocol,
        endpoint=agent.endpoint,
    )


async def ingest_hook_event(
    session: AsyncSession, name: str, req: HookEventIngest
) -> HookEventResponse | None:
    """외부 런타임의 인라인 훅이 보고한 집행 이벤트를 감사 기록으로 적재한다."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    data = req.model_dump()
    metadata = data.pop("metadata", None)
    ev = AIAgentHookEvent(agent_id=agent.id, event_metadata=metadata, **data)
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    logger.info(
        "AIAgent 훅 이벤트: agent=%s stage=%s decision=%s policy=%s",
        name,
        req.stage,
        req.decision,
        req.policy_ref,
    )
    return HookEventResponse.model_validate(ev)


async def list_hook_events(
    session: AsyncSession,
    name: str,
    stage: str | None = None,
    decision: str | None = None,
    limit: int = 100,
) -> list[HookEventResponse] | None:
    """집행 훅 이벤트 감사 로그를 조회한다(최신순)."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    query = select(AIAgentHookEvent).where(AIAgentHookEvent.agent_id == agent.id)
    if stage:
        query = query.where(AIAgentHookEvent.stage == stage)
    if decision:
        query = query.where(AIAgentHookEvent.decision == decision)
    query = query.order_by(AIAgentHookEvent.occurred_at.desc()).limit(limit)
    rows = (await session.execute(query)).scalars().all()
    return [HookEventResponse.model_validate(e) for e in rows]
