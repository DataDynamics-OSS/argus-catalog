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

logger = logging.getLogger(__name__)


def _generate_agent_urn(name: str) -> str:
    """에이전트 URN 을 ``{name}.agent`` 형식으로 생성한다."""
    return f"{name}.agent"


# ---------------------------------------------------------------------------
# AIAgent CRUD
# ---------------------------------------------------------------------------


async def get_agent_created_by(session: AsyncSession, name: str) -> str | None:
    """에이전트 생성자 — 소유권 체크용."""
    return (await session.execute(
        select(AIAgent.created_by).where(AIAgent.name == name)
    )).scalar_one_or_none()


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
    await session.commit()
    await session.refresh(agent)
    logger.info("AIAgent 생성됨: %s (id=%d, urn=%s)", agent.name, agent.id, agent.urn)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 생성 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("ai_agent", agent.id)

    return AIAgentSummary.model_validate(agent)


async def _get_agent_by_name(session: AsyncSession, name: str) -> AIAgent | None:
    result = await session.execute(select(AIAgent).where(AIAgent.name == name))
    return result.scalars().first()


async def list_agents(
    session: AsyncSession,
    search: str | None = None,
    status: str | None = None,
    framework: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedAIAgents:
    """에이전트 목록(검색·필터·페이지네이션)."""
    base = select(AIAgent)
    if search:
        like = f"%{search}%"
        base = base.where(
            (AIAgent.name.ilike(like))
            | (AIAgent.display_name.ilike(like))
            | (AIAgent.description.ilike(like))
        )
    if status:
        base = base.where(AIAgent.status == status)
    if framework:
        base = base.where(AIAgent.framework == framework)
    if category:
        base = base.where(AIAgent.category == category)

    total = await session.scalar(select(func.count()).select_from(base.subquery()))

    offset = (page - 1) * page_size
    query = base.order_by(AIAgent.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    items = [AIAgentSummary.model_validate(a) for a in result.scalars().all()]

    return PaginatedAIAgents(items=items, total=total or 0, page=page, page_size=page_size)


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
    detail.mcp_servers = [AIAgentMcpServerResponse.model_validate(m) for m in mcps]
    detail.lineage = [AIAgentLineageResponse.model_validate(line) for line in lineage]
    detail.versions = [AIAgentVersionResponse.model_validate(v) for v in versions]
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


async def add_tool(
    session: AsyncSession, name: str, req: AIAgentToolCreate
) -> AIAgentToolResponse | None:
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    tool = AIAgentTool(agent_id=agent.id, **req.model_dump())
    session.add(tool)
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
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    mcp = AIAgentMcpServer(agent_id=agent.id, **req.model_dump())
    session.add(mcp)
    await session.commit()
    await session.refresh(mcp)
    logger.info("AIAgent MCP 추가됨: agent=%s mcp=%s", name, req.name)
    return AIAgentMcpServerResponse.model_validate(mcp)


async def add_lineage(
    session: AsyncSession, name: str, req: AIAgentLineageCreate
) -> AIAgentLineageResponse | None:
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    line = AIAgentLineage(agent_id=agent.id, **req.model_dump())
    session.add(line)
    await session.commit()
    await session.refresh(line)
    logger.info("AIAgent 리니지 추가됨: agent=%s -> %s(%s)", name, req.target_ref, req.relation)
    return AIAgentLineageResponse.model_validate(line)


async def create_version(
    session: AsyncSession, name: str, req: AIAgentVersionCreate
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
        raise ValueError(f"Version '{req.version}' already exists for agent '{name}'")

    ver = AIAgentVersion(agent_id=agent.id, **req.model_dump())
    session.add(ver)
    # 메인 엔티티의 현재 버전도 최신으로 갱신
    agent.version = req.version
    await session.commit()
    await session.refresh(ver)
    logger.info("AIAgent 버전 생성됨: agent=%s version=%s", name, req.version)
    return AIAgentVersionResponse.model_validate(ver)


async def list_versions(session: AsyncSession, name: str) -> list[AIAgentVersionResponse] | None:
    agent = await _get_agent_by_name(session, name)
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
    rows = (await session.execute(select(column, func.count()).group_by(column))).all()
    return [NameCount(name=str(value), count=count) for value, count in rows if value is not None]


async def get_stats(session: AsyncSession) -> AIAgentStats:
    """대시보드 통계."""
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

    return AIAgentStats(
        total_agents=total,
        active_agents=active,
        by_status=await _group_count(session, AIAgent.status),
        by_framework=await _group_count(session, AIAgent.framework),
        by_category=await _group_count(session, AIAgent.category),
        multi_agent_count=multi,
        hitl_required_count=hitl,
        total_invocations=int(invocations),
    )


# ---------------------------------------------------------------------------
# 서브리소스 삭제 (tools / mcp / lineage)
# ---------------------------------------------------------------------------


async def _delete_child(session: AsyncSession, model, name: str, child_id: int) -> bool:
    """에이전트 소속 서브리소스를 삭제한다(존재/소유 검증 포함)."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return False
    result = await session.execute(
        sa_delete(model).where(model.id == child_id, model.agent_id == agent.id)
    )
    await session.commit()
    deleted = result.rowcount and result.rowcount > 0
    if deleted:
        logger.info("AIAgent 서브리소스 삭제됨: agent=%s %s=%d", name, model.__name__, child_id)
    return bool(deleted)


async def delete_tool(session: AsyncSession, name: str, tool_id: int) -> bool:
    deleted = await _delete_child(session, AIAgentTool, name, tool_id)
    if deleted:
        # 도구는 임베딩 소스 텍스트에 포함되므로 재임베딩
        agent = await _get_agent_by_name(session, name)
        if agent:
            from app.embedding.service import embed_entity_background
            await embed_entity_background("ai_agent", agent.id)
    return deleted


async def delete_mcp_server(session: AsyncSession, name: str, mcp_id: int) -> bool:
    return await _delete_child(session, AIAgentMcpServer, name, mcp_id)


async def delete_lineage(session: AsyncSession, name: str, lineage_id: int) -> bool:
    return await _delete_child(session, AIAgentLineage, name, lineage_id)


# ---------------------------------------------------------------------------
# Phase 2 — 평가 (Evaluation)
# ---------------------------------------------------------------------------


async def add_eval(
    session: AsyncSession, name: str, req: AIAgentEvalCreate
) -> AIAgentEvalResponse | None:
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    ev = AIAgentEval(agent_id=agent.id, **req.model_dump())
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    # 평가가 들어오면 평판 점수를 갱신
    await _recompute_reputation(session, agent)
    await session.commit()
    logger.info("AIAgent 평가 추가됨: agent=%s type=%s key=%s", name, req.eval_type, req.metric_key)
    return AIAgentEvalResponse.model_validate(ev)


async def list_evals(session: AsyncSession, name: str) -> list[AIAgentEvalResponse] | None:
    agent = await _get_agent_by_name(session, name)
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


async def ingest_invocation(session: AsyncSession, name: str, req: AIAgentInvocationIngest) -> bool:
    """호출 텔레메트리를 적재하고 관측 캐시 컬럼/평판을 갱신한다."""
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
    errors = sum(1 for r in rows if r[3] == "error")

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
    policy_version = agent.updated_at.isoformat() if agent.updated_at else agent.version
    return AIAgentPolicyBundle(
        name=agent.name,
        urn=agent.urn,
        version=agent.version,
        status=agent.status,
        policy_version=policy_version,
        max_steps=agent.max_steps,
        budget_limit=float(agent.budget_limit) if agent.budget_limit is not None else None,
        network_allowlist=list(agent.network_allowlist or []),
        allowed_tools=[
            PolicyTool(name=t.name, description=t.description, schema=t.tool_schema) for t in tools
        ],
        allowed_mcp_servers=[
            PolicyMcpServer(name=m.name, url=m.url, auth_method=m.auth_method) for m in mcps
        ],
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
