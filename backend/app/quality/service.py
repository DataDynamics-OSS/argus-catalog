# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 서비스 레이어.

Method A+B 하이브리드:
- 프로파일링: 샘플 기반 (카탈로그 자체 스키마 + 샘플 데이터 사용)
- 규칙 평가: 가능하면 원천 DB 에 직접 SQL, 불가하면 샘플로 폴백

지원 검사 유형:
  NOT_NULL, UNIQUE, MIN_VALUE, MAX_VALUE, ACCEPTED_VALUES,
  REGEX, ROW_COUNT, FRESHNESS
"""

import json as _json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetSchema, Datasource, DatasourceConfiguration
from app.quality.models import DataProfile, QualityResult, QualityRule, QualityScore
from app.quality.schemas import (
    ColumnProfile,
    ProfileImportRequest,
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
# 프로파일링 — 샘플 기반 (Method B)
# 카탈로그에 저장된 샘플 데이터 또는 원천 DB 직접 쿼리 사용
# ---------------------------------------------------------------------------

async def profile_dataset(session: AsyncSession, dataset_id: int) -> ProfileResponse:
    """원천 DB 를 쿼리하거나 저장된 스키마 정보를 사용해 데이터셋을 프로파일링한다.

    컬럼 단위 통계(NULL 수, 고유값 수, 최소/최대, 평균)를 수집한다.
    원천 DB 에 도달할 수 없으면 스키마 기반 프로파일로 폴백한다.
    """
    # 데이터셋과 데이터소스 정보 조회
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not dataset:
        raise ValueError(f"Dataset not found: {dataset_id}")

    # 스키마 필드 조회
    fields = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )).scalars().all()

    # 원천 DB 에 직접 SQL 프로파일링 시도
    column_profiles = []
    row_count = 0

    try:
        row_count, column_profiles = await _profile_via_source_db(session, dataset, fields)
        logger.info("원천 DB 프로파일링: dataset_id=%d, rows=%d, columns=%d",
                     dataset_id, row_count, len(column_profiles))
    except Exception as e:
        logger.warning("원천 DB 프로파일링 실패 dataset_id=%d: %s. 스키마 기반으로 대체.", dataset_id, e)
        # 폴백: 스키마 기반 프로파일 (실제 데이터 통계 없음)
        logger.info("스키마 기반 프로파일 사용 dataset_id=%d", dataset_id)
        for f in fields:
            column_profiles.append(ColumnProfile(
                column_name=f.field_path,
                column_type=f.field_type,
            ))

    # 프로파일 저장
    profile = DataProfile(
        dataset_id=dataset_id,
        row_count=row_count,
        profile_json=_json.dumps([cp.model_dump() for cp in column_profiles], ensure_ascii=False, default=str),
    )
    session.add(profile)
    await session.flush()
    await session.refresh(profile)

    return ProfileResponse(
        id=profile.id, dataset_id=dataset_id, row_count=row_count,
        columns=column_profiles, profiled_at=profile.profiled_at,
    )


async def _profile_via_source_db(
    session: AsyncSession, dataset: Dataset, fields: list,
) -> tuple[int, list[ColumnProfile]]:
    """원천 데이터베이스에 직접 프로파일링 SQL 을 실행한다."""
    # 데이터소스 연결 설정 조회
    datasource = (await session.execute(
        select(Datasource).where(Datasource.id == dataset.datasource_id)
    )).scalar_one_or_none()
    if not datasource:
        raise ValueError("Datasource not found")

    config_row = (await session.execute(
        select(DatasourceConfiguration).where(DatasourceConfiguration.datasource_id == datasource.id)
    )).scalar_one_or_none()
    if not config_row:
        raise ValueError("Datasource configuration not found")

    config = _json.loads(config_row.config_json) if config_row.config_json else {}

    # 연결 URL 구성
    db_type = datasource.type.lower()
    host = config.get("host", "localhost")
    port = config.get("port", 3306)
    database = config.get("database", "")
    username = config.get("username", "")
    password = config.get("password", "")

    # qualified_name 에서 테이블명 파싱 (예: "sakila.film" → db=sakila, table=film)
    parts = (dataset.qualified_name or dataset.name).split(".")
    if len(parts) >= 2:
        schema_or_db = parts[-2]
        table_name = parts[-1]
    else:
        schema_or_db = database
        table_name = parts[0]

    if db_type in ("mysql", "mariadb"):
        from sqlalchemy.ext.asyncio import create_async_engine as cae
        url = f"mysql+aiomysql://{username}:{password}@{host}:{port}/{schema_or_db}?charset=utf8mb4"
    elif db_type == "postgresql":
        from sqlalchemy.ext.asyncio import create_async_engine as cae
        url = f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{database}"
        table_name = f"{schema_or_db}.{table_name}" if schema_or_db != database else table_name
    else:
        raise ValueError(f"Profiling not supported for datasource type: {db_type}")

    engine = cae(url, pool_size=1, max_overflow=0)

    try:
        async with engine.connect() as conn:
            # 행 수
            result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            row_count = result.scalar() or 0

            column_profiles = []
            for f in fields:
                col = f.field_path
                cp = ColumnProfile(column_name=col, column_type=f.field_type, total_count=row_count)

                try:
                    # NULL 수
                    r = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name} WHERE {col} IS NULL"))
                    cp.null_count = r.scalar() or 0
                    cp.null_percent = round(cp.null_count / row_count * 100, 2) if row_count > 0 else 0

                    # 고유값 수
                    r = await conn.execute(text(f"SELECT COUNT(DISTINCT {col}) FROM {table_name}"))
                    cp.unique_count = r.scalar() or 0
                    cp.unique_percent = round(cp.unique_count / row_count * 100, 2) if row_count > 0 else 0

                    # 최빈값 top 5 — 저카디널리티(코드성) 컬럼만, 바이너리 제외
                    if 0 < cp.unique_count <= 20 and not _is_binary_type(f.field_type):
                        r = await conn.execute(text(
                            f"SELECT {col}, COUNT(*) AS c FROM {table_name} "
                            f"WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY c DESC LIMIT 5"
                        ))
                        cp.top_values = [
                            {"value": str(v), "count": int(c)} for v, c in r.fetchall()
                        ]

                    # Min/Max — 바이너리 계열은 의미가 없고 repr 이 거대해 생략
                    if not _is_binary_type(f.field_type):
                        r = await conn.execute(text(f"SELECT MIN({col}), MAX({col}) FROM {table_name}"))
                        row = r.first()
                        if row:
                            cp.min_value = str(row[0]) if row[0] is not None else None
                            cp.max_value = str(row[1]) if row[1] is not None else None

                    # 평균 (숫자형만)
                    if f.field_type.upper() in ("INT", "INTEGER", "BIGINT", "SMALLINT", "NUMBER",
                                                  "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL"):
                        r = await conn.execute(text(f"SELECT AVG({col}) FROM {table_name}"))
                        avg = r.scalar()
                        if avg is not None:
                            cp.mean_value = round(float(avg), 4)
                except Exception as col_err:
                    # 컬럼 단위 쿼리 실패는 해당 컬럼만 건너뜀 (전체 프로파일링은 계속)
                    logger.debug("컬럼 프로파일링 건너뜀: %s.%s — %s", table_name, col, col_err)

                column_profiles.append(cp)

            return row_count, column_profiles
    finally:
        await engine.dispose()


async def get_latest_profile(session: AsyncSession, dataset_id: int) -> ProfileResponse | None:
    """데이터셋의 가장 최근 프로파일을 조회한다."""
    profile = (await session.execute(
        select(DataProfile).where(DataProfile.dataset_id == dataset_id)
        .order_by(DataProfile.profiled_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not profile:
        return None

    columns = [ColumnProfile(**c) for c in _json.loads(profile.profile_json)]
    return ProfileResponse(
        id=profile.id, dataset_id=dataset_id, row_count=profile.row_count,
        columns=columns, profiled_at=profile.profiled_at,
    )


# ---------------------------------------------------------------------------
# 품질 규칙 CRUD
# ---------------------------------------------------------------------------

async def create_rule(session: AsyncSession, data: QualityRuleCreate) -> QualityRuleResponse:
    rule = QualityRule(**data.model_dump())
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    logger.info("품질 규칙 생성됨: id=%d, name=%s, type=%s", rule.id, rule.rule_name, rule.check_type)
    return QualityRuleResponse.model_validate(rule)


async def list_rules(session: AsyncSession, dataset_id: int) -> list[QualityRuleResponse]:
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset_id)
        .order_by(QualityRule.created_at)
    )).scalars().all()
    return [QualityRuleResponse.model_validate(r) for r in rules]


async def get_rule(session: AsyncSession, rule_id: int) -> QualityRule | None:
    return (await session.execute(
        select(QualityRule).where(QualityRule.id == rule_id)
    )).scalar_one_or_none()


async def update_rule(session: AsyncSession, rule_id: int, data: QualityRuleUpdate) -> QualityRuleResponse | None:
    rule = await get_rule(session, rule_id)
    if not rule:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(rule, k, v)
    await session.flush()
    await session.refresh(rule)
    return QualityRuleResponse.model_validate(rule)


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    rule = await get_rule(session, rule_id)
    if not rule:
        return False
    logger.info("품질 규칙 삭제됨: id=%d, name=%s", rule.id, rule.rule_name)
    await session.delete(rule)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# 품질 검사 실행 — 데이터셋의 모든 활성 규칙을 평가
# ---------------------------------------------------------------------------

async def recommend_rules(session: AsyncSession, dataset_id: int) -> list[dict]:
    """최신 프로파일을 분석해 품질 규칙 후보를 추천한다 (생성하지 않음).

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
        if (cp.top_values and 2 <= len(cp.top_values) <= 10
                and cp.unique_count == len(cp.top_values) and not is_temporal):
            values = ",".join(tv["value"] for tv in cp.top_values)
            add("ACCEPTED_VALUES", col, f"{col} 허용 값", expected=values,
                reason=f"코드성 컬럼 — 현재 값 {cp.unique_count}종을 허용 목록으로")
        if cp.mean_value is not None and cp.min_value is not None:
            try:
                if float(cp.min_value) >= 0:
                    add("MIN_VALUE", col, f"{col} 음수 금지", expected="0",
                        reason=f"현재 최소값 {cp.min_value} — 음수 유입 감지")
            except ValueError:
                pass

    logger.info("규칙 추천: dataset_id=%d, 후보=%d개 (기존 규칙 제외=%d개)",
                dataset_id, len(recs), len(existing_keys))
    return recs


