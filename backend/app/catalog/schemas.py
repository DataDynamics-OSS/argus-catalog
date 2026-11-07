# SPDX-License-Identifier: Apache-2.0
"""요청/응답 검증을 위한 데이터 카탈로그 스키마.

데이터 소스·조직/시스템·토폴로지·데이터셋·스키마 필드·태그·용어집·소유자·파이프라인·
리니지·분류체계의 Pydantic 모델을 모은다. "true"/"false" 문자열 플래그는 DB 컬럼 형식을 따른다.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 열거형(Enum)
# ---------------------------------------------------------------------------

class DatasetOrigin(str, Enum):
    """데이터셋·데이터 소스의 환경 구분."""
    PROD = "PROD"
    DEV = "DEV"
    STAGING = "STAGING"


class DatasetStatus(str, Enum):
    """데이터셋 상태 — removed 는 원본에서 사라진 데이터셋(목록·집계에서 기본 제외)."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


class OwnerType(str, Enum):
    """데이터셋 소유자 역할."""
    TECHNICAL_OWNER = "TECHNICAL_OWNER"
    BUSINESS_OWNER = "BUSINESS_OWNER"
    DATA_STEWARD = "DATA_STEWARD"


# ---------------------------------------------------------------------------
# 데이터 소스 스키마
# ---------------------------------------------------------------------------

class DatasourceCreate(BaseModel):
    """데이터 소스 생성 요청 — type 은 소문자·숫자·_- 로 된 유형 키."""
    # 길이는 DDL 과 맞춘다: name VARCHAR(100), datasource_id VARCHAR(36) = "{type}-" + 접미사
    # (service._generate_datasource_id — 현재 ms 타임스탬프 11자 + 랜덤 6자 = 17자)
    # → type 최대 18자 + "-" 1자 + 접미사 17자 = 36자.
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., min_length=1, max_length=18, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    logo_url: str | None = Field(default=None, max_length=500, pattern=r"^(https?://|/)\S*$")
    # 환경 (DEV/STAGING/PROD). 이후 변경 가능(DatasourceUpdate) — 변경 시 소속 데이터셋도 갱신.
    origin: DatasetOrigin = DatasetOrigin.DEV

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("이름을 입력하세요.")
        return v


class DatasourceResponse(BaseModel):
    """데이터 소스 응답."""
    id: int
    datasource_id: str
    name: str
    type: str
    logo_url: str | None = None
    origin: str = "DEV"
    system_id: int | None = None
    system_name: str | None = None   # 조회 편의 (조인으로 채움)
    created_at: datetime

    model_config = {"from_attributes": True}


class DatasourceConfigurationSave(BaseModel):
    """접속 설정 저장 요청 — 빈 비밀값은 "변경 안 함"(datasource_secrets.seal_config)."""
    config: dict


class DatasourceConnectionTest(BaseModel):
    """연결 설정 테스트 요청 — 저장 전 임의 config 도 검증 가능.

    ``datasource_id`` 를 주면 config 의 빈 비밀값(마스킹되어 내려간 비밀번호 등)을
    저장된 값으로 채워 테스트한다.
    """
    type: str
    config: dict
    datasource_id: int | None = None


class DatasourceConnectionTestResult(BaseModel):
    """연결 테스트 결과."""
    ok: bool
    message: str
    latency_ms: int | None = None
    # 서버에서 연결 테스트를 지원하지 않는 유형(실패와 구분해 안내)
    unsupported: bool = False


class DatasourceUpdate(BaseModel):
    """데이터 소스 부분 수정 요청(이름·환경)."""
    name: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("이름을 입력하세요.")
        return v

    # 환경 변경 가능(승격 등). URN 에는 origin 이 포함되지 않으므로 식별자는 불변이며,
    # 변경 시 소속 데이터셋의 origin 도 함께 갱신된다.
    origin: DatasetOrigin | None = None


