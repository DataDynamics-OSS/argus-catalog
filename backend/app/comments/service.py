# SPDX-License-Identifier: Apache-2.0
"""댓글 서비스 — 중첩 답글을 지원하는 CRUD 처리.

페이지네이션은 최상위 댓글(parent_id IS NULL)만 센다.
답글은 root_id 로 일괄 조회한 뒤 응답 트리에 중첩시킨다.
reply_count 는 성능을 위해 비정규화되어 있다(생성/삭제 시 갱신).
"""

import logging

from sqlalchemy import func, select, update
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

    재귀 쿼리 대신 id 기반 dict 조회로 O(n) 에 조립한다.
    """
    by_id: dict[int, CommentResponse] = {}
    for c in root_comments:
        by_id[c.id] = c

    # 올바른 순서를 위해 depth, created_at 순으로 답글 정렬
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

    logger.info(
        "댓글 목록 조회: entity=%s/%s, page=%d, top=%d, replies=%d",
        entity_type, entity_id, page, len(top_comments), len(all_replies),
    )
    return PaginatedComments(items=items, total=total, page=page, page_size=page_size)


async def create_comment(
    session: AsyncSession,
    req: CommentCreate,
) -> CommentResponse:
    """댓글 또는 답글을 생성한다.

    답글의 경우: 부모로부터 root_id 와 depth 를 구하고 부모의 reply_count 를 증가시킨다.
    """
    parent_id = req.parent_id
    root_id = None
    depth = 0

    if parent_id:
        # 부모를 조회해 root_id 와 depth 를 구함
        parent = await session.get(Comment, parent_id)
        if not parent:
            raise ValueError(f"Parent comment {parent_id} not found")
        if parent.is_deleted:
            raise ValueError(f"Cannot reply to deleted comment {parent_id}")

        root_id = parent.root_id or parent.id  # 부모가 최상위면 root_id = parent.id
        depth = parent.depth + 1

        # 부모의 reply_count 증가
        await session.execute(
            update(Comment)
            .where(Comment.id == parent_id)
            .values(reply_count=Comment.reply_count + 1)
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
        author_name=req.author_name,
        author_email=req.author_email,
        author_avatar=req.author_avatar,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)

    logger.info(
        "댓글 생성: id=%d, entity=%s/%s, author=%s, parent=%s, category=%s",
        comment.id, req.entity_type, req.entity_id, req.author_name, parent_id, req.category,
    )
    return _to_response(comment)


async def update_comment(
    session: AsyncSession,
    comment_id: int,
    req: CommentUpdate,
) -> CommentResponse | None:
    """댓글의 본문 또는 분류를 수정한다."""
    comment = await session.get(Comment, comment_id)
    if not comment or comment.is_deleted:
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
    """댓글을 소프트 삭제한다. 답글이면 부모의 reply_count 를 감소시킨다."""
    comment = await session.get(Comment, comment_id)
    if not comment or comment.is_deleted:
        return False

    comment.is_deleted = True

    # 부모의 reply_count 감소
    if comment.parent_id:
        await session.execute(
            update(Comment)
            .where(Comment.id == comment.parent_id)
            .values(reply_count=func.greatest(Comment.reply_count - 1, 0))
        )

    await session.commit()
    logger.info("댓글 소프트 삭제: id=%d, entity=%s/%s", comment_id, comment.entity_type, comment.entity_id)
    return True


async def get_comment_count(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
) -> int:
    """엔티티의 전체 댓글 수를 조회(최상위만, 삭제 제외)."""
    result = await session.execute(
        select(func.count()).where(
            Comment.entity_type == entity_type,
            Comment.entity_id == entity_id,
            Comment.parent_id.is_(None),
            Comment.is_deleted == False,
        )
    )
    return result.scalar() or 0
