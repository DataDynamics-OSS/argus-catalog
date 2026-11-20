# SPDX-License-Identifier: Apache-2.0
"""품질 진단 작업 실행의 리포트 — 요약(영역·지표별, 데이터셋별), 회차별 추이, 엑셀.

숫자는 모두 규칙 결과(``catalog_quality_result``)의 진단 건수·오류 건수에서 나온다. 오류율은
``Σ 오류 건수 / Σ 진단 건수`` 이고(설계 문서 §7), 건수를 세지 않는 결과(쿼리 실패 등)는 분자·분모에서 빠진다.
작업 실행과 규칙 결과는 ``결과.run_id → 실행.job_run_id`` 로 이어진다 — 그 작업 실행에서 실제로 평가한 결과만 센다.

지표는 결과에 따로 저장하지 않고 규칙의 유형·도메인·컬럼 이름에서 구한다(``indicators.indicator_of``).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.quality import indicators as quality_indicators
from app.quality import jobs, runs
from app.quality.models import (
    QualityDefinition,
    QualityJob,
    QualityJobRun,
    QualityResult,
    QualityRule,
    QualityRun,
)
from app.quality.schemas import (
    QualityJobRunReport,
    QualityJobTrend,
    QualityJobTrendPoint,
    QualityReportDataset,
    QualityReportGroup,
    QualityReportPrevious,
    QualityReportRule,
)

# 추이에 넣지 않는 상태 — 건너뛴 차례와 아직 도는 실행
_NO_TREND = ("SKIPPED", "RUNNING")


def _rate(checked: int | None, errors: int | None) -> float | None:
    if not checked:
        return None
    return round((errors or 0) / checked * 100, 4)


@dataclass
class _RuleView:
    """리포트에 적는 규칙의 모습 — 진단 당시 값."""
    id: int
    rule_name: str | None
    check_type: str | None
    column_name: str | None
    domain: str | None
    severity: str | None


class _Sum:
    """진단·오류 건수와 규칙 수의 합."""

    __slots__ = ("checked", "errors", "rules", "failed", "counted")

    def __init__(self) -> None:
        self.checked = 0
        self.errors = 0
        self.rules = 0
        self.failed = 0
        self.counted = False

    def add(self, passed: str | None, checked: int | None, errors: int | None) -> None:
        self.rules += 1
        if passed != "true":
            self.failed += 1
        if checked is not None:
            self.counted = True
            self.checked += checked
            self.errors += errors or 0

    def merge(self, other: "_Sum") -> None:
        """다른 합계를 더한다 — 지표 합계로 영역 합계를 만들 때."""
        self.rules += other.rules
        self.failed += other.failed
        self.checked += other.checked
        self.errors += other.errors
        self.counted = self.counted or other.counted

    def group(self, key: str, name: str, area: str | None = None, area_name: str | None = None) -> QualityReportGroup:
        return QualityReportGroup(
            key=key, name=name, area=area, area_name=area_name, rules=self.rules, failed_rules=self.failed,
            checked_count=self.checked if self.counted else None,
            error_count=self.errors if self.counted else None,
            error_rate=_rate(self.checked, self.errors) if self.counted else None,
        )


async def _snapshots(session: AsyncSession, job_run_ids: list[int]) -> dict[tuple[int, int], dict]:
    """(실행 ID, 규칙 ID) → 그 실행이 진단할 때의 규칙 모습(실행 계획에 남긴 유형·컬럼·도메인·이름·지표 이름).

    리포트는 이 값으로 집계한다 — 지표의 기준 방식을 나중에 바꿔도 지난 회차의 분류가 옮겨 가지 않는다.
    계획에 없는 값(예전 실행)은 호출한 쪽이 규칙의 지금 값으로 채운다.
    """
    if not job_run_ids:
        return {}
    rows = await session.execute(
        select(QualityRun.id, QualityRun.plan_json).where(QualityRun.job_run_id.in_(job_run_ids)))
    out: dict[tuple[int, int], dict] = {}
    for run_id, plan_json in rows.all():
        try:
            items = json.loads(plan_json or "{}").get("rules") or []
        except (ValueError, AttributeError):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                out[(run_id, item["id"])] = item
    return out


async def job_run_report(session: AsyncSession, job_run_id: int) -> QualityJobRunReport:
    """작업 실행 한 번의 요약. 없으면 ``JobNotFoundError``."""
    summary = await jobs.get_job_run(session, job_run_id)
    # 함께 넣은 프로파일 실행은 리포트에 넣지 않는다 — 규칙 결과가 없다
    children = await runs.list_runs(session, job_run_id=job_run_id, limit=10_000, kind=runs.CHECK_KIND)
    rows = (await session.execute(
        select(QualityResult, QualityRule, QualityDefinition.name)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .join(QualityRun, QualityRun.id == QualityResult.run_id)
        .outerjoin(QualityDefinition, QualityDefinition.id == QualityRule.definition_id)
        .where(QualityRun.job_run_id == job_run_id)
        .order_by(QualityResult.dataset_id, QualityRule.created_at, QualityRule.id)
    )).all()

    by_indicator: dict[str, _Sum] = {}
    by_dataset: dict[int, _Sum] = {}
    names = {r.dataset_id: r.dataset_name for r in children}
    snapshots = await _snapshots(session, [job_run_id])
    rules: list[QualityReportRule] = []
    for result, current, current_definition in rows:
        snap = snapshots.get((result.run_id, current.id), {})
        # 진단 당시의 규칙 — 계획에 남아 있으면 그 값, 없으면(예전 실행) 지금 값
        rule = _RuleView(
            id=current.id, severity=current.severity,
            rule_name=snap.get("rule_name") or current.rule_name,
            check_type=snap.get("check_type") or current.check_type,
            column_name=snap["column_name"] if "column_name" in snap else current.column_name,
            domain=snap["domain"] if "domain" in snap else current.domain,
        )
        definition_name = snap["definition_name"] if "definition_name" in snap else current_definition
        indicator = quality_indicators.indicator_of(rule.check_type, rule.domain, rule.column_name)
        key = indicator.key if indicator else "OTHER"
        by_indicator.setdefault(key, _Sum()).add(result.passed, result.checked_count, result.error_count)
        by_dataset.setdefault(result.dataset_id, _Sum()).add(result.passed, result.checked_count, result.error_count)
        rules.append(QualityReportRule(
            dataset_id=result.dataset_id, dataset_name=names.get(result.dataset_id), run_id=result.run_id,
            rule_id=rule.id, rule_name=rule.rule_name, check_type=rule.check_type, column_name=rule.column_name,
            indicator=indicator.key if indicator else None, indicator_name=indicator.name if indicator else None,
            definition_name=definition_name, severity=rule.severity, passed=result.passed == "true",
            checked_count=result.checked_count, error_count=result.error_count,
            error_rate=_rate(result.checked_count, result.error_count) if result.checked_count is not None else None,
            detail=result.detail,
        ))

    # 지표는 정해진 순서로, 영역은 그 지표들의 합. AI-Ready 지표(가이드라인 §2.3)도 같은 지표 합계를 다시 묶는다
    indicator_groups: list[QualityReportGroup] = []
    ai_ready_groups: list[QualityReportGroup] = []
    by_area: dict[str, _Sum] = {}
    by_ai_ready: dict[str, _Sum] = {}
    for i in quality_indicators.INDICATORS:
        s = by_indicator.get(i.key)
        if s is None:
            continue
        indicator_groups.append(s.group(i.key, i.name, i.area, quality_indicators.AREAS[i.area]))
        by_area.setdefault(i.area, _Sum()).merge(s)
        if i.ai_ready:
            ai_key, detail = i.ai_ready
            group = s.group(i.key, i.name, ai_key, quality_indicators.AI_READY_AREAS[ai_key])
            group.ai_ready_detail = detail
            ai_ready_groups.append(group)
            by_ai_ready.setdefault(ai_key, _Sum()).merge(s)
    area_groups = [by_area[a].group(a, name) for a, name in quality_indicators.AREAS.items() if a in by_area]
    ai_ready_areas = [by_ai_ready[a].group(a, name) for a, name in quality_indicators.AI_READY_AREAS.items() if a in by_ai_ready]
    # AI-Ready 지표 순서(완전성 → … → 유일성)로, 같은 지표 안에서는 진단 기준 순서대로
    ai_order = list(quality_indicators.AI_READY_AREAS)
    ai_ready_groups.sort(key=lambda g: ai_order.index(g.area))

    datasets = []
    for run in children:
        s = by_dataset.get(run.dataset_id, _Sum())
        datasets.append(QualityReportDataset(
            dataset_id=run.dataset_id, dataset_name=run.dataset_name, datasource_name=run.datasource_name,
            engine_table=run.engine_table, run_id=run.id, status=run.status, rules=s.rules, failed_rules=s.failed,
            checked_count=run.checked_count, error_count=run.error_count, error_rate=run.error_rate,
            error_message=run.error_message if run.status != "SUCCEEDED" else None,
        ))
    # 오류율이 높은 것부터, 건수가 없는 것(실패 등)은 그 뒤에 상태 순
    datasets.sort(key=lambda d: (d.error_rate is None, -(d.error_rate or 0), d.dataset_name or ""))

    return QualityJobRunReport(
        job_run=summary, areas=area_groups, indicators=indicator_groups,
        ai_ready_areas=ai_ready_areas, ai_ready_indicators=ai_ready_groups, datasets=datasets, rules=rules,
        rules_total=len(rules), failed_rules=sum(1 for r in rules if not r.passed),
        # 규칙을 지우면 그 결과도 지워진다 — 실행이 평가했다고 적은 수보다 남은 결과가 적으면 그만큼 표에 없다
        rules_missing=max(0, sum(r.rules_evaluated or 0 for r in children) - len(rules)),
        previous=await _previous(session, summary.job_id, job_run_id),
    )


async def _previous(session: AsyncSession, job_id: int, job_run_id: int) -> QualityReportPrevious | None:
    """같은 작업의 직전 회차 요약 — 건너뛴 차례와 실행 중인 것은 건너뛴다. 없으면 None."""
    prev_id = (await session.execute(
        select(func.max(QualityJobRun.id)).where(
            QualityJobRun.job_id == job_id, QualityJobRun.id < job_run_id, QualityJobRun.status.not_in(_NO_TREND))
    )).scalar()
    if prev_id is None:
        return None
    prev = await jobs.get_job_run(session, prev_id)
    passed = (await session.execute(
        select(QualityResult.passed).join(QualityRun, QualityRun.id == QualityResult.run_id)
        .where(QualityRun.job_run_id == prev_id)
    )).scalars().all()
    return QualityReportPrevious(
        job_run_id=prev.id, started_at=prev.started_at, status=prev.status, score=prev.score,
        checked_count=prev.checked_count, error_count=prev.error_count, error_rate=prev.error_rate,
        rules_total=len(passed), failed_rules=sum(1 for v in passed if v != "true"),
    )


async def job_trend(session: AsyncSession, job_id: int, limit: int = 30) -> QualityJobTrend:
    """품질 진단 작업의 회차별 오류율 — 오래된 것부터 최근 ``limit`` 회. 없으면 ``JobNotFoundError``."""
    job = await session.get(QualityJob, job_id)
    if job is None:
        raise jobs.JobNotFoundError("품질 진단 작업을 찾을 수 없습니다.")
    job_runs = list((await session.execute(
        select(QualityJobRun).where(QualityJobRun.job_id == job_id, QualityJobRun.status.not_in(_NO_TREND))
        .order_by(QualityJobRun.id.desc()).limit(limit)
    )).scalars().all())
    job_runs.reverse()
    per_run: dict[int, dict[str, list[int]]] = {}
    if job_runs:
        ids = [r.id for r in job_runs]
        snapshots = await _snapshots(session, ids)
        rows = await session.execute(
            select(QualityRun.job_run_id, QualityResult.run_id, QualityRule.id, QualityRule.check_type,
                   QualityRule.domain, QualityRule.column_name, QualityResult.checked_count, QualityResult.error_count)
            .join(QualityRun, QualityRun.id == QualityResult.run_id)
            .join(QualityRule, QualityRule.id == QualityResult.rule_id)
            .where(QualityRun.job_run_id.in_(ids), QualityResult.checked_count.is_not(None)))
        for job_run_id, run_id, rule_id, check_type, domain, column_name, checked, errors in rows.all():
            snap = snapshots.get((run_id, rule_id), {})
            indicator = quality_indicators.indicator_of(
                snap.get("check_type") or check_type,
                snap["domain"] if "domain" in snap else domain,
                snap["column_name"] if "column_name" in snap else column_name)
            if indicator is None:
                continue
            pair = per_run.setdefault(job_run_id, {}).setdefault(indicator.key, [0, 0])
            pair[0] += int(checked or 0)
            pair[1] += int(errors or 0)
    # 중지된 데이터셋 실행은 실패와 따로 센다(DEF-156) — 저장된 runs_failed 에는 중지도 들어 있다
    cancelled: dict[int, int] = dict((await session.execute(
        select(QualityRun.job_run_id, func.count(QualityRun.id))
        .where(QualityRun.job_run_id.in_([r.id for r in job_runs]), QualityRun.status == "CANCELLED",
               QualityRun.kind != runs.PROFILE_KIND)
        .group_by(QualityRun.job_run_id))).all()) if job_runs else {}
    points = []
    for r in job_runs:
        n_cancelled = cancelled.get(r.id, 0)
        points.append(QualityJobTrendPoint(
            job_run_id=r.id, started_at=r.started_at, status=r.status,
            runs_total=r.runs_total, runs_succeeded=r.runs_succeeded,
            runs_failed=max(0, r.runs_failed - n_cancelled), runs_cancelled=n_cancelled,
            checked_count=r.checked_count, error_count=r.error_count,
            error_rate=_rate(r.checked_count, r.error_count) if r.checked_count is not None else None,
            indicators={k: _rate(c, e) for k, (c, e) in per_run.get(r.id, {}).items()},
        ))
    return QualityJobTrend(job_id=job.id, job_name=job.name, points=points)


# ---------------------------------------------------------------------------
# 엑셀
# ---------------------------------------------------------------------------

_STATUS = {"RUNNING": "실행 중", "QUEUED": "대기 중", "SUCCEEDED": "완료", "PARTIAL": "일부 실패", "FAILED": "실패",
           "CANCELLED": "중지됨", "SKIPPED": "건너뜀"}


# 화면과 같은 이름(DEF-155) — 심각도는 frontend lib/severity.ts 의 SEVERITY_LABEL,
# 규칙 유형은 features/quality/check-types.ts 의 CHECK_TYPE_LABEL 과 같게 둔다
SEVERITY_LABEL = {"BREAKING": "치명", "WARNING": "경고", "INFO": "정보"}
CHECK_TYPE_LABEL = {
    "NOT_NULL": "NULL 없음", "UNIQUE": "고유값", "BROKEN_TEXT": "글자 깨짐", "WHITESPACE": "공백·특수문자",
    "DATE_FORMAT": "날짜 형식", "REGEX": "정규식 패턴", "FUNCTION": "Trino 함수 (UDF)", "ACCEPTED_VALUES": "허용 값 목록",
    "MIN_VALUE": "하한 (이상)", "MAX_VALUE": "상한 (이하)", "COLUMN_COMPARE": "컬럼 간 비교", "LOGICAL": "논리관계",
    "FORMULA": "계산식", "REFERENTIAL": "참조관계", "DUMMY_VALUE": "더미 값", "CONTINUITY": "데이터 연속성",
    "DIVERSITY": "값 분포 다양성", "COMPOSITE_UNIQUE": "여러 컬럼 키 고유값",
    "ROW_COUNT": "행 수 하한", "FRESHNESS": "최신성", "CUSTOM_SQL": "커스텀 SQL", "CUSTOM_PYTHON": "커스텀 Python",
}
# 시간대를 받지 못했거나 알 수 없는 이름일 때 — 서버 OS 시간대(컨테이너는 보통 UTC)가 아니라 화면 기본(KST)
DEFAULT_DISPLAY_TIMEZONE = "Asia/Seoul"


def severity_label(severity: str | None) -> str:
    return SEVERITY_LABEL.get(severity or "", severity or "")


def check_type_label(check_type: str | None) -> str:
    return CHECK_TYPE_LABEL.get(check_type or "", check_type or "")


def display_zone(tz: str | None = None) -> tzinfo:
    """리포트 시각을 적을 시간대 — 브라우저가 보낸 IANA 이름(``tz``), 없거나 모르면 ``DEFAULT_DISPLAY_TIMEZONE``(DEF-155)."""
    try:
        return ZoneInfo(tz) if tz else ZoneInfo(DEFAULT_DISPLAY_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_DISPLAY_TIMEZONE)


def _local(dt: datetime | None, zone: tzinfo | None = None) -> str:
    """리포트에 적을 시각 — 표시 시간대(``display_zone``)로 바꾼 문자열."""
    dt = runs._as_utc(dt)
    return dt.astimezone(zone or display_zone()).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _zone(zone: tzinfo | None = None) -> str:
    """표시 시간대의 이름(예: KST) — 머리글에 밝힌다."""
    return datetime.now(zone or display_zone()).tzname() or "현지 시간"


def _pct(rate: float | None) -> float | str:
    return rate if rate is not None else ""


def report_xlsx(report: QualityJobRunReport, tz: str | None = None) -> bytes:
    """리포트를 엑셀 파일로 — 시트: 요약 / 진단 기준별 / AI-Ready 지표별 / 데이터셋별 / 규칙별.

    ``tz`` 는 시각을 적을 IANA 시간대(화면의 브라우저 시간대). 용어 · 라벨은 화면과 같게 쓴다(DEF-155).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E5E7EB")

    def put(ws, row: int, column: int, value):
        """셀에 값을 적는다. ``=`` · ``+`` · ``-`` · ``@`` 로 시작하는 글자는 수식으로 해석되지 않게 문자열로 고정한다."""
        cell = ws.cell(row=row, column=column, value=value)
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
            cell.data_type = "s"
        return cell

    def table(ws, headers: list[str], rows: list[list], widths: list[int], start: int = 1) -> None:
        for col, title in enumerate(headers, 1):
            cell = ws.cell(row=start, column=col, value=title)
            cell.font = bold
            cell.fill = head_fill
        for r, row in enumerate(rows, start + 1):
            for col, value in enumerate(row, 1):
                put(ws, r, col, value)
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = ws.cell(row=start + 1, column=1)

    jr = report.job_run
    zone = display_zone(tz)
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    names = {i.key: i.name for i in quality_indicators.INDICATORS}
    facts = [
        ("품질 진단 작업", jr.job_name or f"작업 #{jr.job_id}"),
        ("작업 실행", f"#{jr.id}"),
        ("상태", _STATUS.get(jr.status, jr.status)),
        ("시작 방식", "예약" if jr.trigger_type == "SCHEDULE" else f"수동 ({jr.requested_by or '-'})"),
        (f"시작 ({_zone(zone)})", _local(jr.started_at, zone)),
        (f"종료 ({_zone(zone)})", _local(jr.finished_at, zone)),
        ("품질 지표", ", ".join(jr.definitions) or "전체"),
        ("진단 기준", ", ".join(names.get(k, k) for k in jr.indicators) or "전체"),
        ("대상 데이터셋", jr.targets_total),
        ("진단한 데이터셋", jr.runs_total),
        ("  성공", jr.runs_succeeded),
        ("  실패·일부 실패", jr.runs_failed),
        ("  중지", jr.runs_cancelled),
        ("시작하지 못한 데이터셋", jr.skipped_total),
        ("규칙이 없어 진단하지 않은 데이터셋", jr.no_rules),
        ("평가한 규칙", report.rules_total),
        ("  실패한 규칙", report.failed_rules),
        ("  지워진 규칙(결과 없음)", report.rules_missing),
        ("진단 건수", jr.checked_count if jr.checked_count is not None else ""),
        ("오류 건수", jr.error_count if jr.error_count is not None else ""),
        ("오류율(%)", _pct(jr.error_rate)),
        ("비고", jr.message or ""),
    ]
    for r, (label, value) in enumerate(facts, 1):
        ws.cell(row=r, column=1, value=label).font = bold
        put(ws, r, 2, value)
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 48
    start = len(facts) + 2
    ws.cell(row=start, column=1, value="영역별").font = bold
    table(ws, ["영역", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율(%)"],
          [[g.name, g.rules, g.failed_rules, g.checked_count, g.error_count, _pct(g.error_rate)] for g in report.areas],
          [36, 48, 14, 14, 14, 12], start=start + 1)
    ws.freeze_panes = None
    if jr.skipped:
        start += len(report.areas) + 4
        ws.cell(row=start, column=1, value="시작하지 못한 데이터셋 목록").font = bold
        for r, item in enumerate(jr.skipped, start + 1):
            put(ws, r, 1, item.name or f"데이터셋 #{item.dataset_id}")
            put(ws, r, 2, item.reason)

    table(wb.create_sheet("진단 기준별"),
          ["영역", "진단 기준", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율(%)"],
          [[g.area_name, g.name, g.rules, g.failed_rules, g.checked_count, g.error_count, _pct(g.error_rate)]
           for g in report.indicators],
          [12, 22, 10, 14, 14, 14, 12])
    ai_sheet = wb.create_sheet("AI-Ready 지표별")
    table(ai_sheet,
          ["AI-Ready 지표", "세부 지표", "진단 기준", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율(%)"],
          [[a.name, "(합계)", "", a.rules, a.failed_rules, a.checked_count, a.error_count, _pct(a.error_rate)]
           for a in report.ai_ready_areas]
          + [[g.area_name, g.ai_ready_detail, g.name, g.rules, g.failed_rules, g.checked_count, g.error_count,
              _pct(g.error_rate)] for g in report.ai_ready_indicators],
          [14, 18, 22, 10, 14, 14, 14, 12])
    table(wb.create_sheet("데이터셋별"),
          ["데이터셋", "원본", "진단 테이블", "상태", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율(%)", "오류 내용"],
          [[d.dataset_name or f"데이터셋 #{d.dataset_id}", d.datasource_name, d.engine_table,
            _STATUS.get(d.status, d.status), d.rules, d.failed_rules, d.checked_count, d.error_count,
            _pct(d.error_rate), d.error_message]
           for d in report.datasets],
          [30, 20, 40, 12, 8, 14, 14, 14, 12, 60])
    table(wb.create_sheet("규칙별"),
          ["데이터셋", "규칙", "유형", "컬럼", "영역·진단 기준", "품질 지표", "심각도", "결과", "진단 건수", "오류 건수", "오류율(%)", "상세"],
          [[r.dataset_name or f"데이터셋 #{r.dataset_id}", r.rule_name, check_type_label(r.check_type), r.column_name,
            r.indicator_name, r.definition_name, severity_label(r.severity), "통과" if r.passed else "실패", r.checked_count, r.error_count,
            _pct(r.error_rate), r.detail]
           for r in report.rules],
          [30, 30, 18, 22, 18, 24, 10, 8, 14, 14, 12, 60])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

