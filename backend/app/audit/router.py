# SPDX-License-Identifier: Apache-2.0
"""관리 감사 로그 조회 API (관리자 전용).

    GET /admin/audit?category=users|settings|permissions&actor=&action=&q=&since=&until=&page=&page_size=
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service
from app.core.auth import AdminUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])


@router.get("")
async def list_audit_logs(
    _admin: AdminUser,
    category: str | None = Query(None),
    actor: str | None = Query(None),
    action: str | None = Query(None),
    q: str | None = Query(None, description="대상·요약·작업자 부분 일치"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """감사 로그를 최신순으로 페이지 조회한다.

    ``since`` 는 포함, ``until`` 은 제외(반열린 구간). 빈 문자열 필터는 미지정으로 본다.
    알 수 없는 ``category`` 면 400.
    """
    if category and category not in service.CATEGORIES:
        logger.warning("감사 로그 조회 거부 — 알 수 없는 category=%s", category)
        raise HTTPException(status_code=400, detail=f"category 는 {', '.join(service.CATEGORIES)} 중 하나입니다.")
    logger.debug(
        "감사 로그 조회: category=%s actor=%s action=%s q=%s page=%d page_size=%d",
        category, actor, action, q, page, page_size,
    )
    return await service.list_logs(
        session, category=category, actor=actor or None, action=action or None, q=q or None,
        since=since, until=until, page=page, page_size=page_size,
    )
