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
import re

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import discovery
from app.agents.models import (
    AIAgent,
    AIAgentDataAccess,
    AIAgentEval,
    AIAgentHookEvent,
    AIAgentInvocationLog,
    AIAgentLineage,
    AIAgentMcpServer,
    AIAgentSkill,
    AIAgentStatusHistory,
    AIAgentTool,
    AIAgentVersion,
)
from app.agents.schemas import (
    CATALOG_CARD_EXTENSION_URI,
    AgentCardCapabilities,
    AgentCardExtension,
    AgentCardInterface,
    AgentCardSkill,
    AgentOwnerInfo,
    AgentResourceUsage,
    AIAgentCard,
    AIAgentCreate,
    AIAgentDataAccessCreate,
    AIAgentDataAccessResponse,
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
    AIAgentSkillCreate,
    AIAgentSkillResponse,
    AIAgentSkillUpdate,
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
    PolicyDataAccess,
    PolicyIdentity,
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
    risk_tier: str | None = None,
    autonomy_level: str | None = None,
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
    base = governance.sql_filters(base, department=department, owner=owner, managed_by=managed_by,
                                  risk_tier=risk_tier, autonomy_level=autonomy_level)

    # 계산 조건·정렬을 위해 SQL 조건에 맞는 전체를 읽은 뒤 메모리에서 거르고 페이지를 자른다
    agents = list((await session.execute(base)).scalars().all())
    facts = await governance.facts_for(session, agents)
    rows = governance.filter_and_sort(agents, facts, tag=tag, risk=risk, owner_state=owner_state,
                                      incomplete=incomplete, sort=sort)
    logger.debug("AIAgent 목록: SQL 조건 일치 %d건 → 계산 조건 적용 후 %d건 (page=%d)", len(agents), len(rows), page)
    offset = (page - 1) * page_size
    page_rows = rows[offset:offset + page_size]
    owners = await _owner_infos(session, [a.owner_email for a in page_rows])
    items = []
    for a in page_rows:
        item = AIAgentSummary.model_validate(a)
        f = facts[a.id]
        item.owner_state, item.completeness, item.missing_fields, item.max_risk = (
            f.owner_state, f.completeness, f.missing, f.max_risk)
        item.owner = owners.get(a.owner_email.strip().lower()) if a.owner_email else None
        items.append(item)
    return PaginatedAIAgents(items=items, total=len(rows), page=page, page_size=page_size)


async def _owner_infos(session: AsyncSession, emails: list[str | None]) -> dict[str, AgentOwnerInfo]:
    """소유자 이메일들 → 등록 사용자 정보(소문자 이메일 키). 한 번의 IN 쿼리로 조회."""
    from app.usermgr.models import ArgusUser

    keys = {e.strip().lower() for e in emails if e and e.strip()}
    if not keys:
        return {}
    users = (await session.execute(
        select(ArgusUser).where(func.lower(ArgusUser.email).in_(keys))
    )).scalars().all()
    return {u.email.lower(): AgentOwnerInfo.model_validate(u) for u in users}


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
    detail.lineage = await _resolve_lineage(session, lineage)
    detail.versions = [AIAgentVersionResponse.model_validate(v) for v in versions]
    detail.skills = await _list_skills(session, agent.id)
    detail.data_access = await _list_data_access(session, agent.id)
    if agent.base_model_ref:
        from app.models.models import RegisteredModel

        model = await session.get(RegisteredModel, agent.base_model_ref)
        detail.base_model_name = model.name if model else None
    from app.agents import governance

    detail.identity_state = await governance.identity_state(session, agent)

    f = (await governance.facts_for(session, [agent]))[agent.id]
    detail.owner_state, detail.completeness, detail.missing_fields, detail.max_risk = (
        f.owner_state, f.completeness, f.missing, f.max_risk)
    if agent.owner_email:
        owners = await _owner_infos(session, [agent.owner_email])
        detail.owner = owners.get(agent.owner_email.strip().lower())
    return detail


async def _resolve_lineage(session: AsyncSession, rows) -> list[AIAgentLineageResponse]:
    """리니지 대상(자유 문자열)을 카탈로그 엔티티로 해석해 ``target_id`` 를 채운다(화면 링크·검증 표시용).

    agent 는 이름, model 은 등록 모델 이름·URN, dataset 은 데이터셋 URN·이름·정규화 이름으로 찾는다.
    찾지 못하면 None(외부 대상이거나 오타).
    """
    from app.catalog.models import Dataset
    from app.models.models import RegisteredModel

    out = []
    for line in rows:
        item = AIAgentLineageResponse.model_validate(line)
        ref = line.target_ref
        if line.target_type == "agent":
            q = select(AIAgent.id).where(AIAgent.name == ref)
        elif line.target_type == "model":
            q = select(RegisteredModel.id).where(
                (RegisteredModel.name == ref) | (RegisteredModel.urn == ref), RegisteredModel.status != "deleted")
        elif line.target_type == "dataset":
            q = select(Dataset.id).where((Dataset.urn == ref) | (Dataset.name == ref) | (Dataset.qualified_name == ref))
        else:
            q = None
        if q is not None:
            ids = (await session.execute(q.limit(2))).scalars().all()
            item.target_id = ids[0] if len(ids) == 1 else None  # 이름이 여러 개에 맞으면 연결하지 않는다
        out.append(item)
    return out


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
        by_risk_tier=await _group_count(session, AIAgent.risk_tier),
        unassessed_risk_count=await session.scalar(
            select(func.count(AIAgent.id)).where(AIAgent.risk_tier.is_(None), AIAgent.status != "retired")) or 0,
        high_risk_unassessed_count=await session.scalar(
            select(func.count(AIAgent.id)).where(
                AIAgent.risk_tier.in_(("high", "prohibited")), AIAgent.impact_assessed_at.is_(None),
                AIAgent.status != "retired")) or 0,
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
# 서브리소스: 스킬 (A2A AgentSkill, TODO-024)
# ---------------------------------------------------------------------------


async def _list_skills(session: AsyncSession, agent_id: int) -> list[AIAgentSkillResponse]:
    rows = (await session.execute(
        select(AIAgentSkill).where(AIAgentSkill.agent_id == agent_id)
        .order_by(AIAgentSkill.sort_order, AIAgentSkill.id)
    )).scalars().all()
    return [AIAgentSkillResponse.model_validate(r) for r in rows]


def _clean_list(values: list[str] | None) -> list[str] | None:
    """공백 제거·빈 값 제외·중복 제거(순서 유지). 결과가 비면 None."""
    if values is None:
        return None
    out = list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    return out or None


async def add_skill(session: AsyncSession, name: str, req: AIAgentSkillCreate) -> AIAgentSkillResponse | None:
    """스킬 추가. ``skill_key`` 를 비우면 이름에서 만든다. 키가 겹치면 ``ValueError``. 에이전트가 없으면 None."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    used = set((await session.execute(
        select(AIAgentSkill.skill_key).where(AIAgentSkill.agent_id == agent.id))).scalars().all())
    if req.skill_key:
        if req.skill_key in used:
            raise ValueError(f"스킬 ID '{req.skill_key}' 가 이미 있습니다")
        key = req.skill_key
    else:
        key = _card_skill_id(req.name, used)
    data = req.model_dump(exclude={"skill_key"})
    for f in ("tags", "examples", "input_modes", "output_modes"):
        data[f] = _clean_list(data[f])
    skill = AIAgentSkill(agent_id=agent.id, skill_key=key, **data)
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    logger.info("AIAgent 스킬 추가됨: agent=%s skill=%s", name, key)
    return AIAgentSkillResponse.model_validate(skill)


async def update_skill(
    session: AsyncSession, name: str, skill_id: int, req: AIAgentSkillUpdate
) -> AIAgentSkillResponse | None:
    """스킬 부분 수정(``skill_key`` 는 바꾸지 않는다 — 카드를 받아 간 쪽의 참조가 깨지지 않게). 없으면 None."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    skill = (await session.execute(
        select(AIAgentSkill).where(AIAgentSkill.id == skill_id, AIAgentSkill.agent_id == agent.id)
    )).scalars().first()
    if not skill:
        logger.warning("AIAgent 스킬 없음(수정): agent=%s id=%d", name, skill_id)
        return None
    data = req.model_dump(exclude_unset=True)
    for f in ("tags", "examples", "input_modes", "output_modes"):
        if f in data:
            data[f] = _clean_list(data[f])
    for k, v in data.items():
        setattr(skill, k, v)
    await session.commit()
    await session.refresh(skill)
    logger.info("AIAgent 스킬 수정됨: agent=%s skill=%s", name, skill.skill_key)
    return AIAgentSkillResponse.model_validate(skill)


