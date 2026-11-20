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
- ``REPORT_CLOSED`` 처리 결과(해결 · 소명) — 상태를 바꿀 때. ``categories``(생략하면 둘 다). 수신자는 규칙 구독자와
  **신고한 사람**이다(데이터셋 소유자는 처리한 쪽이라 더하지 않는다). 처리가 끝날 때(끝나지 않은 상태 → 해결 · 소명,
  또는 해결 ↔ 소명)마다 규칙당 한 번.

같은 규칙 · 대상(데이터셋, 신고와 단계)에 대해 마지막 알림이 미확인 · 확인 · 무시면 다시 만들지 않는다.
조건이 풀리면(요건 충족, 신고 처리 끝남, 임박이 넘김으로 바뀜) 그 알림을 시스템이 해결로 바꾼다 — 그 뒤 다시
조건이 생기면 새로 알린다. 무시한 알림도 조건이 풀리면 해결로 바뀐다. 신고를 해결 · 소명하거나 분류를 바꾸거나
지우면 그 신고의 접수 · 기한 알림을 그 자리에서 해결한다(``resolve_report_alerts``, DEF-140). 접수 알림은 신고마다
규칙당 한 번 — 분류를 바꿨다 되돌려도 다시 만들지 않는다(DEF-141).

주기 점검은 백엔드 프로세스가 여럿이어도 한 번에 하나만 돈다 — DB 잠금(PostgreSQL ``pg_try_advisory_lock`` ·
MariaDB ``GET_LOCK``)을 못 잡은 프로세스는 그 회차를 건너뛴다(``run_checks_once``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alert.models import AlertRule, LineageAlert
from app.catalog.models import Dataset, DatasetLineage, DatasetTag

logger = logging.getLogger(__name__)

UNMET = "AI_READY_UNMET"
RECEIVED = "REPORT_RECEIVED"
DUE = "REPORT_DUE"
CLOSED = "REPORT_CLOSED"
TRIGGERS = frozenset({UNMET, RECEIVED, DUE, CLOSED})

CONDITIONS = ("missing", "metadata", "freshness", "format")
CATEGORY_LABEL = {"bug": "오류 신고", "suggestion": "개선 요청"}
DEFAULT_DAYS_BEFORE = 2

CHECK_SECONDS = 3600          # 주기 점검 간격
FIRST_CHECK_DELAY = 120       # 기동 직후에는 잠시 기다림
SYSTEM_ACTOR = "system"
_LIVE = ("OPEN", "ACKNOWLEDGED", "DISMISSED")   # 다시 알리지 않는 상태
LOCK_KEY = 0x41524459         # 주기 점검 잠금 — PostgreSQL advisory lock 키('ARDY')
LOCK_NAME = "argus_ai_ready_alerts"   # MariaDB GET_LOCK 이름
DISPLAY_TIMEZONE = "Asia/Seoul"   # 알림 문구의 기한 날짜 시간대(DEF-144)


def _display_tz():
    """알림 문구에 쓰는 표시 시간대 — 화면(한국 사용자 브라우저)과 같은 날짜가 되도록 KST(DEF-144).

    서버(컨테이너)는 보통 UTC 로 돌아 OS 시간대를 쓰면 KST 0~9시 접수분의 기한 날짜가 하루 이르게 나온다.
    """
    try:
        return ZoneInfo(DISPLAY_TIMEZONE)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), "KST")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due_date(due: datetime) -> str:
    """기한 날짜(표시 시간대) — 예: 2026-11-25."""
    return due.astimezone(_display_tz()).date().isoformat()


def _days_left(due: datetime, now: datetime) -> int:
    """남은 일수 — 화면(``OpenDueText``)처럼 올림. 0.3일 남으면 1(D-1), 딱 기한이면 0."""
    return max(0, math.ceil((due - now).total_seconds() / 86400))


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
    # 분류를 바꿨다 되돌려 다시 접수돼도 규칙마다 접수 알림은 한 번만(DEF-141)
    notified = {a.rule_id for a in await _report_alerts(session, comment.id, ds.id, RECEIVED)}
    made = 0
    for rule, cfg in rules:
        cats = cfg.get("categories") or list(CATEGORY_LABEL)
        if comment.category not in cats or not _in_scope(rule, ds, tags, lineages):
            continue
        if rule.id in notified:
            continue
        summary = f"{kind} 접수: {ds.name} — {comment.author_name}: {_snippet(comment.content_plain)}"
        if due:
            summary += f" (기한 {_due_date(due)})"
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


CLOSED_LABEL = {"RESOLVED": "해결", "REJECTED": "소명"}


async def notify_report_closed(session: AsyncSession, comment, status: str, resolution: str | None,
                               actor: str | None) -> int:
    """신고 처리가 끝났을 때(해결 · 소명) ``REPORT_CLOSED`` 규칙으로 신고한 사람에게 알린다. 커밋은 호출자.

    수신자는 규칙 구독자와 신고자(작성자 아이디) — 데이터셋 소유자는 처리한 쪽이라 더하지 않는다.
    """
    from app.alert.service import _persist_and_dispatch
    from app.comments.service import comment_author_username

    if status not in CLOSED_LABEL:
        return 0
    rules = await _rules(session, CLOSED)
    ds_id = _ds_id(comment.entity_id)
    if not rules or ds_id is None:
        return 0
    ds = await session.get(Dataset, ds_id)
    if ds is None:
        return 0
    tags, lineages = await _scope_facts(session, {ds.id})
    reporter = comment_author_username(comment.author_name)
    kind = CATEGORY_LABEL.get(comment.category, "신고")
    made = 0
    for rule, cfg in rules:
        cats = cfg.get("categories") or list(CATEGORY_LABEL)
        if comment.category not in cats or not _in_scope(rule, ds, tags, lineages):
            continue
        summary = f"{kind} {CLOSED_LABEL[status]}: {ds.name} — #{comment.id} {_snippet(comment.content_plain, 40)}"
        if resolution:
            summary += f" → {_snippet(resolution)}"
        await _persist_and_dispatch(session, [LineageAlert(
            alert_type=CLOSED, severity=rule.severity_override or "INFO", source_dataset_id=ds.id,
            rule_id=rule.id, change_summary=summary[:500],
            change_detail=json.dumps({"comment_id": comment.id, "category": comment.category, "status": status,
                                      "resolution": resolution, "reporter": comment.author_name,
                                      "handled_by": actor}, ensure_ascii=False, default=str),
        )], dataset_owners=False, direct=[reporter] if reporter else None)
        made += 1
    if made:
        logger.info("신고 처리 결과 알림 %d건: comment_id=%s status=%s", made, comment.id, status)
    return made