class DatasourceSystemAssign(BaseModel):
    """데이터 소스의 소속 시스템 배정/이동/해제. system_id=null 이면 미분류로 해제."""
    system_id: int | None = None


# ---------------------------------------------------------------------------
# Organization / System schemas (조직 → 시스템 → 데이터 소스 계층)
# ---------------------------------------------------------------------------

def _reject_explicit_null(model: BaseModel, fields: tuple[str, ...]) -> None:
    """부분 수정에서 NOT NULL 컬럼에 null 을 명시하면 422 — 예전에는 DB 제약 오류로 떨어졌다."""
    for f in fields:
        if f in model.model_fields_set and getattr(model, f) is None:
            raise ValueError(f"{f} 는 null 일 수 없습니다.")


def _strip_name(v: str | None) -> str | None:
    """이름 앞뒤 공백 제거 — 공백만이면 거부(422)."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("이름을 입력하세요.")
    return v


class OrganizationCreate(BaseModel):
    """조직 생성 요청 — parent_id 가 없으면 루트 조직."""
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: int | None = None
    description: str | None = None
    sort_order: int = 0

    # 앞뒤 공백 제거, 공백만이면 422 — 예전에는 생성만 검증이 없어 "   " 이름이 201 로 저장됐다
    _name = field_validator("name")(_strip_name)


class OrganizationUpdate(BaseModel):
    """조직 부분 수정 요청 — 보낸 필드만 반영(name·sort_order 는 null 불가)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = None        # 이동 (순환 검증)
    description: str | None = None
    sort_order: int | None = None

    _name = field_validator("name")(_strip_name)

    @model_validator(mode="after")
    def _not_null(self):
        _reject_explicit_null(self, ("name", "sort_order"))
        return self


class OrganizationResponse(BaseModel):
    id: int
    code: str | None = None
    name: str
    parent_id: int | None = None
    description: str | None = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SystemCreate(BaseModel):
    """시스템 생성 요청."""
    name: str = Field(..., min_length=1, max_length=200)
    org_id: int | None = None          # None = 미분류(조직 미배정)
    summary: str | None = Field(None, max_length=200)
    description: str | None = None
    owner: str | None = None
    status: str = "ACTIVE"
    sort_order: int = 0

    _name = field_validator("name")(_strip_name)


