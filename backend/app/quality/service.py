# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 서비스 레이어.

- 프로파일링: 원천 DB 에 직접 통계 SQL 을 실행한다(Method A — mysql/mariadb/postgresql).
  접속·조회에 실패하면 스키마 정보만으로 통계 없는 프로파일(행 수 0)을 만든다(Method B 폴백,
  응답 ``fallback``·``fallback_reason`` 으로 알림).
- 서버 검증(``run_quality_check``): 활성 규칙을 **최신 프로파일 통계**로 평가한다(재프로파일하지 않음).
  NOT_NULL·UNIQUE 는 프로파일의 정확한 건수로 판정하고, threshold 0 도 그대로 존중한다.
- 서버가 평가할 수 없는 유형(``SERVER_UNEVALUABLE_TYPES`` — ACCEPTED_VALUES·REGEX·CUSTOM_SQL·
  CUSTOM_PYTHON)은 새 결과를 남기지 않고 그 규칙의 직전 결과(주로 품질 배치 ``quality/*.py`` 반입)를
  이어받아(carried_forward) 점수에 반영하며, 직전 결과가 없으면 미평가(passed=None)로 점수에서 뺀다.
- 외부 반입(``import_profile``/``import_results``): 품질 배치가 원본 전체 데이터로 계산한
  프로파일·규칙 결과를 저장한다. 결과 반입 점수도 활성 규칙 전체 기준(반입 + 이어받기)이다.

지원 검사 유형: schemas.SUPPORTED_CHECK_TYPES (NOT_NULL, UNIQUE, MIN_VALUE, MAX_VALUE, ROW_COUNT,
FRESHNESS, ACCEPTED_VALUES, REGEX, CUSTOM_SQL, CUSTOM_PYTHON)
"""

import json as _json
import logging
import re
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, text
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
# 프로파일링 — 원천 DB 직접 쿼리(Method A), 실패 시 스키마 기반 폴백(Method B)
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
    logger.debug("프로파일링 시작: dataset_id=%d", dataset_id)

    # 스키마 필드 조회
    fields = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )).scalars().all()

    # 원천 DB 에 직접 SQL 프로파일링 시도
    column_profiles = []
    row_count = 0
    fallback_reason: str | None = None

    try:
        row_count, column_profiles = await _profile_via_source_db(session, dataset, fields)
        logger.info("원천 DB 프로파일링: dataset_id=%d, rows=%d, columns=%d",
                     dataset_id, row_count, len(column_profiles))
    except Exception as e:
        logger.warning("원천 DB 프로파일링 실패 dataset_id=%d: %r. 스키마 기반으로 대체.", dataset_id, e)
        # 폴백: 스키마 기반 프로파일 (실제 데이터 통계 없음) — 응답의 fallback 으로 화면에 알린다.
        # 사유는 접속 정보가 섞일 수 있는 예외 원문 대신 분류된 문구만 노출(상세는 로그).
        fallback_reason = _fallback_reason(e)
        logger.info("스키마 기반 프로파일 사용 dataset_id=%d", dataset_id)
        column_profiles = []
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
        fallback=fallback_reason is not None, fallback_reason=fallback_reason,
    )


class _ProfilingUnsupportedError(ValueError):
    """프로파일링 사전 조건 미충족(데이터소스·설정 없음, 미지원 유형 등) — 메시지를 그대로 노출해도 안전."""


def _fallback_reason(e: Exception) -> str:
    """스키마 기반 폴백 사유 — 사용자에게 보일 문구."""
    if isinstance(e, _ProfilingUnsupportedError):
        return f"원천 DB 프로파일링을 할 수 없어 스키마 기반으로 대체했습니다: {e}"
    from app.catalog.service import _classify_connection_error

    return f"원천 DB 조회에 실패해 스키마 기반으로 대체했습니다(행 수·통계 없음): {_classify_connection_error(e, _PROFILE_CONNECT_TIMEOUT)}"


# 원천 접속·쿼리 타임아웃(초) — 동기화(app/catalog/sync.py)와 같은 값
_PROFILE_CONNECT_TIMEOUT = 10
_PROFILE_QUERY_TIMEOUT = 120


async def _profile_via_source_db(
    session: AsyncSession, dataset: Dataset, fields: list,
) -> tuple[int, list[ColumnProfile]]:
    """원천 데이터베이스에 직접 프로파일링 SQL 을 실행한다.

    반환: (행 수, 컬럼별 ColumnProfile 목록). 컬럼마다 NULL 수·고유값 수·최빈값(저카디널리티)·
    최소/최대·평균(숫자형)을 개별 쿼리로 구한다. 지원 원천: postgresql, mysql/mariadb.

    예외: 사전 조건 미충족은 ``_ProfilingUnsupportedError``, 접속·쿼리 실패는 드라이버 예외 그대로
    (호출자가 스키마 기반으로 폴백한다). 접속 비밀번호는 로그에 남기지 않는다.
    """
    # 데이터소스 연결 설정 조회
    datasource = (await session.execute(
        select(Datasource).where(Datasource.id == dataset.datasource_id)
    )).scalar_one_or_none()
    if not datasource:
        raise _ProfilingUnsupportedError("데이터소스가 없습니다")

    config_row = (await session.execute(
        select(DatasourceConfiguration).where(DatasourceConfiguration.datasource_id == datasource.id)
    )).scalar_one_or_none()
    if not config_row:
        raise _ProfilingUnsupportedError("데이터소스 접속 설정이 없습니다")

    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.catalog.datasource_secrets import unseal_config
    from app.catalog.service import connection_ssl, validate_connection_target
    from app.catalog.sync import _mysql_ident, _pg_ident

    config = unseal_config(config_row.config_json)

    db_type = datasource.type.lower()
    is_pg = db_type == "postgresql"
    if not is_pg and db_type not in ("mysql", "mariadb"):
        logger.debug("원천 프로파일링 미지원 유형: dataset_id=%d type=%s", dataset.id, db_type)
        raise _ProfilingUnsupportedError(f"지원하지 않는 데이터소스 유형: {db_type}")

    # 연결 정보 — 호스트·포트는 동기화와 같은 규칙으로 검증(PostgreSQL 기본 포트 5432)
    try:
        host, port = validate_connection_target(config, 5432 if is_pg else 3306)
    except ValueError as e:
        raise _ProfilingUnsupportedError(f"접속 설정 오류: {e}") from None
    database = config.get("database", "")
    username = config.get("username", "")
    password = config.get("password", "")
    ssl_opt = connection_ssl(config, pg=is_pg)

    # qualified_name 에서 테이블명 파싱 (예: "sakila.film" → db=sakila, table=film)
    parts = (dataset.qualified_name or dataset.name).split(".")
    if len(parts) >= 2:
        schema_or_db = parts[-2]
        table_name = parts[-1]
    else:
        schema_or_db = database
        table_name = parts[0]

    # 접속 URL 은 URL.create 로 조립 — 사용자명·비밀번호의 @ / # 등이 그대로 안전하게 전달된다.
    # 식별자는 원천 메타데이터에서 온 값이므로 방언별 인용(백틱/큰따옴표 이중화)으로 SQL 에 넣는다.
    if is_pg:
        url = URL.create("postgresql+asyncpg", username=username or None, password=password or None,
                         host=host, port=port, database=database or "postgres")
        connect_args = {"timeout": _PROFILE_CONNECT_TIMEOUT, "command_timeout": _PROFILE_QUERY_TIMEOUT}
        quote = _pg_ident
        table_sql = (f"{quote(schema_or_db)}.{quote(table_name)}"
                     if schema_or_db != database else quote(table_name))
    else:
        url = URL.create("mysql+aiomysql", username=username or None, password=password or None,
                         host=host, port=port, database=schema_or_db or None,
                         query={"charset": "utf8mb4"})
        connect_args = {"connect_timeout": _PROFILE_CONNECT_TIMEOUT}
        quote = _mysql_ident
        table_sql = quote(table_name)
    if ssl_opt is not None:
        connect_args["ssl"] = ssl_opt
    # 접속 정보는 호스트·포트·대상 테이블만 남긴다(사용자명·비밀번호 제외).
    logger.debug(
        "원천 프로파일링 접속: type=%s host=%s port=%s table=%s columns=%d ssl=%s",
        db_type, host, port, table_name, len(fields), ssl_opt is not None,
    )

    engine = create_async_engine(url, pool_size=1, max_overflow=0, connect_args=connect_args)

    try:
        async with engine.connect() as conn:
            # 행 수
            result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_sql}"))
            row_count = result.scalar() or 0

            column_profiles = []
            for f in fields:
                col = f.field_path
                qcol = quote(col)
                cp = ColumnProfile(column_name=col, column_type=f.field_type, total_count=row_count)

                try:
                    # NULL 수
                    r = await conn.execute(text(f"SELECT COUNT(*) FROM {table_sql} WHERE {qcol} IS NULL"))
                    cp.null_count = r.scalar() or 0
                    cp.null_percent = round(cp.null_count / row_count * 100, 2) if row_count > 0 else 0

                    # 고유값 수
                    r = await conn.execute(text(f"SELECT COUNT(DISTINCT {qcol}) FROM {table_sql}"))
                    cp.unique_count = r.scalar() or 0
                    cp.unique_percent = round(cp.unique_count / row_count * 100, 2) if row_count > 0 else 0

                    # 최빈값 top 5 — 저카디널리티(코드성) 컬럼만, 바이너리 제외
                    if 0 < cp.unique_count <= 20 and not _is_binary_type(f.field_type):
                        r = await conn.execute(text(
                            f"SELECT {qcol}, COUNT(*) AS c FROM {table_sql} "
                            f"WHERE {qcol} IS NOT NULL GROUP BY {qcol} ORDER BY c DESC LIMIT 5"
                        ))
                        cp.top_values = [
                            {"value": str(v), "count": int(c)} for v, c in r.fetchall()
                        ]

                    # Min/Max — 바이너리 계열은 의미가 없고 repr 이 거대해 생략
                    if not _is_binary_type(f.field_type):
                        r = await conn.execute(text(f"SELECT MIN({qcol}), MAX({qcol}) FROM {table_sql}"))
                        row = r.first()
                        if row:
                            cp.min_value = str(row[0]) if row[0] is not None else None
                            cp.max_value = str(row[1]) if row[1] is not None else None

                    # 평균 (숫자형만)
                    if f.field_type.upper() in ("INT", "INTEGER", "BIGINT", "SMALLINT", "NUMBER",
                                                  "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL"):
                        r = await conn.execute(text(f"SELECT AVG({qcol}) FROM {table_sql}"))
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


async def list_rules(session: AsyncSession, dataset_id: int) -> list[QualityRuleResponse]:
    """데이터셋의 규칙 전체(활성·비활성)를 생성 순으로 반환한다."""
    rules = (await session.execute(
        select(QualityRule).where(QualityRule.dataset_id == dataset_id)
        .order_by(QualityRule.created_at)
    )).scalars().all()
    return [QualityRuleResponse.model_validate(r) for r in rules]


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
    for k, v in changes.items():
        setattr(rule, k, v)
    await session.flush()
    await session.refresh(rule)
    logger.info("품질 규칙 수정됨: id=%d, fields=%s", rule.id, sorted(changes))
    return QualityRuleResponse.model_validate(rule)


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    """규칙을 삭제한다(결과 행은 FK CASCADE). 없으면 False(commit 은 호출자 몫)."""
    rule = await get_rule(session, rule_id)
    if not rule:
        return False
    logger.info("품질 규칙 삭제됨: id=%d, name=%s", rule.id, rule.rule_name)
    await session.delete(rule)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# 규칙 추천 · 품질 검사 실행 — 데이터셋의 모든 활성 규칙을 평가
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
                # 평균은 있으나 최소값이 숫자로 해석되지 않는 컬럼 — 추천하지 않는다.
                logger.debug("MIN_VALUE 추천 건너뜀(숫자 아님): dataset_id=%d column=%s", dataset_id, col)

    # GET(조회) 경로라 DEBUG. 후보는 기존 규칙과 겹치는 것을 이미 뺀 수다.
    logger.debug("규칙 추천: dataset_id=%d, 후보=%d개 (기존 규칙=%d개)",
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
        logger.debug(
            "데이터셋 품질 상태 갱신: dataset_id=%d status=%s score=%.1f failed=%d",
            dataset_id, dataset.quality_status, score, len(failed),
        )
    else:
        logger.warning("품질 상태 갱신 대상 데이터셋 없음: dataset_id=%d", dataset_id)

    if failed:
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


async def run_quality_check(session: AsyncSession, dataset_id: int) -> RunCheckResponse:
    """데이터셋의 모든 활성 품질 규칙을 서버에서 평가한다 (수동 "검증 실행"·예약 검증 공통).

    - 평가 기준은 **최신 프로파일 통계**다. 프로파일이 없을 때만 먼저 프로파일링하고,
      있으면 재프로파일하지 않는다(데이터가 바뀌었으면 프로파일을 먼저 갱신해야 한다).
    - 서버가 평가할 수 있는 유형은 새 결과 행을 저장한다.
    - 평가할 수 없는 유형(SERVER_UNEVALUABLE_TYPES)은 결과를 저장하지 않고 직전 결과를 이어받거나
      (carried_forward) 미평가(passed=None, 점수 제외)로 응답에만 싣는다 — ``_carry_forward``.
    - 점수·데이터셋 상태·알림은 ``_record_score`` 가 처리한다.
    """
    # 없는 데이터셋 — 라우터가 404 로 변환한다(프로파일 이력이 없으므로 아래 자동 프로파일링 전에 확인)
    if not await dataset_exists(session, dataset_id):
        raise ValueError(f"Dataset not found: {dataset_id}")

    # 최신 프로파일 조회
    profile = await get_latest_profile(session, dataset_id)
    if not profile:
        # 먼저 자동 프로파일링
        logger.info("프로파일 없음 — 검증 전 자동 프로파일링: dataset_id=%d", dataset_id)
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

    # 시간대 없는 시각 값(FRESHNESS)을 해석할 원천 데이터 시간대 — 설정 quality_source_timezone
    source_tz = (await get_source_timezone(session)
                 if any(r.check_type == "FRESHNESS" for r in rules) else None)
    logger.debug("품질 검증 대상 규칙: dataset_id=%d active_rules=%d", dataset_id, len(rules))

    results: list[QualityResultResponse] = []

    for rule in rules:
        passed, actual, detail = _evaluate_rule(rule, profile, col_map, source_tz)
        # actual 에는 원천 데이터 값(최소/최대 등)이 들어갈 수 있어 로그에 남기지 않는다.
        logger.debug("규칙 평가: rule_id=%d type=%s passed=%s", rule.id, rule.check_type, passed)

        if passed is None:
            # 서버가 평가할 수 없는 유형 — 새 결과를 남기지 않는다(예전엔 "평가 제외"를 통과로 저장해
            # 배치가 찾아낸 실패를 덮어쓰고 점수를 올렸다). 직전 결과(배치 반입)를 이어받거나 미평가.
            results.append(await _carry_forward(session, rule, dataset_id, detail))
            continue

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
        results.append(_result_response(rule, result))

    return await _record_score(session, dataset_id, results, "품질 검사 완료")


# 서버(프로파일 통계)로는 평가할 수 없어 품질 배치(quality/*.py) 반입 결과에 의존하는 유형
SERVER_UNEVALUABLE_TYPES = ("ACCEPTED_VALUES", "REGEX", "CUSTOM_SQL", "CUSTOM_PYTHON")
# 예전 서버 검증이 위 유형에 대해 통과로 저장하던 자리표시 결과의 actual_value — 이어받기 대상에서 제외
_LEGACY_SKIP_ACTUAL = "평가 제외"
_UNEVALUABLE_REASONS = {
    "ACCEPTED_VALUES": "허용 값 검사는 프로파일 통계만으로 평가할 수 없습니다 — 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다",
    "REGEX": "정규식 검사는 프로파일 통계만으로 평가할 수 없습니다 — 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다",
    # 임의 SQL·코드 실행은 서버에서 하지 않는다 (보안) — 품질 배치 전용.
    "CUSTOM_SQL": "커스텀 SQL 은 서버에서 실행하지 않습니다 — 품질 배치(quality/*.py)에서 원본 DB 로 평가됩니다",
    "CUSTOM_PYTHON": "커스텀 Python 체크는 서버에서 실행하지 않습니다 — 품질 배치(quality/*.py)의 custom_checks 플러그인으로 평가됩니다",
}


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
        checked_at=result.checked_at, carried_forward=carried_forward,
    )


async def _latest_result(session: AsyncSession, rule_id: int) -> QualityResult | None:
    """규칙의 가장 최근 실제 결과 (예전 서버 검증의 "평가 제외" 자리표시 통과 행은 제외)."""
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
    평가 가능한 결과가 하나도 없으면 예전과 같이 100.0 (규칙 없음과 동일하게 취급).
    """
    evaluated = [r for r in results if r.passed is not None]
    passed_count = sum(1 for r in evaluated if r.passed == "true")
    total = len(evaluated)
    failed_count = total - passed_count
    score = round(passed_count / total * 100, 1) if total > 0 else 100.0

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


def _meets_threshold(count: int, total: int, threshold: Decimal) -> bool:
    """count/total×100 ≥ threshold 를 반올림 없이 정확히 비교한다."""
    return Decimal(count) * 100 >= threshold * total


def _evaluate_rule(
    rule: QualityRule, profile: ProfileResponse, col_map: dict[str, ColumnProfile],
    source_tz: tzinfo | None = None,
) -> tuple[bool | None, str, str]:
    """프로파일 데이터에 대해 단일 품질 규칙을 평가한다.

    ``source_tz`` 는 FRESHNESS 에서 시간대 없는 시각 값을 해석할 원천 데이터 시간대(기본 Asia/Seoul).

    반환: (passed, actual_value, detail). passed 가 None 이면 서버에서 평가할 수 없는 유형
    (SERVER_UNEVALUABLE_TYPES) — 호출자가 직전 결과를 이어받거나 미평가로 처리한다.
    """
    # threshold 0 도 유효한 값 — falsy 검사로 100 이 되지 않게 None 만 기본값 처리
    threshold_dec = Decimal(str(rule.threshold)) if rule.threshold is not None else Decimal(100)
    threshold = float(threshold_dec)
    expected = rule.expected_value

    if rule.check_type == "NOT_NULL":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        total = cp.total_count
        if total > 0:
            # 판정은 정확한 건수로 — 반올림된 null_percent 로는 대용량 테이블의 NULL 1건이 0.00% 가 되어 통과했다
            non_null = max(total - cp.null_count, 0)
            passed = _meets_threshold(non_null, total, threshold_dec)
            non_null_pct = non_null / total * 100
            counts = f", NULL {cp.null_count:,}건 / {total:,}행"
        else:
            # 빈 테이블·건수 없는 반입 프로파일 — 예전과 같이 비율로 판정
            non_null_pct = 100.0 - cp.null_percent
            passed = non_null_pct >= threshold
            counts = ""
        return passed, f"{non_null_pct:.1f}%", f"비-NULL {non_null_pct:.1f}%{counts} (임계값 {threshold}%)"

    elif rule.check_type == "UNIQUE":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        total = cp.total_count
        if total > 0:
            # 판정은 정확한 건수로 (반올림된 unique_percent 로는 중복 1건이 100% 로 보여 통과했다)
            passed = _meets_threshold(cp.unique_count, total, threshold_dec)
            unique_pct = cp.unique_count / total * 100
            counts = f", 고유 {cp.unique_count:,}개 / {total:,}행"
        else:
            unique_pct = cp.unique_percent
            passed = unique_pct >= threshold
            counts = ""
        return passed, f"{unique_pct:.1f}%", f"고유값 {unique_pct:.1f}%{counts} (임계값 {threshold}%)"

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
        # 이 검사는 실제 데이터가 필요 — 서버는 평가하지 않고 배치 결과를 이어받는다
        return None, "평가 제외", _UNEVALUABLE_REASONS["ACCEPTED_VALUES"]

    elif rule.check_type == "REGEX":
        cp = col_map.get(rule.column_name or "")
        if not cp:
            return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
        return None, "평가 제외", _UNEVALUABLE_REASONS["REGEX"]

    elif rule.check_type == "CUSTOM_SQL":
        return None, "평가 제외", _UNEVALUABLE_REASONS["CUSTOM_SQL"]

    elif rule.check_type == "CUSTOM_PYTHON":
        return None, "평가 제외", _UNEVALUABLE_REASONS["CUSTOM_PYTHON"]

    elif rule.check_type == "FRESHNESS":
        try:
            max_hours = float(expected) if expected else 24
        except (ValueError, TypeError):
            return False, "N/A", f"잘못된 기대값: {expected}"
        # 컬럼(적재/수정 시각 컬럼)을 지정하면 프로파일이 수집한 그 컬럼의 최대값 = 데이터 최신 시각 기준
        if rule.column_name:
            cp = col_map.get(rule.column_name)
            if not cp:
                return False, "N/A", f"프로파일에 '{rule.column_name}' 컬럼이 없습니다"
            latest = _parse_timestamp(cp.max_value, source_tz)
            if latest is None:
                return False, "N/A", f"'{rule.column_name}' 의 최대값을 시각으로 해석할 수 없습니다: {cp.max_value}"
            # 미래 시각(시간대 차이 등)은 0 으로 본다
            age_hours = max(0.0, (datetime.now(timezone.utc) - latest).total_seconds() / 3600)
            passed = age_hours <= max_hours
            return passed, f"{age_hours:.1f}h", (
                f"최신 데이터({rule.column_name} 최대값 {cp.max_value}) 이후 {age_hours:.1f}시간 (최대 {max_hours}시간)")
        # 컬럼 미지정 — 데이터 시각을 알 수 없어 마지막 프로파일 시각 기준(하위 호환)
        if profile.profiled_at:
            age_hours = (datetime.now(timezone.utc) - profile.profiled_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            passed = age_hours <= max_hours
            return passed, f"{age_hours:.1f}h", (
                f"프로파일 이후 {age_hours:.1f}시간 (최대 {max_hours}시간) — 데이터 시각 기준으로 보려면 시각 컬럼을 지정하세요")
        return False, "N/A", "프로파일 시각 정보가 없습니다"

    else:
        return False, "N/A", f"알 수 없는 검증 유형: {rule.check_type}"


def _parse_timestamp(value: str | None, source_tz: tzinfo | None = None) -> datetime | None:
    """프로파일 min/max 문자열(str(datetime|date))을 UTC aware datetime 으로 해석한다.

    시간대가 없는 값(MySQL DATETIME 등)은 **원천 데이터 시간대**(``source_tz``, 기본 Asia/Seoul —
    설정 ``quality_source_timezone``)의 벽시계 시각으로 본다. 서버 프로세스(컨테이너)의 로컬 시간대에
    의존하지 않으므로 품질 배치(``--source-timezone``)와 같은 결과가 나온다. 시간대가 있는 값은 그대로.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=source_tz or _DEFAULT_SOURCE_TZINFO)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 원천 데이터 시간대 (설정 quality_source_timezone)
# ---------------------------------------------------------------------------

QUALITY_CONFIG_CATEGORY = "quality"
SOURCE_TIMEZONE_KEY = "quality_source_timezone"
DEFAULT_SOURCE_TIMEZONE = "Asia/Seoul"
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def resolve_timezone(name: str | None) -> tzinfo:
    """시간대 이름 → tzinfo. IANA 이름(``Asia/Seoul``·``UTC``) 또는 고정 오프셋(``+09:00``).

    빈 값·알 수 없는 이름은 ``ValueError``.
    """
    text = (name or "").strip()
    if not text:
        raise ValueError("시간대가 비어 있습니다")
    m = _OFFSET_RE.match(text)
    if m:
        sign, hh, mm = m.groups()
        if int(hh) > 23 or int(mm) > 59:
            raise ValueError(f"잘못된 시간대 오프셋: {text}")
        delta = timedelta(hours=int(hh), minutes=int(mm))
        return timezone(-delta if sign == "-" else delta)
    if text.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"알 수 없는 시간대: {text} (예: Asia/Seoul, UTC, +09:00)") from e


def _default_tzinfo() -> tzinfo:
    """기본 원천 시간대(Asia/Seoul) tzinfo — tzdata 가 없으면 UTC+9 고정 오프셋."""
    try:
        return resolve_timezone(DEFAULT_SOURCE_TIMEZONE)
    except ValueError:  # tzdata 가 없는 환경 — 한국 표준시는 일광 절약 시간이 없어 고정 오프셋과 같다
        return timezone(timedelta(hours=9))


_DEFAULT_SOURCE_TZINFO = _default_tzinfo()


async def get_source_timezone_name(session: AsyncSession) -> str:
    """설정된 원천 데이터 시간대 이름(없거나 비어 있으면 기본 Asia/Seoul)."""
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, QUALITY_CONFIG_CATEGORY)
    return (cfg.get(SOURCE_TIMEZONE_KEY) or "").strip() or DEFAULT_SOURCE_TIMEZONE


async def get_source_timezone(session: AsyncSession) -> tzinfo:
    """설정된 원천 데이터 시간대 tzinfo — 해석할 수 없는 값이면 경고 후 기본값."""
    name = await get_source_timezone_name(session)
    try:
        return resolve_timezone(name)
    except ValueError:
        logger.warning("잘못된 %s 설정값 %r — 기본 %s 사용", SOURCE_TIMEZONE_KEY, name, DEFAULT_SOURCE_TIMEZONE)
        return _DEFAULT_SOURCE_TZINFO


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
    """각 활성 규칙에 대한 가장 최근 결과를 조회한다.

    결과가 없는 규칙은 빠지지만, 서버가 평가할 수 없는 유형(SERVER_UNEVALUABLE_TYPES)은 서버 검증이
    결과를 남기지 않으므로 미평가(passed=None, 점수 제외) 항목으로 함께 돌려준다.
    """
    rules = (await session.execute(
        select(QualityRule).where(
            QualityRule.dataset_id == dataset_id,
            QualityRule.is_active == "true",
        )
    )).scalars().all()

    results = []
    for rule in rules:
        # 예전 서버 검증의 "평가 제외" 자리표시 통과 행은 건너뛰고 실제 마지막 결과를 보여준다
        result = await _latest_result(session, rule.id)
        if result:
            results.append(_result_response(rule, result))
        elif rule.check_type in SERVER_UNEVALUABLE_TYPES:
            results.append(_not_evaluated(rule, dataset_id, _UNEVALUABLE_REASONS[rule.check_type]))

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

    점수는 데이터셋의 **활성 규칙 전체** 기준 (서버 검증과 같은 시맨틱):
    반입된 결과 + 반입에 없는 활성 규칙은 직전 결과를 이어받고(carried_forward), 없으면 미평가로 제외.
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
        )
        session.add(result)
        await session.flush()
        await session.refresh(result)
        results.append(_result_response(rule, result))

    imported_ids = {item.rule_id for item in req.results}
    for rule in rules:
        if rule.is_active == "true" and rule.id not in imported_ids:
            results.append(await _carry_forward(
                session, rule, dataset_id, "이번 반입에 포함되지 않은 규칙입니다"))

    return await _record_score(session, dataset_id, results, "품질 결과 반입 (외부)")
