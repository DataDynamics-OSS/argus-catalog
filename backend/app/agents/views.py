# SPDX-License-Identifier: Apache-2.0
"""에이전트 목록의 저장된 보기 — 기본 보기 + 개인 보기 + 공유 보기.

설계: design/ai-agent-ownership-design.md §4.3
- 개인 보기는 만든 사람에게만 보인다. 공유 보기는 모두에게 보이고 만든 사람·관리자만 고치거나 지운다.
- 기본 보기는 저장하지 않고 요청한 사용자에 맞춰 만든다(``id`` 가 ``builtin:`` 으로 시작).
- ``query`` 는 목록 질의 값만 받는다(처음 보는 키는 422).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AIAgentView

logger = logging.getLogger(__name__)

MAX_VIEWS_PER_USER = 50


class ViewQuery(BaseModel):
    """목록 질의 값(``GET /ai-agents`` 와 같은 이름)."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(None, max_length=200)
    status: str | None = Field(None, max_length=20)
    framework: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)
    department: str | None = Field(None, max_length=500)
    owner: str | None = Field(None, max_length=200)
    tag: str | None = Field(None, max_length=500)
    risk: Literal["low", "medium", "high", "critical"] | None = None
    managed_by: str | None = Field(None, max_length=100)
    owner_state: str | None = Field(
        None, pattern=r"^(attention|(ok|missing|unknown|inactive)(,(ok|missing|unknown|inactive))*)$")
    incomplete: bool | None = None
    sort: str | None = Field(None, pattern=r"^-?(updated|name|usage|last_invoked|completeness|risk)$")


class ViewSave(BaseModel):
    """보기 만들기·수정 요청."""

    name: str = Field(..., min_length=1, max_length=100)
    shared: bool = False
    query: ViewQuery


class ViewResponse(BaseModel):
    """보기 응답. ``id`` 는 저장된 보기면 숫자 문자열, 기본 보기면 ``builtin:*``."""

    id: str
    name: str
    kind: Literal["builtin", "personal", "shared"]
    owner_name: str | None = None
    editable: bool = False
    query: dict


class ViewError(ValueError):
    """보기 처리 오류. ``status`` 는 라우터가 돌려줄 HTTP 상태(400 개수 초과 / 403 권한 / 404 없음)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def _department_of(session: AsyncSession, user) -> str:
    """로그인 사용자의 부서. 토큰에 없으면(로컬 로그인 토큰에는 부서가 실리지 않음) 사용자 목록에서 찾는다."""
    if user is None:
        return ""
    if getattr(user, "department", ""):
        return user.department
    from app.usermgr.models import ArgusUser

    dept = (await session.execute(
        select(ArgusUser.department).where(ArgusUser.username == user.username)
    )).scalar_one_or_none()
    return (dept or "").strip()


def _builtin(department: str) -> list[ViewResponse]:
    """기본 보기 — 내 에이전트, (부서가 있으면) 우리 부서, 고위험, 책임자 확인 필요, 정보 부족."""
    out =[ViewResponse(id="builtin:mine", name="내 에이전트", kind="builtin", query={"owner": "me"})]
    if department:
        out.append(ViewResponse(id="builtin:department", name=f"우리 부서({department})", kind="builtin",
                                query={"department": department}))
    out += [
        ViewResponse(id="builtin:high-risk", name="고위험", kind="builtin", query={"risk": "high", "sort": "risk"}),
        ViewResponse(id="builtin:owner-attention", name="책임자 확인 필요", kind="builtin",
                     query={"owner_state": "attention"}),
        ViewResponse(id="builtin:incomplete", name="정보 부족", kind="builtin",
                     query={"incomplete": True, "sort": "completeness"}),
    ]
    return out


def _to_response(v: AIAgentView, user) -> ViewResponse:
    """저장된 보기 → 응답. 만든 사람이거나, 공유 보기에 대해 관리자면 ``editable``."""
    mine =user is not None and v.owner_sub == user.sub
    return ViewResponse(
        id=str(v.id), name=v.name, kind="shared" if v.shared else "personal", owner_name=v.owner_name,
        editable=mine or (user is not None and user.is_admin and v.shared), query=dict(v.query or {}),
    )


async def list_views(session: AsyncSession, user) -> list[ViewResponse]:
    """기본 보기 → 내 보기 → 다른 사람의 공유 보기 순."""
    cond = AIAgentView.shared.is_(True)
    if user is not None:
        cond = or_(cond, AIAgentView.owner_sub == user.sub)
    rows = (await session.execute(select(AIAgentView).where(cond).order_by(AIAgentView.name, AIAgentView.id))).scalars()
    rows = list(rows)
    mine = [r for r in rows if user is not None and r.owner_sub == user.sub]
    others = [r for r in rows if r not in mine]
    return _builtin(await _department_of(session, user)) + [_to_response(r, user) for r in mine + others]


async def create_view(session: AsyncSession, req: ViewSave, user) -> ViewResponse:
    """보기 저장. 한 사람당 ``MAX_VIEWS_PER_USER`` 개를 넘으면 ``ViewError``(400)."""
    count = len((await session.execute(select(AIAgentView.id).where(AIAgentView.owner_sub == user.sub))).all())
    if count >= MAX_VIEWS_PER_USER:
        logger.warning("저장된 보기 개수 상한 초과: user=%s count=%d", user.username, count)
        raise ViewError(f"보기는 한 사람당 {MAX_VIEWS_PER_USER}개까지 저장할 수 있습니다.")
    v = AIAgentView(name=req.name.strip(), owner_sub=user.sub, owner_name=user.username, shared=req.shared,
                    query=req.query.model_dump(exclude_none=True))
    session.add(v)
    await session.commit()
    await session.refresh(v)
    logger.info("저장된 보기 생성: id=%d name=%s shared=%s user=%s", v.id, v.name, v.shared, user.username)
    return _to_response(v, user)


async def _editable(session: AsyncSession, view_id: int, user) -> AIAgentView:
    """수정·삭제 가능한 보기를 가져온다. 없거나 남의 개인 보기면 404, 남의 공유 보기는 관리자만(아니면 403)."""
    v = await session.get(AIAgentView, view_id)
    # 남의 개인 보기는 있는지조차 드러내지 않는다
    if v is None or (not v.shared and v.owner_sub != user.sub):
        raise ViewError("보기를 찾을 수 없습니다.", 404)
    if v.owner_sub != user.sub and not user.is_admin:
        logger.warning("저장된 보기 변경 권한 없음: id=%d user=%s", view_id, user.username)
        raise ViewError("만든 사람이나 관리자만 바꿀 수 있습니다.", 403)
    return v


async def update_view(session: AsyncSession, view_id: int, req: ViewSave, user) -> ViewResponse:
    """보기 이름·공유 여부·질의 값을 바꾼다."""
    v = await _editable(session, view_id, user)
    v.name, v.shared, v.query = req.name.strip(), req.shared, req.query.model_dump(exclude_none=True)
    await session.commit()
    await session.refresh(v)
    logger.info("저장된 보기 수정: id=%d name=%s shared=%s user=%s", view_id, v.name, v.shared, user.username)
    return _to_response(v, user)


async def delete_view(session: AsyncSession, view_id: int, user) -> None:
    """보기 삭제."""
    v = await _editable(session, view_id, user)
    await session.delete(v)
    await session.commit()
    logger.info("저장된 보기 삭제: id=%d user=%s", view_id, user.username)