async def delete_skill(session: AsyncSession, name: str, skill_id: int) -> bool:
    return await _delete_child(session, AIAgentSkill, name, skill_id)


# ---------------------------------------------------------------------------
# 서브리소스: 데이터 접근 (TODO-019)
# ---------------------------------------------------------------------------


async def _list_data_access(session: AsyncSession, agent_id: int) -> list[AIAgentDataAccessResponse]:
    """데이터 접근 목록 — 대상 데이터 소스·데이터셋 이름을 붙여 돌려준다."""
    from app.catalog.models import Dataset, DatasetSchema, Datasource

    rows = (await session.execute(
        select(AIAgentDataAccess).where(AIAgentDataAccess.agent_id == agent_id).order_by(AIAgentDataAccess.id)
    )).scalars().all()
    ds_ids = [r.dataset_id for r in rows if r.dataset_id]
    pii: set[int] = set()
    if ds_ids:
        pii = set((await session.execute(
            select(DatasetSchema.dataset_id).where(
                DatasetSchema.dataset_id.in_(ds_ids), DatasetSchema.pii_type.is_not(None),
                DatasetSchema.pii_type != "")
            .distinct()
        )).scalars().all())
    out = []
    for r in rows:
        item = AIAgentDataAccessResponse(
            id=r.id, target_type="dataset" if r.dataset_id else "datasource",
            datasource_id=r.datasource_id, dataset_id=r.dataset_id, access=r.access, description=r.description,
        )
        if r.dataset_id:
            item.contains_pii = r.dataset_id in pii
            ds = await session.get(Dataset, r.dataset_id)
            if ds:
                item.target_name, item.target_urn = ds.name, ds.urn
                src = await session.get(Datasource, ds.datasource_id)
                item.datasource_name = src.name if src else None
        elif r.datasource_id:
            src = await session.get(Datasource, r.datasource_id)
            if src:
                item.target_name, item.target_urn = src.name, src.datasource_id
        out.append(item)
    return out


