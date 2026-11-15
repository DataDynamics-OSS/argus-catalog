# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 ORM 모델."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)

from app.core.database import Base


class DataProfile(Base):
    """데이터셋에 대한 원천 DB 프로파일링의 컬럼 단위 통계 스냅샷."""

    __tablename__ = "catalog_data_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    profile_json = Column(Text, nullable=False, default="[]")
    profiled_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityDefinition(Base):
    """품질 지표 — 미리 정해 두고 컬럼에 할당하는 진단 기준(``app/quality/definitions.py``).

    할당하면 그 컬럼에 이 지표를 가리키는 규칙(``QualityRule.definition_id``)이 생기고, 지표의 기준을 바꾸면
    할당된 규칙이 함께 바뀐다. ``category`` 는 관리지침의 진단 기준 키(``indicators.INDICATORS``)다.
    """

    __tablename__ = "catalog_quality_definition"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    category = Column(String(32), nullable=False)          # 분류 — indicators.INDICATORS 의 키 (DATE, YN, AMOUNT …)
    check_type = Column(String(50), nullable=False)        # 분류에 속한 규칙 유형
    expected_value = Column(Text)                          # 기준값 — 날짜 형식·허용 값·범위·정규식
    threshold = Column(Numeric(5, 2), default=100.00)      # 통과 임계값 % (NOT_NULL·UNIQUE 만 사용)
    severity = Column(String(16), nullable=False, default="WARNING")
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QualityRule(Base):
    """품질 검사 규칙 정의. 지원 유형은 schemas.SUPPORTED_CHECK_TYPES — 평가는 정밀 진단(품질 워커)이 한다.
    커스텀 SQL · Trino 함수는 Trino 경로에서만, 커스텀 Python 은 원천 직접 경로에서만 평가한다.
    더미 값(DUMMY_VALUE) · 데이터 연속성(CONTINUITY)은 AI-Ready 품질 지표(TODO-096 5단계)."""

    __tablename__ = "catalog_quality_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    rule_name = Column(String(255), nullable=False)
    check_type = Column(String(50), nullable=False)       # NOT_NULL, UNIQUE, MIN_VALUE, MAX_VALUE, ACCEPTED_VALUES, REGEX, ROW_COUNT, FRESHNESS, CUSTOM_SQL, CUSTOM_PYTHON
    column_name = Column(String(256))                      # 테이블 단위 검사면 NULL
    expected_value = Column(Text)                          # 기대값 문자열 — 유형별 해석(숫자·시간·정규식·SQL, 허용 값 · 더미 값은 JSON 배열(예전 쉼표 구분도 읽음), CUSTOM_PYTHON 은 JSON 설정)
    threshold = Column(Numeric(5, 2), default=100.00)      # 통과 임계값 % (NOT_NULL·UNIQUE 만 사용)
    # 같은 유형을 품질 지표로 나누는 도메인(app/quality/indicators.py DOMAINS) — 허용 값: CODE·YN,
    # 숫자 범위: AMOUNT·QUANTITY·RATE, 컬럼 간 비교: TIME_ORDER·SEQUENCE. NULL 이면 컬럼 이름으로 정한다
    domain = Column(String(32))
    # 할당된 품질 지표 — 있으면 유형·기준값·심각도·도메인은 그 지표를 따른다(definitions.update_definition 이 맞춘다).
    # NULL 이면 이 데이터셋에서 직접 정한 규칙. 지표를 지우면 NULL 이 되어 직접 정한 규칙으로 남는다
    definition_id = Column(Integer, ForeignKey("catalog_quality_definition.id", ondelete="SET NULL"))
    severity = Column(String(16), nullable=False, default="WARNING")
    is_active = Column(String(5), nullable=False, default="true")   # "true"/"false" 문자열
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QualityResult(Base):
    """단일 품질 규칙 평가의 실행 결과."""

    __tablename__ = "catalog_quality_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("catalog_quality_rule.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    passed = Column(String(5), nullable=False)                      # "true"/"false" 문자열
    actual_value = Column(Text)
    detail = Column(Text)
    failed_samples = Column(Text)  # 위반 행 샘플 JSON 배열 (배치 반입 시 최대 5행)
    # 건수 기반 결과 — 진단한 건수와 그 가운데 오류 건수. 건수를 세지 않는 평가(프로파일 통계 판정,
    # ROW_COUNT 등)는 NULL. 데이터 소스 단위 오류율(Σ 오류 / Σ 진단)의 재료다.
    checked_count = Column(BigInteger)
    error_count = Column(BigInteger)
    # 이 결과를 만든 진단 실행 — 예전 간이 진단 · 품질 배치 반입이 남긴 결과는 NULL
    run_id = Column(Integer, ForeignKey("catalog_quality_runs.id", ondelete="SET NULL"), index=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityScore(Base):
    """데이터셋의 집계 품질 점수. score = passed/total × 100 (total 은 평가·이어받은 결과 수, 미평가 제외).

    warning_rules 는 현재 항상 0 으로 기록된다(service._record_score).
    """

    __tablename__ = "catalog_quality_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    score = Column(Numeric(5, 2), nullable=False, default=0)
    total_rules = Column(Integer, nullable=False, default=0)
    passed_rules = Column(Integer, nullable=False, default=0)
    warning_rules = Column(Integer, nullable=False, default=0)
    failed_rules = Column(Integer, nullable=False, default=0)
    scored_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityRun(Base):
    """품질 진단 실행 1회 — 워커가 처리하는 대기열이자 모니터링의 정본.

    API 는 ``QUEUED`` 행만 만들고, 별도 서비스인 품질 워커(``extensions/argus-catalog-quality-worker``)가
    워커용 API 로 행을 가져가 엔진(Trino)에 쿼리를 내고 진행·결과를 보고한다(``execution.py``). ``heartbeat_at`` 이 오래 끊기면
    조회 시점에 FAILED 로 바꾼다(runs.STALE_SECONDS).
    """

    __tablename__ = "catalog_quality_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine = Column(String(20), nullable=False, default="TRINO")
    # 실행 종류 — CHECK(규칙 진단) / PROFILE(컬럼 통계 수집, TODO-095)
    kind = Column(String(20), nullable=False, default="CHECK", server_default="CHECK")
    # 쿼리를 실행하는 엔진 데이터 소스(Trino)와 그 안에서의 테이블 이름 — 요청 시점에 해석해 고정한다
    engine_datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="SET NULL"))
    engine_table = Column(String(700))
    trigger_type = Column(String(20), nullable=False, default="MANUAL")  # MANUAL / SCHEDULE
    # 작업 실행이 넣은 실행이면 그 작업 실행
    job_run_id = Column(Integer, ForeignKey("catalog_quality_job_runs.id", ondelete="SET NULL"), index=True)
    # 측정할 품질 지표(쉼표 구분 키, app/quality/indicators.py). NULL 이면 Trino 로 평가할 수 있는 규칙 전부
    indicators = Column(String(500))
    # 측정할 품질 지표(미리 정의한 지표, catalog_quality_definition) ID(쉼표 구분). NULL 이면 가리지 않는다
    definition_ids = Column(String(2000))
    # QUEUED / RUNNING / SUCCEEDED / PARTIAL / FAILED / CANCELLED
    status = Column(String(20), nullable=False, default="QUEUED", index=True)
    current_target = Column(String(500))  # 지금 실행 중인 쿼리 설명
    progress_done = Column(Integer, nullable=False, default=0)   # 끝난 쿼리 수
    progress_total = Column(Integer)                              # 전체 쿼리 수
    rules_total = Column(Integer, nullable=False, default=0)      # 활성 규칙 수
    rules_evaluated = Column(Integer, nullable=False, default=0)  # 이번 실행에서 평가한 규칙 수
    checked_count = Column(BigInteger)  # Σ 진단 건수
    error_count = Column(BigInteger)    # Σ 오류 건수
    score = Column(Numeric(5, 2))       # 실행 직후 기록된 품질 점수
    query_ids = Column(Text)            # 엔진 쿼리 ID JSON 배열
    # 실행 계획의 바탕(규칙·컬럼 타입·참조 대상) JSON — 워커가 돌려준 결과를 낸 쿼리와 같은 기준으로 판정한다
    plan_json = Column(Text)
    error_message = Column(Text)
    requested_by = Column(String(200))
    cancel_requested_by = Column(String(200))
    worker_id = Column(String(200))
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))


