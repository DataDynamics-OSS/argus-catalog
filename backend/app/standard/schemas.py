# SPDX-License-Identifier: Apache-2.0
"""데이터 표준 Pydantic 스키마.

입력 검증 원칙:
- 문자열 길이 제한은 DB 컬럼 길이와 맞춘다(초과 시 DB 오류로 500 이 나지 않도록 422 로 먼저 거른다).
- 이름류는 앞뒤 공백을 지우고, 공백만 있는 값은 거부한다.
- 수정(Update) 스키마에서 NOT NULL 컬럼에 명시적 null 을 보내면 422.
- 상태·유형·여부 값은 허용 목록(Literal)으로 제한한다.
- 작성자(created_by)·변경자(changed_by)는 클라이언트 입력을 받지 않고 서버가 토큰 사용자로 채운다.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Status = Literal["ACTIVE", "INACTIVE", "DEPRECATED"]
WordType = Literal["GENERAL", "SUFFIX", "PREFIX"]
BoolStr = Literal["true", "false"]
MappingType = Literal["MATCHED", "SIMILAR", "VIOLATION"]

# 물리명 — 소문자·숫자·밑줄, 문자 또는 밑줄로 시작 (DB 컬럼명으로 그대로 쓰이는 값)
PHYSICAL_NAME_PATTERN = r"^[a-z_][a-z0-9_]*$"


def _strip_required(v: str | None) -> str | None:
    """필수 문자열 — 앞뒤 공백 제거, 공백만 있으면 422(None 은 통과시켜 _reject_nulls 가 판단)."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("값을 입력하세요.")
    return v


def _strip_optional(v: str | None) -> str | None:
    """선택 문자열 — 앞뒤 공백 제거, 빈 값은 null."""
    if v is None:
        return v
    v = v.strip()
    return v or None


def _reject_nulls(model: BaseModel, fields: tuple[str, ...]) -> None:
    """명시적으로 보낸(fields_set) NOT NULL 필드가 null 이면 422. 생략은 미변경으로 허용."""
    for f in fields:
        if f in model.model_fields_set and getattr(model, f) is None:
            raise ValueError(f"{f} 는 비울 수 없습니다.")


# ---------------------------------------------------------------------------
# 사전
# ---------------------------------------------------------------------------