async def _resolve_data_target(session: AsyncSession, req: AIAgentDataAccessCreate) -> tuple[int | None, int | None]:
    """요청의 대상 → (datasource_id, dataset_id) 내부 id. 찾지 못하면 ``ValueError``."""
    from app.catalog.models import Dataset, Datasource

    if req.dataset_id is not None or req.dataset_urn:
        q = select(Dataset.id).where(
            Dataset.id == req.dataset_id if req.dataset_id is not None else Dataset.urn == req.dataset_urn)
        ds_id = (await session.execute(q)).scalars().first()
        if ds_id is None:
            raise ValueError(f"데이터셋을 찾을 수 없습니다: {req.dataset_urn or req.dataset_id}")
        return None, ds_id
    if req.datasource_id is not None:
        q = select(Datasource.id).where(Datasource.id == req.datasource_id)
    else:
        # 이름은 고유하지 않으므로 datasource_id(식별 문자열)로도 찾는다
        q = select(Datasource.id).where(
            (Datasource.name == req.datasource_name) | (Datasource.datasource_id == req.datasource_name))
    src_ids = (await session.execute(q)).scalars().all()
    if not src_ids:
        raise ValueError(f"데이터 소스를 찾을 수 없습니다: {req.datasource_name or req.datasource_id}")
    if len(src_ids) > 1:
        raise ValueError(f"같은 이름의 데이터 소스가 여러 개입니다: {req.datasource_name} — 식별 ID 로 지정하세요")
    return src_ids[0], None


async def add_data_access(
    session: AsyncSession, name: str, req: AIAgentDataAccessCreate
) -> AIAgentDataAccessResponse | None:
    """데이터 접근 추가(정책 버전 갱신). 대상이 없거나 이미 등록돼 있으면 ``ValueError``. 에이전트가 없으면 None."""
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    src_id, ds_id = await _resolve_data_target(session, req)
    dup = (await session.execute(select(AIAgentDataAccess.id).where(
        AIAgentDataAccess.agent_id == agent.id,
        AIAgentDataAccess.datasource_id.is_(None) if src_id is None else AIAgentDataAccess.datasource_id == src_id,
        AIAgentDataAccess.dataset_id.is_(None) if ds_id is None else AIAgentDataAccess.dataset_id == ds_id,
    ))).scalars().first()
    if dup:
        raise ValueError("이미 등록된 데이터 접근입니다 — 기존 항목을 지우고 다시 추가하세요")
    row = AIAgentDataAccess(agent_id=agent.id, datasource_id=src_id, dataset_id=ds_id,
                            access=req.access, description=req.description)
    session.add(row)
    _bump_policy_version(agent)  # 정책 번들의 data_access 변경
    await session.commit()
    logger.info("AIAgent 데이터 접근 추가됨: agent=%s datasource=%s dataset=%s access=%s",
                name, src_id, ds_id, req.access)
    items = await _list_data_access(session, agent.id)
    return next(i for i in items if i.id == row.id)


