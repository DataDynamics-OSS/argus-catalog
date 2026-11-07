# SPDX-License-Identifier: Apache-2.0
"""관리 감사 로그 기록·조회.

변경이 커밋된 뒤에 기록한다(:func:`record`). 기록 실패(테이블 미생성 등)는 오류 로그만 남기고
본 작업은 그대로 성공시킨다 — 감사 기록 문제로 관리 작업이 막히지 않도록. 비밀값은
:func:`secret_change` 로 값 없이 "변경됨"만 남긴다.

요청자 IP 는 :class:`ClientIpMiddleware` 가 요청마다 contextvar 에 넣어 둔 값을 쓴다.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AdminAuditLog
from app.core.sql import LIKE_ESCAPE, like_contains

logger = logging.getLogger(__name__)

CATEGORIES = ("users", "settings", "permissions")
SECRET_CHANGED = "(변경됨)"

_client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar("audit_client_ip", default=None)
# 세션 없이 기록할 때(세션을 받지 않는 라우트) 쓸 팩토리 — None 이면 app.core.database.async_session
audit_session_factory = None


class ClientIpMiddleware:
    """요청자 IP 를 contextvar 에 담는 순수 ASGI 미들웨어(프록시 뒤면 X-Forwarded-For 첫 값)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            ip = None
            for k, v in scope.get("headers") or []:
                if k == b"x-forwarded-for":
                    ip = v.decode("latin-1").split(",")[0].strip()
                    break
            if not ip and scope.get("client"):
                ip = scope["client"][0]
            token = _client_ip.set(ip)
            try:
                await self.app(scope, receive, send)
            finally:
                _client_ip.reset(token)
        else:
            await self.app(scope, receive, send)


def secret_change(old: str | None, new: str | None) -> dict | None:
    """비밀값 변경 표시 — 바뀌었으면 값 없이 표시, 같으면 None."""
    if (old or "") == (new or ""):
        return None
    if not new:
        return {"old": SECRET_CHANGED if old else "", "new": "(삭제됨)"}
    return {"old": SECRET_CHANGED if old else "", "new": SECRET_CHANGED}


def _actor_name(actor: Any) -> str | None:
    """작업자 표기 — 사용자 객체면 username, 그 밖에는 문자열 변환값."""
    if actor is None:
        return None
    return getattr(actor, "username", None) or str(actor)


async def record(
    session: AsyncSession | None,
    actor: Any,
    category: str,
    action: str,
    *,
    target: str | None = None,
    summary: str | None = None,
    detail: dict | list | None = None,
) -> None:
    """감사 로그 1건 기록(커밋 포함). 실패해도 예외를 내지 않는다.

    ``session`` 이 None 이면 별도 세션(:data:`audit_session_factory` 또는 기본 팩토리)을 열어
    기록한다. 주어진 세션을 쓰면 그 세션을 커밋하므로 호출 측의 미커밋 변경도 함께 커밋된다.
    ``target``·``summary`` 는 컬럼 길이(200·500자)에 맞춰 잘린다.
    """
    entry = AdminAuditLog(
        actor=_actor_name(actor),
        category=category,
        action=action,
        target=(target or None) and str(target)[:200],
        summary=(summary or None) and summary[:500],
        detail=json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None,
        client_ip=_client_ip.get(),
    )
    try:
        if session is None:
            factory = audit_session_factory
            if factory is None:
                from app.core.database import async_session as factory
            async with factory() as own:
                own.add(entry)
                await own.commit()
        else:
            session.add(entry)
            await session.commit()
        logger.info("감사 기록: %s %s %s by %s", category, action, target, entry.actor)
    except Exception as e:  # noqa: BLE001 — 감사 기록 실패가 본 작업을 막지 않도록
        logger.error("감사 기록 실패(%s %s %s): %s", category, action, target, e)
        if session is not None:
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass


async def list_logs(
    session: AsyncSession,
    *,
    category: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """감사 로그를 필터·페이지 조회한다.

    ``q`` 는 대상·요약·작업자 부분 일치(대소문자 무시), ``since`` 이상·``until`` 미만.
    반환: ``{"total", "page", "page_size", "items"}`` — ``detail`` 은 JSON 이면 파싱한 값, 아니면 원문.
    """
    stmt = select(AdminAuditLog)
    if category:
        stmt = stmt.where(AdminAuditLog.category == category)
    if actor:
        stmt = stmt.where(AdminAuditLog.actor == actor)
    if action:
        stmt = stmt.where(AdminAuditLog.action == action)
    if since:
        stmt = stmt.where(AdminAuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AdminAuditLog.created_at < until)
    if q:
        p = like_contains(q)
        stmt = stmt.where(or_(
            AdminAuditLog.target.ilike(p, escape=LIKE_ESCAPE),
            AdminAuditLog.summary.ilike(p, escape=LIKE_ESCAPE),
            AdminAuditLog.actor.ilike(p, escape=LIKE_ESCAPE),
        ))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(
        stmt.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    logger.debug("감사 로그 조회 결과: total=%d page=%d rows=%d", total, page, len(rows))

    def _detail(text: str | None):
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                "id": r.id, "created_at": r.created_at, "actor": r.actor, "category": r.category,
                "action": r.action, "target": r.target, "summary": r.summary,
                "detail": _detail(r.detail), "client_ip": r.client_ip,
            }
            for r in rows
        ],
    }
