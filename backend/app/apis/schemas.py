"""API Catalog Pydantic 스키마."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ApiStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# 생성 / 수정
# ---------------------------------------------------------------------------

class ApiCreate(BaseModel):
    """API 등록. 스펙(text/url)을 주면 파싱해 엔드포인트 추출(source=spec),
    스펙이 없으면 수동 등록(source=manual, name 필수)."""
    name: str | None = Field(None, max_length=255)
    display_name: str | None = None
    description: str | None = None
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    status: ApiStatus = ApiStatus.DRAFT
    tags: list[str] | None = None
    # 스펙 입력(둘 중 하나) — 없으면 수동 등록
    spec_text: str | None = None
    spec_url: str | None = None
    # 수동 등록용 메타데이터
    version: str | None = None
    base_url: str | None = None
    protocol: str | None = None
    contract_text: str | None = None
    contract_url: str | None = None


class EndpointCreate(BaseModel):
    """수동 엔드포인트(오퍼레이션) 추가/수정."""
    method: str = Field(..., max_length=40)
    path: str = Field(..., min_length=1, max_length=2000)
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    parameters: list[dict[str, Any]] | None = None
    request_body: dict[str, Any] | None = None
    responses: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None


class ApiUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    version: str | None = None
    status: ApiStatus | None = None
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    certification: str | None = None
    tier: str | None = None
    tags: list[str] | None = None
    note: str | None = None
    contract_text: str | None = None
    contract_url: str | None = None


class SpecUpload(BaseModel):
    """기존 API 에 새 스펙 버전 등록."""
    spec_text: str | None = None
    spec_url: str | None = None


# ---------------------------------------------------------------------------
# 응답
# ---------------------------------------------------------------------------

class ApiSummary(BaseModel):
    id: int
    name: str
    urn: str
    display_name: str | None = None
    description: str | None = None
    version: str
    status: str
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    protocol: str | None = None
    source: str | None = None
    spec_format: str | None = None
    base_url: str | None = None
    base_url_overridden: str | None = None
    contract_url: str | None = None
    certification: str | None = None
    tier: str | None = None
    tags: list[str] | None = None
    endpoint_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApiServerResponse(BaseModel):
    id: int
    url: str
    description: str | None = None
    env: str | None = None
    model_config = {"from_attributes": True}


class ApiSecuritySchemeResponse(BaseModel):
    id: int
    scheme_name: str
    type: str | None = None
    config: dict[str, Any] | None = None
    model_config = {"from_attributes": True}


class ApiEndpointResponse(BaseModel):
    id: int
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    parameters: list[dict[str, Any]] | None = None
    request_body: dict[str, Any] | None = None
    responses: dict[str, Any] | None = None
    security: list[Any] | None = None
    extra: dict[str, Any] | None = None
    model_config = {"from_attributes": True}


class ApiSpecResponse(BaseModel):
    id: int
    version: str
    format: str | None = None
    source_url: str | None = None
    is_current: str | None = None
    created_at: datetime
    created_by: str | None = None
    model_config = {"from_attributes": True}


class ApiDetailResponse(ApiSummary):
    note: str | None = None
    contract_text: str | None = None
    raw_spec: str | None = None
    servers: list[ApiServerResponse] = []
    security_schemes: list[ApiSecuritySchemeResponse] = []
    endpoints: list[ApiEndpointResponse] = []
    specs: list[ApiSpecResponse] = []
    tag_defs: list[dict[str, str]] = []  # 엔드포인트 카테고리(태그) 정의 {name, description}


class ApiStatusHistoryResponse(BaseModel):
    id: int
    from_status: str | None = None
    to_status: str
    note: str | None = None
    changed_by: str | None = None
    changed_at: datetime
    model_config = {"from_attributes": True}


class SpecDiffResponse(BaseModel):
    from_spec_id: int | None = None
    to_spec_id: int | None = None
    from_version: str | None = None
    to_version: str | None = None
    added: list[str] = []
    removed: list[str] = []
    changed: list[dict[str, Any]] = []
    breaking: bool = False
    breaking_count: int = 0
    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    message: str | None = None


class ApiAlertResponse(BaseModel):
    """스펙 Breaking 변경 알림."""
    id: int
    api_id: int
    from_spec_id: int | None = None
    to_spec_id: int | None = None
    from_version: str | None = None
    to_version: str | None = None
    severity: str
    breaking_count: int
    summary: str
    detail: str | None = None
    status: str
    created_by: str | None = None
    created_at: datetime
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    model_config = {"from_attributes": True}


class LineageCreate(BaseModel):
    """API 리니지 엣지 추가."""
    relation: str = Field(..., max_length=20)       # provides / consumes / depends_on
    target_type: str = Field(..., max_length=20)    # api / dataset / model / agent / system
    target_ref: str = Field(..., min_length=1, max_length=300)
    target_label: str | None = None
    note: str | None = None


class LineageResponse(BaseModel):
    id: int
    api_id: int
    relation: str
    target_type: str
    target_ref: str
    target_label: str | None = None
    note: str | None = None
    created_by: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


class ApiUsageResponse(BaseModel):
    """API 호출 사용량 집계(최근 N일)."""
    days: int
    total_calls: int
    success_calls: int
    error_calls: int
    success_rate: float          # %
    avg_latency_ms: int
    p95_latency_ms: int
    by_status: list[dict[str, Any]] = []      # {status, count}
    top_endpoints: list[dict[str, Any]] = []  # {endpoint, count, avg_latency_ms}
    top_callers: list[dict[str, Any]] = []    # {name, count}
    daily: list[dict[str, Any]] = []          # {date, count}
    recent: list[dict[str, Any]] = []         # 최근 호출 20건


class ApiLintResponse(BaseModel):
    """스펙 품질 린팅 결과(Spectral 스타일)."""
    spec_id: int | None = None
    version: str | None = None
    score: int                  # 0~100 품질 점수
    error_count: int
    warning_count: int
    info_count: int
    findings: list[dict[str, Any]] = []  # {rule, severity, message, location}


class PaginatedApis(BaseModel):
    items: list[ApiSummary]
    total: int
    page: int
    page_size: int


class ApiStats(BaseModel):
    total_apis: int
    published_apis: int
    by_status: list[dict[str, Any]]
    by_protocol: list[dict[str, Any]]
    total_endpoints: int


# ---------------------------------------------------------------------------
# Try-it 프록시
# ---------------------------------------------------------------------------

class CredentialCreate(BaseModel):
    scheme_name: str | None = None
    label: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., max_length=30)  # apiKey / bearer / basic / oauth2
    values: dict[str, Any] = Field(default_factory=dict)  # 시크릿(서버에서 암호화)


class CredentialResponse(BaseModel):
    """시크릿은 절대 반환하지 않는다."""
    id: int
    scheme_name: str | None = None
    label: str
    type: str
    created_by: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


class InvokeRequest(BaseModel):
    method: str = "GET"
    url: str  # 완성된 호출 URL (서버 base + path + query)
    headers: dict[str, str] | None = None
    body: Any | None = None
    timeout: float = 30.0
    # 저장된 자격증명 주입(서버 측). api_name + credential_id 동시 지정 시 적용.
    api_name: str | None = None
    credential_id: int | None = None
    # 입력 이력 저장용(엔드포인트 식별 + 원본 입력값) — 선택.
    endpoint_method: str | None = None
    endpoint_path: str | None = None
    path_params: dict[str, str] | None = None
    query_params: dict[str, str] | None = None


class ApiInvocationResponse(BaseModel):
    """엔드포인트 호출 이력(입력 파라미터 포함)."""
    id: int
    endpoint_method: str | None = None
    endpoint_path: str | None = None
    method: str
    url: str
    status_code: int
    ok: bool
    latency_ms: int
    error: str | None = None
    called_by: str | None = None
    created_at: datetime
    request_input: dict[str, Any] | None = None


class FavoriteCreate(BaseModel):
    method: str = Field(..., max_length=10)
    path: str = Field(..., min_length=1, max_length=2000)


class FavoriteResponse(BaseModel):
    id: int
    api_id: int
    api_name: str | None = None
    api_display_name: str | None = None
    method: str
    path: str
    summary: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


class InvokeResponse(BaseModel):
    status: int
    headers: dict[str, str]
    body: str
    latency_ms: int
    error: str | None = None
