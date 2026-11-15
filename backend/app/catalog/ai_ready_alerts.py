# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 알림 — 공유 최소 요건 미충족, 오류 신고 접수, 처리 기한 임박·넘김 (TODO-096).

알림 규칙(``argus_alert_rule``)의 트리거 세 가지를 평가한다. 범위는 다른 데이터셋 트리거처럼
전체 · 데이터셋 · 태그 · 리니지 · 데이터 소스이고, 수신자는 규칙 구독자와(소유자 알림이 켜져 있으면)
데이터셋 소유자 · 생성자다.

- ``AI_READY_UNMET`` 공유 최소 요건 미충족 — 주기 점검. ``conditions``(missing · metadata · freshness · format,
  생략하면 넷 다) 중 하나라도 **미충족**이면 알림. 판정할 수 없는 조건(프로파일 없음 등)은 미충족으로 보지 않는다.
- ``REPORT_RECEIVED`` 오류 신고 · 개선 요청 접수 — 댓글 저장 때. ``categories``(bug · suggestion, 생략하면 둘 다).
- ``REPORT_DUE`` 처리 기한 임박 · 넘김 — 주기 점검. ``days_before``(기본 2, 0 이면 넘김만) 일 안으로 남으면 임박(경고),
  넘기면 넘김(치명).

같은 규칙 · 대상(데이터셋, 신고와 단계)에 대해 마지막 알림이 미확인 · 확인 · 무시면 다시 만들지 않는다.
조건이 풀리면(요건 충족, 신고 처리 끝남, 임박이 넘김으로 바뀜) 그 알림을 시스템이 해결로 바꾼다 — 그 뒤 다시
조건이 생기면 새로 알린다. 무시한 알림도 조건이 풀리면 해결로 바뀐다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert.models import AlertRule, LineageAlert
from app.catalog.models import Dataset, DatasetLineage, DatasetTag

logger = logging.getLogger(__name__)

UNMET = "AI_READY_UNMET"
RECEIVED = "REPORT_RECEIVED"
DUE = "REPORT_DUE"
TRIGGERS = frozenset({UNMET, RECEIVED, DUE})

CONDITIONS = ("missing", "metadata", "freshness", "format")
CATEGORY_LABEL = {"bug": "오류 신고", "suggestion": "개선 요청"}
DEFAULT_DAYS_BEFORE = 2

CHECK_SECONDS = 3600          # 주기 점검 간격
FIRST_CHECK_DELAY = 120       # 기동 직후에는 잠시 기다림
SYSTEM_ACTOR = "system"
_LIVE = ("OPEN", "ACKNOWLEDGED", "DISMISSED")   # 다시 알리지 않는 상태


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _config(rule: AlertRule) -> dict | None:
    try:
        cfg = json.loads(rule.trigger_config or "{}")
    except ValueError:
        logger.warning("알림 규칙 설정이 깨져 건너뜀: rule_id=%s", rule.id)
        return None
    return cfg if isinstance(cfg, dict) else None


def _snippet(text: str | None, limit: int = 60) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit] + "…"