class SystemUpdate(BaseModel):
    """시스템 부분 수정 요청 — 보낸 필드만 반영(name·status·sort_order 는 null 불가)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    org_id: int | None = None           # 조직 이동
    summary: str | None = Field(None, max_length=200)
    description: str | None = None
    owner: str | None = None
    status: str | None = None
    sort_order: int | None = None

    _name = field_validator("name")(_strip_name)

    @model_validator(mode="after")
    def _not_null(self):
        _reject_explicit_null(self, ("name", "status", "sort_order"))
        return self


class SystemResponse(BaseModel):
    id: int
    code: str | None = None
    name: str
    org_id: int | None = None
    summary: str | None = None
    description: str | None = None
    owner: str | None = None
    status: str = "ACTIVE"
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Topology (사이드바 트리)
# ---------------------------------------------------------------------------

class TopologyDatasource(BaseModel):
    id: int
    name: str
    type: str
    origin: str = "DEV"
    dataset_count: int = 0


class TopologySystem(BaseModel):
    id: int
    name: str
    status: str = "ACTIVE"
    owner: str | None = None
    org_id: int | None = None
    summary: str | None = None
    description: str | None = None
    datasources: list[TopologyDatasource] = Field(default_factory=list)


class TopologyOrganization(BaseModel):
    id: int
    name: str
    parent_id: int | None = None
    children: list["TopologyOrganization"] = Field(default_factory=list)
    systems: list[TopologySystem] = Field(default_factory=list)


class TopologyUnassigned(BaseModel):
    datasources: list[TopologyDatasource] = Field(default_factory=list)   # 시스템 미배정 데이터 소스
    systems: list[TopologySystem] = Field(default_factory=list)       # 조직 미배정 시스템


class TopologyResponse(BaseModel):
    """사이드바 토폴로지 — 조직 트리와 미분류 버킷."""
    organizations: list[TopologyOrganization] = Field(default_factory=list)
    unassigned: TopologyUnassigned = Field(default_factory=TopologyUnassigned)


# 자기참조(children) forward ref 해소
TopologyOrganization.model_rebuild()


class DatasourceConfigurationResponse(BaseModel):
    """접속 설정 응답 — 비밀값은 마스킹된다."""
    id: int
    datasource_id: int
    config: dict
    # 저장된 비밀값 키 — config 에는 빈 문자열로 마스킹되어 내려간다.
    secret_fields: list[str] = []
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# 스키마 필드 스키마
# ---------------------------------------------------------------------------

class SchemaFieldCreate(BaseModel):
    """스키마 필드 생성 요청 — 플래그는 "true"/"false" 문자열."""
    field_path: str = Field(..., min_length=1, max_length=500)
    display_name: str | None = Field(default=None, max_length=255)
    field_type: str = Field(..., min_length=1, max_length=100)
    native_type: str | None = None
    description: str | None = None
    nullable: str = "true"
    is_primary_key: str = "false"
    is_unique: str = "false"
    is_indexed: str = "false"
    is_partition_key: str = "false"
    is_distribution_key: str = "false"
    ordinal: int = 0


class SchemaFieldResponse(BaseModel):
    id: int
    field_path: str
    display_name: str | None = None
    field_type: str
    native_type: str | None = None
    description: str | None = None
    nullable: str
    is_primary_key: str = "false"
    is_unique: str = "false"
    is_indexed: str = "false"
    is_partition_key: str = "false"
    is_distribution_key: str = "false"
    ordinal: int

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 태그 스키마
# ---------------------------------------------------------------------------

_TAG_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


def _strip_tag_name(v: str | None) -> str | None:
    """앞뒤 공백 제거 — 공백만 있는 이름은 거부한다."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("이름을 입력하세요.")
    return v


class TagCreate(BaseModel):
    """태그 생성 요청 — 색상은 #rrggbb(소문자로 정규화)."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    # DB 컬럼이 VARCHAR(7) 이고 배지 배경색으로 그대로 쓰이므로 #rrggbb 만 허용한다
    color: str = Field("#3b82f6", pattern=_TAG_COLOR_PATTERN)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return _strip_tag_name(v)

    @field_validator("color")
    @classmethod
    def _lower_color(cls, v: str) -> str:
        return v.lower()


class TagUpdate(BaseModel):
    """태그 부분 수정 요청."""
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    color: str | None = Field(None, pattern=_TAG_COLOR_PATTERN)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        return _strip_tag_name(v)

    @field_validator("color")
    @classmethod
    def _lower_color(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class TagResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 용어집 용어 스키마
# ---------------------------------------------------------------------------

GlossaryTermType = Literal["CATEGORY", "TERM"]


def _strip_glossary_name(v: str | None) -> str | None:
    """앞뒤 공백 제거 — 공백만 있는 이름은 빈 값으로 보고 거부한다."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("이름을 입력하세요.")
    return v