async def delete_data_access(session: AsyncSession, name: str, access_id: int) -> bool:
    return await _delete_child(session, AIAgentDataAccess, name, access_id, affects_policy=True)


async def resource_usage(
    session: AsyncSession, *, dataset_id: int | None = None, datasource_id: int | None = None,
    model_id: int | None = None,
) -> list[AgentResourceUsage]:
    """자원을 쓰는 에이전트(역방향 조회). 하나의 자원만 받는다.

    - 데이터셋: 데이터 접근에 직접 등록된 것 + 소속 데이터 소스 전체를 등록한 것(via=datasource)
      + 리니지에 데이터셋 URN·이름으로 적힌 것(via=lineage).
    - 데이터 소스: 데이터 접근에 데이터 소스로 등록된 것.
    - 모델: 기반 모델(base_model_ref)로 연결된 것 + 리니지에 모델 이름·URN 으로 적힌 것.
    """
    from app.catalog.models import Dataset

    out: list[AgentResourceUsage] = []

    def _add(agent: AIAgent, relation: str, via: str, description: str | None = None):
        out.append(AgentResourceUsage(
            agent_id=agent.id, agent_name=agent.name, display_name=agent.display_name, status=agent.status,
            owner_email=agent.owner_email, relation=relation, via=via, description=description))

    async def _access(cond, via: str):
        rows = (await session.execute(
            select(AIAgent, AIAgentDataAccess).join(AIAgentDataAccess, AIAgentDataAccess.agent_id == AIAgent.id)
            .where(cond).order_by(AIAgent.name)
        )).all()
        for agent, acc in rows:
            _add(agent, acc.access, via, acc.description)

    async def _lineage(target_type: str, refs: list[str]):
        refs = [r for r in refs if r]
        if not refs:
            return
        rows = (await session.execute(
            select(AIAgent, AIAgentLineage).join(AIAgentLineage, AIAgentLineage.agent_id == AIAgent.id)
            .where(AIAgentLineage.target_type == target_type, AIAgentLineage.target_ref.in_(refs))
            .order_by(AIAgent.name)
        )).all()
        for agent, line in rows:
            _add(agent, line.relation, "lineage", line.description)

    if dataset_id is not None:
        ds = await session.get(Dataset, dataset_id)
        if ds is None:
            return []
        await _access(AIAgentDataAccess.dataset_id == ds.id, "data_access")
        await _access(AIAgentDataAccess.datasource_id == ds.datasource_id, "datasource")
        await _lineage("dataset", [ds.urn, ds.name, ds.qualified_name])
    elif datasource_id is not None:
        await _access(AIAgentDataAccess.datasource_id == datasource_id, "data_access")
    elif model_id is not None:
        from app.models.models import RegisteredModel

        model = await session.get(RegisteredModel, model_id)
        if model is None:
            return []
        for agent in (await session.execute(
            select(AIAgent).where(AIAgent.base_model_ref == model.id).order_by(AIAgent.name)
        )).scalars().all():
            _add(agent, "base_model", "base_model")
        await _lineage("model", [model.name, model.urn])
    return out


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


# 카탈로그 protocol 값 → A2A protocolBinding. A2A 에이전트는 JSON-RPC 가 기본 바인딩이다.
_CARD_BINDINGS = {"a2a": "JSONRPC", "jsonrpc": "JSONRPC", "grpc": "GRPC", "rest": "HTTP+JSON", "http": "HTTP+JSON"}


def _card_skill_id(label: str, used: set[str]) -> str:
    """기능 이름 → skill id(소문자·숫자·하이픈). 겹치면 뒤에 번호를 붙인다."""
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "skill"
    sid, n = base, 2
    while sid in used:
        sid, n = f"{base}-{n}", n + 1
    used.add(sid)
    return sid


