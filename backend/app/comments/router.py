"""댓글(Comment) API 엔드포인트.

다형성 댓글 시스템의 CRUD 를 제공한다.
댓글은 entity_type + entity_id 를 통해 어떤 엔티티에도 붙는다.
페이지네이션은 최상위 댓글만 세며, 답글은 응답에 중첩되어 담긴다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.comments import service
from app.comments.schemas import (
    CommentCreate,
    CommentResponse,
    CommentUpdate,
    PaginatedComments,
)
from app.core.auth import CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("", response_model=PaginatedComments)
async def list_comments(
    entity_type: str = Query(..., description="Entity type (dataset, model, glossary, ...)"),
    entity_id: str = Query(..., description="Entity identifier"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """엔티티의 댓글을 페이지네이션으로 조회(최상위만, 답글은 중첩)."""
    logger.info("GET /comments: entity=%s/%s, page=%d", entity_type, entity_id, page)
    return await service.list_comments(session, entity_type, entity_id, page, page_size)


@router.post("", response_model=CommentResponse)
async def create_comment(_guard: CurrentUser,
    req: CommentCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 댓글 또는 답글을 생성한다."""
    logger.info("POST /comments: entity=%s/%s, author=%s, parent=%s",
                req.entity_type, req.entity_id, req.author_name, req.parent_id)
    try:
        return await service.create_comment(session, req)
    except ValueError as e:
        logger.warning("댓글 생성 실패: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{comment_id}", response_model=CommentResponse)
async def update_comment(_guard: CurrentUser,
    comment_id: int,
    req: CommentUpdate,
    session: AsyncSession = Depends(get_session),
):
    """댓글의 본문 또는 분류를 수정한다."""
    logger.info("PUT /comments/%d", comment_id)
    result = await service.update_comment(session, comment_id, req)
    if not result:
        logger.warning("수정 대상 댓글 없음: %d", comment_id)
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")
    return result


@router.delete("/{comment_id}")
async def delete_comment(_guard: CurrentUser,
    comment_id: int,
    session: AsyncSession = Depends(get_session),
):
    """댓글을 소프트 삭제한다."""
    logger.info("DELETE /comments/%d", comment_id)
    if not await service.delete_comment(session, comment_id):
        logger.warning("삭제 대상 댓글 없음: %d", comment_id)
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")
    logger.info("댓글 삭제: %d", comment_id)
    return {"status": "ok", "message": f"Comment {comment_id} deleted"}


@router.get("/count")
async def get_comment_count(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """엔티티의 댓글 수를 조회(최상위만)."""
    count = await service.get_comment_count(session, entity_type, entity_id)
    return {"count": count}