class DictionaryCreate(BaseModel):
    dict_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    version: str | None = Field(None, max_length=50)
    effective_date: date | None = None
    expiry_date: date | None = None

    _n = field_validator("dict_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("version", "description")(classmethod(lambda cls, v: _strip_optional(v)))


class DictionaryUpdate(BaseModel):
    dict_name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    version: str | None = Field(None, max_length=50)
    status: Status | None = None
    effective_date: date | None = None
    expiry_date: date | None = None

    _n = field_validator("dict_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("version", "description")(classmethod(lambda cls, v: _strip_optional(v)))

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_nulls(self, ("dict_name", "status"))
        return self


class DictionaryResponse(BaseModel):
    id: int
    dict_name: str
    description: str | None = None
    version: str | None = None
    status: str
    effective_date: date | None = None
    expiry_date: date | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    word_count: int = 0
    domain_count: int = 0
    term_count: int = 0
    code_group_count: int = 0
    mapping_count: int = 0          # 이 사전 용어로 매핑된 컬럼 수 (삭제 영향 안내용)


# ---------------------------------------------------------------------------
# 단어
# ---------------------------------------------------------------------------

class WordCreate(BaseModel):
    dictionary_id: int
    word_name: str = Field(..., min_length=1, max_length=100)
    word_english: str = Field(..., min_length=1, max_length=100)
    word_abbr: str = Field(..., min_length=1, max_length=50)
    description: str | None = None
    word_type: WordType = "GENERAL"
    is_forbidden: BoolStr = "false"
    synonym_group_id: int | None = Field(None, ge=1)

    _n = field_validator("word_name", "word_english", "word_abbr")(
        classmethod(lambda cls, v: _strip_required(v)))


class WordUpdate(BaseModel):
    word_name: str | None = Field(None, min_length=1, max_length=100)
    word_english: str | None = Field(None, min_length=1, max_length=100)
    word_abbr: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    word_type: WordType | None = None
    is_forbidden: BoolStr | None = None
    synonym_group_id: int | None = Field(None, ge=1)
    status: Status | None = None

    _n = field_validator("word_name", "word_english", "word_abbr")(
        classmethod(lambda cls, v: _strip_required(v)))

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_nulls(self, ("word_name", "word_english", "word_abbr", "word_type", "is_forbidden", "status"))
        return self


class WordResponse(BaseModel):
    id: int
    dictionary_id: int
    word_name: str
    word_english: str
    word_abbr: str
    description: str | None = None
    word_type: str
    is_forbidden: str
    synonym_group_id: int | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    term_count: int = 0             # 이 단어로 구성된 용어 수

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 도메인
# ---------------------------------------------------------------------------

class DomainCreate(BaseModel):
    dictionary_id: int
    domain_name: str = Field(..., min_length=1, max_length=100)
    domain_group: str | None = Field(None, max_length=100)
    data_type: str = Field(..., min_length=1, max_length=50)
    data_length: int | None = Field(None, ge=0)
    data_precision: int | None = Field(None, ge=0)
    data_scale: int | None = Field(None, ge=0)
    description: str | None = None
    code_group_id: int | None = None

    _n = field_validator("domain_name", "data_type")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("domain_group")(classmethod(lambda cls, v: _strip_optional(v)))
    _u = field_validator("data_type")(classmethod(lambda cls, v: v.upper() if v else v))


class DomainUpdate(BaseModel):
    domain_name: str | None = Field(None, min_length=1, max_length=100)
    domain_group: str | None = Field(None, max_length=100)
    data_type: str | None = Field(None, min_length=1, max_length=50)
    data_length: int | None = Field(None, ge=0)
    data_precision: int | None = Field(None, ge=0)
    data_scale: int | None = Field(None, ge=0)
    description: str | None = None
    code_group_id: int | None = None
    status: Status | None = None

    _n = field_validator("domain_name", "data_type")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("domain_group")(classmethod(lambda cls, v: _strip_optional(v)))
    _u = field_validator("data_type")(classmethod(lambda cls, v: v.upper() if v else v))

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_nulls(self, ("domain_name", "data_type", "status"))
        return self


class DomainResponse(BaseModel):
    id: int
    dictionary_id: int
    domain_name: str
    domain_group: str | None = None
    data_type: str
    data_length: int | None = None
    data_precision: int | None = None
    data_scale: int | None = None
    description: str | None = None
    code_group_id: int | None = None
    code_group_name: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    term_count: int = 0             # 이 도메인을 쓰는 용어 수


# ---------------------------------------------------------------------------
# 용어
# ---------------------------------------------------------------------------

class TermCreate(BaseModel):
    dictionary_id: int
    term_name: str = Field(..., min_length=1, max_length=200)
    term_english: str | None = Field(None, max_length=200)   # 생략 시 자동 생성
    term_abbr: str | None = Field(None, max_length=100)      # 생략 시 자동 생성
    physical_name: str | None = Field(None, max_length=100)  # 생략 시 자동 생성
    domain_id: int | None = None
    description: str | None = None

    _n = field_validator("term_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("term_english", "term_abbr")(classmethod(lambda cls, v: _strip_optional(v)))

    @field_validator("physical_name")
    @classmethod
    def _physical(cls, v: str | None) -> str | None:
        return _normalize_physical(_strip_optional(v))


class TermUpdate(BaseModel):
    term_name: str | None = Field(None, min_length=1, max_length=200)
    term_english: str | None = Field(None, min_length=1, max_length=200)
    term_abbr: str | None = Field(None, min_length=1, max_length=100)
    physical_name: str | None = Field(None, min_length=1, max_length=100)
    domain_id: int | None = None
    description: str | None = None
    status: Status | None = None

    _n = field_validator("term_name", "term_english", "term_abbr")(
        classmethod(lambda cls, v: _strip_required(v)))

    @field_validator("physical_name")
    @classmethod
    def _physical(cls, v: str | None) -> str | None:
        return _normalize_physical(_strip_required(v))

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_nulls(self, ("term_name", "term_english", "term_abbr", "physical_name", "status"))
        return self


def _normalize_physical(v: str | None) -> str | None:
    """물리명 — 소문자로 정규화 후 형식 검사."""
    import re

    if v is None:
        return v
    v = v.lower()
    if not re.match(PHYSICAL_NAME_PATTERN, v):
        raise ValueError("물리명은 영문 소문자·숫자·밑줄(_)만 쓸 수 있고 숫자로 시작할 수 없습니다.")
    return v


class TermWordInfo(BaseModel):
    word_id: int
    word_name: str
    word_english: str
    word_abbr: str
    word_type: str
    ordinal: int


class TermResponse(BaseModel):
    id: int
    dictionary_id: int
    term_name: str
    term_english: str
    term_abbr: str
    physical_name: str
    domain_id: int | None = None
    domain_name: str | None = None
    domain_data_type: str | None = None
    description: str | None = None
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    words: list[TermWordInfo] = Field(default_factory=list)
    mapping_count: int = 0


# ---------------------------------------------------------------------------
# 코드 그룹 / 값
# ---------------------------------------------------------------------------

class CodeGroupCreate(BaseModel):
    dictionary_id: int
    group_name: str = Field(..., min_length=1, max_length=200)
    group_english: str | None = Field(None, max_length=200)
    description: str | None = None

    _n = field_validator("group_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("group_english")(classmethod(lambda cls, v: _strip_optional(v)))


class CodeGroupUpdate(BaseModel):
    group_name: str | None = Field(None, min_length=1, max_length=200)
    group_english: str | None = Field(None, max_length=200)
    description: str | None = None
    status: Status | None = None

    _n = field_validator("group_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("group_english")(classmethod(lambda cls, v: _strip_optional(v)))

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_nulls(self, ("group_name", "status"))
        return self


class CodeValueCreate(BaseModel):
    code_value: str = Field(..., min_length=1, max_length=100)
    code_name: str = Field(..., min_length=1, max_length=200)
    code_english: str | None = Field(None, max_length=200)
    description: str | None = None
    sort_order: int = 0

    _n = field_validator("code_value", "code_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("code_english")(classmethod(lambda cls, v: _strip_optional(v)))


class CodeValueUpdate(BaseModel):
    """코드 값 부분 갱신 — 보낸 필드만 반영한다(UI 셀 편집은 바뀐 필드 하나만 보낸다)."""
    code_value: str | None = Field(None, min_length=1, max_length=100)
    code_name: str | None = Field(None, min_length=1, max_length=200)
    code_english: str | None = Field(None, max_length=200)
    description: str | None = None
    sort_order: int | None = None

    _n = field_validator("code_value", "code_name")(classmethod(lambda cls, v: _strip_required(v)))
    _o = field_validator("code_english")(classmethod(lambda cls, v: _strip_optional(v)))

    @model_validator(mode="after")
    def _no_null_required(self):
        # DB 상 NOT NULL 인 필드에 명시적 null 을 보내면 500 대신 422
        _reject_nulls(self, ("code_value", "code_name", "sort_order"))
        return self


class CodeValueResponse(BaseModel):
    id: int
    code_value: str
    code_name: str
    code_english: str | None = None
    description: str | None = None
    sort_order: int
    is_active: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CodeGroupResponse(BaseModel):
    id: int
    dictionary_id: int
    group_name: str
    group_english: str | None = None
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    values: list[CodeValueResponse] = Field(default_factory=list)
    domain_count: int = 0           # 이 코드 그룹을 쓰는 도메인 수

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 삭제 영향 (사용 현황)
# ---------------------------------------------------------------------------

class UsageRef(BaseModel):
    id: int
    name: str


class StandardUsage(BaseModel):
    """삭제 전 영향 범위 — 항목 종류에 따라 해당하는 목록만 채워진다."""
    terms: list[UsageRef] = Field(default_factory=list)      # 단어·도메인을 쓰는 용어
    domains: list[UsageRef] = Field(default_factory=list)    # 코드 그룹을 쓰는 도메인
    value_count: int = 0                                     # 코드 그룹의 코드 값 수
    mapping_count: int = 0                                   # 용어(또는 사전)로 매핑된 컬럼 수


# ---------------------------------------------------------------------------
# 용어-컬럼 매핑
# ---------------------------------------------------------------------------

class TermMappingCreate(BaseModel):
    term_id: int
    dataset_id: int
    schema_id: int
    mapping_type: MappingType = "MATCHED"


class TermMappingResponse(BaseModel):
    id: int
    term_id: int
    term_name: str | None = None
    dataset_id: int
    dataset_name: str | None = None
    schema_id: int
    column_name: str | None = None
    mapping_type: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Morpheme Analysis (형태소 분석 결과)
# ---------------------------------------------------------------------------

class ForbiddenWordInfo(BaseModel):
    """용어에 포함된 금칙어와, 같은 이음동의어 그룹의 표준 단어(대체어) 제안."""
    word_name: str
    suggestion: str | None = None


class MorphemeResult(BaseModel):
    """용어 형태소 분석 결과. 자동 추천용."""
    words: list[TermWordInfo]
    term_english: str           # 자동 생성된 영문명
    term_abbr: str              # 자동 생성된 영문 약어
    physical_name: str          # 자동 생성된 물리명
    recommended_domain: DomainResponse | None = None  # SUFFIX 단어 기반 도메인 추천
    unmatched_parts: list[str] = Field(default_factory=list)  # 매칭 안 된 부분
    forbidden_words: list[ForbiddenWordInfo] = Field(default_factory=list)  # 포함된 금칙어


# ---------------------------------------------------------------------------
# Compliance (표준 준수율)
# ---------------------------------------------------------------------------

class ComplianceStats(BaseModel):
    """표준 준수율 통계 — 컬럼별 대표 매핑 1건 기준으로 센다."""
    total_columns: int = 0
    matched: int = 0
    similar: int = 0
    violation: int = 0
    unmapped: int = 0
    compliance_rate: float = 0.0     # (matched / total) * 100
    dataset_count: int = 0           # 집계 대상 데이터셋 수 (사전 단위 집계 시)


# ---------------------------------------------------------------------------
# Auto-mapping (자동 매핑 결과)
# ---------------------------------------------------------------------------

class ColumnTermStatus(BaseModel):
    """데이터셋 컬럼별 표준 용어 매핑 상태."""
    schema_id: int
    column_name: str                         # 실제 컬럼명 (field_path)
    column_type: str                         # 실제 데이터 타입
    native_type: str | None = None           # 실제 네이티브 타입
    mapping_id: int | None = None            # 매핑 ID (없으면 미매핑)
    mapping_type: str | None = None          # MATCHED, SIMILAR, VIOLATION
    term_id: int | None = None               # 매핑된 용어 ID
    term_name: str | None = None             # 매핑된 용어 한글명
    term_physical_name: str | None = None    # 용어의 표준 물리명
    term_data_type: str | None = None        # 용어 도메인의 표준 데이터 타입
    term_data_length: int | None = None      # 용어 도메인의 표준 길이


class DatasetTermMapping(BaseModel):
    """데이터셋의 전체 컬럼-용어 매핑 현황."""
    dataset_id: int
    dictionary_id: int
    columns: list[ColumnTermStatus]
    compliance: ComplianceStats


class AutoMapResult(BaseModel):
    """자동 매핑 실행 결과."""
    created: int = 0                         # 새로 생성된 매핑 수
    updated: int = 0                         # 업데이트된 매핑 수
    removed: int = 0                         # 지운 매핑 수(맞지 않는 자동 매핑 + 같은 컬럼 중복 정리분)
    matched: int = 0
    similar: int = 0
    violation: int = 0
    unmapped: int = 0


class DatasetDictionarySelection(BaseModel):
    """데이터셋 기본 사전 선택 (표준 용어 탭)."""
    dictionary_id: int


class ChangeLogEntry(BaseModel):
    """변경 이력 항목 — CREATE/DELETE 마커 값은 old/new 에서 비우고 entity_name 으로 대신 보인다."""
    id: int
    entity_type: str
    entity_id: int
    entity_name: str | None = None
    change_type: str
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None
