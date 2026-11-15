# SPDX-License-Identifier: Apache-2.0
"""댓글 서비스 — 중첩 답글을 지원하는 CRUD 처리.

페이지네이션은 최상위 댓글(parent_id IS NULL)만 센다.
답글은 root_id 로 일괄 조회한 뒤 응답 트리에 중첩시킨다.
reply_count 는 성능을 위해 비정규화된 "보이는 직접 답글 수" 다(생성/삭제 시 갱신,
삭제된 댓글은 reply_count > 0 일 때만 "삭제된 댓글" 로 보인다).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.comments.models import Comment
from app.comments.schemas import (
    CommentCreate,
    CommentResponse,
    CommentUpdate,
    PaginatedComments,
    ReportItem,
)
from app.core.html_sanitize import html_to_plain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 오류 신고·개선 요청 처리 (TODO-096 4단계, 가이드라인 체크리스트 C31)
# ---------------------------------------------------------------------------

# 처리 대상 — 데이터셋의 최상위 댓글 중 이 분류. feature(기능 요청)는 카탈로그 기능에 대한 것이라 뺀다.
REPORT_ENTITY_TYPES = frozenset({"dataset"})
REPORT_CATEGORIES = frozenset({"bug", "suggestion"})
REPORT_STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED")
OPEN_STATUSES = ("OPEN", "IN_PROGRESS")
SETTING_DUE_DAYS = "ai_ready_report_due_days"
DEFAULT_DUE_DAYS = 10  # 가이드라인 §3 "신고 후 10일 안에 수정 또는 소명"(기준 예시)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: datetime | None) -> datetime | None:
    """SQLite 등 시간대 없이 돌려주는 값은 UTC 로 본다."""
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def is_overdue(status: str | None, due_at: datetime | None, now: datetime | None = None) -> bool:
    return status in OPEN_STATUSES and due_at is not None and _aware(due_at) < (now or _now())


def is_report(entity_type: str, parent_id: int | None, category: str | None) -> bool:
    return entity_type in REPORT_ENTITY_TYPES and parent_id is None and category in REPORT_CATEGORIES


async def report_due_days(session: AsyncSession) -> int:
    """설정 › AI-Ready 의 처리 기한(일). 비었거나 잘못되면 10."""
    from app.settings.service import get_config_by_category

    raw = (await get_config_by_category(session, "ai_ready")).get(SETTING_DUE_DAYS)
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_DUE_DAYS
    return days if days > 0 else DEFAULT_DUE_DAYS


async def _sync_report_state(session: AsyncSession, comment: Comment) -> None:
    """분류에 맞춰 처리 상태를 붙이거나 뗀다 — 새로 처리 대상이 되면 접수(OPEN), 기한은 작성 시각 + 설정 일수."""
    if is_report(comment.entity_type, comment.parent_id, comment.category):
        if comment.status is None:
            base = _aware(comment.created_at) or _now()
            comment.status = "OPEN"
            comment.due_at = base + timedelta(days=await report_due_days(session))
    elif comment.status is not None:
        comment.status = comment.due_at = comment.resolved_at = comment.resolution = comment.handled_by = None


async def _notify_received(session: AsyncSession, comment: Comment) -> None:
    """신고 접수 알림(``REPORT_RECEIVED`` 규칙) — 알림이 실패해도 댓글 저장은 막지 않는다."""
    from app.catalog.ai_ready_alerts import notify_report_received

    try:
        async with session.begin_nested():
            await notify_report_received(session, comment)
    except Exception as e:  # noqa: BLE001
        logger.warning("신고 접수 알림 실패: comment_id=%s: %s", comment.id, e)


def _to_response(comment: Comment) -> CommentResponse:
    """ORM Comment 를 응답 스키마로 변환(답글은 제외 — 이후에 추가)."""
    return CommentResponse(
        id=comment.id,
        entity_type=comment.entity_type,
        entity_id=comment.entity_id,
        parent_id=comment.parent_id,
        root_id=comment.root_id,
        depth=comment.depth,
        content=comment.content,
        content_plain=comment.content_plain,
        category=comment.category,
        author_name=comment.author_name,
        author_email=comment.author_email,
        author_avatar=comment.author_avatar,
        reply_count=comment.reply_count,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        is_deleted=comment.is_deleted,
        status=comment.status,
        due_at=comment.due_at,
        resolved_at=comment.resolved_at,
        resolution=comment.resolution,
        handled_by=comment.handled_by,
        overdue=is_overdue(comment.status, comment.due_at),
    )


def _build_reply_tree(
    root_comments: list[CommentResponse],
    all_replies: list[CommentResponse],
) -> list[CommentResponse]:
    """답글을 부모 댓글에 중첩시켜 트리 구조를 만든다.

    재귀 쿼리 대신 id 기반 dict 조회로 O(n log n)(정렬) 에 조립한다. 부모가 목록에 없는
    답글(예: 숨겨진 삭제 댓글의 답글)은 트리에 붙지 않고 버려진다. ``root_comments`` 를
    제자리에서 수정해 그대로 반환한다.
    """
    by_id: dict[int, CommentResponse] = {}
    for c in root_comments:
        by_id[c.id] = c

    # 부모가 자식보다 먼저 by_id 에 들어가도록 depth 우선, 같은 depth 는 created_at 순으로 정렬
    sorted_replies = sorted(all_replies, key=lambda r: (r.depth, r.created_at))

    for reply in sorted_replies:
        by_id[reply.id] = reply
        parent = by_id.get(reply.parent_id)
        if parent:
            parent.replies.append(reply)

    return root_comments


async def list_comments(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    page: int = 1,
    page_size: int = 10,
) -> PaginatedComments:
    """최상위 댓글만 페이지네이션하여 댓글 목록을 조회한다.

    답글은 일괄 조회되어 응답에 중첩된다.
    페이지 크기는 최상위 댓글에만 적용되며 답글은 포함되지 않는다.
    """
    # 최상위 댓글 카운트(노출 대상: 삭제되지 않았거나, 삭제됐어도 답글이 있는 경우)
    count_q = (
        select(func.count())
        .where(
            Comment.entity_type == entity_type,
            Comment.entity_id == entity_id,
            Comment.parent_id.is_(None),
            # 노출 조건: 삭제되지 않았거나, 삭제됐어도 답글이 남아 있는 경우
            ((Comment.is_deleted == False) | (Comment.reply_count > 0)),
        )
    )
    total = (await session.execute(count_q)).scalar() or 0

    # 최상위 댓글 조회(페이지네이션)
    # 답글이 남아 있는 삭제된 댓글도 포함("삭제된 댓글" 로 표시)
    offset = (page - 1) * page_size
    top_q = (
        select(Comment)
        .where(
            Comment.entity_type == entity_type,
            Comment.entity_id == entity_id,
            Comment.parent_id.is_(None),
            ((Comment.is_deleted == False) | (Comment.reply_count > 0)),
        )
        .order_by(Comment.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    top_result = await session.execute(top_q)
    top_comments = [_to_response(c) for c in top_result.scalars().all()]

    if not top_comments:
        logger.debug("댓글 없음: entity=%s/%s page=%d total=%d", entity_type, entity_id, page, total)
        return PaginatedComments(items=[], total=total, page=page, page_size=page_size)

    # 모든 답글 조회(자신의 답글이 있는 삭제된 답글도 포함)
    root_ids = [c.id for c in top_comments]
    reply_q = (
        select(Comment)
        .where(
            Comment.root_id.in_(root_ids),
            ((Comment.is_deleted == False) | (Comment.reply_count > 0)),
        )
        .order_by(Comment.created_at.asc())
    )
    reply_result = await session.execute(reply_q)
    all_replies = [_to_response(r) for r in reply_result.scalars().all()]

    # 트리 구성
    items = _build_reply_tree(top_comments, all_replies)

    logger.debug(
        "댓글 목록 조회: entity=%s/%s, page=%d, top=%d, replies=%d",
        entity_type, entity_id, page, len(top_comments), len(all_replies),
    )
    return PaginatedComments(items=items, total=total, page=page, page_size=page_size)


def author_name_for(user) -> str:
    """인증 사용자로 작성자 표시명을 만든다 — UI 와 같은 "성이름(username)" 형식(이름 없으면 username)."""
    full = f"{user.last_name or ''}{user.first_name or ''}".strip()
    return f"{full}({user.username})" if full else user.username


def comment_author_username(author_name: str | None) -> str | None:
    """저장된 author_name 에서 username 을 꺼낸다 — "이름(username)" 또는 구 형식 "username"."""
    if not author_name:
        return None
    # "표시명(username)" — 마지막 괄호 안(괄호 없는 문자열)을 username 으로 본다
    m = re.match(r"^(.+?)\(([^()]+)\)\s*$", author_name)
    return m.group(2) if m else author_name.strip()


async def get_comment_author(session: AsyncSession, comment_id: int) -> str | None:
    """권한 검사용 — 삭제되지 않은 댓글의 작성자 username.

    댓글이 없거나 삭제됐으면 None, author_name 이 비어 있으면 빈 문자열.
    """
    comment = await session.get(Comment, comment_id)
    if not comment or comment.is_deleted:
        return None
    return comment_author_username(comment.author_name) or ""


async def create_comment(
    session: AsyncSession,
    req: CommentCreate,
    author=None,
) -> CommentResponse:
    """댓글 또는 답글을 생성한다.

    답글의 경우: 부모로부터 root_id 와 depth 를 구하고 부모의 reply_count 를 증가시킨다.
    ``author``(인증 사용자)가 주어지면 작성자 정보는 요청 본문이 아니라 그 사용자에서 정한다.
    """
    parent_id = req.parent_id
    root_id = None
    depth = 0

    if parent_id:
        # 부모를 조회해 root_id 와 depth 를 구함
        parent = await session.get(Comment, parent_id)
        if not parent:
            logger.warning("답글 대상 부모 댓글 없음: parent_id=%s", parent_id)
            raise ValueError(f"Parent comment {parent_id} not found")
        if parent.is_deleted:
            logger.warning("삭제된 댓글에 답글 시도: parent_id=%s", parent_id)
            raise ValueError(f"Cannot reply to deleted comment {parent_id}")

        root_id = parent.root_id or parent.id  # 부모가 최상위면 root_id = parent.id
        depth = parent.depth + 1

        # 부모의 reply_count 증가
        await session.execute(
            update(Comment)
            .where(Comment.id == parent_id)
            # updated_at 은 본문 수정 시각("수정됨" 표시) — 답글 수 갱신으로 바뀌지 않게 유지
            .values(reply_count=Comment.reply_count + 1, updated_at=Comment.updated_at)
        )

    comment = Comment(
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        parent_id=parent_id,
        root_id=root_id,
        depth=depth,
        content=req.content,
        # 평문을 보내지 않은 API 호출도 목록 · 신고 처리 · 알림 요약에 본문이 보이게 서버가 만든다(DEF-133)
        content_plain=req.content_plain if (req.content_plain or "").strip() else html_to_plain(req.content),
        category=req.category,
        author_name=author_name_for(author) if author is not None else (req.author_name or "anonymous"),
        author_email=(author.email or None) if author is not None else req.author_email,
        author_avatar=None if author is not None else req.author_avatar,
    )
    if is_report(req.entity_type, parent_id, req.category):
        comment.status = "OPEN"
        comment.due_at = _now() + timedelta(days=await report_due_days(session))
    session.add(comment)
    if comment.status is not None:
        await session.flush()
        await _notify_received(session, comment)
    await session.commit()
    await session.refresh(comment)

    logger.info(
        "댓글 생성: id=%d, entity=%s/%s, author=%s, parent=%s, category=%s",
        comment.id, req.entity_type, req.entity_id, comment.author_name, parent_id, req.category,
    )
    return _to_response(comment)


async def update_comment(
    session: AsyncSession,
    comment_id: int,
    req: CommentUpdate,
) -> CommentResponse | None:
    """댓글의 본문 또는 분류를 수정한다. 댓글이 없거나 삭제됐으면 None."""
    comment = await session.get(Comment, comment_id)
    if not comment or comment.is_deleted:
        logger.debug("수정 대상 댓글 없음 또는 삭제됨: id=%d", comment_id)
        return None

    comment.content = req.content
    # 평문이 없으면 새 본문에서 만든다 — 예전 평문이 남아 바뀐 본문과 어긋나지 않게(DEF-133)
    comment.content_plain = req.content_plain if (req.content_plain or "").strip() else html_to_plain(req.content)
    if req.category is not None:
        comment.category = req.category
        was = comment.status
        await _sync_report_state(session, comment)
        if was is None and comment.status is not None:
            await _notify_received(session, comment)

    await session.commit()
    await session.refresh(comment)
    logger.info("댓글 수정: id=%d", comment_id)
    return _to_response(comment)


async def delete_comment(
    session: AsyncSession,
    comment_id: int,
) -> bool:
    """댓글을 소프트 삭제한다.

    댓글이 없거나 이미 삭제됐으면 False. 행은 남겨 두므로 보이는 답글이 있는 삭제 댓글은
    목록에서 "삭제된 댓글" 로 계속 보인다.

    reply_count 는 "보이는 직접 답글 수" 로 유지한다(보임 = 삭제되지 않았거나 reply_count > 0).
    삭제로 이 댓글이 목록에서 사라질 때(보이는 답글 없음)만 부모의 reply_count 를 줄이고,
    그 결과 삭제된 부모도 사라지면 조상 쪽으로 같은 갱신을 이어 간다. 이렇게 해야
    A←B←C 에서 B, A 를 차례로 지워도 살아 있는 C 와 그 조상이 계속 보인다.
    """
    comment = await session.get(Comment, comment_id)
    if not comment or comment.is_deleted:
        logger.debug("삭제 대상 댓글 없음 또는 이미 삭제됨: id=%d", comment_id)
        return False

    comment.is_deleted = True

    # 보이는 답글이 남아 있으면 "삭제된 댓글" 로 계속 보이므로 조상 카운트는 그대로 둔다.
    node = comment
    while node.reply_count <= 0 and node.parent_id:
        parent = await session.get(Comment, node.parent_id)
        if parent is None:
            break
        # updated_at 은 본문 수정 시각("수정됨" 표시) — 답글 수 갱신으로 바뀌지 않게 유지
        await session.execute(
            update(Comment)
            .where(Comment.id == parent.id)
            # 0 미만 방지(기존 데이터 불일치 방어)
            .values(reply_count=case((Comment.reply_count > 0, Comment.reply_count - 1), else_=0),
                    updated_at=Comment.updated_at)
        )
        await session.refresh(parent, ["reply_count"])
        if not parent.is_deleted:
            break  # 살아 있는 부모는 계속 보이므로 위로 전파할 필요 없음
        logger.debug("삭제된 조상 댓글도 숨김 처리: id=%d", parent.id)
        node = parent

    await session.commit()
    logger.info("댓글 소프트 삭제: id=%d, entity=%s/%s", comment_id, comment.entity_type, comment.entity_id)
    return True


async def get_comment_count(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
) -> int:
    """엔티티의 댓글 수를 조회(최상위 댓글만 세며 답글·삭제 댓글은 제외)."""
    result = await session.execute(
        select(func.count()).where(
            Comment.entity_type == entity_type,
            Comment.entity_id == entity_id,
            Comment.parent_id.is_(None),
            Comment.is_deleted == False,
        )
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# 오류 신고·개선 요청 — 상태 변경, 목록, 집계
# ---------------------------------------------------------------------------


async def get_report(session: AsyncSession, comment_id: int) -> Comment | None:
    """처리 대상 댓글(삭제되지 않음, 상태 있음). 아니면 None."""
    c = await session.get(Comment, comment_id)
    if not c or c.is_deleted or c.status is None:
        return None
    return c


async def set_report_status(
    session: AsyncSession, comment: Comment, status: str, resolution: str | None, actor: str,
) -> CommentResponse:
    """처리 상태를 바꾼다. 해결·소명은 처리 시각을 남기고, 다시 열면 지운다. 소명은 사유 필수(ValueError)."""
    status = (status or "").strip().upper()
    if status not in REPORT_STATUSES:
        raise ValueError(f"처리 상태는 {', '.join(REPORT_STATUSES)} 중 하나입니다.")
    text = (resolution or "").strip() or None
    if status == "REJECTED" and not text:
        raise ValueError("소명(반려)은 사유를 적어야 합니다.")
    # updated_at 은 본문 수정 시각("수정됨" 표시) — 상태 변경으로 바뀌지 않게 둔다(onupdate 를 피해 UPDATE 로)
    await session.execute(
        update(Comment).where(Comment.id == comment.id).values(
            status=status, resolution=text, handled_by=actor,
            resolved_at=_now() if status in ("RESOLVED", "REJECTED") else None,
            updated_at=Comment.updated_at,
        )
    )
    await session.commit()
    await session.refresh(comment)
    logger.info("신고 처리 상태 변경: id=%d status=%s by=%s", comment.id, status, actor)
    return _to_response(comment)


def _report_filter(entity_type: str, entity_ids: list[str] | None):
    conds = [Comment.entity_type == entity_type, Comment.parent_id.is_(None),
             Comment.is_deleted == False, Comment.status.isnot(None)]  # noqa: E712
    if entity_ids is not None:
        conds.append(Comment.entity_id.in_(entity_ids))
    return conds


async def list_reports(
    session: AsyncSession, *, entity_type: str = "dataset", entity_ids: list[str] | None = None,
    state: str = "open", category: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[ReportItem], int, dict[str, int]]:
    """신고 목록 — state: open(접수·처리 중) · overdue(기한 넘김) · closed(해결·소명) · all.

    정렬은 처리 중인 것은 기한 이른 순, 끝난 것은 처리 최근 순. counts 는 범위(entity_ids) 안의 상태별 수.
    """
    now = _now()
    base = _report_filter(entity_type, entity_ids)
    if category in REPORT_CATEGORIES:
        base.append(Comment.category == category)

    counts = {"open": 0, "overdue": 0, "closed": 0, "all": 0}
    for st, n in (await session.execute(
        select(Comment.status, func.count()).where(*base).group_by(Comment.status)
    )).all():
        counts["all"] += n
        counts["open" if st in OPEN_STATUSES else "closed"] += n
    counts["overdue"] = (await session.execute(
        select(func.count()).where(*base, Comment.status.in_(OPEN_STATUSES), Comment.due_at < now)
    )).scalar() or 0

    conds = list(base)
    if state == "open":
        conds.append(Comment.status.in_(OPEN_STATUSES))
        order = (Comment.due_at.asc(), Comment.id.asc())
    elif state == "overdue":
        conds += [Comment.status.in_(OPEN_STATUSES), Comment.due_at < now]
        order = (Comment.due_at.asc(), Comment.id.asc())
    elif state == "closed":
        conds.append(Comment.status.in_(("RESOLVED", "REJECTED")))
        order = (Comment.resolved_at.desc(), Comment.id.desc())
    else:
        order = (Comment.created_at.desc(), Comment.id.desc())
    total = (await session.execute(select(func.count()).where(*conds))).scalar() or 0
    rows = (await session.execute(
        select(Comment).where(*conds).order_by(*order).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = [ReportItem(
        id=c.id, entity_type=c.entity_type, entity_id=c.entity_id, category=c.category,
        content_plain=(c.content_plain or "")[:300] or None, author_name=c.author_name, created_at=c.created_at,
        status=c.status, due_at=c.due_at, resolved_at=c.resolved_at, resolution=c.resolution,
        handled_by=c.handled_by, overdue=is_overdue(c.status, c.due_at, now), reply_count=c.reply_count,
    ) for c in rows]
    return items, total, counts


async def report_stats(session: AsyncSession, entity_type: str, entity_ids: list[str]) -> dict[str, dict[str, int]]:
    """엔티티별 신고 집계 {entity_id: {total, open, overdue, closed}} — AI-Ready 점검(C31)용 묶음 조회."""
    out: dict[str, dict[str, int]] = {}
    if not entity_ids:
        return out
    now = _now()
    for eid, st, due in (await session.execute(
        select(Comment.entity_id, Comment.status, Comment.due_at).where(*_report_filter(entity_type, entity_ids))
    )).all():
        s = out.setdefault(eid, {"total": 0, "open": 0, "overdue": 0, "closed": 0})
        s["total"] += 1
        if st in OPEN_STATUSES:
            s["open"] += 1
            if is_overdue(st, due, now):
                s["overdue"] += 1
        else:
            s["closed"] += 1
    return out