async def _finalize_quality_outcome(
    session: AsyncSession,
    dataset_id: int,
    score: float,
    failed: list[dict],
) -> None:
    """검증/반입 공통 후처리 — 점수를 데이터셋에 동기화하고 실패 알림을 평가한다.

    - dataset.quality_score / quality_status(GOOD·WARN·BAD) 자동 갱신:
        치명(BREAKING) 실패 ≥1 또는 점수 < 70 → BAD
        그 외 실패 존재 또는 점수 < 90       → WARN
        전부 통과                            → GOOD
    - 실패가 있으면 QUALITY_FAILED 알림 규칙 평가 (거버넌스 > 알림)
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

    if failed:
        try:
            from app.alert.service import evaluate_quality_alerts
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


async def run_quality_check(session: AsyncSession, dataset_id: int) -> RunCheckResponse:
    """데이터셋의 모든 활성 품질 규칙을 실행한다.

    Method A+B 하이브리드:
    - 대부분의 검사는 최신 프로파일 데이터(Method B)를 사용
    - 원천 DB 를 쓸 수 없으면 프로파일 데이터로 폴백
    """
    # 최신 프로파일 조회
    profile = await get_latest_profile(session, dataset_id)
    if not profile:
        # 먼저 자동 프로파일링
        profile = await profile_dataset(session, dataset_id)
        await session.flush()

    # 프로파일로부터 컬럼 조회 맵 구성
    col_map = {cp.column_name: cp for cp in profile.columns}

    # 활성 규칙 조회
    rules = (await session.execute(
        select(QualityRule).where(
            QualityRule.dataset_id == dataset_id,
            QualityRule.is_active == "true",
        ).order_by(QualityRule.created_at)
    )).scalars().all()

    results: list[QualityResultResponse] = []
    passed_count = 0
    failed_count = 0

    for rule in rules:
        passed, actual, detail = _evaluate_rule(rule, profile, col_map)

        result = QualityResult(
            rule_id=rule.id,
            dataset_id=dataset_id,
            passed="true" if passed else "false",
            actual_value=actual,
            detail=detail,
        )
        session.add(result)
        await session.flush()
        await session.refresh(result)

        if passed:
            passed_count += 1
        else:
            failed_count += 1

        results.append(QualityResultResponse(
            id=result.id, rule_id=rule.id, rule_name=rule.rule_name,
            check_type=rule.check_type, column_name=rule.column_name,
            dataset_id=dataset_id, passed=result.passed,
            actual_value=actual, detail=detail, severity=rule.severity,
            checked_at=result.checked_at,
        ))

    # 점수 산출 및 저장
    total = len(rules)
    score = round(passed_count / total * 100, 1) if total > 0 else 100.0

    qs = QualityScore(
        dataset_id=dataset_id,
        score=score,
        total_rules=total,
        passed_rules=passed_count,
        warning_rules=0,
        failed_rules=failed_count,
    )
    session.add(qs)
    await session.flush()

    failed_summary = [
        {"rule_name": r.rule_name, "check_type": r.check_type, "severity": r.severity,
         "actual": r.actual_value, "detail": r.detail}
        for r in results if r.passed == "false"
    ]
    await _finalize_quality_outcome(session, dataset_id, score, failed_summary)

    logger.info("품질 검사 완료: dataset_id=%d, score=%.1f%%, passed=%d/%d",
                dataset_id, score, passed_count, total)

    return RunCheckResponse(
        dataset_id=dataset_id, score=score,
        total_rules=total, passed=passed_count, failed=failed_count,
        results=results,
    )


def _evaluate_rule(
    rule: QualityRule, profile: ProfileResponse, col_map: dict[str, ColumnProfile],
) -> tuple[bool, str, str]:
    """프로파일 데이터에 대해 단일 품질 규칙을 평가한다.

    반환: (passed, actual_value, detail)
    """
    threshold = float(rule.threshold) if rule.threshold else 100.0
    expected = rule.expected_value

    if rule.check_type == "NOT_NULL":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        non_null_pct = 100.0 - cp.null_percent
        passed = non_null_pct >= threshold
        return passed, f"{non_null_pct:.1f}%", f"비-NULL {non_null_pct:.1f}% (임계값 {threshold}%)"

    elif rule.check_type == "UNIQUE":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        passed = cp.unique_percent >= threshold
        return passed, f"{cp.unique_percent:.1f}%", f"고유값 {cp.unique_percent:.1f}% (임계값 {threshold}%)"

    elif rule.check_type == "MIN_VALUE":
        cp = col_map.get(rule.column_name or "")
        if not cp or cp.min_value is None:
            return False, "N/A", "최소값을 구할 수 없습니다"
        try:
            actual_min = float(cp.min_value)
            expected_min = float(expected) if expected else 0
            passed = actual_min >= expected_min
            return passed, str(actual_min), f"최소값 {actual_min} (기대 ≥ {expected_min})"
        except (ValueError, TypeError):
            return False, cp.min_value, f"숫자 비교 불가: {cp.min_value}"

    elif rule.check_type == "MAX_VALUE":
        cp = col_map.get(rule.column_name or "")
        if not cp or cp.max_value is None:
            return False, "N/A", "최대값을 구할 수 없습니다"
        try:
            actual_max = float(cp.max_value)
            expected_max = float(expected) if expected else 0
            passed = actual_max <= expected_max
            return passed, str(actual_max), f"최대값 {actual_max} (기대 ≤ {expected_max})"
        except (ValueError, TypeError):
            return False, cp.max_value, f"숫자 비교 불가: {cp.max_value}"

    elif rule.check_type == "ROW_COUNT":
        actual_count = profile.row_count
        try:
            expected_min = int(expected) if expected else 0
            passed = actual_count >= expected_min
            return passed, str(actual_count), f"행 수 {actual_count} (기대 ≥ {expected_min})"
        except (ValueError, TypeError):
            return False, str(actual_count), f"잘못된 기대값: {expected}"

    elif rule.check_type == "ACCEPTED_VALUES":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        # 이 검사는 실제 데이터가 필요 — 프로파일에서는 참고용으로만 표시
        return True, "평가 제외", "허용 값 검사는 프로파일 통계만으로 평가할 수 없습니다 — 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다"

    elif rule.check_type == "REGEX":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        return True, "평가 제외", "정규식 검사는 프로파일 통계만으로 평가할 수 없습니다 — 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다"

    elif rule.check_type == "CUSTOM_SQL":
        # 임의 SQL 실행은 서버에서 하지 않는다 (보안) — 품질 배치 전용.
        return True, "평가 제외", "커스텀 SQL 은 서버에서 실행하지 않습니다 — 품질 배치(quality/*.py)에서 원본 DB 로 평가됩니다"

    elif rule.check_type == "CUSTOM_PYTHON":
        # 임의 코드 실행은 서버에서 하지 않는다 (보안) — 품질 배치 전용.
        return True, "평가 제외", "커스텀 Python 체크는 서버에서 실행하지 않습니다 — 품질 배치(quality/*.py)의 custom_checks 플러그인으로 평가됩니다"

    elif rule.check_type == "FRESHNESS":
        # 마지막 프로파일 시각 확인
        if profile.profiled_at:
            age_hours = (datetime.now(timezone.utc) - profile.profiled_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            try:
                max_hours = float(expected) if expected else 24
                passed = age_hours <= max_hours
                return passed, f"{age_hours:.1f}h", f"데이터 나이 {age_hours:.1f}시간 (최대 {max_hours}시간)"
            except (ValueError, TypeError):
                return False, "N/A", f"잘못된 기대값: {expected}"
        return False, "N/A", "프로파일 시각 정보가 없습니다"

    else:
        return False, "N/A", f"알 수 없는 검증 유형: {rule.check_type}"


# ---------------------------------------------------------------------------
# 점수 이력
# ---------------------------------------------------------------------------

async def get_latest_score(session: AsyncSession, dataset_id: int) -> QualityScoreResponse | None:
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
    scores = (await session.execute(
        select(QualityScore).where(QualityScore.dataset_id == dataset_id)
        .order_by(QualityScore.scored_at.desc()).limit(limit)
    )).scalars().all()
    return [QualityScoreResponse.model_validate(s) for s in reversed(scores)]


async def get_latest_results(
    session: AsyncSession, dataset_id: int,
) -> list[QualityResultResponse]:
    """각 활성 규칙에 대한 가장 최근 결과를 조회한다."""
    rules = (await session.execute(
        select(QualityRule).where(
            QualityRule.dataset_id == dataset_id,
            QualityRule.is_active == "true",
        )
    )).scalars().all()

    results = []
    for rule in rules:
        result = (await session.execute(
            select(QualityResult).where(QualityResult.rule_id == rule.id)
            .order_by(QualityResult.checked_at.desc()).limit(1)
        )).scalar_one_or_none()

        if result:
            samples = None
            if result.failed_samples:
                try:
                    samples = _json.loads(result.failed_samples)
                except ValueError:
                    samples = None
            results.append(QualityResultResponse(
                id=result.id, rule_id=rule.id, rule_name=rule.rule_name,
                check_type=rule.check_type, column_name=rule.column_name,
                dataset_id=dataset_id, passed=result.passed,
                actual_value=result.actual_value, detail=result.detail,
                severity=rule.severity, failed_samples=samples,
                checked_at=result.checked_at,
            ))

    return results


# ---------------------------------------------------------------------------
# 외부 반입 — PySpark 등 외부 엔진이 계산한 프로파일/검증 결과 반입
# ---------------------------------------------------------------------------

async def import_profile(
    session: AsyncSession, dataset_id: int, req: "ProfileImportRequest",
) -> ProfileResponse:
    """외부에서 계산한 프로파일을 저장한다 (서버 프로파일링과 동일한 저장 형식)."""
    dataset = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not dataset:
        raise ValueError(f"Dataset not found: {dataset_id}")

    profile = DataProfile(
        dataset_id=dataset_id,
        row_count=req.row_count,
        profile_json=_json.dumps(
            [cp.model_dump() for cp in req.columns], ensure_ascii=False, default=str,
        ),
    )
    session.add(profile)
    await session.flush()
    await session.refresh(profile)

    logger.info("프로파일 반입 (외부): dataset_id=%d, rows=%d, columns=%d",
                dataset_id, req.row_count, len(req.columns))
    return ProfileResponse(
        id=profile.id, dataset_id=dataset_id, row_count=req.row_count,
        columns=req.columns, profiled_at=profile.profiled_at,
    )


async def import_results(
    session: AsyncSession, dataset_id: int, req: "ResultsImportRequest",
) -> RunCheckResponse:
    """외부에서 평가한 규칙 결과를 저장하고 점수를 산출한다.

    서버 측 ``run_quality_check`` 와 동일한 저장/점수 시맨틱:
    QualityResult 행 적재 → 통과/전체 비율로 QualityScore 기록.
    규칙이 이 데이터셋에 속하지 않으면 ValueError.
    """
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset_id)
    )).scalars().all()
    rule_map = {r.id: r for r in rules}

    results: list[QualityResultResponse] = []
    passed_count = 0
    failed_count = 0

    for item in req.results:
        rule = rule_map.get(item.rule_id)
        if not rule:
            raise ValueError(f"Rule {item.rule_id} does not belong to dataset {dataset_id}")

        result = QualityResult(
            rule_id=rule.id,
            dataset_id=dataset_id,
            passed="true" if item.passed else "false",
            actual_value=item.actual_value,
            detail=item.detail,
            failed_samples=_json.dumps(item.failed_samples, ensure_ascii=False, default=str)
            if item.failed_samples else None,
        )
        session.add(result)
        await session.flush()
        await session.refresh(result)

        if item.passed:
            passed_count += 1
        else:
            failed_count += 1

        results.append(QualityResultResponse(
            id=result.id, rule_id=rule.id, rule_name=rule.rule_name,
            check_type=rule.check_type, column_name=rule.column_name,
            dataset_id=dataset_id, passed=result.passed,
            actual_value=item.actual_value, detail=item.detail, severity=rule.severity,
            failed_samples=item.failed_samples, checked_at=result.checked_at,
        ))

    total = len(req.results)
    score = round(passed_count / total * 100, 1) if total > 0 else 100.0

    qs = QualityScore(
        dataset_id=dataset_id,
        score=score,
        total_rules=total,
        passed_rules=passed_count,
        warning_rules=0,
        failed_rules=failed_count,
    )
    session.add(qs)
    await session.flush()

    failed_summary = [
        {"rule_name": r.rule_name, "check_type": r.check_type, "severity": r.severity,
         "actual": r.actual_value, "detail": r.detail}
        for r in results if r.passed == "false"
    ]
    await _finalize_quality_outcome(session, dataset_id, score, failed_summary)

    logger.info("품질 결과 반입 (외부): dataset_id=%d, score=%.1f%%, passed=%d/%d",
                dataset_id, score, passed_count, total)
    return RunCheckResponse(
        dataset_id=dataset_id, score=score,
        total_rules=total, passed=passed_count, failed=failed_count,
        results=results,
    )
