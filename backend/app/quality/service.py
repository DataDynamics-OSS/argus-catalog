# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 서비스 레이어 — 규칙 CRUD · 추천, 결과 · 점수 저장과 조회.

진단과 프로파일은 품질 워커가 한다(``execution`` · ``runs`` — Trino 연결이 있으면 Trino, 없으면 원천 직접).
이 모듈은 그 결과를 저장하고 점수를 낸다(``import_results`` — 이번에 평가하지 않은 활성 규칙은 직전 결과를 이어받는다).
예전의 간이 진단(프로파일 통계로 판정) · 카탈로그 서버의 원천 직접 프로파일 · 품질 배치 반입은 없앴다(TODO-095).
"""

import json as _json
import logging

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset
from app.core.text_list import encode_list
from app.quality import rule_sql
from app.quality.indicators import indicator_of, validate_domain
from app.quality.models import (
    DataProfile,
    QualityDefinition,
    QualityResult,
    QualityRule,
    QualityScore,
)
from app.quality.schemas import (
    ColumnProfile,
    ProfileResponse,
    QualityResultResponse,
    QualityRuleCreate,
    QualityRuleResponse,
    QualityRuleUpdate,
    QualityScoreResponse,
    ResultsImportRequest,
    RunCheckResponse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 프로파일 — 품질 워커의 프로파일 실행(execution._store_profile)이 저장한 최신 것
# ---------------------------------------------------------------------------

async def get_latest_profile(session: AsyncSession, dataset_id: int) -> ProfileResponse | None:
    """데이터셋의 가장 최근 프로파일을 조회한다."""
    profile = (await session.execute(
        select(DataProfile).where(DataProfile.dataset_id == dataset_id)
        .order_by(DataProfile.profiled_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not profile:
        logger.debug("저장된 프로파일 없음: dataset_id=%d", dataset_id)
        return None

    columns = [ColumnProfile(**c) for c in _json.loads(profile.profile_json)]
    return ProfileResponse(
        id=profile.id, dataset_id=dataset_id, row_count=profile.row_count,
        columns=columns, profiled_at=profile.profiled_at,
    )


# ---------------------------------------------------------------------------
# 품질 규칙 CRUD
# ---------------------------------------------------------------------------

async def dataset_exists(session: AsyncSession, dataset_id: int) -> bool:
    """데이터셋 존재 여부 — 규칙 생성·주기 설정·검사 실행 전 404 판정용."""
    return (await session.execute(
        select(Dataset.id).where(Dataset.id == dataset_id)
    )).scalar_one_or_none() is not None


async def create_rule(session: AsyncSession, data: QualityRuleCreate) -> QualityRuleResponse:
    """품질 규칙을 생성한다(flush 만 하고 commit 은 호출자 몫)."""
    rule = QualityRule(**data.model_dump())
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    logger.info("품질 규칙 생성됨: id=%d, name=%s, type=%s", rule.id, rule.rule_name, rule.check_type)
    return QualityRuleResponse.model_validate(rule)


# 품질 지표에 연결된 규칙에서 지표가 정하는 필드 — 규칙에서 바꿀 수 없다(지표에서 바꾸거나 연결을 끊는다)
_DEFINITION_FIELDS = ("check_type", "expected_value", "severity", "domain")


async def _rule_responses(session: AsyncSession, rules: list[QualityRule]) -> list[QualityRuleResponse]:
    """규칙 응답 — 할당된 품질 지표의 이름을 채운다."""
    ids = {r.definition_id for r in rules if r.definition_id is not None}
    names: dict[int, str] = {}
    if ids:
        names = dict((await session.execute(
            select(QualityDefinition.id, QualityDefinition.name).where(QualityDefinition.id.in_(ids))
        )).all())
    out = []
    for rule in rules:
        item = QualityRuleResponse.model_validate(rule)
        item.definition_name = names.get(rule.definition_id) if rule.definition_id is not None else None
        out.append(item)
    return out


async def list_rules(session: AsyncSession, dataset_id: int) -> list[QualityRuleResponse]:
    """데이터셋의 규칙 전체(활성·비활성)를 생성 순으로 반환한다."""
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset_id)
        .order_by(QualityRule.created_at)
    )).scalars().all()
    return await _rule_responses(session, list(rules))


async def get_rule(session: AsyncSession, rule_id: int) -> QualityRule | None:
    """id 로 규칙을 조회한다(없으면 None)."""
    return (await session.execute(
        select(QualityRule).where(QualityRule.id == rule_id)
    )).scalar_one_or_none()


async def update_rule(session: AsyncSession, rule_id: int, data: QualityRuleUpdate) -> QualityRuleResponse | None:
    """명시된 필드만 부분 수정한다. 규칙이 없으면 None(commit 은 호출자 몫)."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return None
    changes = data.model_dump(exclude_unset=True)
    if rule.definition_id is not None and "definition_id" not in changes:
        # 지표에 연결된 규칙 — 유형·기준값·심각도·도메인은 지표를 따른다. 같은 값을 다시 보내는 것은 받아 준다
        locked = [
            k for k in _DEFINITION_FIELDS
            if k in changes and (changes[k] or None) != (getattr(rule, k) or None)
        ]
        if locked:
            raise ValueError(
                "품질 지표에 연결된 규칙입니다 — 유형·기준값·심각도는 지표에서 바꾸거나, 연결을 끊고 바꾸세요.")
        for k in _DEFINITION_FIELDS:
            changes.pop(k, None)
    for k, v in changes.items():
        setattr(rule, k, v)
    if "domain" in changes or "check_type" in changes:
        # 도메인은 유형에 딸린 값 — 유형이 바뀌어 맞지 않게 된 도메인은 자동으로 되돌리고, 직접 적은 값은 검증한다
        try:
            rule.domain = validate_domain(rule.check_type, rule.domain)
        except ValueError:
            if "domain" in changes and changes["domain"]:
                raise
            rule.domain = None
    if "expected_value" in changes or "check_type" in changes:
        rule_sql.check_expression(rule.check_type, rule.expected_value)
    if rule.check_type == "COMPOSITE_UNIQUE":
        rule.column_name = None  # 테이블 수준 — 키 컬럼은 기대값의 목록
    await session.flush()
    await session.refresh(rule)
    logger.info("품질 규칙 수정됨: id=%d, fields=%s", rule.id, sorted(changes))
    return (await _rule_responses(session, [rule]))[0]


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    """규칙을 삭제한다(결과 행은 FK CASCADE). 없으면 False(commit 은 호출자 몫).

    삭제 뒤 남은 규칙의 최신 결과로 점수·데이터셋 품질 상태를 다시 계산한다(DEF-056 — 예전엔 지운 규칙의
    실패가 다음 검증 전까지 점수 카드·하위 데이터셋 업스트림 경고에 남았다).
    """
    rule = await get_rule(session, rule_id)
    if not rule:
        return False
    dataset_id = rule.dataset_id
    logger.info("품질 규칙 삭제됨: id=%d, name=%s", rule.id, rule.rule_name)
    await session.delete(rule)
    await session.flush()
    await _recompute_latest_score(session, dataset_id)
    return True


