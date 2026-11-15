# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 Pydantic 스키마.

프로파일링·품질 규칙·검사 결과·점수에 대한 요청/응답 모델.
"""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.quality import indicators as quality_indicators
from app.quality import rule_sql


def _id_list(v):
    """DB 의 쉼표 구분 ID 문자열(또는 NULL) → 정수 목록."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [int(k) for k in v.split(",") if k.strip().isdigit()]
    return v


# ---------------------------------------------------------------------------
# 프로파일
# ---------------------------------------------------------------------------

class ColumnProfile(BaseModel):
    """컬럼 단위 프로파일링 통계."""
    column_name: str
    column_type: str
    total_count: int = 0
    null_count: int = 0
    null_percent: float = 0.0
    unique_count: int = 0
    unique_percent: float = 0.0
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    top_values: list[dict] | None = None   # [{"value": "X", "count": 10}, ...]


class ProfileResponse(BaseModel):
    """데이터셋 프로파일(컬럼 통계 스냅샷) 응답."""
    id: int
    dataset_id: int
    row_count: int
    columns: list[ColumnProfile] = Field(default_factory=list)
    profiled_at: datetime


# ---------------------------------------------------------------------------
# 품질 규칙
# ---------------------------------------------------------------------------

# 품질 탭이 만들 수 있는 진단 유형 — 평가는 정밀 진단(Trino: rule_sql, 원천 직접: rule_direct)이 한다
SUPPORTED_CHECK_TYPES = (
    "NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ROW_COUNT", "FRESHNESS",
    "ACCEPTED_VALUES", "REGEX", "CUSTOM_SQL", "CUSTOM_PYTHON",
    # 날짜 도메인, 컬럼 간 비교(시간순서·선후관계), 참조관계
    "DATE_FORMAT", "COLUMN_COMPARE", "REFERENTIAL",
    # 글자 깨짐, 공백·특수문자, 논리관계, 계산식
    "BROKEN_TEXT", "WHITESPACE", "LOGICAL", "FORMULA",
    # Trino 경로 전용 — Trino 에 미리 등록한 함수(UDF)로 값 검증. 기대값은 함수 이름
    "FUNCTION",
    # AI-Ready 품질 지표(TODO-096 5단계) — 더미 값 빈도(통계적 타당성), 데이터 연속성(적시성). 정밀 진단 전용
    "DUMMY_VALUE", "CONTINUITY",
)
# 심각도 — 알림 평가(alert.service._SEVERITY_RANK)·품질 탭 선택지와 동일
SUPPORTED_SEVERITIES = ("BREAKING", "WARNING", "INFO")


def _check_check_type(v: str | None) -> str | None:
    """진단 유형이 SUPPORTED_CHECK_TYPES 중 하나인지 확인(아니면 422)."""
    if v is not None and v not in SUPPORTED_CHECK_TYPES:
        raise ValueError(f"지원하지 않는 진단 유형: {v} ({', '.join(SUPPORTED_CHECK_TYPES)})")
    return v


def _check_severity(v: str | None) -> str | None:
    """심각도가 SUPPORTED_SEVERITIES 중 하나인지 확인(아니면 422)."""
    if v is not None and v not in SUPPORTED_SEVERITIES:
        raise ValueError(f"지원하지 않는 심각도: {v} ({'/'.join(SUPPORTED_SEVERITIES)})")
    return v


class QualityRuleCreate(BaseModel):
    """품질 규칙 생성 요청."""
    dataset_id: int
    rule_name: str = Field(..., min_length=1, max_length=255)
    check_type: str        # SUPPORTED_CHECK_TYPES 중 하나
    column_name: str | None = None
    expected_value: str | None = None   # 유형별 해석 문자열(숫자·정규식·SQL, 허용 값 · 더미 값은 JSON 배열 — 예전 쉼표 구분도 읽음, CUSTOM_PYTHON 은 JSON)
    # 통과 임계값 % (0~100) — NOT_NULL·UNIQUE 만 사용. 0 도 유효한 값(0% 이상 = 항상 통과)
    threshold: float = Field(100.0, ge=0, le=100)
    severity: str = "WARNING"
    # 품질 지표를 가르는 도메인(indicators.DOMAINS) — 비우면 컬럼 이름으로 정한다. 유형에 없는 도메인은 422
    domain: str | None = Field(None, max_length=32)

    _v_check_type = field_validator("check_type")(_check_check_type)
    _v_severity = field_validator("severity")(_check_severity)

    @model_validator(mode="after")
    def _v_domain(self):
        self.domain = quality_indicators.validate_domain(self.check_type, self.domain)
        rule_sql.check_expression(self.check_type, self.expected_value)  # 논리관계·계산식은 모양을 미리 본다
        if self.check_type == "FRESHNESS" and not (self.column_name or "").strip():
            raise ValueError("최신성 검사는 적재 · 수정 시각 컬럼을 지정해야 합니다")
        return self


