# SPDX-License-Identifier: Apache-2.0
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
    ReportPage,
    ReportStatusUpdate,
)
from app.core.auth import CurrentUser, OptionalUser, assert_owner_or_admin
from app.core.database import get_session
from app.permissions.router import require_feature

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
    logger.debug("GET /comments: entity=%s/%s, page=%d", entity_type, entity_id, page)
    return await service.list_comments(session, entity_type, entity_id, page, page_size)


@router.post("", response_model=CommentResponse, dependencies=[Depends(require_feature("comments.write"))])
async def create_comment(current: CurrentUser,
    req: CommentCreate,
    session: AsyncSession = Depends(get_session),
):
    """새 댓글 또는 답글을 생성한다. 작성자는 인증 사용자(본문의 author_* 는 무시 — 사칭 방지).

    부모 댓글이 없거나 삭제된 경우 서비스의 ValueError 를 400 으로 바꾼다.
    """
    logger.info("POST /comments: entity=%s/%s, author=%s, parent=%s",
                req.entity_type, req.entity_id, current.username, req.parent_id)
    try:
        return await service.create_comment(session, req, author=current)
    except ValueError as e:
        logger.warning("댓글 생성 실패: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


async def _require_comment_author(current, session: AsyncSession, comment_id: int) -> None:
    """댓글 수정·삭제 — 작성자 본인 또는 관리자만. 댓글이 없거나 삭제됐으면 404."""
    author = await service.get_comment_author(session, comment_id)
    if author is None:
        logger.warning("대상 댓글 없음: %d", comment_id)
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")
    assert_owner_or_admin(current, author, "댓글")


@router.put("/{comment_id}", response_model=CommentResponse, dependencies=[Depends(require_feature("comments.write"))])
async def update_comment(current: CurrentUser,
    comment_id: int,
    req: CommentUpdate,
    session: AsyncSession = Depends(get_session),
):
    """댓글의 본문 또는 분류를 수정한다 — 작성자 또는 관리자."""
    logger.info("PUT /comments/%d", comment_id)
    await _require_comment_author(current, session, comment_id)
    result = await service.update_comment(session, comment_id, req)
    if not result:
        logger.warning("수정 대상 댓글 없음: %d", comment_id)
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")
    return result


@router.delete("/{comment_id}")
async def delete_comment(current: CurrentUser,
    comment_id: int,
    session: AsyncSession = Depends(get_session),
):
    """댓글을 소프트 삭제한다 — 작성자 또는 관리자."""
    logger.info("DELETE /comments/%d", comment_id)
    await _require_comment_author(current, session, comment_id)
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
    """엔티티의 댓글 수를 조회(최상위만, 삭제 제외)."""
    count = await service.get_comment_count(session, entity_type, entity_id)
    logger.debug("댓글 수 조회: entity=%s/%s count=%d", entity_type, entity_id, count)
    return {"count": count}


# ---------------------------------------------------------------------------
# 오류 신고·개선 요청 처리 (TODO-096 4단계) — 데이터셋 댓글 중 분류가 오류 신고(bug)·개선 요청(suggestion)
# ---------------------------------------------------------------------------


@router.get("/reports", response_model=ReportPage)
async def list_reports(
    _user: OptionalUser,
    dataset_id: int | None = Query(None, description="한 데이터셋만"),
    state: str = Query("open", pattern="^(open|overdue|closed|all)$"),
    category: str | None = Query(None, pattern="^(bug|suggestion)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 오류 신고·개선 요청 목록 — 처리 중은 기한 이른 순, 끝난 것은 처리 최근 순. 데이터셋 경로를 붙인다."""
    from sqlalchemy import select

    from app.catalog.ai_ready_check import split_path
    from app.catalog.models import Dataset, Datasource

    ids = [str(dataset_id)] if dataset_id is not None else None
    items, total, counts = await service.list_reports(
        session, entity_ids=ids, state=state, category=category, page=page, page_size=page_size)
    ds_ids = {int(i.entity_id) for i in items if i.entity_id.isdigit()}
    if ds_ids:
        names = {}
        for did, name, display, urn, dkey in (await session.execute(
            select(Dataset.id, Dataset.name, Dataset.display_name, Dataset.urn, Datasource.datasource_id)
            .outerjoin(Datasource, Datasource.id == Dataset.datasource_id).where(Dataset.id.in_(ds_ids))
        )).all():
            db, table = split_path(urn or "", dkey)
            names[str(did)] = (display or name, f"{db}.{table}" if db else table)
        for i in items:
            i.entity_name, i.entity_path = names.get(i.entity_id, (None, None))
    return ReportPage(items=items, total=total, page=page, page_size=page_size, counts=counts)


@router.put("/{comment_id}/status", response_model=CommentResponse, dependencies=[Depends(require_feature("data-reports.handle"))])
async def update_report_status(current: CurrentUser,
    comment_id: int,
    req: ReportStatusUpdate,
    session: AsyncSession = Depends(get_session),
):
    """오류 신고·개선 요청의 처리 상태를 바꾼다 — 그 데이터셋의 소유자(생성자) 또는 관리자."""
    from app.catalog import service as catalog_service

    report = await service.get_report(session, comment_id)
    if report is None:
        raise HTTPException(status_code=404, detail="처리할 오류 신고·개선 요청을 찾을 수 없습니다.")
    created_by = (await catalog_service.get_dataset_created_by(session, int(report.entity_id))
                  if report.entity_id.isdigit() else None)
    assert_owner_or_admin(current, created_by, "데이터셋")
    try:
        return await service.set_report_status(session, report, req.status, req.resolution, current.username)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
