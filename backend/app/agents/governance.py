# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 분류와 책임 — 책임자 상태 · 정보 완성도 · 최고 위험도 계산, 목록 필터·정렬, 부서별 통계.

설계: design/ai-agent-ownership-design.md §3·§4·§5

모두 조회 때 계산하고 저장하지 않는다(수백~수천 건 기준). 목록은 단순 조건(검색·상태·부서 등)을 SQL 로 거른 뒤,
계산이 필요한 조건(태그·위험도·책임자 상태·정보 부족)과 정렬은 여기서 처리하고 페이지를 자른다.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AIAgent, AIAgentEval, AIAgentTool
from app.core.text_list import decode_list

logger = logging.getLogger(__name__)

RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
OWNER_STATES = ("ok", "missing", "unknown", "inactive")
SORTS = ("updated", "name", "usage", "last_invoked", "completeness", "risk")

# (키, 가중치, 표시 이름) — 합 100
COMPLETENESS_ITEMS: tuple[tuple[str, int, str], ...] = (
    ("ownership", 25, "소유자·부서·분류"),
    ("description", 15, "설명(20자 이상)"),
    ("endpoint", 10, "호출 주소"),
    ("model", 10, "기반 모델"),
    ("tool_risk", 10, "도구 위험도"),
    ("io_schema", 10, "입출력 형식"),
    ("safeguards", 10, "안전장치(개인정보 처리 + 가드레일 또는 사람 승인)"),
    ("evaluation", 10, "평가 기록"),
)
LABELS = {k: label for k, _, label in COMPLETENESS_ITEMS}


@dataclass
class AgentFacts:
    """에이전트 하나의 계산 결과."""

    owner_state: str
    completeness: int
    missing: list[str] = field(default_factory=list)  # COMPLETENESS_ITEMS 의 키
    max_risk: str | None = None


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------


async def owner_states(session: AsyncSession, emails: set[str]) -> dict[str, str]:
    """소문자 이메일 → 'ok' / 'inactive'. 사용자 목록에 없는 이메일은 결과에 없다('unknown')."""
    from app.usermgr.models import ArgusUser

    emails = {e.lower() for e in emails if e}
    if not emails:
        return {}
    rows = (await session.execute(
        select(func.lower(ArgusUser.email), ArgusUser.status).where(func.lower(ArgusUser.email).in_(emails))
    )).all()
    return {email: ("ok" if status == "active" else "inactive") for email, status in rows}


# 사용자 목록(argus_users)에서 확인하는 실행 신원 유형 — OAuth 클라이언트·워크로드 신원은 사용자 계정이 아니다
IDENTITY_USER_TYPES = ("service_account", "user_delegated")


async def identity_state(session: AsyncSession, agent: AIAgent) -> str | None:
    """실행 신원 상태 — 사용자 계정형 신원만 확인한다(TODO-021).

    ``identity_principal`` 을 사용자 이름이나 이메일로 찾아 활성이면 'ok', 비활성이면 'inactive',
    없으면 'unknown'. 확인 대상이 아니면(신원 없음·OAuth 클라이언트·워크로드) None.
    """
    from app.usermgr.models import ArgusUser

    principal = (agent.identity_principal or "").strip().lower()
    if not principal or agent.identity_type not in IDENTITY_USER_TYPES:
        return None
    status = (await session.execute(
        select(ArgusUser.status).where(
            (func.lower(ArgusUser.username) == principal) | (func.lower(ArgusUser.email) == principal))
    )).scalars().first()
    if status is None:
        return "unknown"
    return "ok" if status == "active" else "inactive"


def owner_state_of(agent: AIAgent, known: dict[str, str]) -> str:
    """책임자 상태 — 소유자 없음 'missing', 사용자 목록에 없음 'unknown', 그 밖은 ``owner_states`` 결과."""
    if not agent.owner_email:
        return "missing"
    return known.get(agent.owner_email.lower(), "unknown")


def completeness_of(agent: AIAgent, tool_risks: list[str | None], eval_count: int) -> tuple[int, list[str]]:
    """정보 완성도(0~100, ``COMPLETENESS_ITEMS`` 가중치 합)와 채워지지 않은 항목 키 목록."""
    checks = {
        "ownership": bool(agent.owner_email and agent.department and agent.category),
        "description": len((agent.description or "").strip()) >= 20,
        "endpoint": bool(agent.endpoint),
        "model": bool(agent.base_model),
        # 모든 도구에 위험도가 있어야 충족(도구가 하나도 없으면 all([]) 이므로 충족)
        "tool_risk": all(r for r in tool_risks),
        "io_schema": bool(agent.input_schema) and bool(agent.output_schema),
        "safeguards": bool(agent.pii_handling) and (bool(agent.guardrails) or bool(agent.hitl_required)),
        "evaluation": eval_count > 0,
    }
    score = sum(w for k, w, _ in COMPLETENESS_ITEMS if checks[k])
    return score, [k for k, _, _ in COMPLETENESS_ITEMS if not checks[k]]