class QualityRuleUpdate(BaseModel):
    """품질 규칙 부분 수정 요청 — 명시한 필드만 반영(exclude_unset)."""
    rule_name: str | None = Field(None, min_length=1, max_length=255)
    check_type: str | None = None
    column_name: str | None = None
    expected_value: str | None = None
    threshold: float | None = Field(None, ge=0, le=100)
    severity: str | None = None
    is_active: str | None = None
    # 도메인 — null·빈 문자열이면 자동(컬럼 이름으로 정함). 유형과 맞는지는 저장할 때 본다(service.update_rule)
    domain: str | None = Field(None, max_length=32)
    # 품질 지표와의 연결 — null 을 보내면 연결을 끊어 직접 정한 규칙으로 만든다. 연결은 지표의 할당으로만 한다
    definition_id: int | None = None

    @field_validator("definition_id")
    @classmethod
    def _only_unlink(cls, v: int | None) -> int | None:
        if v is not None:
            raise ValueError("규칙을 품질 지표에 연결하려면 지표의 할당을 쓰세요 (여기서는 null 로 연결만 끊을 수 있습니다)")
        return v

    _v_check_type = field_validator("check_type")(_check_check_type)
    _v_severity = field_validator("severity")(_check_severity)

    @field_validator("rule_name", "check_type", "severity", "is_active", mode="before")
    @classmethod
    def _not_null(cls, v):
        # NOT NULL 컬럼 — 명시적 null 은 DB 무결성 오류(409) 대신 422 로 거절한다(생략은 미변경)
        if v is None:
            raise ValueError("null 로 바꿀 수 없습니다 (변경하지 않으려면 필드를 생략)")
        return v

    @field_validator("is_active")
    @classmethod
    def _check_is_active(cls, v: str | None) -> str | None:
        if v is not None and v not in ("true", "false"):
            raise ValueError("is_active 는 'true' 또는 'false' 여야 합니다")
        return v


class QualityRuleResponse(BaseModel):
    """품질 규칙 응답."""
    id: int
    dataset_id: int
    rule_name: str
    check_type: str
    column_name: str | None = None
    expected_value: str | None = None
    threshold: float
    severity: str
    is_active: str
    # 적어 둔 도메인(없으면 None = 자동)과, 그것으로 정해진 품질 지표 키(Trino 진단 대상이 아닌 유형은 None)
    domain: str | None = None
    indicator: str | None = None
    # 할당된 품질 지표(없으면 직접 정한 규칙)
    definition_id: int | None = None
    definition_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _v_indicator(self):
        found = quality_indicators.indicator_of(self.check_type, self.domain, self.column_name)
        self.indicator = found.key if found else None
        return self


# ---------------------------------------------------------------------------
# 품질 지표 — 미리 정해 두고 컬럼에 할당하는 진단 기준 (definitions.py)
# ---------------------------------------------------------------------------

