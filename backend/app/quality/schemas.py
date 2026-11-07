# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 Pydantic 스키마.

프로파일링·품질 규칙·검사 결과·점수에 대한 요청/응답 모델.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # 원천 DB 접속 실패로 스키마 기반 프로파일(행 수 0, 통계 없음)로 대체됐는지.
    # 저장 형식에는 남지 않으므로 프로파일 실행 응답에서만 의미가 있다(조회 응답은 항상 False).
    fallback: bool = False
    fallback_reason: str | None = None


# ---------------------------------------------------------------------------
# 품질 규칙
# ---------------------------------------------------------------------------

# 서버 평가(service._evaluate_rule)·품질 배치(quality/*.py)·품질 탭이 지원하는 검증 유형
SUPPORTED_CHECK_TYPES = (
    "NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ROW_COUNT", "FRESHNESS",
    "ACCEPTED_VALUES", "REGEX", "CUSTOM_SQL", "CUSTOM_PYTHON",
)
# 심각도 — 알림 평가(alert.service._SEVERITY_RANK)·품질 탭 선택지와 동일
SUPPORTED_SEVERITIES = ("BREAKING", "WARNING", "INFO")


def _check_check_type(v: str | None) -> str | None:
    """검증 유형이 SUPPORTED_CHECK_TYPES 중 하나인지 확인(아니면 422)."""
    if v is not None and v not in SUPPORTED_CHECK_TYPES:
        raise ValueError(f"지원하지 않는 검증 유형: {v} ({', '.join(SUPPORTED_CHECK_TYPES)})")
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
    expected_value: str | None = None   # 유형별 해석 문자열(숫자·쉼표 구분 허용 값·정규식·SQL, CUSTOM_PYTHON 은 JSON)
    # 통과 임계값 % (0~100) — NOT_NULL·UNIQUE 만 사용. 0 도 유효한 값(0% 이상 = 항상 통과)
    threshold: float = Field(100.0, ge=0, le=100)
    severity: str = "WARNING"

    _v_check_type = field_validator("check_type")(_check_check_type)
    _v_severity = field_validator("severity")(_check_severity)


class QualityRuleUpdate(BaseModel):
    """품질 규칙 부분 수정 요청 — 명시한 필드만 반영(exclude_unset)."""
    rule_name: str | None = Field(None, min_length=1, max_length=255)
    check_type: str | None = None
    column_name: str | None = None
    expected_value: str | None = None
    threshold: float | None = Field(None, ge=0, le=100)
    severity: str | None = None
    is_active: str | None = None

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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 품질 결과
# ---------------------------------------------------------------------------

class QualityResultResponse(BaseModel):
    """규칙 1건의 평가 결과.

    - ``passed``: "true"/"false". ``None`` 이면 **미평가** — 서버가 평가할 수 없는 유형
      (ACCEPTED_VALUES·REGEX·CUSTOM_SQL·CUSTOM_PYTHON)인데 이전 결과(품질 배치 반입)도 없어
      점수 분모에서 제외된 규칙. 이때 ``id``·``checked_at`` 도 ``None`` (저장된 결과 행이 없음).
    - ``carried_forward``: True 면 이번 실행에서 새로 평가하지 않고 그 규칙의 직전 결과
      (주로 품질 배치가 반입한 결과)를 그대로 가져와 점수에 반영한 것. ``checked_at`` 은 원래 평가 시각.
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
    checked_at: datetime | None = None
    carried_forward: bool = False


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
    """검증 실행·결과 반입 응답 — 점수와 규칙별 결과. total_rules 는 미평가를 뺀 평가 수."""
    dataset_id: int
    score: float
    total_rules: int
    passed: int
    failed: int
    results: list[QualityResultResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 외부 반입 — PySpark 등 외부 엔진이 계산한 프로파일/검증 결과 반입
# ---------------------------------------------------------------------------

class ProfileImportRequest(BaseModel):
    """외부 엔진에서 계산한 프로파일 반입 요청.

    컬럼 통계는 서버 프로파일과 같은 형식(ColumnProfile). ``total_count``·``null_count``·
    ``unique_count`` 를 채우면 이후 서버 검증의 NOT_NULL·UNIQUE 도 정확한 건수로 판정된다.
    """
    row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)


class ResultImportItem(BaseModel):
    """외부 엔진에서 평가한 규칙 결과 1건.

    ``failed_samples`` 는 위반 행 샘플(품질 배치는 최대 5행, 값은 문자열) — 품질 탭 "위반 샘플" 로 표시.
    """
    rule_id: int
    passed: bool
    actual_value: str | None = None
    detail: str | None = None
    failed_samples: list[dict] | None = None


class ResultsImportRequest(BaseModel):
    """외부 엔진 검증 결과 일괄 반입 요청.

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