def max_risk_of(tool_risks: list[str | None]) -> str | None:
    """도구 위험도 중 가장 높은 값(``RISK_ORDER`` 기준). 알려진 값이 없으면 None."""
    known =[r for r in tool_risks if r in RISK_ORDER]
    return max(known, key=RISK_ORDER.__getitem__) if known else None


async def mcp_tool_risks(session: AsyncSession, agent_ids: list[int]) -> dict[int, list[str | None]]:
    """에이전트별로 연결한 MCP 배포에서 쓸 수 있는 도구의 위험도 목록.

    쓸 수 있는 도구는 MCP 카탈로그의 실효 도구(노출 − 차단 ∩ 연결의 허용 그룹·도구)이다. 도구 위험도가 없으면
    그 도구가 속한 그룹의 기본 위험도를 쓴다. 미등록 MCP(배포 참조 없음)와 차단·폐기된 배포는 도구를 알 수 없어 뺀다.
    배포마다 실효 도구를 한 번만 계산한다.
    """
    from app.agents.models import AIAgentMcpServer
    from app.mcp_servers.models import McpDeployment
    from app.mcp_servers.service import _effective_tools, _filter_agent_tools

    links = (await session.execute(
        select(AIAgentMcpServer).where(AIAgentMcpServer.agent_id.in_(agent_ids),
                                       AIAgentMcpServer.mcp_deployment_id.is_not(None))
    )).scalars().all()
    out: dict[int, list[str | None]] = defaultdict(list)
    cache: dict[int, tuple] = {}
    for link in links:
        dep_id = link.mcp_deployment_id
        if dep_id not in cache:
            dep = await session.get(McpDeployment, dep_id)
            if not dep or dep.status in ("blocked", "retired"):
                cache[dep_id] = ([], {})
            else:
                tools, groups = await _effective_tools(session, dep)
                cache[dep_id] = (tools, {g.key: g.risk for g in groups})
        tools, group_risk = cache[dep_id]
        for t in _filter_agent_tools(tools, link.allowed_groups, link.allowed_tools):
            out[link.agent_id].append(t.risk or group_risk.get(t.group_key))
    return out


async def facts_for(session: AsyncSession, agents: list[AIAgent]) -> dict[int, AgentFacts]:
    """에이전트 목록의 계산 결과(질의 3번: 도구 위험도·평가 수·사용자 상태).

    도구 위험도는 직접 등록한 도구와 연결한 MCP 배포에서 쓸 수 있는 도구를 함께 본다(``mcp_tool_risks``).
    """
    if not agents:
        return {}
    ids = [a.id for a in agents]
    risks: dict[int, list[str | None]] = defaultdict(list)
    for agent_id, risk in (await session.execute(
        select(AIAgentTool.agent_id, AIAgentTool.risk).where(AIAgentTool.agent_id.in_(ids))
    )).all():
        risks[agent_id].append(risk)
    for agent_id, mcp_risks in (await mcp_tool_risks(session, ids)).items():
        risks[agent_id].extend(mcp_risks)
    evals = dict((await session.execute(
        select(AIAgentEval.agent_id, func.count(AIAgentEval.id)).where(AIAgentEval.agent_id.in_(ids))
        .group_by(AIAgentEval.agent_id)
    )).all())
    known = await owner_states(session, {a.owner_email for a in agents if a.owner_email})
    out = {}
    for a in agents:
        score, missing = completeness_of(a, risks.get(a.id, []), evals.get(a.id, 0))
        out[a.id] = AgentFacts(owner_state=owner_state_of(a, known), completeness=score, missing=missing,
                               max_risk=max_risk_of(risks.get(a.id, [])))
    logger.debug("에이전트 계산 결과 생성: agents=%d known_owners=%d", len(agents), len(known))
    return out


# ---------------------------------------------------------------------------
# 목록 필터 · 정렬
# ---------------------------------------------------------------------------