class GlossaryTermCreate(BaseModel):
    """용어집 항목 생성 요청 — term_type 은 CATEGORY(분류) 또는 TERM(용어)."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    parent_id: int | None = None
    term_type: GlossaryTermType = "TERM"

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        return _strip_glossary_name(v)


class GlossaryTermUpdate(BaseModel):
    """용어집 항목 부분 수정 요청."""
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    parent_id: int | None = None
    term_type: GlossaryTermType | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        return _strip_glossary_name(v)


class GlossaryTermResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    parent_id: int | None = None
    term_type: str = "TERM"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 소유자 스키마
# ---------------------------------------------------------------------------

class OwnerCreate(BaseModel):
    """데이터셋 소유자 추가 요청."""
    owner_name: str = Field(..., min_length=1, max_length=200)
    owner_type: OwnerType = OwnerType.TECHNICAL_OWNER


class OwnerResponse(BaseModel):
    id: int
    dataset_id: int
    owner_name: str
    owner_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 데이터셋 스키마
# ---------------------------------------------------------------------------

class DatasetPropertyCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: str


class DatasetPropertyResponse(BaseModel):
    id: int
    property_key: str
    property_value: str

    model_config = {"from_attributes": True}


class DatasetCreate(BaseModel):
    """데이터셋 생성 요청 — 스키마 필드·태그·소유자·속성을 함께 받을 수 있다."""
    name: str = Field(..., min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    datasource_id: int
    summary: str | None = Field(default=None, max_length=200)
    description: str | None = None
    # 생략하면 데이터 소스의 환경(origin)을 따른다(UI 와 동일) — 이전에는 항상 PROD 였다.
    origin: DatasetOrigin | None = None
    qualified_name: str | None = None
    table_type: str | None = None
    storage_format: str | None = None
    # 원본 DDL (예: trino 의 SHOW CREATE TABLE 결과). UI 의 DDL tab 에서 read-only
    # SQL highlight 로 노출.
    ddl: str | None = None
    # datasource-specific 메타 (column-level extra, indexes 등). DatasourceSpecificCard
    # 의 COLUMNS / INDEXES grid 가 이 dict 의 ``columns`` / ``indexes`` 키를 읽는다.
    # 자유 형식이라 dict 그대로 받아 JSON 직렬화해 저장.
    datasource_properties: dict | None = None
    schema_fields: list[SchemaFieldCreate] = Field(default_factory=list)
    tags: list[int] = Field(default_factory=list, description="Tag IDs to attach")
    owners: list[OwnerCreate] = Field(default_factory=list)
    properties: list[DatasetPropertyCreate] = Field(default_factory=list)


class DatasetUpdate(BaseModel):
    """데이터셋 부분 수정 요청(None 인 필드는 변경하지 않음)."""
    name: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=200)
    description: str | None = None
    origin: DatasetOrigin | None = None
    qualified_name: str | None = None
    table_type: str | None = None
    storage_format: str | None = None
    ddl: str | None = None
    datasource_properties: dict | None = None
    status: DatasetStatus | None = None
    # --- 확장 메타데이터 (편집 가능) ---
    ingestion_frequency: str | None = None
    ingestion_time: str | None = None
    ingestion_day: str | None = None
    ingestion_timezone: str | None = None
    ingestion_cron: str | None = None
    ingestion_mode: str | None = None
    update_type: str | None = None
    freshness_sla: str | None = None
    last_ingested_at: datetime | None = None
    retention_days: int | None = None
    purge_days: int | None = None
    data_category: str | None = None
    data_format: str | None = None
    compression: str | None = None
    encoding: str | None = None
    row_count: int | None = None
    byte_size: int | None = None
    file_count: int | None = None
    sensitivity: str | None = None
    contains_pii: bool | None = None
    pii_fields: str | None = None
    compliance_tags: str | None = None
    tier: str | None = None
    certification: str | None = None
    steward: str | None = None
    quality_status: str | None = None
    show_quality_score: bool | None = None  # 품질 점수는 자동 연동, 표시 여부만 편집
    note: str | None = None


class DatasetResponse(BaseModel):
    """데이터셋 상세 응답 — 스키마·태그·소유자·용어·속성 포함."""
    id: int
    urn: str
    created_by: str | None = None
    name: str
    display_name: str | None = None
    datasource: DatasourceResponse
    summary: str | None = None
    description: str | None = None
    origin: str
    qualified_name: str | None = None
    table_type: str | None = None
    storage_format: str | None = None
    ddl: str | None = None
    status: str
    is_synced: str = "false"
    # --- 확장 메타데이터 ---
    ingestion_frequency: str | None = None
    ingestion_time: str | None = None
    ingestion_day: str | None = None
    ingestion_timezone: str | None = None
    ingestion_cron: str | None = None
    ingestion_mode: str | None = None
    update_type: str | None = None
    freshness_sla: str | None = None
    last_ingested_at: datetime | None = None
    retention_days: int | None = None
    purge_days: int | None = None
    data_category: str | None = None
    data_format: str | None = None
    compression: str | None = None
    encoding: str | None = None
    row_count: int | None = None
    byte_size: int | None = None
    file_count: int | None = None
    sensitivity: str | None = None
    contains_pii: bool | None = None
    pii_fields: str | None = None
    compliance_tags: str | None = None
    tier: str | None = None
    certification: str | None = None
    steward: str | None = None
    view_count: int = 0
    query_count: int = 0
    last_accessed_at: datetime | None = None
    quality_score: int | None = None
    quality_status: str | None = None
    show_quality_score: bool = False
    note: str | None = None
    datasource_properties: dict | None = None
    schema_fields: list[SchemaFieldResponse] = Field(default_factory=list)
    tags: list[TagResponse] = Field(default_factory=list)
    owners: list[OwnerResponse] = Field(default_factory=list)
    glossary_terms: list[GlossaryTermResponse] = Field(default_factory=list)
    properties: list[DatasetPropertyResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DatasetSummary(BaseModel):
    """목록 화면용 경량 데이터셋 표현."""
    id: int
    urn: str
    name: str
    display_name: str | None = None
    datasource_name: str
    datasource_type: str
    summary: str | None = None
    description: str | None = None
    origin: str
    status: str
    is_synced: str = "false"
    tag_count: int = 0
    owner_count: int = 0
    schema_field_count: int = 0
    # 생성자(소유자) — 목록 행 ⋮ "삭제" 노출 판단용(삭제는 생성자 또는 관리자만)
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class PaginatedDatasets(BaseModel):
    """데이터셋 목록 페이지 응답."""
    items: list[DatasetSummary]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 스키마 이력 스키마
# ---------------------------------------------------------------------------

class SchemaChangeEntry(BaseModel):
    """스키마 스냅샷의 필드 변경 1건(before/after 는 필드 속성 dict)."""
    type: str  # ADD, MODIFY, DROP
    field: str
    before: dict | None = None
    after: dict | None = None


class SchemaSnapshotResponse(BaseModel):
    """스키마 변경 이력 스냅샷 응답."""
    id: int
    dataset_id: int
    synced_at: datetime
    field_count: int
    change_summary: str | None = None
    changes: list[SchemaChangeEntry] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PaginatedSchemaSnapshots(BaseModel):
    items: list[SchemaSnapshotResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 검색 스키마
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = ""
    datasource: str | None = None
    origin: str | None = None
    tag: str | None = None
    page: int = 1
    page_size: int = 20


class SearchResult(BaseModel):
    datasets: PaginatedDatasets


# ---------------------------------------------------------------------------
# 대시보드 스키마
# ---------------------------------------------------------------------------

class TagUsageRef(BaseModel):
    """태그를 쓰는 데이터셋 외 항목(OCI 모델·알림 규칙) 요약."""
    id: int
    name: str
    is_active: bool | None = None


class TagUsage(BaseModel):
    """카탈로그 전반의 태그 사용 정보 — 데이터셋·OCI 모델·알림 규칙(TAG 범위)."""
    tag: TagResponse
    datasets: list[DatasetSummary] = Field(default_factory=list)
    total_datasets: int = 0
    oci_models: list[TagUsageRef] = Field(default_factory=list)
    alert_rules: list[TagUsageRef] = Field(default_factory=list)


class CatalogStats(BaseModel):
    """카탈로그 대시보드 통계."""
    total_datasets: int
    total_datasources: int
    total_tags: int
    total_glossary_terms: int
    total_owners: int
    synced_datasets: int
    datasets_by_datasource: list[dict]
    datasets_by_origin: list[dict]
    datasets_by_datasource_type: list[dict]
    schema_fields_by_datasource: list[dict]
    top_tagged_datasets: list[dict]
    daily_datasets_1d: list[dict]
    daily_datasets_7d: list[dict]
    daily_datasets_30d: list[dict]
    recent_datasets: list[DatasetSummary]


# ---------------------------------------------------------------------------
# 데이터 소스 메타데이터 스키마
# ---------------------------------------------------------------------------

class DatasourceDataTypeResponse(BaseModel):
    id: int
    type_name: str
    type_category: str
    description: str | None = None
    ordinal: int

    model_config = {"from_attributes": True}


class DatasourceTableTypeResponse(BaseModel):
    id: int
    type_name: str
    display_name: str
    description: str | None = None
    is_default: str
    ordinal: int

    model_config = {"from_attributes": True}


class DatasourceStorageFormatResponse(BaseModel):
    id: int
    format_name: str
    display_name: str
    description: str | None = None
    is_default: str
    ordinal: int

    model_config = {"from_attributes": True}


class DatasourceFeatureResponse(BaseModel):
    id: int
    feature_key: str
    display_name: str
    description: str | None = None
    value_type: str
    is_required: str
    ordinal: int

    model_config = {"from_attributes": True}


class DatasourceMetadataResponse(BaseModel):
    """데이터 소스 유형별 메타데이터(데이터 타입·테이블 타입·저장 포맷·기능)."""
    datasource: DatasourceResponse
    data_types: list[DatasourceDataTypeResponse] = Field(default_factory=list)
    table_types: list[DatasourceTableTypeResponse] = Field(default_factory=list)
    storage_formats: list[DatasourceStorageFormatResponse] = Field(default_factory=list)
    features: list[DatasourceFeatureResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 데이터 파이프라인 스키마
# 이기종 시스템 간 데이터 흐름(ETL, CDC, 파일 내보내기 등)을 등록/관리하기 위한 스키마
# ---------------------------------------------------------------------------

class PipelineType(str, Enum):
    """파이프라인 유형."""
    ETL = "ETL"                  # 추출-변환-적재 (Extract-Transform-Load)
    FILE_EXPORT = "FILE_EXPORT"  # 파일 내보내기 (Parquet, CSV 등)
    CDC = "CDC"                  # Change Data Capture (실시간 변경 동기화)
    REPLICATION = "REPLICATION"  # 데이터 복제
    API = "API"                  # API 기반 데이터 전송
    MANUAL = "MANUAL"            # 수동 전달


class PipelineStatus(str, Enum):
    """파이프라인 상태."""
    ACTIVE = "ACTIVE"            # 운영 중
    INACTIVE = "INACTIVE"        # 비활성 (일시 중지)
    DEPRECATED = "DEPRECATED"    # 폐기 예정


class PipelineCreate(BaseModel):
    """파이프라인 생성 요청."""
    pipeline_name: str = Field(..., min_length=1, max_length=255)  # 파이프라인 고유 이름
    description: str | None = None                                  # 설명
    pipeline_type: PipelineType = PipelineType.ETL                  # 유형
    schedule: str | None = None                                     # 실행 주기 (cron 표현식)
    owner: str | None = None                                        # 담당자


class PipelineUpdate(BaseModel):
    """파이프라인 수정 요청. 변경할 필드만 포함."""
    pipeline_name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    pipeline_type: PipelineType | None = None
    schedule: str | None = None
    owner: str | None = None
    status: PipelineStatus | None = None


class PipelineResponse(BaseModel):
    """파이프라인 응답."""
    id: int
    pipeline_name: str
    description: str | None = None
    pipeline_type: str
    schedule: str | None = None
    owner: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Cross-Datasource 리니지 스키마
# 이기종 시스템 간 데이터셋 관계 및 컬럼 매핑을 관리하기 위한 스키마.
# 예: PostgreSQL.employees → Impala.emp_fact
# ---------------------------------------------------------------------------

class LineageSource(str, Enum):
    """리니지 출처. 관계가 어떻게 등록되었는지를 나타냄."""
    QUERY_AGGREGATED = "QUERY_AGGREGATED"  # 쿼리 로그 분석으로 자동 수집 (같은 데이터 소스 내)
    PIPELINE = "PIPELINE"                  # 파이프라인 등록을 통해 명시적 연결
    MANUAL = "MANUAL"                      # 사용자가 UI에서 직접 등록
    FK = "FK"                              # RDBMS FOREIGN KEY 제약을 metadata-sync 가 자동 등록


class LineageRelationType(str, Enum):
    """리니지 관계 유형. 데이터가 어떤 방식으로 전달되는지를 나타냄."""
    READ_WRITE = "READ_WRITE"    # 읽기/쓰기 (같은 데이터 소스 내 쿼리 기반)
    ETL = "ETL"                  # ETL 처리
    FILE_EXPORT = "FILE_EXPORT"  # 파일 내보내기를 통한 전달
    CDC = "CDC"                  # 실시간 변경 동기화
    REPLICATION = "REPLICATION"  # 데이터 복제
    DERIVED = "DERIVED"          # 파생 (집계, 변환 등)


class ColumnMappingCreate(BaseModel):
    """컬럼 매핑 생성 요청. 원본↔대상 컬럼 간 매핑 관계를 정의."""
    source_column: str = Field(..., min_length=1, max_length=256)  # 원본 컬럼명
    target_column: str = Field(..., min_length=1, max_length=256)  # 대상 컬럼명
    transform_type: str = "DIRECT"   # 변환 유형: DIRECT, CAST, EXPRESSION, DERIVED
    transform_expr: str | None = None  # 변환 수식 (예: "CAST(emp_id AS BIGINT)")


class ColumnMappingResponse(BaseModel):
    """컬럼 매핑 응답."""
    id: int
    source_column: str
    target_column: str
    transform_type: str
    transform_expr: str | None = None

    model_config = {"from_attributes": True}


class FKColumnPair(BaseModel):
    """FK 의 컬럼 쌍 — local(자식의 컬럼) 이 referenced(부모의 컬럼) 를 가리킨다."""
    local: str = Field(..., min_length=1, max_length=256)
    referenced: str = Field(..., min_length=1, max_length=256)


class FKLineageEntry(BaseModel):
    """metadata-sync 가 보내는 FK lineage 한 건."""
    source_urn: str = Field(..., min_length=1, description="참조 대상(부모) dataset URN")
    columns: list[FKColumnPair] = Field(default_factory=list)
    description: str | None = None


class FKLineageReplaceRequest(BaseModel):
    """target dataset 의 FK lineage 를 통째 교체 — lineage_source=FK 만 영향."""
    entries: list[FKLineageEntry] = Field(default_factory=list)


class DatasetLineageCreate(BaseModel):
    """데이터셋 리니지 생성 요청.

    이기종 시스템 간 데이터셋 관계를 등록한다.
    column_mappings를 함께 제공하면 컬럼 수준 매핑도 동시에 생성.
    컬럼 매핑은 선택사항이며, 나중에 별도로 추가할 수도 있음.
    """
    source_dataset_id: int                                          # 원본 데이터셋 ID
    target_dataset_id: int                                          # 대상 데이터셋 ID
    relation_type: LineageRelationType = LineageRelationType.ETL     # 관계 유형
    lineage_source: LineageSource = LineageSource.MANUAL             # 출처 (수동/파이프라인)
    pipeline_id: int | None = None                                  # 파이프라인 참조 (PIPELINE 출처일 때)
    description: str | None = None                                  # 관계 설명
    created_by: str | None = None                                   # 등록자
    column_mappings: list[ColumnMappingCreate] = Field(default_factory=list)  # 컬럼 매핑 (선택)


class DatasetLineageResponse(BaseModel):
    """데이터셋 리니지 응답.

    원본/대상 데이터셋의 이름과 데이터 소스 정보를 포함하여
    UI에서 이기종 시스템 간 관계를 시각적으로 표현할 수 있도록 한다.
    """
    id: int
    source_dataset_id: int                          # 원본 데이터셋 ID
    target_dataset_id: int                          # 대상 데이터셋 ID
    source_dataset_name: str | None = None          # 원본 데이터셋 이름 (JOIN 조회)
    target_dataset_name: str | None = None          # 대상 데이터셋 이름 (JOIN 조회)
    source_datasource_type: str | None = None         # 원본 데이터 소스 타입 (예: PostgreSQL)
    target_datasource_type: str | None = None         # 대상 데이터 소스 타입 (예: Impala)
    source_datasource_name: str | None = None         # 원본 데이터 소스 인스턴스 이름
    target_datasource_name: str | None = None         # 대상 데이터 소스 인스턴스 이름
    relation_type: str                              # 관계 유형 (ETL, FILE_EXPORT 등)
    lineage_source: str                             # 출처 (MANUAL, PIPELINE, QUERY_AGGREGATED, FK)
    pipeline_id: int | None = None                  # 파이프라인 ID
    pipeline_name: str | None = None                # 파이프라인 이름 (JOIN 조회)
    description: str | None = None                  # 관계 설명
    created_by: str | None = None                   # 등록자
    query_count: int = 0                            # 이 관계를 확인한 쿼리 수
    last_seen_at: datetime | None = None            # 마지막 확인 시각
    created_at: datetime                            # 생성 시각
    column_mappings: list[ColumnMappingResponse] = Field(default_factory=list)  # 컬럼 매핑 목록


# ---------------------------------------------------------------------------
# Taxonomy (분류체계) schemas
# ---------------------------------------------------------------------------

class TaxonomyCreate(BaseModel):
    """분류체계 생성 요청."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    sort_order: int = 0