class QualityJob(Base):
    """품질 진단 작업 — 무엇을(대상), 어떤 품질로(지표), 언제(주기) 진단할지의 정의.

    실행할 때마다 ``QualityJobRun`` 한 건이 생기고, 그 아래 대상 데이터셋마다 ``QualityRun`` 이 대기열에 들어간다.
    """

    __tablename__ = "catalog_quality_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    # 대상 JSON 배열 — {"type": "catalog"|"schema"|"dataset", ...}. 카탈로그·스키마는 실행 시점에 데이터셋으로 푼다
    targets_json = Column(Text, nullable=False, default="[]")
    indicators = Column(String(500))  # 쉼표 구분 지표 키, NULL = 전체
    # 측정할 품질 지표(catalog_quality_definition) ID, 쉼표 구분. NULL = 전체(지표 없이 직접 만든 규칙 포함)
    definition_ids = Column(String(2000))
    schedule_type = Column(String(20), nullable=False, default="NONE")  # NONE / HOURLY / DAILY / WEEKLY / MONTHLY
    schedule_time = Column(String(5))             # HH:MM (DAILY·WEEKLY·MONTHLY)
    schedule_day = Column(Integer)                # WEEKLY: 0(월)~6(일), MONTHLY: 1~31(없는 날은 말일)
    schedule_interval_hours = Column(Integer)     # HOURLY: N 시간마다
    timezone = Column(String(64), nullable=False, default="Asia/Seoul")
    enabled = Column(Boolean, nullable=False, default=True)
    # 진단과 함께 프로파일(컬럼 통계 수집)도 대기열에 넣는다 — 규칙이 없는 대상 데이터셋도 프로파일한다
    include_profile = Column(Boolean, nullable=False, default=False)
    next_run_at = Column(DateTime(timezone=True), index=True)  # 스케줄러가 볼 다음 실행 시각 (없으면 주기 실행 안 함)
    created_by = Column(String(200))
    updated_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QualityJobRun(Base):
    """품질 진단 작업의 실행 1회 — 작업 이력 한 줄. 집계는 아래 ``QualityRun`` 들이 끝날 때 갱신한다."""

    __tablename__ = "catalog_quality_job_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("catalog_quality_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_type = Column(String(20), nullable=False, default="SCHEDULE")  # SCHEDULE / MANUAL
    # RUNNING / SUCCEEDED / PARTIAL(일부 실패) / FAILED / CANCELLED / SKIPPED(이전 실행이 안 끝나 건너뜀)
    status = Column(String(20), nullable=False, default="RUNNING", index=True)
    indicators = Column(String(500))  # 실행 시점의 지표 (작업을 고쳐도 이력은 그대로)
    definition_ids = Column(String(2000))  # 실행 시점에 고른 품질 지표 ID
    targets_total = Column(Integer, nullable=False, default=0)   # 대상 데이터셋 수
    runs_total = Column(Integer, nullable=False, default=0)      # 대기열에 넣은 데이터셋 실행 수
    runs_succeeded = Column(Integer, nullable=False, default=0)
    runs_failed = Column(Integer, nullable=False, default=0)     # 실패·일부 실패·중지
    skipped_json = Column(Text)  # 실행하지 않은 데이터셋 JSON 배열 — {"dataset_id", "name", "reason"}
    checked_count = Column(BigInteger)
    error_count = Column(BigInteger)
    message = Column(Text)
    requested_by = Column(String(200))
    cancel_requested_by = Column(String(200))  # 값이 있으면 마감할 때 CANCELLED
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))


class QualityWorker(Base):
    """살아 있는 품질 워커 — 워커가 주기적으로 ``heartbeat_at`` 을 갱신한다(모니터링용)."""

    __tablename__ = "catalog_quality_workers"

    worker_id = Column(String(200), primary_key=True)
    hostname = Column(String(255))
    version = Column(String(50))
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at = Column(DateTime(timezone=True))
    current_run_id = Column(Integer)
    # 이 워커 ID 를 처음 쓴 인증 주체(API 키) — 다른 주체가 같은 ID 로 실행을 가로채지 못하게 한다
    owner = Column(String(200))