def default_modes(agent: AIAgent) -> tuple[list[str], list[str]]:
    """에이전트 기본 입출력 MIME 형식. 등록값이 없으면 텍스트에, 입출력 스키마가 있으면 JSON 을 더한다."""
    inp = list(agent.default_input_modes or []) or (
        ["text/plain"] + (["application/json"] if agent.input_schema else []))
    out = list(agent.default_output_modes or []) or (
        ["text/plain"] + (["application/json"] if agent.output_schema else []))
    return inp, out


async def get_agent_card(session: AsyncSession, name: str) -> AIAgentCard | None:
    """보유 메타데이터로부터 A2A 1.0 에이전트 카드를 조립한다(별도 저장 없음).

    - skills: 등록된 스킬(``catalog_ai_agent_skills``)을 내보낸다. 스킬이 하나도 없으면 외부에 제공하는
      기능(``capabilities`` 목록)에서 만든다. 에이전트가 호출하는 도구(tools)는 내보내지 않는다.
    - supportedInterfaces: 호출 주소가 있을 때만. 바인딩은 ``protocol`` 에서(``_CARD_BINDINGS``, 모르면 HTTP+JSON).
    - 입출력 형식: 등록된 기본 형식, 없으면 텍스트가 기본이고 입출력 스키마가 있으면 ``application/json`` 을 더한다.
    - provider: A2A 는 조직 URL 이 필수라 넣지 않고, 소유자·부서 등 카탈로그 정보는 카탈로그 확장 ``params`` 에 싣는다.
    """
    agent = await _get_agent_by_name(session, name)
    if not agent:
        return None
    interfaces = []
    if agent.endpoint:
        binding = _CARD_BINDINGS.get((agent.protocol or "").strip().lower(), "HTTP+JSON")
        interfaces.append(AgentCardInterface(url=agent.endpoint, protocol_binding=binding))
    tags = [str(t) for t in (agent.tags or [])]
    stored = await _list_skills(session, agent.id)
    if stored:
        skills = [
            AgentCardSkill(
                id=sk.skill_key, name=sk.name, description=sk.description or sk.name,
                tags=list(sk.tags or []), examples=sk.examples or None,
                input_modes=sk.input_modes or None, output_modes=sk.output_modes or None,
            )
            for sk in stored
        ]
    else:
        used: set[str] = set()
        skills = [
            AgentCardSkill(id=_card_skill_id(str(c), used), name=str(c), description=str(c), tags=tags)
            for c in (agent.capabilities or []) if str(c).strip()
        ]
    input_modes, output_modes = default_modes(agent)
    params = {
        "name": agent.name,
        "urn": agent.urn,
        "displayName": agent.display_name,
        "status": agent.status,
        "owner": agent.owner_email,
        "department": agent.department,
        "category": agent.category,
        "protocol": agent.protocol,
        "invocationMethod": agent.invocation_method,
        "authMethod": agent.auth_method,
        "supportedLanguages": list(agent.supported_languages or []),
        "capabilities": list(agent.capabilities or []),
        "riskTier": agent.risk_tier,
        "autonomyLevel": agent.autonomy_level,
        # skills 를 등록 스킬에서 만들었는지(skills), 기능 목록에서 만들었는지(capabilities) — 가져오기에서 구분
        "skillSource": "skills" if stored else ("capabilities" if skills else None),
    }
    extension = AgentCardExtension(
        uri=CATALOG_CARD_EXTENSION_URI,
        description="Argus Catalog 에이전트 정보(식별 이름·상태·소유자·부서·인증 방식·위험 등급 등)",
        params={k: v for k, v in params.items() if v not in (None, [], "")},
    )
    return AIAgentCard(
        name=agent.display_name or agent.name,
        description=agent.description or agent.display_name or agent.name,
        version=agent.version,
        supported_interfaces=interfaces,
        capabilities=AgentCardCapabilities(streaming=bool(agent.streaming), extensions=[extension]),
        default_input_modes=input_modes,
        default_output_modes=output_modes,
        skills=skills,
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
        data_access=[
            PolicyDataAccess(target_type=d.target_type, name=d.target_name, urn=d.target_urn, access=d.access)
            for d in await _list_data_access(session, agent.id)
        ],
        identity=PolicyIdentity(
            type=agent.identity_type, principal=agent.identity_principal, provider=agent.identity_provider,
            scopes=list(agent.granted_scopes or []), on_behalf_of=bool(agent.on_behalf_of),
        ) if agent.identity_type or agent.identity_principal else None,
        risk_tier=agent.risk_tier,
        autonomy_level=agent.autonomy_level,
        log_retention_days=agent.log_retention_days,
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