class QualityDefinitionWrite(BaseModel):
    """품질 지표 생성·수정 요청."""
    name: str = Field(..., min_length=1, max_length=255)
    category: str                        # 분류 — indicators.INDICATORS 의 키
    check_type: str | None = None        # 분류에 유형이 하나면 생략
    expected_value: str | None = None
    threshold: float = Field(100.0, ge=0, le=100)
    severity: str = "WARNING"
    description: str | None = None

    _v_severity = field_validator("severity")(_check_severity)

    @model_validator(mode="after")
    def _v_shape(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("이름을 적어야 합니다")
        category = quality_indicators.get(self.category)
        if category is None or category not in quality_indicators.definable():
            keys = ", ".join(i.key for i in quality_indicators.definable())
            raise ValueError(f"미리 정의할 수 있는 진단 기준이 아닙니다: {self.category} ({keys})")
        self.category = category.key
        self.check_type = self.check_type or category.check_types[0]
        if self.check_type not in category.check_types:
            raise ValueError(f"{category.name} 진단 기준의 유형이 아닙니다: {self.check_type} ({', '.join(category.check_types)})")
        expected = (self.expected_value or "").strip()
        self.expected_value = expected or None
        if self.check_type in ("DATE_FORMAT", "REGEX", "ACCEPTED_VALUES", "MIN_VALUE", "MAX_VALUE", "FUNCTION") and not expected:
            raise ValueError("기준값을 적어야 합니다")
        if self.check_type in ("MIN_VALUE", "MAX_VALUE"):
            try:
                float(expected)
            except ValueError:
                raise ValueError(f"기준값은 숫자여야 합니다: {expected}") from None
        if self.check_type == "ACCEPTED_VALUES" and not rule_sql.accepted_values(expected):
            raise ValueError("허용 값을 하나 이상 적어야 합니다")
        rule_sql.check_expression(self.check_type, self.expected_value)
        return self


class QualityDefinitionResponse(BaseModel):
    """품질 지표 응답 — 할당 수 포함."""
    id: int
    name: str
    category: str
    category_name: str = ""
    area: str = ""
    area_name: str = ""
    check_type: str
    expected_value: str | None = None
    threshold: float
    severity: str
    description: str | None = None
    rules: int = 0        # 할당된 컬럼(규칙) 수
    datasets: int = 0     # 그 컬럼들이 있는 데이터셋 수
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _v_names(self):
        category = quality_indicators.get(self.category)
        if category:
            self.category_name, self.area = category.name, category.area
            self.area_name = quality_indicators.AREAS.get(category.area, category.area)
        return self


class QualityColumnRef(BaseModel):
    dataset_id: int
    column_name: str = Field(..., min_length=1, max_length=256)


class QualityAssignRequest(BaseModel):
    """지표를 할당할 컬럼들."""
    columns: list[QualityColumnRef] = Field(..., min_length=1, max_length=2000)


class QualityAssignSkipped(BaseModel):
    dataset_id: int
    column_name: str
    reason: str


class QualityAssignResult(BaseModel):
    """할당 결과. ``adopted`` 는 같은 기준으로 직접 만들어 둔 규칙을 지표에 연결한 수."""
    created: int = 0
    adopted: int = 0
    skipped: list[QualityAssignSkipped] = Field(default_factory=list)


class QualityUnassignRequest(BaseModel):
    rule_ids: list[int] = Field(..., min_length=1, max_length=2000)


class QualityAssignment(BaseModel):
    """지표가 할당된 컬럼 하나(= 규칙 하나)."""
    rule_id: int
    dataset_id: int
    dataset_name: str | None = None
    datasource_name: str | None = None   # 이름이 같은 데이터셋을 가르는 원본 데이터 소스
    column_name: str | None = None
    threshold: float
    is_active: str


class QualityColumnCandidate(BaseModel):
    """지표를 할당할 수 있는 컬럼. ``suggested`` 는 컬럼 이름·제약으로 보아 이 지표가 맞을 법한 것."""
    dataset_id: int
    dataset_name: str
    datasource_name: str | None = None   # 이름이 같은 데이터셋을 가르는 원본 데이터 소스
    column_name: str
    field_type: str | None = None
    suggested: bool = False
    assigned: bool = False


class QualitySqlPreviewRequest(BaseModel):
    """진단 쿼리 미리보기 — 저장하기 전의 지표·규칙에도 쓴다. 데이터셋을 주면 그 테이블·컬럼 타입으로 만든다."""
    check_type: str
    expected_value: str | None = None
    column_name: str | None = None
    threshold: float = Field(100.0, ge=0, le=100)
    dataset_id: int | None = None

    _v_check_type = field_validator("check_type")(_check_check_type)


class QualitySqlPreview(BaseModel):
    sql: str | None = None        # 실행할 Trino 쿼리. 만들 수 없으면 None(사유는 note)
    judgement: str = ""           # 쿼리 결과로 통과를 어떻게 판정하는지
    note: str | None = None       # 쿼리를 만들지 못한 사유, 예시 이름을 쓴 사유
    table: str = ""               # 쿼리에 쓴 테이블
    actual_table: bool = False    # 실제 Trino 테이블 이름인가(아니면 예시 이름)


# ---------------------------------------------------------------------------
# 품질 결과
# ---------------------------------------------------------------------------

class QualityResultResponse(BaseModel):
    """규칙 1건의 평가 결과.

    - ``passed``: "true"/"false". ``None`` 이면 **미평가** — 이번 진단에서 평가하지 않았고(경로에서 빠지는 유형,
      고르지 않은 지표) 이전 결과도 없어 점수 분모에서 제외된 규칙. 이때 ``id``·``checked_at`` 도 ``None``.
    - ``carried_forward``: True 면 이번 실행에서 새로 평가하지 않고 그 규칙의 직전 결과를 그대로 가져와
      점수에 반영한 것. ``checked_at`` 은 원래 평가 시각.
    """
    id: int | None = None
    rule_id: int
    rule_name: str | None = None
    check_type: str | None = None
    column_name: str | None = None
    dataset_id: int
    passed: str | None = None
    actual_value: str | None = None
    detail: str | None = None
    severity: str | None = None
    failed_samples: list[dict] | None = None
    # 진단 건수와 오류 건수 — 건수를 세지 않는 평가는 None
    checked_count: int | None = None
    error_count: int | None = None
    checked_at: datetime | None = None
    carried_forward: bool = False
    # 규칙 유형이 속한 품질 지표 키(app/quality/indicators.py) — Trino 진단 대상이 아닌 유형은 None
    indicator: str | None = None
    # 이 결과를 만든 진단 실행 — 예전 간이 진단 · 품질 배치 반입이 남긴 결과는 None
    run_id: int | None = None


# ---------------------------------------------------------------------------
# 품질 점수
# ---------------------------------------------------------------------------

class QualityScoreResponse(BaseModel):
    """품질 점수 기록 응답."""
    id: int
    dataset_id: int
    score: float
    total_rules: int
    passed_rules: int
    warning_rules: int
    failed_rules: int
    scored_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 검사 실행 응답
# ---------------------------------------------------------------------------

class RunCheckResponse(BaseModel):
    """진단 결과 저장(``service.import_results``)의 점수와 규칙별 결과. total_rules 는 미평가를 뺀 평가 수."""
    dataset_id: int
    score: float
    total_rules: int
    passed: int
    failed: int
    results: list[QualityResultResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 진단 결과 — 품질 워커의 정밀 진단 결과를 execution 이 이 모양으로 바꿔 저장한다
# ---------------------------------------------------------------------------

class ResultImportItem(BaseModel):
    """규칙 결과 1건. ``failed_samples`` 는 위반 행 샘플(최대 5행) — 품질 탭 "위반 샘플" 로 표시."""
    rule_id: int
    passed: bool
    actual_value: str | None = None
    detail: str | None = None
    failed_samples: list[dict] | None = None
    checked_count: int | None = Field(None, ge=0)
    error_count: int | None = Field(None, ge=0)


class ResultsImportRequest(BaseModel):
    """규칙 결과 묶음.

    빈 목록(예전엔 100점/GOOD 으로 기록)과 같은 rule_id 중복(예전엔 점수에 두 번 반영)은 422.
    """
    results: list[ResultImportItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_rule_ids(self) -> "ResultsImportRequest":
        seen: set[int] = set()
        # seen.add() 는 None 을 반환하므로, 처음 보는 id 는 집합에 넣고 거르고 두 번째부터만 남긴다.
        dups = sorted({r.rule_id for r in self.results if r.rule_id in seen or seen.add(r.rule_id)})
        if dups:
            raise ValueError(f"같은 rule_id 가 중복되었습니다: {dups}")
        return self


# ---------------------------------------------------------------------------
# 진단 실행 — 품질 워커가 처리하는 대기열 (app/quality/runs.py)
# ---------------------------------------------------------------------------

class QualityEngineStatus(BaseModel):
    """이 데이터셋을 정밀 진단할 수 있는지와, 안 되면 그 이유.

    ``engine`` 은 경로 — ``TRINO``(Trino 연결로 SQL) 또는 ``DIRECT``(품질 워커가 원천 DB 를 직접 읽음). ``DIRECT`` 면
    ``engine_datasource_*`` 는 원천 데이터 소스다.
    """
    available: bool
    reason: str | None = None
    engine: str = "TRINO"
    engine_datasource_id: int | None = None
    engine_datasource_name: str | None = None
    engine_table: str | None = None
    # 마지막 heartbeat 가 살아 있는 워커 수 — 0 이면 실행을 요청해도 대기 상태로 남는다
    workers_alive: int = 0
    # 이 경로가 평가하는 규칙 유형 — 품질 탭이 경로에서 빠지는 규칙을 표시한다(비어 있으면 진단할 수 없음)
    check_types: list[str] = Field(default_factory=list)


class QualityIndicatorResponse(BaseModel):
    """품질 지표 1개 — 품질 진단 작업·진단 실행이 "어떤 품질을 측정할지" 고르는 단위."""
    key: str
    name: str
    area: str        # 진단 영역 키 (COMPLETENESS/VALIDITY/CONSISTENCY/OTHER)
    area_name: str
    check_types: list[str]
    description: str
    # 같은 유형을 나누는 도메인 — 이 지표로 세려면 규칙이 가져야 하는 도메인(없으면 유형만으로 정해짐)
    domain: str | None = None
    # 이 분류로 품질 지표를 미리 정의해 컬럼에 할당할 수 있는가(definitions.py) — 아니면 데이터셋에서 직접 규칙을 만든다
    definable: bool = False
    # AI-Ready 품질 지표(가이드라인 §2.3) — 지표 키(AI_VALIDITY 등)·이름·세부 지표. 건수처럼 해당 없으면 None
    ai_ready: str | None = None
    ai_ready_name: str | None = None
    ai_ready_detail: str | None = None


class QualityRunCreate(BaseModel):
    """진단 실행 요청. ``indicators`` 를 비우면 이 경로로 평가할 수 있는 활성 규칙 전부.

    ``kind`` 가 ``PROFILE`` 이면 규칙 대신 컬럼 통계(프로파일)를 수집한다 — ``indicators`` 는 쓰지 않는다.
    """
    kind: Literal["CHECK", "PROFILE"] = "CHECK"
    indicators: list[str] = Field(default_factory=list, max_length=50)


class QualityRunQuery(BaseModel):
    """진단 실행이 Trino 에 낸 쿼리 하나."""
    key: str
    label: str
    sql: str
    # 같은 쿼리를 읽기 좋게 — 식마다 줄을 바꾸고 규칙 이름을 주석으로 단 것
    formatted_sql: str
    # 순서로 짝지은 Trino 쿼리 ID — 아직 실행하지 않았거나 짝지을 수 없으면 None
    query_id: str | None = None
    # 묶은 스캔 쿼리가 실패해 규칙별로 다시 낸 쿼리면 True
    retry: bool = False


class QualityRunResponse(BaseModel):
    """진단 실행 1회의 상태·진행·결과."""
    id: int
    dataset_id: int
    # 이 실행을 넣은 작업 실행 (직접 요청한 실행은 None)
    job_run_id: int | None = None
    # 그 작업 실행의 작업 (목록 화면용)
    job_id: int | None = None
    job_name: str | None = None
    # 측정한 품질 지표 키 — 빈 목록이면 전체
    indicators: list[str] = Field(default_factory=list)

    @field_validator("indicators", mode="before")
    @classmethod
    def _indicators(cls, v):
        # 행에는 쉼표 구분 문자열(또는 NULL)로 들어 있다
        if v is None or v == "":
            return []
        return [k for k in v.split(",") if k] if isinstance(v, str) else v

    engine: str
    # CHECK(규칙 진단) / PROFILE(프로파일 — 컬럼 통계 수집)
    kind: str = "CHECK"
    engine_datasource_id: int | None = None
    engine_table: str | None = None
    trigger_type: str
    status: str
    # RUNNING 인데 워커 보고가 한동안 없으면 True (STALE_SECONDS 가 지나면 FAILED 로 바뀐다)
    unresponsive: bool = False
    current_target: str | None = None
    progress_done: int = 0
    progress_total: int | None = None
    rules_total: int = 0
    rules_evaluated: int = 0
    checked_count: int | None = None
    error_count: int | None = None
    # 오류율(%) = 오류 건수 / 진단 건수 × 100 — 진단 건수가 없으면 None
    error_rate: float | None = None
    score: float | None = None
    query_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None
    requested_by: str | None = None
    # 고른 품질 지표(미리 정의한 지표) ID 와 이름 — 비어 있으면 가리지 않는다. 지운 지표는 "삭제된 지표 #ID"
    definition_ids: list[int] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list)

    @field_validator("definition_ids", mode="before")
    @classmethod
    def _definition_ids(cls, v):
        return _id_list(v)

    @field_validator("query_ids", mode="before")
    @classmethod
    def _query_ids(cls, v):
        # 행에는 JSON 문자열(또는 NULL)로 들어 있다
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                return list(json.loads(v))
            except ValueError:
                return []
        return v

    cancel_requested_by: str | None = None
    worker_id: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    # 목록 화면용 — 데이터셋과 그 원본 데이터 소스, 엔진(Trino) 데이터 소스 이름
    dataset_name: str | None = None
    datasource_id: int | None = None
    datasource_name: str | None = None
    engine_datasource_name: str | None = None

    model_config = {"from_attributes": True}


class QualityWorkerResponse(BaseModel):
    """품질 워커 1개의 상태."""
    worker_id: str
    hostname: str | None = None
    version: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    current_run_id: int | None = None
    alive: bool = False


class QualityServiceStatus(BaseModel):
    """데이터 품질 화면 상단의 상태 — 살아 있는 워커와 대기열."""
    workers_alive: int = 0
    running: int = 0
    queued: int = 0
    workers: list[QualityWorkerResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 워커용 API (/quality/worker/*) — 품질 워커(extensions/argus-catalog-quality-worker)만 부른다
# ---------------------------------------------------------------------------

class WorkerIdentity(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    hostname: str | None = Field(None, max_length=255)
    version: str | None = Field(None, max_length=50)


class WorkerHeartbeat(WorkerIdentity):
    run_id: int | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    current_target: str | None = None
    query_ids: list[str] = Field(default_factory=list)


class WorkerQueryResult(BaseModel):
    """쿼리 하나의 결과 — 성공이면 ``row``(첫 행), 실패면 ``error``."""
    key: str
    row: list | None = None
    error: str | None = None
    # Trino 의 errorType — USER_ERROR 면 규칙·쿼리가 잘못된 것(규칙 실패로 저장), 그 밖은 진단하지 못한 것
    error_type: str | None = None
    # 원천 직접 진단의 읽기 요약(key="direct") — 읽은 행 수, 워커의 행 수 상한에 걸렸는지
    rows_read: int | None = None
    truncated: bool | None = None
    # 커스텀 Python 의 판정 — 플러그인이 돌려준 {"passed", "actual", "detail"}
    outcome: dict | None = None
    # 여러 행을 돌려주는 쿼리(프로파일의 최빈값, 위반 샘플)의 행들과 그 컬럼 이름
    rows: list[list] | None = Field(default=None, max_length=100)
    columns: list[str] | None = Field(default=None, max_length=1000)
    # 원천 직접 진단이 읽는 동안 모은 위반 행(값은 문자열)과 그 컬럼 이름
    samples: list[list] | None = Field(default=None, max_length=20)
    sample_columns: list[str] | None = Field(default=None, max_length=1000)


class WorkerComplete(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    results: list[WorkerQueryResult] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)


class WorkerCompleteResponse(QualityRunResponse):
    """결과 제출 응답 — 끝난 실행과, 워커가 이어서 낼 위반 샘플 쿼리(``{key, label, sql, catalog?, schema?}``)."""
    samples: list[dict] = Field(default_factory=list)


class WorkerSamples(BaseModel):
    """위반 샘플 제출 — 결과마다 ``{key: "rule:<id>", columns, rows}`` 또는 ``{key, error}``."""
    worker_id: str = Field(min_length=1, max_length=200)
    results: list[WorkerQueryResult] = Field(default_factory=list, max_length=100)


class WorkerFinish(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    outcome: Literal["FAILED", "CANCELLED", "REQUEUE"]
    message: str | None = None
    query_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 품질 진단 작업
# ---------------------------------------------------------------------------

class QualityJobTarget(BaseModel):
    """품질 진단 작업의 대상 하나. 카탈로그·스키마는 실행 시점에 데이터셋으로 푼다."""
    type: str                       # catalog / schema / dataset
    catalog_id: int | None = None   # catalog·schema
    schema_name: str | None = Field(None, max_length=255)  # schema
    dataset_id: int | None = None   # dataset
    # 응답에서만 — 화면에 보일 이름(카탈로그, 카탈로그.스키마, 데이터셋)
    label: str | None = None


class QualityJobWrite(BaseModel):
    """품질 진단 작업 등록·수정."""
    name: str = Field(..., max_length=200)
    description: str | None = Field(None, max_length=2000)
    targets: list[QualityJobTarget]
    # 측정할 분류 키(관리지침 진단 기준) — 비우면 가리지 않는다
    indicators: list[str] = Field(default_factory=list, max_length=50)
    # 측정할 품질 지표(미리 정의한 지표) ID — 비우면 전체(지표 없이 직접 만든 규칙 포함)
    definition_ids: list[int] = Field(default_factory=list, max_length=300)
    schedule_type: str = "NONE"     # NONE / HOURLY / DAILY / WEEKLY / MONTHLY
    schedule_time: str | None = None            # HH:MM
    schedule_day: int | None = None             # WEEKLY 0(월)~6(일), MONTHLY 1~31
    schedule_interval_hours: int | None = None  # HOURLY
    timezone: str = Field("Asia/Seoul", max_length=64)
    enabled: bool = True
    include_profile: bool = False   # 진단과 함께 프로파일(컬럼 통계)도 대기열에 넣는다


class QualityJobSkipped(BaseModel):
    """작업 실행에서 진단을 시작하지 못한 데이터셋과 사유."""
    dataset_id: int
    name: str | None = None
    reason: str


class QualityJobRunResponse(BaseModel):
    """작업 실행 1회 — 작업 이력 한 줄."""
    id: int
    job_id: int
    job_name: str | None = None
    trigger_type: str
    status: str   # RUNNING / SUCCEEDED / PARTIAL / FAILED / CANCELLED / SKIPPED
    indicators: list[str] = Field(default_factory=list)
    # 고른 품질 지표(미리 정의한 지표) ID 와 이름 — 비어 있으면 가리지 않는다. 지운 지표는 "삭제된 지표 #ID"
    definition_ids: list[int] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list)
    targets_total: int = 0      # 대상 데이터셋 수
    runs_total: int = 0         # 대기열에 넣은 데이터셋 실행 수
    runs_succeeded: int = 0
    runs_failed: int = 0
    runs_active: int = 0        # 아직 끝나지 않은 데이터셋 실행 수
    # 아래는 이 작업 실행이 넣은 데이터셋 실행에서 모은 값 — 작업 이력 목록이 실행 현황과 같은 열을 보여 준다
    run_id: int | None = None           # 데이터셋 실행이 하나뿐이면 그 실행(상세를 바로 연다)
    dataset_id: int | None = None       # 〃 그 데이터셋
    dataset_name: str | None = None
    engine_table: str | None = None
    rules_total: int = 0                # Σ 활성 규칙 수
    rules_evaluated: int = 0            # Σ 평가한 규칙 수
    progress_done: int = 0              # Σ 끝난 쿼리 수
    progress_total: int | None = None   # Σ 전체 쿼리 수 (아직 계획이 없으면 None)
    current_target: str | None = None   # 지금 실행 중인 쿼리 설명
    score: float | None = None          # 데이터셋 실행의 품질 점수 평균
    no_rules: int = 0           # 고른 지표의 규칙이 없어 진단하지 않은 데이터셋 수
    skipped_total: int = 0      # 진단을 시작하지 못한 데이터셋 수
    skipped: list[QualityJobSkipped] = Field(default_factory=list)
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None
    # 함께 넣은 프로파일 실행(작업의 include_profile) — 진단 실행 수(runs_*)에는 들어가지 않는다
    profiles_total: int = 0
    profiles_succeeded: int = 0
    profiles_failed: int = 0
    message: str | None = None
    requested_by: str | None = None
    cancel_requested_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("indicators", mode="before")
    @classmethod
    def _indicators(cls, v):
        if v is None or v == "":
            return []
        return [k for k in v.split(",") if k] if isinstance(v, str) else v

    @field_validator("definition_ids", mode="before")
    @classmethod
    def _definition_ids(cls, v):
        return _id_list(v)

    model_config = {"from_attributes": True}


class QualityJobResponse(BaseModel):
    """품질 진단 작업 — 정의와 다음 실행 시각, 마지막 작업 실행."""
    id: int
    name: str
    description: str | None = None
    targets: list[QualityJobTarget] = Field(default_factory=list)
    indicators: list[str] = Field(default_factory=list)
    # 고른 품질 지표(미리 정의한 지표) ID 와 이름 — 비어 있으면 가리지 않는다. 지운 지표는 "삭제된 지표 #ID"
    definition_ids: list[int] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list)
    schedule_type: str
    schedule_time: str | None = None
    schedule_day: int | None = None
    schedule_interval_hours: int | None = None
    timezone: str
    enabled: bool
    include_profile: bool = False
    next_run_at: datetime | None = None
    last_run: QualityJobRunResponse | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("targets", mode="before")
    @classmethod
    def _targets(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("indicators", mode="before")
    @classmethod
    def _indicators(cls, v):
        if v is None or v == "":
            return []
        return [k for k in v.split(",") if k] if isinstance(v, str) else v

    @field_validator("definition_ids", mode="before")
    @classmethod
    def _definition_ids(cls, v):
        return _id_list(v)

    model_config = {"from_attributes": True}


class QualityJobPreviewRequest(BaseModel):
    targets: list[QualityJobTarget] = Field(default_factory=list)
    indicators: list[str] = Field(default_factory=list, max_length=50)
    definition_ids: list[int] = Field(default_factory=list, max_length=300)


class QualityJobPreview(BaseModel):
    """지금 이 대상·지표로 실행하면 — 대상 데이터셋 수와 그중 규칙이 있어 진단되는 수."""
    datasets_total: int = 0
    datasets_with_rules: int = 0


class QualityJobRuleItem(BaseModel):
    """대상 데이터셋에 있는 규칙 하나 — 등록 화면이 "이 지표로 실제 무엇을 보는지" 보여 준다."""
    rule_id: int
    rule_name: str
    dataset_id: int
    dataset_name: str | None = None
    check_type: str
    column_name: str | None = None
    expected_value: str | None = None   # 날짜 형식·허용 값·범위·정규식·조건 등 사용자가 정한 기준
    definition_id: int | None = None    # 할당된 품질 지표(없으면 직접 정한 규칙)


class QualityJobIndicatorRules(BaseModel):
    """지표 하나에 대해 대상 데이터셋이 가진 활성 규칙."""
    indicator: str
    # 품질 지표로 묶었을 때(group_by=definition) 그 지표 ID — 지표 없이 직접 만든 규칙의 묶음은 None
    definition_id: int | None = None
    rules_total: int = 0
    datasets: int = 0                   # 이 지표의 규칙이 있는 데이터셋 수
    rules: list[QualityJobRuleItem] = Field(default_factory=list)   # 앞에서부터 일부만
    truncated: bool = False             # rules 가 rules_total 보다 적다


class QualityJobRulesRequest(BaseModel):
    targets: list[QualityJobTarget] = Field(default_factory=list)
    # 묶는 기준 — indicator(분류) 또는 definition(미리 정의한 품질 지표)
    group_by: Literal["indicator", "definition"] = "indicator"


class QualitySchemaOption(BaseModel):
    """대상 선택기 — 카탈로그의 스키마와 데이터셋 수."""
    schema_name: str
    datasets: int = 0


# ---------------------------------------------------------------------------
# 품질 배치 리포트
# ---------------------------------------------------------------------------

class QualityReportGroup(BaseModel):
    """영역 또는 지표 하나의 합계."""
    key: str
    name: str
    area: str | None = None        # 지표일 때 — 속한 영역
    area_name: str | None = None
    ai_ready_detail: str | None = None   # 진단 기준일 때 — AI-Ready 세부 지표(예: 형식 유효성)
    rules: int = 0                 # 평가한 규칙 수
    failed_rules: int = 0
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None


class QualityReportDataset(BaseModel):
    """작업 실행이 진단한 데이터셋 하나."""
    dataset_id: int
    dataset_name: str | None = None
    datasource_name: str | None = None
    engine_table: str | None = None
    run_id: int
    status: str
    rules: int = 0
    failed_rules: int = 0
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None
    error_message: str | None = None


class QualityReportRule(BaseModel):
    """작업 실행에서 평가한 규칙 하나의 결과."""
    dataset_id: int
    dataset_name: str | None = None
    run_id: int | None = None
    rule_id: int
    rule_name: str | None = None
    check_type: str | None = None
    column_name: str | None = None
    indicator: str | None = None
    indicator_name: str | None = None
    definition_name: str | None = None   # 할당된 품질 지표(직접 정한 규칙은 None)
    severity: str | None = None
    passed: bool
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None
    detail: str | None = None


class QualityReportPrevious(BaseModel):
    """같은 작업의 직전 회차(건너뛴 차례·실행 중 제외) — 리포트의 증감 표시에 쓴다."""
    job_run_id: int
    started_at: datetime | None = None
    status: str
    score: float | None = None
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None
    rules_total: int = 0
    failed_rules: int = 0


class QualityJobRunReport(BaseModel):
    """작업 실행 한 번의 리포트 — 요약, 영역·지표별, AI-Ready 지표별, 데이터셋별(오류율 높은 순), 규칙별."""
    job_run: QualityJobRunResponse
    areas: list[QualityReportGroup] = Field(default_factory=list)
    indicators: list[QualityReportGroup] = Field(default_factory=list)
    # 같은 결과를 AI-Ready 품질 지표(가이드라인 §2.3 — 완전성·일관성·정확성·적시성·유효성·유일성)로 묶은 합계.
    # 진단 기준 그룹은 area·area_name 에 AI-Ready 지표를, ai_ready_detail 에 세부 지표를 담는다
    ai_ready_areas: list[QualityReportGroup] = Field(default_factory=list)
    ai_ready_indicators: list[QualityReportGroup] = Field(default_factory=list)
    datasets: list[QualityReportDataset] = Field(default_factory=list)
    rules: list[QualityReportRule] = Field(default_factory=list)
    rules_total: int = 0
    failed_rules: int = 0
    # 이 실행이 평가했지만 규칙이 지워져 결과가 남지 않은 수 — 표와 요약 건수가 그만큼 다를 수 있다
    rules_missing: int = 0
    previous: QualityReportPrevious | None = None


class QualityJobTrendPoint(BaseModel):
    """추이의 한 점 — 작업 실행 한 번."""
    job_run_id: int
    started_at: datetime | None = None
    status: str
    runs_total: int = 0
    runs_succeeded: int = 0
    runs_failed: int = 0
    checked_count: int | None = None
    error_count: int | None = None
    error_rate: float | None = None
    # 지표 키 → 오류율(%). 그 회차에 평가하지 않은 지표는 없다
    indicators: dict[str, float | None] = Field(default_factory=dict)


class QualityJobTrend(BaseModel):
    """품질 진단 작업의 회차별 오류율 — 오래된 것부터."""
    job_id: int
    job_name: str
    points: list[QualityJobTrendPoint] = Field(default_factory=list)
