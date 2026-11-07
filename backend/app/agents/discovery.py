# SPDX-License-Identifier: Apache-2.0
"""미등록 에이전트 발견.

실행 환경(Execution Plane)이 카탈로그에 없는 이름으로 호출 기록(``/invocations``)·정책 판정 기록(``/hook-events``)을
보내면 지금처럼 404 를 돌려주되, 이름별로 발견 기록(``catalog_ai_agent_discoveries``)에 모은다. 관리 화면에서
'미등록 에이전트'로 보여 주고, 초안 에이전트로 등록하거나 무시할 수 있다.

설계: design/ai-agent-bulk-registration-design.md §5

남용 방지
- 이름 형식 제한(영문·숫자로 시작, 영문·숫자·점·하이픈·밑줄, 최대 255자) — 어긋나면 기록하지 않는다.
- 최근 24시간 동안 새로 기록되는 이름 수 상한(``MAX_NEW_PER_DAY``). 이미 있는 이름의 횟수 갱신은 상한과 무관.
- 기록 실패는 호출 응답(404)에 영향을 주지 않는다.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AIAgent, AIAgentDiscovery
from app.agents.schemas import (
    AIAgentDiscoveryRegister,
    AIAgentDiscoveryResponse,
    AIAgentSummary,
    PaginatedAIAgentDiscoveries,
)
from app.core.sql import LIKE_ESCAPE, like_contains

logger = logging.getLogger(__name__)

# 영문·숫자로 시작, 이후 영문·숫자·점·하이픈·밑줄, 전체 1~255자
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
MAX_NEW_PER_DAY = 1000
SOURCES = ("invocation", "hook_event")
STATUSES = ("new", "ignored", "registered")


def missing_required(owner_email: str | None, department: str | None, category: str | None) -> str | None:
    """새 에이전트 필수 항목(소유자 이메일·부서·분류) 검사 — 문제 있으면 사유, 없으면 None.

    화면 등록(``POST /ai-agents``)과 발견 기록 등록이 같은 규칙을 쓴다.
    """
    missing = [label for label, v in (("소유자 이메일(owner_email)", owner_email),
                                      ("부서(department)", department), ("분류(category)", category))
               if not (v and v.strip())]
    if missing:
        return f"필수 항목이 비어 있습니다: {', '.join(missing)}"
    if "@" not in owner_email:
        return "소유자는 이메일 형식이어야 합니다."
    return None


class RequiredFieldsError(ValueError):
    """새로 만들 에이전트의 필수 항목이 비었거나 형식이 틀림 → HTTP 422."""


class DiscoveryStateError(ValueError):
    """현재 상태에서 할 수 없는 처리 — HTTP 409."""


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


async def record(
    session: AsyncSession, name: str, source: str, reported_by: str | None, consumer: str | None = None,
) -> bool:
    """없는 이름으로 들어온 기록을 발견 기록에 반영한다. 기록했으면 True.

    호출한 쪽의 세션을 그대로 쓰며 여기서 커밋한다. 오류는 삼키고 로그만 남긴다(호출 응답은 계속 404).
    """
    if source not in SOURCES or not NAME_PATTERN.match(name or ""):
        logger.warning("미등록 에이전트 기록 건너뜀(형식 불일치): %r", (name or "")[:80])
        return False
    try:
        return await _record(session, name, source, reported_by, consumer)
    except Exception:
        await session.rollback()
        logger.exception("미등록 에이전트 기록 실패: %s", name)
        return False


async def _record(session, name, source, reported_by, consumer, _retry: bool = True) -> bool:
    """발견 기록 upsert 본체. 새 이름이면 24시간 상한을 검사하고, 동시 삽입 충돌 시 한 번만 재시도한다."""
    row =(await session.execute(select(AIAgentDiscovery).where(AIAgentDiscovery.name == name))).scalars().first()
    now = _now()
    if row is None:
        since = now - _dt.timedelta(hours=24)
        recent = await session.scalar(
            select(func.count(AIAgentDiscovery.id)).where(AIAgentDiscovery.first_seen_at >= since)
        ) or 0
        if recent >= MAX_NEW_PER_DAY:
            logger.warning("미등록 에이전트 기록 상한 도달(24시간 %d건) — 건너뜀: %s (보낸 계정 %s)",
                           recent, name, reported_by)
            return False
        row = AIAgentDiscovery(name=name, first_seen_at=now, last_seen_at=now, seen_count=0,
                               invocation_count=0, hook_event_count=0, status="new")
        session.add(row)
        logger.info("미등록 에이전트 발견: %s (출처 %s, 보낸 계정 %s)", name, source, reported_by)
    elif row.status == "registered" and (
        row.agent_id is None or await session.get(AIAgent, row.agent_id) is None
    ):
        # 등록했던 에이전트가 삭제된 뒤 다시 들어오면 처리 전으로 되돌린다
        row.status, row.agent_id, row.status_changed_by, row.status_changed_at = "new", None, None, now
        logger.info("미등록 에이전트 재발견(등록 에이전트 삭제됨) — 처리 전으로 되돌림: %s", name)
    row.seen_count += 1
    if source == "invocation":
        row.invocation_count += 1
    else:
        row.hook_event_count += 1
    row.last_seen_at = now
    row.reported_by = reported_by
    if consumer:
        row.last_consumer = consumer[:200]
    try:
        await session.commit()
    except IntegrityError:
        # 같은 이름이 동시에 처음 들어온 경우 — 다른 요청이 만든 행에 다시 반영
        await session.rollback()
        if not _retry:
            raise
        logger.warning("미등록 에이전트 기록 동시 삽입 충돌 — 재시도: %s", name)
        return await _record(session, name, source, reported_by, consumer, _retry=False)
    logger.debug("미등록 에이전트 기록 갱신: %s (출처 %s)", name, source)
    return True


async def mark_registered(session: AsyncSession, name: str, agent_id: int, by: str | None) -> None:
    """에이전트가 생성될 때(화면·정의 파일·발견 등록) 같은 이름의 발견 기록을 '등록됨'으로. 커밋은 호출한 쪽."""
    row = (await session.execute(select(AIAgentDiscovery).where(AIAgentDiscovery.name == name))).scalars().first()
    if row is not None and row.status != "registered":
        row.status, row.agent_id, row.status_changed_by, row.status_changed_at = "registered", agent_id, by, _now()
        logger.info("발견 기록을 등록됨으로 표시: %s (agent_id=%d)", name, agent_id)


async def list_discoveries(
    session: AsyncSession, status: str | None = "new", search: str | None = None, page: int = 1, page_size: int = 20,
) -> PaginatedAIAgentDiscoveries:
    """발견 기록 목록(마지막 발견 시각 내림차순). 등록된 기록은 연결된 에이전트 이름을 붙인다."""
    base = select(AIAgentDiscovery)
    if status:
        base = base.where(AIAgentDiscovery.status == status)
    if search:
        base = base.where(AIAgentDiscovery.name.ilike(like_contains(search), escape=LIKE_ESCAPE))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (await session.execute(
        base.order_by(AIAgentDiscovery.last_seen_at.desc(), AIAgentDiscovery.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    names = {}
    ids = [r.agent_id for r in rows if r.agent_id]
    if ids:
        names = dict((await session.execute(select(AIAgent.id, AIAgent.name).where(AIAgent.id.in_(ids)))).all())
    items = []
    for r in rows:
        item = AIAgentDiscoveryResponse.model_validate(r)
        item.agent_name = names.get(r.agent_id)
        items.append(item)
    return PaginatedAIAgentDiscoveries(items=items, total=total, page=page, page_size=page_size)


async def count_new(session: AsyncSession) -> int:
    """처리 전(new) 발견 기록 수 — 대시보드 지표용."""
    return await session.scalar(
        select(func.count(AIAgentDiscovery.id)).where(AIAgentDiscovery.status == "new")
    ) or 0


async def _get(session: AsyncSession, discovery_id: int) -> AIAgentDiscovery | None:
    return await session.get(AIAgentDiscovery, discovery_id)


async def register(
    session: AsyncSession, discovery_id: int, user, req: AIAgentDiscoveryRegister | None = None,
) -> tuple[AIAgentDiscovery, AIAgentSummary] | None:
    """발견 기록으로 초안(draft) 에이전트를 만든다. 같은 이름이 이미 있으면 그 에이전트에 연결만 한다.

    새로 만들 때는 소유자 이메일·부서·분류가 필수(화면 등록과 같은 규칙). 소유자·부서는 비우면
    로그인 사용자 값으로 채운다. 없는 기록이면 None, 이미 등록됨이면 ``DiscoveryStateError``,
    필수 항목이 비면 ``RequiredFieldsError``.
    """
    row = await _get(session, discovery_id)
    if row is None:
        return None
    if row.status == "registered" and row.agent_id and await session.get(AIAgent, row.agent_id):
        raise DiscoveryStateError(f"'{row.name}' 은(는) 이미 등록되었습니다.")
    agent = (await session.execute(select(AIAgent).where(AIAgent.name == row.name))).scalars().first()
    created = agent is None
    if created:
        from app.agents.views import _department_of

        req = req or AIAgentDiscoveryRegister()
        owner = (req.owner_email or "").strip() or (user.email or "")
        dept = (req.department or "").strip() or await _department_of(session, user)
        category = (req.category or "").strip()
        problem = missing_required(owner, dept, category)
        if problem:
            raise RequiredFieldsError(problem)
        agent = AIAgent(
            name=row.name, urn=f"{row.name}.agent", status="draft", version="0.1.0",
            owner_email=owner, department=dept, category=category,
            created_by=user.username, updated_by=user.username,
            managed_by="discovered", usage_count=0, is_multi_agent=False, streaming=False, hitl_required=False,
            description=f"실행 기록에서 발견된 에이전트 — {row.first_seen_at:%Y-%m-%d} 처음 발견, {row.seen_count}회 기록",
        )
        session.add(agent)
        await session.flush()
    row.status, row.agent_id, row.status_changed_by, row.status_changed_at = "registered", agent.id, user.username, _now()
    await session.commit()
    await session.refresh(agent)
    logger.info("미등록 에이전트 %s: %s (요청자 %s)", "등록" if created else "기존 에이전트에 연결", row.name, user.username)
    if created:
        from app.embedding.service import embed_entity_background

        await embed_entity_background("ai_agent", agent.id)
    return row, AIAgentSummary.model_validate(agent)


async def set_status(session: AsyncSession, discovery_id: int, status: str, user) -> AIAgentDiscovery | None:
    """무시(new→ignored) 또는 되돌리기(ignored→new). 등록된 기록은 바꿀 수 없다."""
    row = await _get(session, discovery_id)
    if row is None:
        return None
    if row.status == "registered":
        raise DiscoveryStateError(f"'{row.name}' 은(는) 이미 등록되어 상태를 바꿀 수 없습니다.")
    if row.status != status:
        row.status, row.status_changed_by, row.status_changed_at = status, user.username, _now()
        await session.commit()
        logger.info("미등록 에이전트 상태 변경: %s → %s (요청자 %s)", row.name, status, user.username)
    return row
