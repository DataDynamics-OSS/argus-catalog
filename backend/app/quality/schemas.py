"""데이터 품질 Pydantic 스키마.

프로파일링·품질 규칙·검사 결과·점수에 대한 요청/응답 모델.
"""

from datetime import datetime

from pydantic import BaseModel, Field


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
    id: int
    dataset_id: int
    row_count: int
    columns: list[ColumnProfile] = Field(default_factory=list)
    profiled_at: datetime


# ---------------------------------------------------------------------------
# 품질 규칙
# ---------------------------------------------------------------------------

class QualityRuleCreate(BaseModel):
    dataset_id: int
    rule_name: str = Field(..., min_length=1, max_length=255)
    check_type: str        # NOT_NULL, UNIQUE, MIN_VALUE, MAX_VALUE, ACCEPTED_VALUES, REGEX, ROW_COUNT, FRESHNESS
    column_name: str | None = None
    expected_value: str | None = None   # JSON 문자열
    threshold: float = 100.0
    severity: str = "WARNING"


class QualityRuleUpdate(BaseModel):
    rule_name: str | None = None
    check_type: str | None = None
    column_name: str | None = None
    expected_value: str | None = None
    threshold: float | None = None
    severity: str | None = None
    is_active: str | None = None


class QualityRuleResponse(BaseModel):
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
    id: int
    rule_id: int
    rule_name: str | None = None
    check_type: str | None = None
    column_name: str | None = None
    dataset_id: int
    passed: str
    actual_value: str | None = None
    detail: str | None = None
    severity: str | None = None
    failed_samples: list[dict] | None = None
    checked_at: datetime


# ---------------------------------------------------------------------------
# 품질 점수
# ---------------------------------------------------------------------------

class QualityScoreResponse(BaseModel):
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
    """외부 엔진에서 계산한 프로파일 반입 요청."""
    row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)


class ResultImportItem(BaseModel):
    """외부 엔진에서 평가한 규칙 결과 1건."""
    rule_id: int
    passed: bool
    actual_value: str | None = None
    detail: str | None = None
    failed_samples: list[dict] | None = None


class ResultsImportRequest(BaseModel):
    """외부 엔진 검증 결과 일괄 반입 요청."""
    results: list[ResultImportItem] = Field(default_factory=list)