def _detail(alert: LineageAlert) -> dict:
    try:
        d = json.loads(alert.change_detail or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _auto_resolve(alert: LineageAlert, now: datetime) -> None:
    """조건이 풀린 알림을 해결로 — 무시한 알림도(그래야 다시 생기면 새로 알린다)."""
    alert.status = "RESOLVED"
    alert.resolved_by = SYSTEM_ACTOR
    alert.resolved_at = now


async def _rules(session: AsyncSession, trigger: str) -> list[tuple[AlertRule, dict]]:
    rules = (await session.execute(
        select(AlertRule).where(AlertRule.is_active == "true", AlertRule.trigger_type == trigger)
    )).scalars().all()
    return [(r, c) for r in rules if (c := _config(r)) is not None]


async def _scope_facts(session: AsyncSession, ids: set[int]) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """데이터셋별 태그 · 리니지 id(규칙 범위 대조용) — 묶음 조회."""
    tags: dict[int, set[int]] = {i: set() for i in ids}
    lineages: dict[int, set[int]] = {i: set() for i in ids}
    if not ids:
        return tags, lineages
    for ds_id, tag_id in (await session.execute(
        select(DatasetTag.dataset_id, DatasetTag.tag_id).where(DatasetTag.dataset_id.in_(ids))
    )).all():
        tags[ds_id].add(tag_id)
    for lid, src, tgt in (await session.execute(
        select(DatasetLineage.id, DatasetLineage.source_dataset_id, DatasetLineage.target_dataset_id)
        .where(or_(DatasetLineage.source_dataset_id.in_(ids), DatasetLineage.target_dataset_id.in_(ids)))
    )).all():
        for d in (src, tgt):
            if d in lineages:
                lineages[d].add(lid)
    return tags, lineages


def _in_scope(rule: AlertRule, ds: Dataset, tags: dict[int, set[int]], lineages: dict[int, set[int]]) -> bool:
    from app.alert.service import _match_scope

    return _match_scope(rule, ds.id, ds.datasource_id, tags.get(ds.id, set()), lineages.get(ds.id, set()))


async def _latest(session: AsyncSession, trigger: str) -> list[LineageAlert]:
    """이 트리거의 알림을 최신 순으로 — 대상별 마지막 알림을 고르는 데 쓴다."""
    return list((await session.execute(
        select(LineageAlert).where(LineageAlert.alert_type == trigger)
        .order_by(LineageAlert.created_at.desc(), LineageAlert.id.desc())
    )).scalars().all())


async def _send(session: AsyncSession, alert: LineageAlert, ds: Dataset) -> None:
    from app.alert.service import _persist_and_dispatch

    # 데이터셋 소유자(Owner)에 더해 생성자 — AI-Ready 판정 · 신고 처리 권한이 생성자 · 관리자이므로
    await _persist_and_dispatch(session, [alert], extra_owners=[ds.created_by] if ds.created_by else None)


# ---------------------------------------------------------------------------
# 공유 최소 요건 미충족
# ---------------------------------------------------------------------------

async def evaluate_unmet_alerts(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """``AI_READY_UNMET`` 규칙마다 범위 안 활성 데이터셋의 최소 요건을 보고 알리거나 해결한다. 커밋은 호출자."""
    from app.catalog.ai_ready_check import min_requirements_for

    now = now or _now()
    rules = await _rules(session, UNMET)
    if not rules:
        return {"created": 0, "resolved": 0}
    datasets = list((await session.execute(select(Dataset).where(Dataset.status == "active"))).scalars().all())
    tags, lineages = await _scope_facts(session, {d.id for d in datasets})
    results = await min_requirements_for(session, datasets, now)

    last: dict[tuple[int, int], LineageAlert] = {}
    for a in await _latest(session, UNMET):
        last.setdefault((a.rule_id or 0, a.source_dataset_id or 0), a)

    created = resolved = 0
    for rule, cfg in rules:
        wanted = set(cfg.get("conditions") or CONDITIONS)
        for ds in datasets:
            if not _in_scope(rule, ds, tags, lineages):
                continue
            unmet = [c for c in results[ds.id]["checks"] if c["key"] in wanted and c["met"] is False]
            prev = last.get((rule.id, ds.id))
            live = prev is not None and prev.status in _LIVE
            if not unmet:
                if live:
                    _auto_resolve(prev, now)
                    resolved += 1
                continue
            if live:
                continue
            labels = ", ".join(c["label"] for c in unmet)
            await _send(session, LineageAlert(
                alert_type=UNMET, severity=rule.severity_override or "WARNING", source_dataset_id=ds.id,
                rule_id=rule.id, change_summary=f"AI-Ready 최소 요건 미충족: {ds.name} — {labels}"[:500],
                change_detail=json.dumps({"unmet": unmet}, ensure_ascii=False, default=str),
            ), ds)
            created += 1
    if created or resolved:
        logger.info("AI-Ready 최소 요건 알림: 생성 %d, 자동 해결 %d", created, resolved)
    return {"created": created, "resolved": resolved}


# ---------------------------------------------------------------------------
# 오류 신고 접수 · 처리 기한
# ---------------------------------------------------------------------------

def _ds_id(entity_id: str | None) -> int | None:
    try:
        return int(str(entity_id))
    except (TypeError, ValueError):
        return None


async def notify_report_received(session: AsyncSession, comment) -> int:
    """오류 신고 · 개선 요청이 접수됐을 때 ``REPORT_RECEIVED`` 규칙을 평가한다. 커밋은 호출자."""
    rules = await _rules(session, RECEIVED)
    ds_id = _ds_id(comment.entity_id)
    if not rules or ds_id is None:
        return 0
    ds = await session.get(Dataset, ds_id)
    if ds is None:
        return 0
    tags, lineages = await _scope_facts(session, {ds.id})
    kind = CATEGORY_LABEL.get(comment.category, "신고")
    due = _aware(comment.due_at)
    made = 0
    for rule, cfg in rules:
        cats = cfg.get("categories") or list(CATEGORY_LABEL)
        if comment.category not in cats or not _in_scope(rule, ds, tags, lineages):
            continue
        summary = f"{kind} 접수: {ds.name} — {comment.author_name}: {_snippet(comment.content_plain)}"
        if due:
            summary += f" (기한 {due.date().isoformat()})"
        await _send(session, LineageAlert(
            alert_type=RECEIVED, severity=rule.severity_override or "INFO", source_dataset_id=ds.id,
            rule_id=rule.id, change_summary=summary[:500],
            change_detail=json.dumps({"comment_id": comment.id, "category": comment.category,
                                      "author": comment.author_name, "due_at": due}, ensure_ascii=False, default=str),
        ), ds)
        made += 1
    if made:
        logger.info("신고 접수 알림 %d건: comment_id=%s dataset_id=%s", made, comment.id, ds.id)
    return made


def _stage(due: datetime, now: datetime, days_before: int) -> str | None:
    if due < now:
        return "overdue"
    if days_before > 0 and due - now <= timedelta(days=days_before):
        return "due_soon"
    return None


async def evaluate_due_alerts(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """``REPORT_DUE`` 규칙마다 처리 중인 신고의 기한을 보고 알리거나 해결한다. 커밋은 호출자."""
    from app.comments.models import Comment
    from app.comments.service import OPEN_STATUSES, REPORT_CATEGORIES, REPORT_ENTITY_TYPES

    now = now or _now()
    rules = await _rules(session, DUE)
    if not rules:
        return {"created": 0, "resolved": 0}
    reports = (await session.execute(
        select(Comment).where(Comment.entity_type.in_(REPORT_ENTITY_TYPES), Comment.parent_id.is_(None),
                              Comment.category.in_(REPORT_CATEGORIES), Comment.status.in_(OPEN_STATUSES),
                              Comment.due_at.is_not(None), Comment.is_deleted.is_(False))
    )).scalars().all()
    ds_ids = {i for r in reports if (i := _ds_id(r.entity_id)) is not None}
    datasets = {d.id: d for d in (await session.execute(
        select(Dataset).where(Dataset.id.in_(ds_ids))
    )).scalars().all()} if ds_ids else {}
    tags, lineages = await _scope_facts(session, set(datasets))

    last: dict[tuple[int, int, str], LineageAlert] = {}
    for a in await _latest(session, DUE):
        d = _detail(a)
        last.setdefault((a.rule_id or 0, int(d.get("comment_id") or 0), str(d.get("stage"))), a)
    active: set[tuple[int, int, str]] = set()

    created = resolved = 0
    for rule, cfg in rules:
        try:
            days_before = int(cfg.get("days_before", DEFAULT_DAYS_BEFORE))
        except (TypeError, ValueError):
            days_before = DEFAULT_DAYS_BEFORE
        for c in reports:
            ds = datasets.get(_ds_id(c.entity_id) or 0)
            if ds is None or not _in_scope(rule, ds, tags, lineages):
                continue
            due = _aware(c.due_at)
            stage = _stage(due, now, days_before)
            if stage is None:
                continue
            key = (rule.id, c.id, stage)
            active.add(key)
            prev = last.get(key)
            if prev is not None and prev.status in _LIVE:
                continue
            kind = CATEGORY_LABEL.get(c.category, "신고")
            if stage == "overdue":
                days = max(1, (now - due).days)
                head, severity = f"{kind} 처리 기한 넘김({days}일)", "BREAKING"
            else:
                left = (due - now).total_seconds() / 86400
                head, severity = f"{kind} 처리 기한 임박({'오늘' if left < 1 else f'{int(left)}일 남음'})", "WARNING"
            await _send(session, LineageAlert(
                alert_type=DUE, severity=rule.severity_override or severity, source_dataset_id=ds.id,
                rule_id=rule.id,
                change_summary=f"{head}: {ds.name} — #{c.id} {_snippet(c.content_plain)} (기한 {due.date().isoformat()})"[:500],
                change_detail=json.dumps({"comment_id": c.id, "stage": stage, "category": c.category,
                                          "status": c.status, "due_at": due}, ensure_ascii=False, default=str),
            ), ds)
            created += 1

    # 처리가 끝났거나(해결 · 소명 · 분류 바뀜 · 삭제) 임박이 넘김으로 바뀐 신고의 알림은 해결로
    for key, a in last.items():
        if a.status in _LIVE and key not in active:
            _auto_resolve(a, now)
            resolved += 1
    if created or resolved:
        logger.info("신고 기한 알림: 생성 %d, 자동 해결 %d", created, resolved)
    return {"created": created, "resolved": resolved}


# ---------------------------------------------------------------------------
# 주기 점검
# ---------------------------------------------------------------------------

async def run_checks(session: AsyncSession, now: datetime | None = None) -> dict[str, dict[str, int]]:
    """두 주기 점검을 한 번 돈다. 하나가 실패해도 다른 하나는 돈다."""
    out: dict[str, dict[str, int]] = {}
    for name, fn in (("unmet", evaluate_unmet_alerts), ("due", evaluate_due_alerts)):
        try:
            out[name] = await fn(session, now)
            await session.commit()
        except Exception as e:  # noqa: BLE001 — 한 점검 실패가 다른 점검을 막지 않게
            await session.rollback()
            logger.warning("AI-Ready 알림 점검 실패(%s): %s", name, e)
    return out


async def alert_loop() -> None:
    """lifespan 에서 기동 — 1시간마다 최소 요건 미충족 · 신고 기한 알림을 점검한다(규칙이 없으면 조회만)."""
    from app.core.database import async_session

    logger.info("AI-Ready 알림 점검 루프 시작 (간격 %ds)", CHECK_SECONDS)
    await asyncio.sleep(FIRST_CHECK_DELAY)
    while True:
        try:
            async with async_session() as session:
                await run_checks(session)
        except asyncio.CancelledError:
            logger.info("AI-Ready 알림 점검 루프 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("AI-Ready 알림 점검 실패: %s", e)
        await asyncio.sleep(CHECK_SECONDS)
