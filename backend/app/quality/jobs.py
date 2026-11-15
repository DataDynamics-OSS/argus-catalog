# SPDX-License-Identifier: Apache-2.0
"""품질 진단 작업 — 대상·품질 지표·주기를 정해 두고 주기적으로 Trino 진단을 돌린다.

설계는 ``design/public-data-quality-guideline-review.md`` §6.7.

- **품질 진단 작업**(``catalog_quality_jobs``)은 정의다: 대상(카탈로그·스키마·데이터셋), 품질 지표, 주기.
- **작업 실행**(``catalog_quality_job_runs``)은 작업이 한 번 돈 것이다. ``launch`` 가 대상을 데이터셋으로 풀어
  데이터셋마다 진단 실행(``catalog_quality_runs``)을 대기열에 넣는다. 쿼리는 품질 워커가 낸다 — 워커는 배치를 모른다.
- **집계·마감**은 ``settle`` 이 한다: 작업 실행 아래 데이터셋 실행의 상태를 세어 적고, 모두 끝났으면 마감한다.
  워커의 보고마다 부르지 않고 스케줄러 tick 과 조회 시점에 부른다(데이터 소스 수집 실행의 "응답 없음" 판정과 같은 방식).
- **스케줄러**(``job_scheduler_loop``)는 서버 안에서 1분마다 ``next_run_at`` 이 지난 작업을 실행한다. 서버가 여러 대여도
  한 번만 실행되게 ``next_run_at`` 을 조건부 UPDATE 로 먼저 옮긴 쪽만 진행한다.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Catalog, Dataset
from app.quality import indicators as quality_indicators
from app.quality import runs
from app.quality.models import QualityDefinition, QualityJob, QualityJobRun, QualityRule, QualityRun
from app.quality.rule_direct import ENGINE_CHECK_TYPES
from app.quality.rule_sql import TRINO_CHECK_TYPES
from app.quality.schemas import (
    QualityJobIndicatorRules,
    QualityJobPreview,
    QualityJobResponse,
    QualityJobRuleItem,
    QualityJobRunResponse,
    QualityJobSkipped,
    QualityJobTarget,
    QualityJobWrite,
    QualitySchemaOption,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
SCHEDULE_TYPES = ("NONE", "HOURLY", "DAILY", "WEEKLY", "MONTHLY")
TARGET_TYPES = ("catalog", "schema", "dataset")
MAX_TARGETS = 500
# 작업 실행에 남기는 "실행하지 못한 데이터셋" 목록의 상한 (넘는 것은 개수만)
MAX_SKIPPED_ITEMS = 200
JOB_RUN_FINAL = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "SKIPPED")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class JobNotFoundError(LookupError):
    """품질 진단 작업·작업 실행이 없다 (404)."""


class JobConflictError(RuntimeError):
    """지금 상태에서는 할 수 없는 요청이다 (409)."""


# ---------------------------------------------------------------------------
# 주기
# ---------------------------------------------------------------------------

def validate_schedule(req: QualityJobWrite) -> None:
    """주기 항목이 유형에 맞게 채워졌는지. 틀리면 ``ValueError``."""
    kind = req.schedule_type
    if kind not in SCHEDULE_TYPES:
        raise ValueError(f"알 수 없는 주기: {kind} ({', '.join(SCHEDULE_TYPES)})")
    try:
        ZoneInfo(req.timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"알 수 없는 시간대: {req.timezone}") from e
    if kind in ("DAILY", "WEEKLY", "MONTHLY") and not _TIME.match(req.schedule_time or ""):
        raise ValueError("실행 시각은 HH:MM 형식이어야 합니다.")
    if kind == "WEEKLY" and (req.schedule_day is None or not 0 <= req.schedule_day <= 6):
        raise ValueError("요일은 0(월)~6(일) 이어야 합니다.")
    if kind == "MONTHLY" and (req.schedule_day is None or not 1 <= req.schedule_day <= 31):
        raise ValueError("날짜는 1~31 이어야 합니다.")
    if kind == "HOURLY" and (req.schedule_interval_hours is None or not 1 <= req.schedule_interval_hours <= 168):
        raise ValueError("간격은 1~168 시간이어야 합니다.")


def next_run_after(job: QualityJob, after: datetime, planned: datetime | None = None) -> datetime | None:
    """``after`` 뒤의 첫 실행 시각(UTC). 주기가 없거나 사용하지 않는 작업은 None.

    DAILY·WEEKLY·MONTHLY 는 작업의 시간대로 해석한다. MONTHLY 의 날짜가 그 달에 없으면 말일이다.
    HOURLY 는 ``after`` 로부터 N 시간 뒤다 — ``planned``(이번에 실행한 예정 시각)를 주면 거기서 N 시간씩 더해
    ``after`` 를 넘는 첫 시각을 준다(스케줄러가 늦게 돈 만큼 주기가 밀리지 않게).
    """
    if not job.enabled or job.schedule_type == "NONE":
        return None
    after = runs._as_utc(after)
    if job.schedule_type == "HOURLY":
        step = timedelta(hours=job.schedule_interval_hours or 1)
        planned = runs._as_utc(planned)
        if planned is None or planned > after:
            return after + step
        # 오래 멈춰 있었으면 밀린 차례는 건너뛴다
        return planned + step * (int((after - planned) / step) + 1)
    tz = ZoneInfo(job.timezone or "Asia/Seoul")
    local = after.astimezone(tz)
    hour, minute = (int(x) for x in (job.schedule_time or "00:00").split(":"))

    def at(day: datetime) -> datetime:
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if job.schedule_type == "DAILY":
        candidate = at(local)
        if candidate <= local:
            candidate = at(local + timedelta(days=1))
    elif job.schedule_type == "WEEKLY":
        candidate = at(local + timedelta(days=((job.schedule_day or 0) - local.weekday()) % 7))
        if candidate <= local:
            candidate = at(candidate + timedelta(days=7))
    else:  # MONTHLY
        def in_month(year: int, month: int) -> datetime:
            day = min(job.schedule_day or 1, calendar.monthrange(year, month)[1])
            return at(local.replace(year=year, month=month, day=day))

        candidate = in_month(local.year, local.month)
        if candidate <= local:
            year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
            candidate = in_month(year, month)
    return candidate.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 대상
# ---------------------------------------------------------------------------

def _schema_of(qualified_name: str | None) -> str | None:
    """데이터셋 이름의 스키마 토막(끝에서 두 번째)."""
    parts = [p for p in (qualified_name or "").split(".") if p]
    return parts[-2] if len(parts) >= 2 else None


def _targets_of(job: QualityJob) -> list[QualityJobTarget]:
    try:
        raw = json.loads(job.targets_json or "[]")
    except ValueError:
        logger.warning("품질 진단 작업 대상 JSON 손상: job_id=%s", job.id)
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(QualityJobTarget.model_validate(item))
        except ValueError:
            continue
    return out


async def _validate_targets(session: AsyncSession, targets: list[QualityJobTarget]) -> list[dict]:
    """대상 목록을 검증하고 저장할 모양으로 돌려준다. 틀리면 ``ValueError``."""
    if not targets:
        raise ValueError("진단 대상을 하나 이상 고르세요.")
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"진단 대상은 {MAX_TARGETS}개까지 고를 수 있습니다. 카탈로그나 스키마 단위로 고르세요.")
    out: list[dict] = []
    seen: set[tuple] = set()
    for t in targets:
        if t.type not in TARGET_TYPES:
            raise ValueError(f"알 수 없는 대상 유형: {t.type}")
        if t.type == "dataset":
            if t.dataset_id is None or await session.get(Dataset, t.dataset_id) is None:
                raise ValueError(f"데이터셋을 찾을 수 없습니다: {t.dataset_id}")
            item = {"type": "dataset", "dataset_id": t.dataset_id}
        else:
            if t.catalog_id is None or await session.get(Catalog, t.catalog_id) is None:
                raise ValueError(f"카탈로그를 찾을 수 없습니다: {t.catalog_id}")
            item = {"type": "catalog", "catalog_id": t.catalog_id}
            if t.type == "schema":
                if not (t.schema_name or "").strip():
                    raise ValueError("스키마 이름이 비어 있습니다.")
                item = {"type": "schema", "catalog_id": t.catalog_id, "schema_name": t.schema_name.strip()}
        key = tuple(item.items())
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


async def resolve_dataset_ids(session: AsyncSession, targets: list[QualityJobTarget]) -> list[int]:
    """대상을 지금의 데이터셋 ID 로 푼다(활성 데이터셋만, 중복 없이, ID 순).

    카탈로그·스키마 대상은 실행할 때마다 다시 풀기 때문에 새로 수집된 테이블이 자동으로 들어온다.
    """
    ids: set[int] = set()
    catalog_ids = {t.catalog_id for t in targets if t.type == "catalog" and t.catalog_id is not None}
    schemas: dict[int, set[str]] = {}
    for t in targets:
        if t.type == "schema" and t.catalog_id is not None and t.schema_name:
            schemas.setdefault(t.catalog_id, set()).add(t.schema_name)
    dataset_ids = {t.dataset_id for t in targets if t.type == "dataset" and t.dataset_id is not None}

    wanted_catalogs = catalog_ids | schemas.keys()
    if wanted_catalogs:
        rows = await session.execute(
            select(Dataset.id, Dataset.catalog_id, Dataset.qualified_name)
            .where(Dataset.catalog_id.in_(wanted_catalogs), Dataset.status == "active"))
        for dataset_id, catalog_id, qualified_name in rows.all():
            if catalog_id in catalog_ids or _schema_of(qualified_name) in schemas.get(catalog_id, ()):
                ids.add(dataset_id)
    if dataset_ids:
        rows = await session.execute(
            select(Dataset.id).where(Dataset.id.in_(dataset_ids), Dataset.status == "active"))
        ids.update(rows.scalars().all())
    return sorted(ids)


async def _with_rules(
    session: AsyncSession, dataset_ids: list[int], indicator_keys: list[str], definition_ids: list[int] | None = None,
) -> set[int]:
    """고른 분류·품질 지표로 Trino 가 평가할 활성 규칙이 하나라도 있는 데이터셋."""
    if not dataset_ids:
        return set()
    # 유형으로 먼저 거르고(DB), 같은 유형의 다른 도메인은 규칙마다 본다(indicators.wants)
    types = quality_indicators.check_types_for(indicator_keys) or frozenset(ENGINE_CHECK_TYPES)
    found: set[int] = set()
    for start in range(0, len(dataset_ids), 500):
        rows = await session.execute(
            select(QualityRule.dataset_id, QualityRule.check_type, QualityRule.domain, QualityRule.column_name,
                   QualityRule.definition_id)
            .distinct().where(
                QualityRule.dataset_id.in_(dataset_ids[start:start + 500]),
                QualityRule.is_active == "true", QualityRule.check_type.in_(types)))
        found.update(
            r.dataset_id for r in rows.all()
            if quality_indicators.wants(indicator_keys, r.check_type, r.domain, r.column_name)
            and runs.wants_definition(definition_ids, r.definition_id))
    return found


async def preview(
    session: AsyncSession, targets: list[QualityJobTarget], indicator_keys: list[str],
    definition_ids: list[int] | None = None,
) -> QualityJobPreview:
    """등록 화면용 — 지금 이 대상·지표로 실행하면 몇 개 데이터셋이 진단되는지."""
    keys = quality_indicators.normalize(indicator_keys)
    dataset_ids = await resolve_dataset_ids(session, targets)
    return QualityJobPreview(
        datasets_total=len(dataset_ids),
        datasets_with_rules=len(await _with_rules(session, dataset_ids, keys, definition_ids)))


# 등록 화면에 지표마다 보여 줄 규칙 수 — 나머지는 개수만 알린다
RULES_PER_INDICATOR = 50


async def rules_overview(
    session: AsyncSession, targets: list[QualityJobTarget], group_by: str = "indicator",
) -> list[QualityJobIndicatorRules]:
    """등록 화면용 — 대상 데이터셋이 지표마다 어떤 규칙(컬럼·기준)을 갖고 있는지. 규칙이 있는 지표만, 정해진 순서로.

    ``group_by="definition"`` 이면 분류 대신 미리 정의한 품질 지표로 묶는다(ID 순). 지표 없이 직접 만든 규칙은
    ``definition_id`` 가 None 인 묶음 하나로 맨 뒤에 둔다.
    """
    by_definition = group_by == "definition"
    dataset_ids = await resolve_dataset_ids(session, targets)
    grouped: dict[str, QualityJobIndicatorRules] = {}
    seen: dict[str, set[int]] = {}
    for start in range(0, len(dataset_ids), 500):
        rows = await session.execute(
            select(QualityRule, Dataset.name)
            .join(Dataset, Dataset.id == QualityRule.dataset_id)
            .where(QualityRule.dataset_id.in_(dataset_ids[start:start + 500]),
                   QualityRule.is_active == "true", QualityRule.check_type.in_(TRINO_CHECK_TYPES))
            .order_by(Dataset.name, QualityRule.dataset_id, QualityRule.column_name, QualityRule.id))
        for rule, dataset_name in rows.all():
            indicator = quality_indicators.indicator_of(rule.check_type, rule.domain, rule.column_name)
            if indicator is None:
                continue
            key = (str(rule.definition_id) if rule.definition_id is not None else "") if by_definition else indicator.key
            group = grouped.setdefault(key, QualityJobIndicatorRules(
                indicator="" if by_definition else indicator.key,
                definition_id=rule.definition_id if by_definition else None))
            group.rules_total += 1
            seen.setdefault(key, set()).add(rule.dataset_id)
            if len(group.rules) < RULES_PER_INDICATOR:
                group.rules.append(QualityJobRuleItem(
                    rule_id=rule.id, rule_name=rule.rule_name, dataset_id=rule.dataset_id, dataset_name=dataset_name,
                    check_type=rule.check_type, column_name=rule.column_name, expected_value=rule.expected_value,
                    definition_id=rule.definition_id))
    out = []
    order = sorted(grouped, key=lambda k: (k == "", int(k or 0))) if by_definition \
        else [i.key for i in quality_indicators.INDICATORS]
    for key in order:
        group = grouped.get(key)
        if group is None:
            continue
        group.datasets = len(seen[key])
        group.truncated = group.rules_total > len(group.rules)
        out.append(group)
    return out


async def list_schemas(session: AsyncSession, catalog_id: int) -> list[QualitySchemaOption]:
    """카탈로그의 스키마 이름과 데이터셋 수 — 대상 선택기."""
    rows = await session.execute(
        select(Dataset.qualified_name).where(Dataset.catalog_id == catalog_id, Dataset.status == "active"))
    counts: dict[str, int] = {}
    for (qualified_name,) in rows.all():
        schema = _schema_of(qualified_name)
        if schema:
            counts[schema] = counts.get(schema, 0) + 1
    return [QualitySchemaOption(schema_name=name, datasets=n) for name, n in sorted(counts.items())]


async def _label_targets(session: AsyncSession, jobs: list[QualityJob]) -> dict[int, list[QualityJobTarget]]:
    """작업별 대상에 표시 이름(카탈로그·데이터셋 이름)을 붙인다."""
    parsed = {job.id: _targets_of(job) for job in jobs}
    catalog_ids = {t.catalog_id for ts in parsed.values() for t in ts if t.catalog_id is not None}
    dataset_ids = {t.dataset_id for ts in parsed.values() for t in ts if t.dataset_id is not None}
    catalogs: dict[int, str] = {}
    datasets: dict[int, str] = {}
    if catalog_ids:
        catalogs = dict((await session.execute(
            select(Catalog.id, Catalog.name).where(Catalog.id.in_(catalog_ids)))).all())
    if dataset_ids:
        datasets = dict((await session.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(dataset_ids)))).all())
    for targets in parsed.values():
        for t in targets:
            if t.type == "dataset":
                t.label = datasets.get(t.dataset_id) or f"(삭제된 데이터셋 {t.dataset_id})"
            else:
                name = catalogs.get(t.catalog_id) or f"(삭제된 카탈로그 {t.catalog_id})"
                t.label = f"{name}.{t.schema_name}" if t.type == "schema" else name
    return parsed


# ---------------------------------------------------------------------------
# 작업 실행 — 응답, 집계·마감
# ---------------------------------------------------------------------------

def _skipped_of(job_run: QualityJobRun) -> tuple[int, int, list[QualityJobSkipped]]:
    """``skipped_json`` → (규칙 없는 데이터셋 수, 실행하지 못한 데이터셋 수, 그 목록)."""
    try:
        data = json.loads(job_run.skipped_json or "{}")
    except ValueError:
        return 0, 0, []
    if not isinstance(data, dict):
        return 0, 0, []
    items = [QualityJobSkipped.model_validate(i) for i in data.get("items") or []]
    return int(data.get("no_rules") or 0), int(data.get("skipped") or len(items)), items


async def _fill_children(session: AsyncSession, responses: list[QualityJobRunResponse]) -> None:
    """작업 실행마다 그 아래 데이터셋 실행에서 규칙 수·진행·품질 점수를 모아 붙인다. 실행이 하나뿐이면 그 실행과 데이터셋도."""
    by_id = {r.id: r for r in responses}
    if not by_id:
        return
    rows = (await session.execute(
        select(QualityRun, Dataset.name)
        .outerjoin(Dataset, Dataset.id == QualityRun.dataset_id)
        .where(QualityRun.job_run_id.in_(by_id)).order_by(QualityRun.id)
    )).all()
    children: dict[int, list[tuple[QualityRun, str | None]]] = {}
    for run, dataset_name in rows:
        children.setdefault(run.job_run_id, []).append((run, dataset_name))
    for job_run_id, both in children.items():
        resp = by_id[job_run_id]
        # 진행(쿼리 수 · 지금 실행 중인 쿼리)은 프로파일 실행도 센다. 규칙 · 점수 · 단일 실행은 진단 실행만
        profiles = [run for run, _ in both if _is_profile(run)]
        resp.profiles_total = len(profiles)
        resp.profiles_succeeded = sum(1 for r in profiles if r.status == "SUCCEEDED")
        resp.profiles_failed = sum(1 for r in profiles if r.status in ("FAILED", "PARTIAL", "CANCELLED"))
        resp.progress_done = sum(run.progress_done or 0 for run, _ in both)
        totals = [run.progress_total for run, _ in both]
        resp.progress_total = sum(totals) if all(t is not None for t in totals) else None
        resp.current_target = next((run.current_target for run, _ in both if run.status == "RUNNING"), None)
        items = [(run, name) for run, name in both if not _is_profile(run)]
        resp.rules_total = sum(run.rules_total or 0 for run, _ in items)
        resp.rules_evaluated = sum(run.rules_evaluated or 0 for run, _ in items)
        scores = [float(run.score) for run, _ in items if run.score is not None]
        resp.score = round(sum(scores) / len(scores), 2) if scores else None
        if len(items) == 1:
            run, dataset_name = items[0]
            resp.run_id, resp.dataset_id = run.id, run.dataset_id
            resp.dataset_name, resp.engine_table = dataset_name, run.engine_table


def _is_profile(run: QualityRun) -> bool:
    return run.kind == runs.PROFILE_KIND


async def _decorate(session: AsyncSession, responses: list[QualityJobRunResponse]) -> None:
    await runs.fill_definition_names(session, responses)
    await _fill_children(session, responses)


async def _named(session: AsyncSession, resp: QualityJobRunResponse) -> QualityJobRunResponse:
    await _decorate(session, [resp])
    return resp


def job_run_response(job_run: QualityJobRun, job_name: str | None = None) -> QualityJobRunResponse:
    resp = QualityJobRunResponse.model_validate(job_run)
    resp.job_name = job_name
    resp.indicators = quality_indicators.load(job_run.indicators)
    resp.definition_ids = runs.load_definition_ids(job_run.definition_ids)
    resp.no_rules, resp.skipped_total, resp.skipped = _skipped_of(job_run)
    resp.runs_active = max(0, job_run.runs_total - job_run.runs_succeeded - job_run.runs_failed) \
        if job_run.status == "RUNNING" else 0
    if job_run.checked_count:
        resp.error_rate = round((job_run.error_count or 0) / job_run.checked_count * 100, 4)
    return resp


async def settle(session: AsyncSession, job_runs: list[QualityJobRun]) -> None:
    """진행 중인 작업 실행의 집계를 갱신하고, 아래 데이터셋 실행이 모두 끝났으면 마감한다."""
    for job_run in job_runs:
        if job_run.status != "RUNNING":
            continue
        both = list((await session.execute(
            select(QualityRun).where(QualityRun.job_run_id == job_run.id))).scalars().all())
        await runs.settle_stale(session, both)
        # 작업은 프로파일 실행까지 끝나야 마감한다. 성공 · 실패 · 건수는 진단 실행으로 센다
        checks = [r for r in both if not _is_profile(r)]
        profiles = [r for r in both if _is_profile(r)]
        active = sum(1 for r in both if r.status in runs.ACTIVE_STATUSES)
        succeeded = sum(1 for r in checks if r.status == "SUCCEEDED")
        partial = sum(1 for r in checks if r.status == "PARTIAL")
        job_run.runs_succeeded = succeeded
        job_run.runs_failed = sum(1 for r in checks if r.status not in runs.ACTIVE_STATUSES) - succeeded
        counted = [r for r in checks if r.checked_count is not None]
        job_run.checked_count = sum(r.checked_count or 0 for r in counted) if counted else None
        job_run.error_count = sum(r.error_count or 0 for r in counted) if counted else None
        if active:
            continue
        # 진단 실행 없이 프로파일만 넣은 작업 실행(대상에 규칙이 없음)은 프로파일 결과로 마감한다
        children = checks or profiles
        if not checks:
            succeeded = sum(1 for r in profiles if r.status == "SUCCEEDED")
            partial = sum(1 for r in profiles if r.status == "PARTIAL")
        profiles_failed = sum(1 for r in profiles if r.status != "SUCCEEDED") if checks else 0
        _, skipped_total, _ = _skipped_of(job_run)
        cancelled = any(r.status == "CANCELLED" for r in both)
        # 취소를 눌렀어도 그 전에 다 끝났으면(취소된 실행이 없으면) 실제 결과대로 마감한다
        if job_run.cancel_requested_by and (cancelled or not children):
            job_run.status = "CANCELLED"
            job_run.message = "사용자가 중지했습니다."
        elif not children:
            # 실행 중에 데이터셋이 지워지면 그 진단 실행도 함께 지워진다 — 0개를 "모두 성공" 으로 세지 않는다
            job_run.status = "FAILED"
            job_run.message = "진단 실행이 남아 있지 않습니다. 실행 중에 데이터셋이 지워졌을 수 있습니다."
        elif succeeded == len(children) and not skipped_total and not profiles_failed:
            job_run.status = "SUCCEEDED"
        elif succeeded or partial:
            job_run.status = "PARTIAL"
            job_run.message = _problem_summary(len(children) - succeeded, skipped_total, profiles_failed)
        else:
            job_run.status = "FAILED"
            job_run.message = _problem_summary(len(children), skipped_total, profiles_failed)
        job_run.finished_at = runs._now()
        logger.info("품질 진단 작업 실행 종료: job_run_id=%d job_id=%d status=%s 성공 %d / 실패 %d",
                    job_run.id, job_run.job_id, job_run.status, job_run.runs_succeeded, job_run.runs_failed)
    await session.flush()


def _problem_summary(failed: int, skipped: int, profiles_failed: int = 0) -> str:
    parts = []
    if failed:
        parts.append(f"실패·일부 실패 {failed}개")
    if skipped:
        parts.append(f"실행하지 못한 데이터셋 {skipped}개")
    if profiles_failed:
        parts.append(f"프로파일 실패 {profiles_failed}개")
    return ", ".join(parts) or "성공한 진단이 없습니다."


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

async def launch(
    session: AsyncSession, job: QualityJob, trigger_type: str, requested_by: str | None,
) -> QualityJobRun:
    """작업 실행을 만들고 대상 데이터셋의 진단 실행을 대기열에 넣는다.

    이전 작업 실행이 끝나지 않았으면 — 주기 실행은 ``SKIPPED`` 이력만 남기고, 수동 실행은 ``JobConflictError``.
    """
    running = list((await session.execute(
        select(QualityJobRun).where(QualityJobRun.job_id == job.id, QualityJobRun.status == "RUNNING")
    )).scalars().all())
    await settle(session, running)
    now = runs._now()
    if any(r.status == "RUNNING" for r in running):
        if trigger_type != "SCHEDULE":
            raise JobConflictError("이 품질 진단 작업의 이전 실행이 아직 끝나지 않았습니다.")
        skipped = QualityJobRun(
            job_id=job.id, trigger_type=trigger_type, status="SKIPPED", indicators=job.indicators,
            definition_ids=job.definition_ids,
            message="이전 실행이 끝나지 않아 이번 차례를 건너뛰었습니다.", started_at=now, finished_at=now)
        session.add(skipped)
        await session.flush()
        logger.warning("품질 진단 작업 실행 건너뜀(이전 실행 진행 중): job_id=%d", job.id)
        return skipped

    keys = quality_indicators.load(job.indicators)
    dataset_ids = await resolve_dataset_ids(session, _targets_of(job))
    definition_ids = runs.load_definition_ids(job.definition_ids)
    with_rules = await _with_rules(session, dataset_ids, keys, definition_ids)
    job_run = QualityJobRun(
        job_id=job.id, trigger_type=trigger_type, status="RUNNING", indicators=job.indicators,
        definition_ids=job.definition_ids,
        targets_total=len(dataset_ids), requested_by=requested_by, started_at=now)
    session.add(job_run)
    await session.flush()

    queued = 0
    profiled = 0
    items: list[dict] = []
    skipped_total = 0
    names: dict[int, str] = {}

    async def skip(dataset_id: int, reason: str) -> None:
        nonlocal skipped_total, names
        skipped_total += 1
        if len(items) < MAX_SKIPPED_ITEMS:
            if not names:
                names = dict((await session.execute(
                    select(Dataset.id, Dataset.name).where(Dataset.id.in_(dataset_ids)))).all())
            items.append({"dataset_id": dataset_id, "name": names.get(dataset_id), "reason": reason})

    for dataset_id in dataset_ids:
        if job.include_profile:
            # 프로파일 먼저 — 같은 원본의 실행은 워커가 넣은 순서대로 하나씩 잡는다. 규칙이 없는 데이터셋도 프로파일한다
            try:
                await runs.create_run(session, dataset_id, requested_by, trigger_type, job_run_id=job_run.id,
                                      kind=runs.PROFILE_KIND)
                profiled += 1
            except (runs.RunConflictError, runs.EngineUnavailableError, runs.RunNotFoundError) as e:
                await skip(dataset_id, f"프로파일: {e}")
        if dataset_id not in with_rules:
            continue
        try:
            await runs.create_run(
                session, dataset_id, requested_by, trigger_type, indicators=keys, definition_ids=definition_ids,
                job_run_id=job_run.id)
            queued += 1
        except (runs.RunConflictError, runs.EngineUnavailableError, runs.RunNotFoundError) as e:
            await skip(dataset_id, str(e))
    job_run.runs_total = queued
    job_run.skipped_json = json.dumps(
        {"no_rules": len(dataset_ids) - len(with_rules), "skipped": skipped_total, "items": items},
        ensure_ascii=False)
    if not queued and not profiled:
        job_run.status = "FAILED"
        job_run.finished_at = runs._now()
        if not dataset_ids:
            job_run.message = "진단 대상 데이터셋이 없습니다."
            if trigger_type == "SCHEDULE":
                # 대상이 지워졌거나 비활성이다 — 주기마다 실패 이력만 쌓이므로 작업을 끈다
                job.enabled = False
                job.next_run_at = None
                job_run.message += " 작업을 사용 안 함으로 바꿨습니다 — 대상을 다시 고른 뒤 켜세요."
                logger.warning("품질 진단 작업 사용 안 함으로 전환(대상 없음): job_id=%d", job.id)
        elif not with_rules:
            job_run.message = ("선택한 품질 지표가 할당된 활성 규칙이 있는 데이터셋이 없습니다." if definition_ids
                               else "선택한 진단 기준의 활성 규칙이 있는 데이터셋이 없습니다.")
        else:
            job_run.message = f"진단을 시작한 데이터셋이 없습니다(실행하지 못한 데이터셋 {skipped_total}개)."
    await session.flush()
    logger.info("품질 진단 작업 실행 시작: job_run_id=%d job_id=%d trigger=%s 대상 %d / 대기열 %d (프로파일 %d) / 규칙 없음 %d / 못 넣음 %d",
                job_run.id, job.id, trigger_type, len(dataset_ids), queued, profiled,
                len(dataset_ids) - len(with_rules), skipped_total)
    return job_run


async def _blocked_by_other_run(session: AsyncSession, job: QualityJob) -> bool:
    """데이터셋 하나짜리 작업의 대상을 **다른** 진단(다른 작업·품질 탭의 수동 진단)이 쓰고 있는가.

    데이터셋마다 진행 중인 진단은 하나뿐이라, 이때 실행하면 시작도 못 하고 실패 이력만 남는다. 이 작업 자신의
    이전 실행이 끝나지 않은 경우는 여기서 다루지 않는다(``launch`` 가 건너뜀 이력을 남긴다).
    """
    dataset_ids = await resolve_dataset_ids(session, _targets_of(job))
    if len(dataset_ids) != 1:
        return False
    active = list((await session.execute(
        select(QualityRun).where(
            QualityRun.dataset_id == dataset_ids[0], QualityRun.status.in_(runs.ACTIVE_STATUSES))
    )).scalars().all())
    await runs.settle_stale(session, active)
    active = [r for r in active if r.status in runs.ACTIVE_STATUSES]
    if not active:
        return False
    own = set((await session.execute(
        select(QualityJobRun.id).where(QualityJobRun.job_id == job.id))).scalars().all()) if job.id else set()
    return any(r.job_run_id not in own for r in active)


async def run_now(session: AsyncSession, job_id: int, requested_by: str | None) -> QualityJobRunResponse:
    """지금 실행 — 주기와 무관하게 한 번 돌린다(``next_run_at`` 은 그대로)."""
    job = await _get(session, job_id)
    if await _blocked_by_other_run(session, job):
        raise JobConflictError("이 데이터셋의 다른 진단이 대기 중이거나 실행 중입니다. 끝난 뒤에 다시 실행하세요.")
    return await _named(session, job_run_response(await launch(session, job, "MANUAL", requested_by), job.name))


async def cancel_job_run(session: AsyncSession, job_run_id: int, requested_by: str | None) -> QualityJobRunResponse:
    """작업 실행 취소 — 아직 끝나지 않은 데이터셋 실행을 모두 취소한다."""
    job_run = await session.get(QualityJobRun, job_run_id)
    if job_run is None:
        raise JobNotFoundError("작업 실행을 찾을 수 없습니다.")
    await settle(session, [job_run])
    if job_run.status != "RUNNING":
        raise JobConflictError("이미 끝난 작업 실행은 중지할 수 없습니다.")
    job_run.cancel_requested_by = requested_by or "unknown"
    active = (await session.execute(
        select(QualityRun.id).where(
            QualityRun.job_run_id == job_run.id, QualityRun.status.in_(runs.ACTIVE_STATUSES))
    )).scalars().all()
    for run_id in active:
        try:
            await runs.cancel_run(session, run_id, requested_by)
        except runs.RunConflictError:
            pass  # 그 사이에 끝났다
    await settle(session, [job_run])
    logger.info("품질 진단 작업 실행 취소 요청: job_run_id=%d by=%s (데이터셋 실행 %d개)", job_run.id, requested_by, len(active))
    return await _named(session, job_run_response(
        job_run, await session.scalar(select(QualityJob.name).where(QualityJob.id == job_run.job_id))))


async def list_job_runs(
    session: AsyncSession, job_id: int | None = None, limit: int = 30, cursor: int = 0,
) -> list[QualityJobRunResponse]:
    """작업 이력 — 최근 것부터."""
    stmt = select(QualityJobRun).order_by(QualityJobRun.id.desc()).offset(max(cursor, 0)).limit(limit)
    if job_id is not None:
        stmt = stmt.where(QualityJobRun.job_id == job_id)
    rows = list((await session.execute(stmt)).scalars().all())
    await settle(session, rows)
    names = await _job_names(session, {r.job_id for r in rows})
    out = [job_run_response(r, names.get(r.job_id)) for r in rows]
    await _decorate(session, out)
    return out


async def get_job_run(session: AsyncSession, job_run_id: int) -> QualityJobRunResponse:
    job_run = await session.get(QualityJobRun, job_run_id)
    if job_run is None:
        raise JobNotFoundError("작업 실행을 찾을 수 없습니다.")
    await settle(session, [job_run])
    return await _named(session, job_run_response(
        job_run, (await _job_names(session, {job_run.job_id})).get(job_run.job_id)))


async def _job_names(session: AsyncSession, job_ids: set[int]) -> dict[int, str]:
    if not job_ids:
        return {}
    return dict((await session.execute(select(QualityJob.id, QualityJob.name).where(QualityJob.id.in_(job_ids)))).all())


# ---------------------------------------------------------------------------
# 작업 등록·조회
# ---------------------------------------------------------------------------

async def _get(session: AsyncSession, job_id: int) -> QualityJob:
    job = await session.get(QualityJob, job_id)
    if job is None:
        raise JobNotFoundError("품질 진단 작업을 찾을 수 없습니다.")
    return job


async def _responses(session: AsyncSession, jobs: list[QualityJob]) -> list[QualityJobResponse]:
    """작업 행 → 응답. 대상 이름과 마지막 작업 실행을 붙인다."""
    targets = await _label_targets(session, jobs)
    last: dict[int, QualityJobRun] = {}
    if jobs:
        latest_ids = (await session.execute(
            select(func.max(QualityJobRun.id)).where(QualityJobRun.job_id.in_([j.id for j in jobs]))
            .group_by(QualityJobRun.job_id))).scalars().all()
        if latest_ids:
            rows = list((await session.execute(
                select(QualityJobRun).where(QualityJobRun.id.in_(latest_ids)))).scalars().all())
            await settle(session, rows)
            last = {r.job_id: r for r in rows}
    out = []
    for job in jobs:
        resp = QualityJobResponse.model_validate(job)
        resp.targets = targets.get(job.id, [])
        resp.indicators = quality_indicators.load(job.indicators)
        resp.definition_ids = runs.load_definition_ids(job.definition_ids)
        resp.last_run = job_run_response(last[job.id], job.name) if job.id in last else None
        out.append(resp)
    await runs.fill_definition_names(session, out)
    await _decorate(session, [r.last_run for r in out if r.last_run is not None])
    return out


async def list_jobs(session: AsyncSession) -> list[QualityJobResponse]:
    jobs = list((await session.execute(select(QualityJob).order_by(QualityJob.name, QualityJob.id))).scalars().all())
    return await _responses(session, jobs)


async def get_job(session: AsyncSession, job_id: int) -> QualityJobResponse:
    return (await _responses(session, [await _get(session, job_id)]))[0]


async def _apply(session: AsyncSession, job: QualityJob, req: QualityJobWrite, username: str | None) -> None:
    name = req.name.strip()
    if not name:
        raise ValueError("작업 이름을 입력하세요.")
    duplicate = await session.scalar(
        select(QualityJob.id).where(func.lower(QualityJob.name) == name.lower(), QualityJob.id != (job.id or 0)))
    if duplicate is not None:
        raise JobConflictError("같은 이름의 품질 진단 작업이 이미 있습니다.")
    validate_schedule(req)
    keys = quality_indicators.normalize(req.indicators)
    definition_ids = sorted(set(req.definition_ids))
    if definition_ids:
        known = set((await session.execute(
            select(QualityDefinition.id).where(QualityDefinition.id.in_(definition_ids)))).scalars().all())
        missing = [i for i in definition_ids if i not in known]
        if missing:
            raise ValueError(f"없는 품질 지표입니다: {', '.join(f'#{i}' for i in missing)}")
    targets = await _validate_targets(session, req.targets)

    def schedule_key() -> tuple:
        return (job.schedule_type, job.schedule_time, job.schedule_day, job.schedule_interval_hours,
                job.timezone, bool(job.enabled))

    before = schedule_key() if job.id else None
    job.name = name
    job.description = (req.description or "").strip() or None
    job.targets_json = json.dumps(targets, ensure_ascii=False)
    job.indicators = quality_indicators.dump(keys)
    job.definition_ids = runs.dump_definition_ids(definition_ids)
    job.schedule_type = req.schedule_type
    timed = req.schedule_type in ("DAILY", "WEEKLY", "MONTHLY")
    job.schedule_time = req.schedule_time if timed else None
    job.schedule_day = req.schedule_day if req.schedule_type in ("WEEKLY", "MONTHLY") else None
    job.schedule_interval_hours = req.schedule_interval_hours if req.schedule_type == "HOURLY" else None
    job.timezone = req.timezone
    job.enabled = req.enabled
    job.include_profile = bool(req.include_profile)
    job.updated_by = username
    # 주기(종류·시각·요일·간격·시간대·사용 여부)를 고친 때에만 다음 실행 시각을 지금 기준으로 다시 잡는다 —
    # 이름·설명·대상만 고쳤는데 긴 간격의 작업이 처음부터 다시 기다리지 않게
    if before != schedule_key():
        job.next_run_at = next_run_after(job, runs._now())


async def create_job(session: AsyncSession, req: QualityJobWrite, username: str | None) -> QualityJobResponse:
    job = QualityJob(created_by=username)
    await _apply(session, job, req, username)
    session.add(job)
    await session.flush()
    await session.refresh(job)
    logger.info("품질 진단 작업 등록: job_id=%d name=%s schedule=%s by=%s", job.id, job.name, job.schedule_type, username)
    return (await _responses(session, [job]))[0]


async def update_job(
    session: AsyncSession, job_id: int, req: QualityJobWrite, username: str | None,
) -> QualityJobResponse:
    job = await _get(session, job_id)
    await _apply(session, job, req, username)
    await session.flush()
    await session.refresh(job)
    logger.info("품질 진단 작업 수정: job_id=%d name=%s schedule=%s by=%s", job.id, job.name, job.schedule_type, username)
    return (await _responses(session, [job]))[0]


async def delete_job(session: AsyncSession, job_id: int, username: str | None) -> None:
    """작업과 그 작업 이력을 지운다. 데이터셋 실행과 규칙 결과는 남는다(작업 실행 연결만 끊긴다)."""
    job = await _get(session, job_id)
    running = list((await session.execute(
        select(QualityJobRun).where(QualityJobRun.job_id == job.id, QualityJobRun.status == "RUNNING")
    )).scalars().all())
    await settle(session, running)
    if any(r.status == "RUNNING" for r in running):
        raise JobConflictError("실행 중인 작업 실행이 있습니다. 중지하거나 끝난 뒤에 지우세요.")
    await session.delete(job)
    await session.flush()
    logger.info("품질 진단 작업 삭제: job_id=%d name=%s by=%s", job_id, job.name, username)


# ---------------------------------------------------------------------------
# 스케줄러
# ---------------------------------------------------------------------------

async def tick(session_factory) -> int:
    """주기가 된 작업을 실행하고 진행 중인 작업 실행을 마감한다. 실행한 작업 수를 돌려준다."""
    launched = 0
    async with session_factory() as session:
        running = list((await session.execute(
            select(QualityJobRun).where(QualityJobRun.status == "RUNNING"))).scalars().all())
        await settle(session, running)
        await session.commit()
        now = runs._now()
        due = (await session.execute(
            select(QualityJob.id, QualityJob.next_run_at).where(
                QualityJob.enabled.is_(True), QualityJob.schedule_type != "NONE",
                QualityJob.next_run_at.is_not(None), QualityJob.next_run_at <= now)
        )).all()
    for job_id, planned in due:
        try:
            async with session_factory() as session:
                job = await session.get(QualityJob, job_id)
                if job is None:
                    continue
                # 다음 시각으로 먼저 옮긴 쪽만 실행한다 — 서버가 여러 대여도 한 번만
                if await _blocked_by_other_run(session, job):
                    # 다른 진단이 끝난 뒤에 실행한다 — 예정 시각을 그대로 두어 다음 tick 에 다시 본다
                    logger.info("품질 진단 작업 주기 실행 미룸(같은 데이터셋의 다른 진단 진행 중): job_id=%d", job_id)
                    await session.commit()  # 만료 처리한 실행이 있으면 반영
                    continue
                claimed = await session.execute(
                    update(QualityJob)
                    .where(QualityJob.id == job_id, QualityJob.next_run_at == planned)
                    .values(next_run_at=next_run_after(job, now, planned)))
                if claimed.rowcount != 1:
                    await session.rollback()
                    continue
                await launch(session, job, "SCHEDULE", None)
                await session.commit()
                launched += 1
        except Exception as e:  # noqa: BLE001 — 한 작업의 오류가 다른 작업을 막지 않는다
            logger.warning("품질 진단 작업 주기 실행 실패: job_id=%d %r", job_id, e)
    return launched


# 예전 데이터셋 품질 탭의 진단 주기(데이터셋 속성) — 간이 진단을 없애며 품질 진단 작업으로 옮긴다(TODO-095)
LEGACY_SCHEDULE_PROPERTY = "argus.quality_schedule"
LEGACY_SCHEDULE_FAILURE_PROPERTY = "argus.quality_schedule_failure"
_LEGACY_SCHEDULES = {
    "hourly": ("HOURLY", None, None, 1, "매시간"),
    "daily": ("DAILY", "03:00", None, None, "매일 03:00"),
    "weekly": ("WEEKLY", "03:00", 0, None, "매주 월요일 03:00"),
}


async def migrate_dataset_schedules(session_factory) -> int:
    """데이터셋마다 남은 예전 진단 주기를 그 데이터셋 하나를 대상으로 하는 품질 진단 작업으로 옮기고 속성을 지운다.

    기동할 때 한 번 — 옮길 것이 없으면 아무것도 하지 않는다. 데이터셋마다 따로 커밋하고, 속성을 먼저 지운 쪽만 작업을
    만든다(서버가 여러 대여도 한 번만). 만든 작업 수를 돌려준다.
    """
    from sqlalchemy import delete

    from app.catalog.models import DatasetProperty

    async with session_factory() as session:
        rows = (await session.execute(
            select(DatasetProperty.dataset_id, DatasetProperty.property_value)
            .where(DatasetProperty.property_key == LEGACY_SCHEDULE_PROPERTY))).all()
    created = 0
    for dataset_id, value in rows:
        try:
            async with session_factory() as session:
                gone = await session.execute(delete(DatasetProperty).where(
                    DatasetProperty.dataset_id == dataset_id,
                    DatasetProperty.property_key.in_([LEGACY_SCHEDULE_PROPERTY, LEGACY_SCHEDULE_FAILURE_PROPERTY])))
                if not gone.rowcount:
                    await session.rollback()
                    continue
                mapping = _LEGACY_SCHEDULES.get((value or "").strip().lower())
                dataset = await session.get(Dataset, dataset_id)
                if mapping is None or dataset is None:
                    logger.warning("예전 진단 주기를 옮기지 않고 지움: dataset_id=%d value=%r", dataset_id, value)
                    await session.commit()
                    continue
                schedule_type, at, day, hours, label = mapping
                name = f"{dataset.name} 주기 진단"[:180]
                if await session.scalar(select(QualityJob.id).where(func.lower(QualityJob.name) == name.lower())):
                    name = f"{name} (#{dataset_id})"
                req = QualityJobWrite(
                    name=name, targets=[QualityJobTarget(type="dataset", dataset_id=dataset_id)],
                    description=(f"데이터셋 품질 탭의 진단 주기({label})에서 옮긴 작업입니다 — 예전 주기는 간이 진단을 돌렸고, "
                                 "이 작업은 정밀 진단을 돌립니다."),
                    schedule_type=schedule_type, schedule_time=at, schedule_day=day, schedule_interval_hours=hours,
                    # 예전 주기 진단은 회차마다 프로파일을 새로 떴다 — 그대로 이어 간다
                    include_profile=True)
                await create_job(session, req, dataset.created_by or "system")
                await session.commit()
                created += 1
                logger.info("예전 진단 주기를 품질 진단 작업으로 옮김: dataset_id=%d schedule=%s", dataset_id, value)
        except Exception as e:  # noqa: BLE001 — 한 데이터셋의 오류가 기동을 막지 않는다(속성이 남아 다음 기동에 다시 본다)
            logger.warning("예전 진단 주기를 옮기지 못함: dataset_id=%d %r", dataset_id, e)
    return created


async def job_scheduler_loop() -> None:
    """lifespan 에서 기동되는 루프."""
    from app.core.database import async_session

    try:
        await migrate_dataset_schedules(async_session)
    except Exception as e:  # noqa: BLE001 — 옮기지 못해도 스케줄러는 돈다
        logger.warning("예전 진단 주기 이전 실패: %r", e)
    logger.info("품질 진단 작업 스케줄러 시작 (tick=%ds)", TICK_SECONDS)
    while True:
        try:
            await tick(async_session)
        except asyncio.CancelledError:
            logger.info("품질 진단 작업 스케줄러 중지됨")
            raise
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("품질 진단 작업 스케줄러 tick 실패: %r", e)
        await asyncio.sleep(TICK_SECONDS)
