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
    focus: int | None = Query(None, description="이 댓글이 있는 쪽을 돌려줌(page 무시) — 신고 · 알림 링크용"),
    session: AsyncSession = Depends(get_session),
):
    """엔티티의 댓글을 페이지네이션으로 조회(최상위만, 답글은 중첩)."""
    logger.debug("GET /comments: entity=%s/%s, page=%d focus=%s", entity_type, entity_id, page, focus)
    return await service.list_comments(session, entity_type, entity_id, page, page_size, focus=focus)


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
    """댓글의 본문 또는 분류를 수정한다 — 작성자 또는 관리자. 해결 · 소명된 신고의 분류 변경은 400."""
    logger.info("PUT /comments/%d", comment_id)
    await _require_comment_author(current, session, comment_id)
    try:
        result = await service.update_comment(session, comment_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    user: OptionalUser,
    dataset_id: int | None = Query(None, description="한 데이터셋만"),
    state: str = Query("open", pattern="^(open|overdue|closed|all)$"),
    category: str | None = Query(None, pattern="^(bug|suggestion)$"),
    mine: str | None = Query(None, pattern="^(owned|reported)$",
                             description="owned: 내가 소유자(생성자 · 소유자 목록)인 데이터셋, reported: 내가 낸 신고"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋 오류 신고·개선 요청 목록 — 처리 중은 기한 이른 순, 끝난 것은 처리 최근 순. 데이터셋 경로를 붙인다.

    항목의 ``can_handle`` 은 현재 사용자가 처리(상태 변경)할 수 있는지 — 데이터셋 소유자 · 관리자. ``mine`` 은 로그인 필요.
    """
    from sqlalchemy import select

    from app.catalog.ai_ready_check import split_path
    from app.catalog.dataset_owner import managed_dataset_ids, owned_dataset_ids
    from app.catalog.models import Dataset, Datasource

    if mine and user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    ids = [str(dataset_id)] if dataset_id is not None else None
    reporter = None
    if mine == "owned":
        owned = {str(i) for i in await owned_dataset_ids(session, user.username)}
        ids = sorted(owned if ids is None else owned & set(ids))
    elif mine == "reported":
        reporter = user.username
    items, total, counts = await service.list_reports(
        session, entity_ids=ids, state=state, category=category, page=page, page_size=page_size, reporter=reporter)
    ds_ids = {int(i.entity_id) for i in items if i.entity_id.isdigit()}
    if user is not None:
        handle = ds_ids if user.is_admin else await managed_dataset_ids(session, user.username, ds_ids)
        for i in items:
            i.can_handle = user.is_admin or (i.entity_id.isdigit() and int(i.entity_id) in handle)
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
    """오류 신고·개선 요청의 처리 상태를 바꾼다 — 그 데이터셋의 소유자(생성자 또는 소유자 목록) 또는 관리자.

    접수 · 기한 알림을 받는 사람과 같은 기준이다(DEF-138).
    """
    from app.catalog.dataset_owner import is_dataset_owner

    report = await service.get_report(session, comment_id)
    if report is None:
        raise HTTPException(status_code=404, detail="처리할 오류 신고·개선 요청을 찾을 수 없습니다.")
    # 데이터셋이 지워졌거나 식별자가 숫자가 아니면 관리자만
    if not current.is_admin and not (report.entity_id.isdigit()
                                     and await is_dataset_owner(session, int(report.entity_id), current.username)):
        logger.warning("신고 처리 거부(403): user=%s comment_id=%d dataset=%s",
                       current.username, comment_id, report.entity_id)
        raise HTTPException(status_code=403, detail="데이터셋의 소유자(생성자 또는 소유자 목록) 또는 관리자만 처리할 수 있습니다.")
    try:
        return await service.set_report_status(session, report, req.status, req.resolution, current.username)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