def _csv(v: str | None) -> list[str]:
    """쉼표 구분 문자열 → 공백 제거한 비어 있지 않은 값 목록."""
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def sql_filters(base, *, department: str | None = None, owner: str | None = None, managed_by: str | None = None,
                risk_tier: str | None = None, autonomy_level: str | None = None):
    """SQL 로 거를 수 있는 조건. ``owner`` 는 이미 'me' 를 실제 이메일로 바꾼 값.

    ``risk_tier``·``autonomy_level`` 은 쉼표로 여러 값을 받고 ``none`` 은 값이 비어 있는 에이전트를 뜻한다.
    """
    for col, raw in ((AIAgent.risk_tier, risk_tier), (AIAgent.autonomy_level, autonomy_level)):
        vals = _csv(raw)
        if vals:
            named = [v for v in vals if v != "none"]
            cond = col.in_(named) if named else None
            if "none" in vals:
                cond = col.is_(None) if cond is None else (cond | col.is_(None))
            base = base.where(cond)
    depts = decode_list(department)  # JSON 배열(값 안의 쉼표 보존, TODO-144) 또는 예전 쉼표 문자열
    if depts:
        base = base.where(AIAgent.department.in_(depts))
    if owner:
        base = base.where(func.lower(AIAgent.owner_email) == owner.lower())
    managed = _csv(managed_by)
    if managed:
        base = base.where(AIAgent.managed_by.in_(managed))
    return base


def _epoch(ts: _dt.datetime | None) -> float:
    """정렬 키용 epoch 초. None 은 -inf(가장 오래된 것으로), tz 없는 값은 UTC 로 간주."""
    if ts is None:
        return float("-inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.timestamp()


def filter_and_sort(
    agents: list[AIAgent], facts: dict[int, AgentFacts], *, tag: str | None = None, risk: str | None = None,
    owner_state: str | None = None, incomplete: bool = False, sort: str | None = None,
) -> list[AIAgent]:
    """계산 결과가 필요한 조건으로 거르고 정렬한다.

    태그는 모두 포함(AND), 위험도는 이 값 이상, ``owner_state='attention'`` 은 ok 를 뺀 나머지 상태 전부.
    알 수 없는 정렬 키는 ``updated`` 로, '-' 접두사는 기본 방향을 뒤집는다.
    """
    tags = decode_list(tag)  # 태그 값에 쉼표가 들어갈 수 있다 — JSON 배열 또는 예전 쉼표 문자열(TODO-144)
    min_risk = RISK_ORDER.get(risk or "", 0)
    states =set(OWNER_STATES) - {"ok"} if owner_state == "attention" else set(_csv(owner_state))

    def keep(a: AIAgent) -> bool:
        f = facts[a.id]
        if tags and not set(tags) <= set(a.tags or []):
            return False
        if min_risk and RISK_ORDER.get(f.max_risk or "", 0) < min_risk:
            return False
        if states and f.owner_state not in states:
            return False
        return not (incomplete and f.completeness >= 100)

    out = [a for a in agents if keep(a)]
    key_name = (sort or "updated").lstrip("-")
    reverse_flip = (sort or "").startswith("-")
    # 기본 방향: 이름은 오름차순, 완성도는 낮은 순, 나머지는 큰 값(최근·많음·높음) 먼저
    keys = {
        "name": (lambda a: a.name, False),
        "updated": (lambda a: _epoch(a.updated_at), True),
        "usage": (lambda a: a.usage_count or 0, True),
        "last_invoked": (lambda a: _epoch(a.last_invoked_at), True),
        "completeness": (lambda a: facts[a.id].completeness, False),
        "risk": (lambda a: RISK_ORDER.get(facts[a.id].max_risk or "", 0), True),
    }
    fn, desc = keys.get(key_name, keys["updated"])
    out.sort(key=lambda a: a.name)  # 같은 값끼리는 이름순(안정 정렬)
    out.sort(key=fn, reverse=desc != reverse_flip)
    logger.debug("에이전트 필터·정렬: 입력 %d건 → %d건 (sort=%s)", len(agents), len(out), sort or "updated")
    return out


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


async def stats(session: AsyncSession) -> dict:
    """책임자 확인 필요 수, 정보 부족 수, 부서별 평균 완성도·필수 항목 누락 수."""
    agents = list((await session.execute(select(AIAgent))).scalars().all())
    facts = await facts_for(session, agents)
    by_dept: dict[str, list] = defaultdict(list)
    for a in agents:
        by_dept[a.department or ""].append(a)
    rows = []
    for dept, items in by_dept.items():
        rows.append({
            "department": dept or None,
            "agents": len(items),
            "avg_completeness": round(sum(facts[a.id].completeness for a in items) / len(items)),
            "missing_required": sum(1 for a in items if "ownership" in facts[a.id].missing),
            "owner_attention": sum(1 for a in items if facts[a.id].owner_state != "ok"),
        })
    rows.sort(key=lambda r: (r["avg_completeness"], -r["agents"]))
    return {
        "owner_attention_count": sum(1 for f in facts.values() if f.owner_state != "ok"),
        "incomplete_count": sum(1 for f in facts.values() if f.completeness < 100),
        "by_department": rows,
    }
