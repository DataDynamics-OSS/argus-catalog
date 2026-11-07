# SPDX-License-Identifier: Apache-2.0
"""여러 에이전트의 상태·소유자·부서·분류·태그를 한 번에 바꾼다.

설계: design/ai-agent-ownership-design.md §6
- 최대 200개, 에이전트마다 독립 처리(하나가 실패해도 나머지는 반영). 결과 형식은 정의 파일 반영과 같다.
- 권한: 에이전트마다 관리자·등록자·소유자(이메일). ``blocked`` 로 바꾸거나 해제하는 것은 관리자만.
- 상태가 바뀌면 상태 이력(사유 "일괄 변경")을 남긴다.
- 정의 파일로 관리되는 에이전트는 다음 반영 때 되돌아간다 — 막지 않고 결과에 안내를 붙인다.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.manifest import _can_edit
from app.agents.models import AIAgent, AIAgentStatusHistory
from app.agents.schemas import AgentStatus

logger = logging.getLogger(__name__)

MAX_AGENTS = 200


class BulkSet(BaseModel):
    """바꿀 값. 지정한 항목만 반영하며 하나 이상은 있어야 한다(소유자는 이메일 형식)."""

    model_config = ConfigDict(extra="forbid")

    status: AgentStatus | None = None
    owner: str | None = Field(None, min_length=3, max_length=200)
    department: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=100)
    add_tags: list[str] = Field(default_factory=list, max_length=50)
    remove_tags: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _check(self):
        if self.owner is not None and "@" not in self.owner:
            raise ValueError("소유자는 이메일 형식이어야 합니다.")
        if not any([self.status, self.owner, self.department, self.category, self.add_tags, self.remove_tags]):
            raise ValueError("바꿀 항목이 하나도 없습니다.")
        return self


class BulkRequest(BaseModel):
    """일괄 변경 요청 — 대상 이름(1~200개), 바꿀 값, 미리 보기 여부."""

    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(..., min_length=1, max_length=MAX_AGENTS)
    set: BulkSet
    dry_run: bool = False


def _tags(current: list[str] | None, add: list[str], remove: list[str]) -> list[str]:
    """현재 태그에서 remove 를 빼고 add 를 (공백 제거·중복 없이) 뒤에 붙인 새 목록."""
    out =[t for t in (current or []) if t not in set(remove)]
    for t in (x.strip() for x in add):
        if t and t not in out:
            out.append(t)
    return out


async def apply_bulk(session: AsyncSession, req: BulkRequest, user) -> dict:
    """에이전트마다 독립적으로 검사·반영하고 이름별 결과(updated/unchanged/failed)와 요약을 돌려준다.

    에이전트마다 커밋(``dry_run`` 이면 롤백)하므로 앞선 에이전트의 반영은 뒤의 실패와 무관하게 유지된다.
    """
    s = req.set
    want_status = s.status.value if s.status else None
    results: list[dict] = []
    for name in dict.fromkeys(req.names):  # 중복 이름은 한 번만
        agent = (await session.execute(select(AIAgent).where(AIAgent.name == name))).scalars().first()
        if agent is None:
            logger.debug("일괄 변경 대상 없음: %s", name)
            results.append({"name": name, "action": "failed", "error": "에이전트가 없습니다."})
            continue
        if not _can_edit(agent, user):
            logger.warning("일괄 변경 권한 없음: %s (요청자 %s)", name, user.username)
            results.append({"name": name, "action": "failed", "error": "권한 없음(관리자·등록자·소유자만)"})
            continue
        # blocked 로 바꾸거나 blocked 에서 푸는 변경은 관리자만
        if want_status and "blocked" in (want_status, agent.status) and want_status != agent.status \
                and not user.is_admin:
            logger.warning("일괄 변경 차단 상태 변경 거부: %s %s -> %s (요청자 %s)",
                           name, agent.status, want_status, user.username)
            results.append({"name": name, "action": "failed", "error": "차단(blocked) 설정·해제는 관리자만 할 수 있습니다."})
            continue

        changes: list[str] = []
        for label, attr, new in (("owner", "owner_email", s.owner), ("department", "department", s.department),
                                 ("category", "category", s.category)):
            if new is not None and getattr(agent, attr) != new.strip():
                changes.append(f"{label}: {getattr(agent, attr) or '∅'} → {new.strip()}")
                setattr(agent, attr, new.strip())
        if s.add_tags or s.remove_tags:
            new_tags = _tags(agent.tags, s.add_tags, s.remove_tags)
            if new_tags != list(agent.tags or []):
                changes.append(f"tags: {', '.join(agent.tags or []) or '∅'} → {', '.join(new_tags) or '∅'}")
                agent.tags = new_tags
        if want_status and want_status != agent.status:
            changes.append(f"status: {agent.status} → {want_status}")
            session.add(AIAgentStatusHistory(agent_id=agent.id, from_status=agent.status, to_status=want_status,
                                             changed_by=user.username, note="일괄 변경"))
            agent.status = want_status

        if not changes:
            await session.rollback()
            results.append({"name": name, "action": "unchanged"})
            continue
        logger.debug("일괄 변경 항목: %s %s", name, "; ".join(changes))
        agent.updated_by = user.username
        r = {"name": name, "action": "updated", "changes": changes}
        if agent.managed_by == "manifest":
            r["note"] = "정의 파일로 관리되는 에이전트 — 다음 반영 때 정의 파일 내용으로 되돌아갑니다."
        results.append(r)
        if req.dry_run:
            await session.rollback()
        else:
            await session.commit()

    summary = {k: sum(1 for r in results if r["action"] == k) for k in ("updated", "unchanged", "failed")}
    logger.info("에이전트 일괄 변경%s: 요청 %d건, 갱신 %d · 변경 없음 %d · 실패 %d (요청자 %s)",
                "(미리 보기)" if req.dry_run else "", len(req.names), summary["updated"], summary["unchanged"],
                summary["failed"], user.username)
    return {"dry_run": req.dry_run, "summary": summary, "results": results}