async def _report_alerts(session: AsyncSession, comment_id: int, dataset_id: int | None,
                         trigger: str | None = None, statuses: tuple[str, ...] | None = None) -> list[LineageAlert]:
    """이 신고(댓글)의 접수 · 기한 알림 — 데이터셋으로 좁힌 뒤 상세의 ``comment_id`` 로 고른다."""
    stmt = select(LineageAlert).where(LineageAlert.alert_type.in_((trigger,) if trigger else (RECEIVED, DUE)))
    if dataset_id is not None:
        stmt = stmt.where(LineageAlert.source_dataset_id == dataset_id)
    if statuses:
        stmt = stmt.where(LineageAlert.status.in_(statuses))
    return [a for a in (await session.execute(stmt)).scalars().all()
            if str(_detail(a).get("comment_id")) == str(comment_id)]


async def resolve_report_alerts(session: AsyncSession, comment, now: datetime | None = None) -> int:
    """신고가 처리 끝남(해결 · 소명) · 신고 아님(분류 바뀜) · 삭제가 되면 그 신고의 접수 · 기한 알림을 바로 해결로(DEF-140).

    매시간 점검(``evaluate_due_alerts``)을 기다리지 않는다. 처리자는 시스템. 커밋은 호출자.
    """
    now = now or _now()
    alerts = await _report_alerts(session, comment.id, _ds_id(comment.entity_id), statuses=_LIVE)
    for a in alerts:
        _auto_resolve(a, now)
    if alerts:
        logger.info("신고 알림 자동 해결 %d건: comment_id=%s", len(alerts), comment.id)
    return len(alerts)


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
                left = _days_left(due, now)
                head, severity = f"{kind} 처리 기한 임박({'오늘까지' if left == 0 else f'{left}일 남음'})", "WARNING"
            await _send(session, LineageAlert(
                alert_type=DUE, severity=rule.severity_override or severity, source_dataset_id=ds.id,
                rule_id=rule.id,
                change_summary=f"{head}: {ds.name} — #{c.id} {_snippet(c.content_plain)} (기한 {_due_date(due)})"[:500],
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


@contextlib.asynccontextmanager
async def _run_lock(engine):
    """주기 점검 잠금 — 이 프로세스가 잡았으면 True. 백엔드 프로세스가 여럿일 때 같은 회차를 두 번 돌지 않게.

    잠금은 전용 연결에 건 세션 잠금(PostgreSQL ``pg_try_advisory_lock`` · MariaDB ``GET_LOCK(.., 0)``)이라
    기다리지 않고, 프로세스가 죽으면 연결과 함께 풀린다. 그 밖의 DB(SQLite 등 단일 프로세스)는 늘 True.
    """
    dialect = engine.dialect.name
    if dialect not in ("postgresql", "mysql", "mariadb"):
        yield True
        return
    async with engine.connect() as conn:
        if dialect == "postgresql":
            got = bool(await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}))
        else:
            got = (await conn.scalar(text("SELECT GET_LOCK(:n, 0)"), {"n": LOCK_NAME})) == 1
        try:
            yield got
        finally:
            if got:
                try:
                    if dialect == "postgresql":
                        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                    else:
                        await conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": LOCK_NAME})
                except Exception as e:  # noqa: BLE001 — 연결을 닫으면 어차피 풀린다
                    logger.warning("AI-Ready 알림 점검 잠금 해제 실패: %s", e)


async def run_checks_once(engine=None, session_factory=None) -> dict[str, dict[str, int]] | None:
    """잠금을 잡고 한 회차 점검. 다른 프로세스가 점검 중이면 건너뛰고 None."""
    if engine is None or session_factory is None:
        from app.core.database import async_session
        from app.core.database import engine as default_engine

        engine = engine or default_engine
        session_factory = session_factory or async_session
    async with _run_lock(engine) as got:
        if not got:
            logger.info("AI-Ready 알림 점검 건너뜀 — 다른 백엔드 프로세스가 점검 중")
            return None
        async with session_factory() as session:
            return await run_checks(session)


async def alert_loop() -> None:
    """lifespan 에서 기동 — 1시간마다 최소 요건 미충족 · 신고 기한 알림을 점검한다(규칙이 없으면 조회만).

    백엔드 프로세스가 여럿이면 잠금을 잡은 하나만 돈다(``run_checks_once``).
    """
    logger.info("AI-Ready 알림 점검 루프 시작 (간격 %ds)", CHECK_SECONDS)
    await asyncio.sleep(FIRST_CHECK_DELAY)
    while True:
        try:
            await run_checks_once()
        except asyncio.CancelledError:
            logger.info("AI-Ready 알림 점검 루프 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("AI-Ready 알림 점검 실패: %s", e)
        await asyncio.sleep(CHECK_SECONDS)