def _reject_explicit_nulls(model: BaseModel, fields: tuple[str, ...]) -> None:
    """수정 요청에서 생략은 허용하되 명시적 null 은 거부 (DB NOT NULL 컬럼 → 409 대신 422)."""
    for f in fields:
        if f in model.model_fields_set and getattr(model, f) is None:
            raise ValueError(f"{f} 는 비울 수 없습니다.")


class TaxonomyUpdate(BaseModel):
    """분류체계 부분 수정 요청(name·sort_order 는 null 불가)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    sort_order: int | None = None

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_explicit_nulls(self, ("name", "sort_order"))
        return self


class TaxonomyResponse(BaseModel):
    id: int
    code: str | None = None
    name: str
    description: str | None = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CategoryCreate(BaseModel):
    """분류 생성 요청 — parent_id 가 없으면 분류체계의 루트 분류."""
    taxonomy_id: int
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: int | None = None
    description: str | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    """분류 부분 수정·이동 요청(name·sort_order 는 null 불가)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = None        # 이동(순환 검증)
    description: str | None = None
    sort_order: int | None = None

    @model_validator(mode="after")
    def _no_null_required(self):
        _reject_explicit_nulls(self, ("name", "sort_order"))
        return self


class CategoryResponse(BaseModel):
    id: int
    taxonomy_id: int
    parent_id: int | None = None
    code: str | None = None
    name: str
    description: str | None = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# 분류 트리(트리 뷰)
class TaxonomyTreeCategory(BaseModel):
    id: int
    name: str
    parent_id: int | None = None
    dataset_count: int = 0                              # 서브트리 롤업
    children: list["TaxonomyTreeCategory"] = Field(default_factory=list)


class TaxonomyTreeResponse(BaseModel):
    taxonomy: TaxonomyResponse
    categories: list[TaxonomyTreeCategory] = Field(default_factory=list)
    uncategorized_count: int = 0                        # 이 분류체계 미매핑 데이터셋 수


TaxonomyTreeCategory.model_rebuild()


# 데이터셋의 분류 매핑 표시
class DatasetCategoryRef(BaseModel):
    category_id: int
    category_name: str
    taxonomy_id: int
    taxonomy_name: str
    path: str = ""  # "분류체계 > 상위 > ... > 카테고리" 경로
