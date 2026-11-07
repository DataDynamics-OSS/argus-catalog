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


async def facts_for(session: AsyncSession, agents: list[AIAgent]) -> dict[int, AgentFacts]:
    """에이전트 목록의 계산 결과(질의 3번: 도구 위험도·평가 수·사용자 상태)."""
    if not agents:
        return {}
    ids = [a.id for a in agents]
    risks: dict[int, list[str | None]] = defaultdict(list)
    for agent_id, risk in (await session.execute(
        select(AIAgentTool.agent_id, AIAgentTool.risk).where(AIAgentTool.agent_id.in_(ids))
    )).all():
        risks[agent_id].append(risk)
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


def sql_filters(base, *, department: str | None = None, owner: str | None = None, managed_by: str | None = None):
    """SQL 로 거를 수 있는 조건. ``owner`` 는 이미 'me' 를 실제 이메일로 바꾼 값."""
    depts = _csv(department)
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
    tags = _csv(tag)
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