async def _recompute_latest_score(session: AsyncSession, dataset_id: int) -> None:
    """남은 활성 규칙의 최신 결과로 가장 최근 점수 행과 데이터셋 품질 상태를 다시 계산한다(DEF-056).

    - 새 검증이 아니므로 점수 이력 행을 추가하지 않고 최신 행을 고쳐 쓴다(scored_at 유지 — '마지막 진단'
      시각이 바뀌지 않는다). 알림도 다시 평가하지 않는다.
    - 점수 기록이 없는 데이터셋(아직 검증 전)은 그대로 둔다.
    - 계산 방식은 ``_record_score`` 와 같다: 결과가 없는 규칙은 미평가로 분모에서 빼고, 평가된 결과가 하나도
      없으면(규칙을 모두 지움) 점수를 내지 않고 진단 전 상태로 되돌린다(DEF-195, ``_clear_quality_outcome``).
    """
    latest = (await session.execute(
        select(QualityScore).where(QualityScore.dataset_id == dataset_id)
        .order_by(QualityScore.scored_at.desc(), QualityScore.id.desc()).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        logger.debug("점수 재계산 생략 — 점수 기록 없음: dataset_id=%d", dataset_id)
        return
    rules = (await session.execute(
        select(QualityRule).where(
            QualityRule.dataset_id == dataset_id, QualityRule.is_active == "true",
        )
    )).scalars().all()
    evaluated: list[QualityResultResponse] = []
    for r in rules:
        prev = await _latest_result(session, r.id)
        if prev is not None:
            evaluated.append(_result_response(r, prev, carried_forward=True))
    passed_count = sum(1 for r in evaluated if r.passed == "true")
    total = len(evaluated)
    if total == 0:
        await _clear_quality_outcome(session, dataset_id)
        logger.info("규칙 삭제 후 점수 재계산: dataset_id=%d — 평가된 규칙 없음, 미평가로 되돌림", dataset_id)
        return
    score = round(passed_count / total * 100, 1)
    latest.score = score
    latest.total_rules = total
    latest.passed_rules = passed_count
    latest.failed_rules = total - passed_count
    await session.flush()
    failed_summary = [
        {"rule_name": r.rule_name, "check_type": r.check_type, "severity": r.severity,
         "actual": r.actual_value, "detail": r.detail}
        for r in evaluated if r.passed != "true"
    ]
    await _finalize_quality_outcome(session, dataset_id, score, failed_summary, evaluate_alerts=False)
    logger.info("규칙 삭제 후 점수 재계산: dataset_id=%d score_id=%d score=%.1f%% passed=%d/%d",
                dataset_id, latest.id, score, passed_count, total)


# ---------------------------------------------------------------------------
# 규칙 추천 · 품질 검사 실행 — 데이터셋의 모든 활성 규칙을 평가
# ---------------------------------------------------------------------------

async def recommend_rules(session: AsyncSession, dataset_id: int, *, reveal_values: bool = True) -> list[dict]:
    """최신 프로파일을 분석해 품질 규칙 후보를 추천한다 (생성하지 않음).

    ``reveal_values=False`` 면(샘플 보기 권한 없음, DEF-164) 실제 값이 들어가는 후보(허용 값 목록)를 빼고
    최소값 근거 문구에도 값을 적지 않는다.

    추천 근거 (프로파일 통계 기반):
    - NULL 0%               → NOT_NULL (지금 깨끗한 컬럼을 계속 깨끗하게)
    - 고유 100% (행 ≥ 10)   → UNIQUE (키 후보)
    - 최빈값 수집됨(코드성) → ACCEPTED_VALUES (현재 값 목록을 허용 목록으로)
    - 숫자형 최소값 ≥ 0     → MIN_VALUE 0 (음수 금지)
    - 행 수 > 0             → ROW_COUNT (현재의 90% 하한 — 급감 감지)

    이미 같은 (check_type, column) 활성/비활성 규칙이 있으면 제외한다.
    """
    profile = await get_latest_profile(session, dataset_id)
    if not profile:
        # 프로파일이 없으면 추천 근거가 없음 — UI 는 "프로파일 먼저" 안내를 표시한다
        logger.info("규칙 추천 건너뜀 (프로파일 없음): dataset_id=%d", dataset_id)
        return []

    existing = (await session.execute(
        select(QualityRule.check_type, QualityRule.column_name).where(
            QualityRule.dataset_id == dataset_id)
    )).all()
    existing_keys = {(ct, col) for ct, col in existing}

    recs: list[dict] = []

    def add(check_type, column, rule_name, expected=None, threshold=100.0,
            severity="WARNING", reason=""):
        """같은 (check_type, column) 규칙이 없을 때만 추천 후보에 추가한다."""
        if (check_type, column) in existing_keys:
            return
        recs.append({
            "rule_name": rule_name, "check_type": check_type, "column_name": column,
            "expected_value": expected, "threshold": threshold, "severity": severity,
            "reason": reason,
        })

    row_count = profile.row_count or 0
    if row_count > 0 and ("ROW_COUNT", None) not in existing_keys:
        floor = max(1, int(row_count * 0.9))
        add("ROW_COUNT", None, f"행 수 {floor:,} 이상", expected=str(floor),
            severity="WARNING", reason=f"현재 {row_count:,}행 — 급감(10%+) 감지용 하한")

    for cp in profile.columns:
        col = cp.column_name
        if _is_binary_type(cp.column_type):
            continue
        if cp.null_percent == 0 and row_count > 0:
            add("NOT_NULL", col, f"{col} 필수",
                reason="현재 NULL 0% — 깨끗한 상태 유지")
        if cp.unique_percent == 100 and row_count >= 10:
            add("UNIQUE", col, f"{col} 유일",
                reason="현재 고유 100% — 키 후보")
        # 허용 값: 진짜 코드성 컬럼만 — 값 2~10종, 날짜/시간 타입 제외
        # (값 1종은 정보가 없고, 타임스탬프는 코드가 아니라 노이즈)
        is_temporal = any(k in (cp.column_type or "").upper()
                          for k in ("DATE", "TIME", "YEAR"))
        if (reveal_values and cp.top_values and 2 <= len(cp.top_values) <= 10
                and cp.unique_count == len(cp.top_values) and not is_temporal):
            values = encode_list(str(tv["value"]) for tv in cp.top_values)  # 값 안의 쉼표 보존(TODO-143)
            add("ACCEPTED_VALUES", col, f"{col} 허용 값", expected=values,
                reason=f"코드성 컬럼 — 현재 값 {cp.unique_count}종을 허용 목록으로")
        if cp.mean_value is not None and cp.min_value is not None:
            try:
                if float(cp.min_value) >= 0:
                    add("MIN_VALUE", col, f"{col} 음수 금지", expected="0",
                        reason=(f"현재 최소값 {cp.min_value} — 음수 유입 감지" if reveal_values
                                else "현재 최소값 0 이상 — 음수 유입 감지"))
            except ValueError:
                # 평균은 있으나 최소값이 숫자로 해석되지 않는 컬럼 — 추천하지 않는다.
                logger.debug("MIN_VALUE 추천 건너뜀(숫자 아님): dataset_id=%d column=%s", dataset_id, col)

    # GET(조회) 경로라 DEBUG. 후보는 기존 규칙과 겹치는 것을 이미 뺀 수다.
    logger.debug("규칙 추천: dataset_id=%d, 후보=%d개 (기존 규칙=%d개)",
                 dataset_id, len(recs), len(existing_keys))
    return recs


async def _clear_quality_outcome(session: AsyncSession, dataset_id: int) -> None:
    """평가된 규칙이 하나도 없는 데이터셋을 진단 전(미평가) 상태로 되돌린다(DEF-195).

    평가한 규칙이 없으면 점수가 없다 — 예전엔 100점 · 양호(GOOD)로 남아 품질 집계 · AI-Ready 판정에 양호로 섞였다.
    - 점수 기록을 모두 지운다: 최신 행만 지우면 그 앞 진단의 점수가 '최신 점수'로 되살아나고, 0개짜리 행으로 두면
      추세가 그 값으로 튄다. 점수를 만든 규칙 · 결과는 이미 없고, 진단 이력은 실행 기록(``catalog_quality_runs``)에 남는다.
    - dataset.quality_score / quality_status 를 비운다(진단한 적 없는 데이터셋과 같은 값).
    """
    await session.execute(delete(QualityScore).where(QualityScore.dataset_id == dataset_id))
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if dataset:
        dataset.quality_score = None
        dataset.quality_status = None
    await session.flush()
    logger.debug("데이터셋 품질 상태 비움(평가된 규칙 없음): dataset_id=%d", dataset_id)


async def _finalize_quality_outcome(
    session: AsyncSession,
    dataset_id: int,
    score: float,
    failed: list[dict],
    *,
    evaluate_alerts: bool = True,
) -> None:
    """검증/반입 공통 후처리 — 점수를 데이터셋에 동기화하고 실패 알림을 평가한다.

    - dataset.quality_score / quality_status(GOOD·WARN·BAD) 자동 갱신:
        치명(BREAKING) 실패 ≥1 또는 점수 < 70 → BAD
        그 외 실패 존재 또는 점수 < 90       → WARN
        전부 통과                            → GOOD
    - 실패가 있으면 QUALITY_FAILED 알림 규칙 평가 (거버넌스 > 알림). ``evaluate_alerts=False`` 면 생략
      (규칙 삭제 후 재계산처럼 새 검증이 아닌 경우 — 같은 실패로 알림이 다시 나지 않게).
    """
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if dataset:
        dataset.quality_score = score
        has_breaking = any(f.get("severity") == "BREAKING" for f in failed)
        if has_breaking or score < 70:
            dataset.quality_status = "BAD"
        elif failed or score < 90:
            dataset.quality_status = "WARN"
        else:
            dataset.quality_status = "GOOD"
        await session.flush()
        logger.debug(
            "데이터셋 품질 상태 갱신: dataset_id=%d status=%s score=%.1f failed=%d",
            dataset_id, dataset.quality_status, score, len(failed),
        )
    else:
        logger.warning("품질 상태 갱신 대상 데이터셋 없음: dataset_id=%d", dataset_id)

    if failed and evaluate_alerts:
        try:
            from app.alert.service import evaluate_quality_alerts
            # savepoint — 알림 평가가 실패해도 검증 결과 저장 트랜잭션은 살린다
            async with session.begin_nested():
                await evaluate_quality_alerts(session, dataset_id, score, failed)
        except Exception as e:  # noqa: BLE001 — 알림 실패가 검증 자체를 막지 않도록
            logger.warning("품질 알림 평가 실패 dataset_id=%d: %s", dataset_id, e)


BINARY_TYPE_KEYWORDS = ("BLOB", "BINARY", "VARBINARY", "BYTEA", "BYTES", "IMAGE", "GEOMETRY", "OBJECT", "RAW")


def _is_binary_type(field_type: str | None) -> bool:
    """바이너리 계열 타입 여부 — min/max/평균 통계가 의미 없는 컬럼."""
    if not field_type:
        return False
    upper = field_type.upper()
    return any(k in upper for k in BINARY_TYPE_KEYWORDS)


# 예전 간이 진단이 평가하지 못하는 유형에 통과로 저장하던 자리표시 결과의 actual_value — 최신 결과로 보지 않는다
_LEGACY_SKIP_ACTUAL = "평가 제외"


def _result_response(
    rule: QualityRule, result: QualityResult, *, carried_forward: bool = False,
) -> QualityResultResponse:
    """저장된 결과 행 → 응답."""
    samples = None
    if result.failed_samples:
        try:
            samples = _json.loads(result.failed_samples)
        except ValueError:
            logger.warning("위반 샘플 JSON 손상 — 샘플 없이 응답: result_id=%s", result.id)
            samples = None
    return QualityResultResponse(
        id=result.id, rule_id=rule.id, rule_name=rule.rule_name,
        check_type=rule.check_type, column_name=rule.column_name,
        dataset_id=rule.dataset_id, passed=result.passed,
        actual_value=result.actual_value, detail=result.detail,
        severity=rule.severity, failed_samples=samples,
        checked_count=result.checked_count, error_count=result.error_count,
        checked_at=result.checked_at, carried_forward=carried_forward,
        indicator=getattr(indicator_of(rule.check_type, rule.domain, rule.column_name), "key", None),
        run_id=result.run_id,
    )


async def _latest_result(session: AsyncSession, rule_id: int) -> QualityResult | None:
    """규칙의 가장 최근 실제 결과 (예전 간이 진단의 "평가 제외" 자리표시 통과 행은 제외)."""
    return (await session.execute(
        select(QualityResult).where(
            QualityResult.rule_id == rule_id,
            or_(QualityResult.passed != "true",
                QualityResult.actual_value.is_(None),
                QualityResult.actual_value != _LEGACY_SKIP_ACTUAL),
        ).order_by(QualityResult.checked_at.desc(), QualityResult.id.desc()).limit(1)
    )).scalar_one_or_none()


async def _carry_forward(
    session: AsyncSession, rule: QualityRule, dataset_id: int, reason: str,
) -> QualityResultResponse:
    """이번 실행에서 평가하지 않은 규칙 — 직전 결과를 이어받아 점수에 반영하고(carried_forward),
    직전 결과가 없으면 미평가(passed=None)로 돌려 점수 분모에서 뺀다."""
    prev = await _latest_result(session, rule.id)
    if prev is not None:
        logger.debug("직전 결과 이어받기: rule_id=%d result_id=%d", rule.id, prev.id)
        return _result_response(rule, prev, carried_forward=True)
    logger.debug("직전 결과 없음 — 미평가 처리: rule_id=%d", rule.id)
    return _not_evaluated(rule, dataset_id, reason)


def _not_evaluated(rule: QualityRule, dataset_id: int, reason: str) -> QualityResultResponse:
    """저장된 결과가 없는 미평가 항목 (passed=None — 점수 분모에서 제외)."""
    return QualityResultResponse(
        id=None, rule_id=rule.id, rule_name=rule.rule_name,
        check_type=rule.check_type, column_name=rule.column_name,
        dataset_id=dataset_id, passed=None, actual_value=None,
        detail=f"{reason} — 이전 평가 결과가 없어 점수에서 제외했습니다",
        severity=rule.severity, checked_at=None,
    )


async def _record_score(
    session: AsyncSession, dataset_id: int, results: list[QualityResultResponse], label: str,
) -> RunCheckResponse:
    """결과 목록으로 점수를 산출·기록하고 데이터셋 상태/알림을 갱신한다 (검증·반입 공통).

    score = 통과 / (이번에 평가 + 이어받은 결과) × 100. 미평가(passed=None)는 분모에서 제외.
    평가 가능한 결과가 하나도 없으면 점수를 기록하지 않고 진단 전 상태로 되돌린다(DEF-195 — score=None).
    """
    evaluated = [r for r in results if r.passed is not None]
    passed_count = sum(1 for r in evaluated if r.passed == "true")
    total = len(evaluated)
    failed_count = total - passed_count
    if total == 0:
        await _clear_quality_outcome(session, dataset_id)
        logger.info("%s: dataset_id=%d, 평가된 규칙 없음 — 점수 없음 (미평가 %d)", label, dataset_id, len(results))
        return RunCheckResponse(
            dataset_id=dataset_id, score=None, total_rules=0, passed=0, failed=0, results=results)
    score = round(passed_count / total * 100, 1)

    session.add(QualityScore(
        dataset_id=dataset_id,
        score=score,
        total_rules=total,
        passed_rules=passed_count,
        warning_rules=0,
        failed_rules=failed_count,
    ))
    await session.flush()

    failed_summary = [
        {"rule_name": r.rule_name, "check_type": r.check_type, "severity": r.severity,
         "actual": r.actual_value, "detail": r.detail}
        for r in evaluated if r.passed != "true"
    ]
    await _finalize_quality_outcome(session, dataset_id, score, failed_summary)

    logger.info("%s: dataset_id=%d, score=%.1f%%, passed=%d/%d (미평가 %d)",
                label, dataset_id, score, passed_count, total, len(results) - total)
    return RunCheckResponse(
        dataset_id=dataset_id, score=score,
        total_rules=total, passed=passed_count, failed=failed_count,
        results=results,
    )


# ---------------------------------------------------------------------------
# 원천 데이터 시간대 (설정 quality_source_timezone) — 최신성(FRESHNESS)의 시간대 없는 시각 해석
# ---------------------------------------------------------------------------

QUALITY_CONFIG_CATEGORY = "quality"
SOURCE_TIMEZONE_KEY = "quality_source_timezone"
DEFAULT_SOURCE_TIMEZONE = rule_sql.DEFAULT_SOURCE_TIMEZONE
resolve_timezone = rule_sql.resolve_timezone


async def get_source_timezone_name(session: AsyncSession) -> str:
    """설정된 원천 데이터 시간대 이름(없거나 비어 있으면 기본 Asia/Seoul)."""
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, QUALITY_CONFIG_CATEGORY)
    return (cfg.get(SOURCE_TIMEZONE_KEY) or "").strip() or DEFAULT_SOURCE_TIMEZONE


# ---------------------------------------------------------------------------
# 점수 이력
# ---------------------------------------------------------------------------

async def get_latest_score(session: AsyncSession, dataset_id: int) -> QualityScoreResponse | None:
    """가장 최근 점수 기록(없으면 None)."""
    score = (await session.execute(
        select(QualityScore).where(QualityScore.dataset_id == dataset_id)
        .order_by(QualityScore.scored_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not score:
        return None
    return QualityScoreResponse.model_validate(score)


async def get_score_history(
    session: AsyncSession, dataset_id: int, limit: int = 30,
) -> list[QualityScoreResponse]:
    """최근 ``limit`` 건의 점수 기록을 오래된 순(차트용)으로 반환한다."""
    scores = (await session.execute(
        select(QualityScore).where(QualityScore.dataset_id == dataset_id)
        .order_by(QualityScore.scored_at.desc()).limit(limit)
    )).scalars().all()
    return [QualityScoreResponse.model_validate(s) for s in reversed(scores)]


async def get_latest_results(
    session: AsyncSession, dataset_id: int,
) -> list[QualityResultResponse]:
    """각 활성 규칙에 대한 가장 최근 결과를 조회한다. 아직 진단하지 않은 규칙은 빠진다.

    데이터셋 점수는 마지막 진단이 평가하지 않은 규칙의 결과를 직전 실행에서 이어받는다 — 그 행은 ``carried_forward``
    로 표시한다(DEF-150). 마지막 진단은 가장 최근에 저장된 결과의 실행이고, 그 실행이 남기지 않은 결과가 이어받은 것이다.
    가장 최근 결과가 실행 없이 저장된 것(예전 간이 진단 · 반입)이면 구분할 수 없어 표시하지 않는다.
    """
    rules = (await session.execute(
        select(QualityRule).where(
            QualityRule.dataset_id == dataset_id,
            QualityRule.is_active == "true",
        )
    )).scalars().all()

    latest: list[tuple[QualityRule, QualityResult]] = []
    for rule in rules:
        # 예전 간이 진단의 "평가 제외" 자리표시 통과 행은 건너뛰고 실제 마지막 결과를 보여준다
        result = await _latest_result(session, rule.id)
        if result:
            latest.append((rule, result))

    last_run_id = max(latest, key=lambda pair: pair[1].id)[1].run_id if latest else None
    return [
        _result_response(rule, result, carried_forward=last_run_id is not None and result.run_id != last_run_id)
        for rule, result in latest
    ]


# ---------------------------------------------------------------------------
# 진단 결과 저장 — 품질 워커의 정밀 진단 결과(execution.complete)
# ---------------------------------------------------------------------------

async def import_results(
    session: AsyncSession, dataset_id: int, req: "ResultsImportRequest",
    label: str = "품질 진단 결과 저장",
    run_id: int | None = None,
) -> RunCheckResponse:
    """진단 결과(품질 워커의 정밀 진단)를 저장하고 점수를 산출한다.

    ``run_id`` 는 이 결과를 만든 진단 실행(``catalog_quality_runs``) — 새로 저장하는 결과 행에 적는다.

    점수는 데이터셋의 **활성 규칙 전체** 기준:
    이번 결과 + 이번에 평가하지 않은 활성 규칙은 직전 결과를 이어받고(carried_forward), 없으면 미평가로 제외.
    규칙이 이 데이터셋에 속하지 않거나 비활성이면 ValueError (빈 목록·rule_id 중복은 스키마에서 422).
    """
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset_id)
        .order_by(QualityRule.created_at, QualityRule.id)
    )).scalars().all()
    rule_map = {r.id: r for r in rules}

    # 저장 전에 전체를 먼저 검증한다 — 일부만 저장된 채 실패하지 않도록.
    for item in req.results:
        rule = rule_map.get(item.rule_id)
        if not rule:
            raise ValueError(f"Rule {item.rule_id} does not belong to dataset {dataset_id}")
        if rule.is_active != "true":
            raise ValueError(f"Rule {item.rule_id} is inactive")

    results: list[QualityResultResponse] = []
    for item in req.results:
        rule = rule_map[item.rule_id]
        result = QualityResult(
            rule_id=rule.id,
            dataset_id=dataset_id,
            passed="true" if item.passed else "false",
            actual_value=item.actual_value,
            detail=item.detail,
            failed_samples=_json.dumps(item.failed_samples, ensure_ascii=False, default=str)
            if item.failed_samples else None,
            checked_count=item.checked_count,
            error_count=item.error_count,
            run_id=run_id,
        )
        session.add(result)
        await session.flush()
        await session.refresh(result)
        results.append(_result_response(rule, result))

    imported_ids = {item.rule_id for item in req.results}
    for rule in rules:
        if rule.is_active == "true" and rule.id not in imported_ids:
            results.append(await _carry_forward(
                session, rule, dataset_id, "이번 진단에 포함되지 않은 규칙입니다"))

    return await _record_score(session, dataset_id, results, label)


async def get_run_results(session: AsyncSession, run_id: int) -> list[QualityResultResponse]:
    """진단 실행 하나가 남긴 규칙별 결과 — 규칙이 만들어진 순서대로.

    그 실행에서 실제로 평가한 규칙만 나온다(이어받은 결과는 없다). 실행 뒤 지워진 규칙의 결과는 함께 지워진다.
    """
    rows = (await session.execute(
        select(QualityResult, QualityRule)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .where(QualityResult.run_id == run_id)
        .order_by(QualityRule.created_at, QualityRule.id, QualityResult.id)
    )).all()
    return [_result_response(rule, result) for result, rule in rows]
