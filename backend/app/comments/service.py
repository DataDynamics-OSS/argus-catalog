# SPDX-License-Identifier: Apache-2.0
"""댓글 서비스 — 중첩 답글을 지원하는 CRUD 처리.

페이지네이션은 최상위 댓글(parent_id IS NULL)만 센다.
답글은 root_id 로 일괄 조회한 뒤 응답 트리에 중첩시킨다.
reply_count 는 성능을 위해 비정규화된 "보이는 직접 답글 수" 다(생성/삭제 시 갱신,
삭제된 댓글은 reply_count > 0 일 때만 "삭제된 댓글" 로 보인다).
"""

import logging
import re

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.comments.models import Comment
from app.comments.schemas import CommentCreate, CommentResponse, CommentUpdate, PaginatedComments

logger = logging.getLogger(__name__)


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
        content_plain=req.content_plain,
        category=req.category,
        author_name=author_name_for(author) if author is not None else (req.author_name or "anonymous"),
        author_email=(author.email or None) if author is not None else req.author_email,
        author_avatar=None if author is not None else req.author_avatar,
    )
    session.add(comment)
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
    if req.content_plain is not None:
        comment.content_plain = req.content_plain
    if req.category is not None:
        comment.category = req.category

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
