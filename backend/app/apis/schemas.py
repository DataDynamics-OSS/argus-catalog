# SPDX-License-Identifier: Apache-2.0
"""API Catalog Pydantic 스키마(요청·응답 DTO).

``/api/v1/apis`` 라우터와 서비스 레이어가 사용한다. 응답 모델은 ``from_attributes`` 로 ORM 객체에서 변환된다.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ApiStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# 생성 / 수정
# ---------------------------------------------------------------------------

# API 식별자 규칙 — URN(`<name>.api`)·URL 경로에 쓰이므로 영문·숫자·점·하이픈·언더스코어만(화면 검사와 동일)
API_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
API_NAME_RULE = "API 식별자는 영문·숫자·점(.)·하이픈(-)·언더스코어(_)만 사용할 수 있습니다."


def _normalize_api_name(v: str | None) -> str | None:
    """식별자 앞뒤 공백 제거. 빈 값은 None(자동 생성 대상), 규칙 위반은 ValueError(→ 422)."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not API_NAME_RE.fullmatch(v):
        raise ValueError(API_NAME_RULE)
    return v


class ApiCreate(BaseModel):
    """API 등록. 스펙(text/url)을 주면 파싱해 엔드포인트 추출(source=spec),
    스펙이 없으면 수동 등록(source=manual, name 필수)."""
    name: str | None = Field(None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _normalize_api_name(v)

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


class SpecPreviewRequest(BaseModel):
    """등록 전 스펙 미리보기 — 저장하지 않고 파싱 결과와 사용될 식별자만 확인."""
    spec_text: str | None = None
    spec_url: str | None = None
    name: str | None = Field(None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _normalize_api_name(v)


class SpecPreviewResponse(BaseModel):
    format: str
    title: str | None = None
    description: str | None = None
    version: str
    base_url: str | None = None
    endpoint_count: int = 0
    server_count: int = 0
    security_count: int = 0
    # 등록 시 사용될 식별자 — 입력값 또는 스펙 제목으로 자동 생성(중복이면 -2, -3 … 을 붙인 값)
    name: str
    name_auto: bool
    name_available: bool


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
    # 상태 변경 사유 — status 와 함께 보내면 상태 변경 이력의 note 로 기록(API 필드에는 저장하지 않음)
    status_note: str | None = Field(None, max_length=1000)


class SpecUpload(BaseModel):
    """기존 API 에 새 스펙 버전 등록."""
    spec_text: str | None = None
    spec_url: str | None = None


# ---------------------------------------------------------------------------
# 응답
# ---------------------------------------------------------------------------

class ApiOwnerInfo(BaseModel):
    """owner_email 과 일치하는 등록 사용자(argus_users) 정보 — 목록·상세 표시용."""
    email: str
    username: str
    first_name: str | None = None
    last_name: str | None = None
    organization: str | None = None
    department: str | None = None
    model_config = {"from_attributes": True}


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
    # owner_email 로 조회한 사용자 정보 — 미지정이거나 등록되지 않은 이메일이면 None
    owner: ApiOwnerInfo | None = None
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


class ApiPermissions(BaseModel):
    """현재 사용자가 이 API 에 할 수 있는 작업 — 화면의 버튼 노출 판단용(실제 통제는 각 엔드포인트 가드)."""
    # 개요 편집·상태 변경·삭제·스펙 새 버전·엔드포인트 추가/수정/삭제·알림 확인 — 생성자 본인 또는 관리자
    can_edit: bool = False
    # 자격증명·리니지 추가/삭제 — 관리자 전용
    can_admin: bool = False


class ApiDetailResponse(ApiSummary):
    permissions: ApiPermissions = ApiPermissions()
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
    # 데이터셋 대상의 상세 화면 id(URN·이름으로 찾은 경우) — 링크용
    target_id: int | None = None
    model_config = {"from_attributes": True}


class LineageTargetOption(BaseModel):
    """리니지 대상 자동완성 항목."""
    ref: str
    label: str
    id: int | None = None


class IncomingLineageResponse(LineageResponse):
    """다른 API 가 이 API 를 대상으로 등록한 엣지(들어오는 관계) — 출발 API 정보 포함."""
    source_api_name: str
    source_display_name: str | None = None


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
    daily: list[dict[str, Any]] = []          # {date, count, errors}
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
    by_category: list[dict[str, Any]] = []  # 목록 카테고리 필터 옵션
    total_endpoints: int
    # 목록 상단 지표 카드용
    certified_apis: int = 0          # 인증 상태 CERTIFIED
    unowned_apis: int = 0            # 소유자(owner_email) 미지정
    open_alert_apis: int = 0         # 확인되지 않은(OPEN) Breaking 변경 알림이 있는 API 수


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
    # 호출 하나가 서버 연결을 오래 붙잡지 않도록 1~60초로 제한
    timeout: float = Field(30.0, ge=1, le=60)
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
    api_status: str | None = None
    api_protocol: str | None = None
    method: str
    path: str
    summary: str | None = None
    # 현재 스펙·엔드포인트 목록에 없는 즐겨찾기(새 스펙에서 제거·경로 변경 등) — 화면에서 따로 표시·정리
    endpoint_missing: bool = False
    created_at: datetime
    model_config = {"from_attributes": True}


class InvokeResponse(BaseModel):
    status: int
    headers: dict[str, str]
    body: str
    latency_ms: int
    error: str | None = None
