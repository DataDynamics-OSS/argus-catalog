# SPDX-License-Identifier: Apache-2.0
"""AI Agent 카탈로그의 Pydantic 스키마(요청·응답 DTO).

``/api/v1/ai-agents`` 라우터와 서비스 레이어가 사용한다. JSON 컬럼은
list/dict 로 그대로 노출되며 ``from_attributes`` 로 ORM → 응답 변환된다.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# 예산 한도 상한 — DB 열이 Numeric(12, 4)(정수부 8자리)라 1억 이상은 저장할 수 없다(넘기면 500 대신 422).
# 최대 단계는 1 이상, 예산은 0 이상 — 정의 파일 반영(manifest)과 같은 기준.
BUDGET_LIMIT_MAX = 100_000_000

# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------


class AgentStatus(str, Enum):
    """에이전트 상태. ``blocked`` 설정·해제는 관리자만 할 수 있다(라우터·일괄 변경·정의 파일 반영 공통)."""

    DRAFT = "draft"
    STAGING = "staging"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# AIAgent — 생성 / 수정
# ---------------------------------------------------------------------------


class AIAgentCreate(BaseModel):
    """에이전트 등록 요청. 소유자 이메일·부서·분류는 라우터에서 필수 검사(``discovery.missing_required``, 없으면 422)."""

    name: str = Field(..., min_length=1, max_length=255)
    display_name: str | None = None
    description: str | None = None
    version: str = Field("0.1.0", max_length=50)
    status: AgentStatus = AgentStatus.DRAFT
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    # 모델 (자유 문자열 + 선택적 사내 레지스트리 FK)
    base_model: str | None = None
    base_model_ref: int | None = None
    model_provider: str | None = None
    # 아키텍처 / 실행
    framework: str | None = None
    execution_policy: str | None = None
    max_steps: int | None = Field(None, ge=1)
    memory_type: str | None = None
    is_multi_agent: bool = False
    # 인터페이스
    endpoint: str | None = None
    protocol: str | None = None
    streaming: bool = False
    invocation_method: str | None = None
    auth_method: str | None = None
    # 거버넌스 / 보안
    pii_handling: str | None = None
    data_residency: str | None = None
    budget_limit: float | None = Field(None, ge=0, lt=BUDGET_LIMIT_MAX)
    hitl_required: bool = False
    audit_log_ref: str | None = None
    # 구조화 메타데이터 (JSON)
    capabilities: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    supported_languages: list[str] | None = None
    use_cases: list[str] | None = None
    limitations: list[str] | None = None
    inference_params: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    rag_config: dict[str, Any] | None = None
    network_allowlist: list[str] | None = None
    dlp_policies: list[str] | None = None
    hitl_config: dict[str, Any] | None = None
    sub_agents: list[dict[str, Any]] | None = None
    tags: list[str] | None = None


class AIAgentUpdate(BaseModel):
    """부분 갱신. 전달된 필드만 반영한다."""

    display_name: str | None = None
    description: str | None = None
    version: str | None = None
    status: AgentStatus | None = None
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    base_model: str | None = None
    base_model_ref: int | None = None
    model_provider: str | None = None
    framework: str | None = None
    execution_policy: str | None = None
    max_steps: int | None = Field(None, ge=1)
    memory_type: str | None = None
    is_multi_agent: bool | None = None
    endpoint: str | None = None
    protocol: str | None = None
    streaming: bool | None = None
    invocation_method: str | None = None
    auth_method: str | None = None
    pii_handling: str | None = None
    data_residency: str | None = None
    budget_limit: float | None = Field(None, ge=0, lt=BUDGET_LIMIT_MAX)
    hitl_required: bool | None = None
    audit_log_ref: str | None = None
    capabilities: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    supported_languages: list[str] | None = None
    use_cases: list[str] | None = None
    limitations: list[str] | None = None
    inference_params: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    rag_config: dict[str, Any] | None = None
    network_allowlist: list[str] | None = None
    dlp_policies: list[str] | None = None
    hitl_config: dict[str, Any] | None = None
    sub_agents: list[dict[str, Any]] | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# AIAgent — 응답
# ---------------------------------------------------------------------------


class AIAgentSummary(BaseModel):
    """목록 뷰용 요약."""

    id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    version: str
    status: str
    owner_email: str | None = None
    department: str | None = None
    category: str | None = None
    base_model: str | None = None
    framework: str | None = None
    execution_policy: str | None = None
    reputation_score: float | None = None
    tags: list[str] | None = None
    updated_at: datetime
    managed_by: str = "ui"  # ui / manifest(정의 파일) / discovered(발견 기록에서 등록)
    usage_count: int = 0
    last_invoked_at: datetime | None = None
    # 조회 때 계산(저장 안 함) — design/ai-agent-ownership-design.md
    owner_state: str | None = None  # ok / missing / unknown / inactive
    completeness: int | None = None  # 0~100
    missing_fields: list[str] = Field(default_factory=list)  # 완성도에서 빠진 항목 키
    max_risk: str | None = None  # 보유 도구의 최고 위험도

    model_config = {"from_attributes": True}


class PaginatedAIAgents(BaseModel):
    items: list[AIAgentSummary]
    total: int
    page: int
    page_size: int


class AIAgentToolResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    tool_schema: dict[str, Any] | None = None
    risk: str | None = None
    requires_approval: bool = False

    model_config = {"from_attributes": True}


class AIAgentMcpServerResponse(BaseModel):
    id: int
    name: str
    url: str | None = None
    auth_method: str | None = None
    description: str | None = None
    # MCP Server 카탈로그 연계 (registered=False 면 직접 입력한 "미등록 MCP")
    mcp_deployment_id: int | None = None
    mcp_deployment_name: str | None = None
    mcp_deployment_urn: str | None = None
    mcp_server_name: str | None = None
    deployment_status: str | None = None
    deployment_environment: str | None = None
    registered: bool = False
    allowed_groups: list[str] | None = None
    allowed_tools: list[str] | None = None
    effective_tool_count: int | None = None

    model_config = {"from_attributes": True}


class AIAgentLineageResponse(BaseModel):
    id: int
    target_type: str
    target_ref: str
    relation: str
    description: str | None = None

    model_config = {"from_attributes": True}


class AIAgentVersionResponse(BaseModel):
    id: int
    version: str
    source: str | None = None
    system_prompt: str | None = None
    changelog: str | None = None
    status: str
    created_at: datetime
    created_by: str | None = None

    model_config = {"from_attributes": True}


class AIAgentDetailResponse(BaseModel):
    """전체 메타데이터 + 도구/MCP/리니지/버전 포함 상세."""

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
    base_model: str | None = None
    base_model_ref: int | None = None
    model_provider: str | None = None
    framework: str | None = None
    execution_policy: str | None = None
    max_steps: int | None = None
    memory_type: str | None = None
    is_multi_agent: bool
    endpoint: str | None = None
    protocol: str | None = None
    streaming: bool = False
    invocation_method: str | None = None
    auth_method: str | None = None
    pii_handling: str | None = None
    data_residency: str | None = None
    budget_limit: float | None = None
    hitl_required: bool
    audit_log_ref: str | None = None
    latency_p50: int | None = None
    latency_p95: int | None = None
    error_rate: float | None = None
    avg_token_usage: int | None = None
    cost_per_call: float | None = None
    reputation_score: float | None = None
    capabilities: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    supported_languages: list[str] | None = None
    use_cases: list[str] | None = None
    limitations: list[str] | None = None
    inference_params: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    rag_config: dict[str, Any] | None = None
    network_allowlist: list[str] | None = None
    dlp_policies: list[str] | None = None
    hitl_config: dict[str, Any] | None = None
    sub_agents: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    usage_count: int
    last_invoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None
    # 정의 파일 관리 정보
    managed_by: str = "ui"
    source_ref: str | None = None
    last_applied_at: datetime | None = None
    last_applied_by: str | None = None
    # 분류와 책임(조회 때 계산)
    owner_state: str | None = None
    completeness: int | None = None
    missing_fields: list[str] = Field(default_factory=list)
    max_risk: str | None = None
    # 관계
    tools: list[AIAgentToolResponse] = []
    mcp_servers: list[AIAgentMcpServerResponse] = []
    lineage: list[AIAgentLineageResponse] = []
    versions: list[AIAgentVersionResponse] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 서브리소스 생성
# ---------------------------------------------------------------------------


class AIAgentToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    tool_schema: dict[str, Any] | None = None
    risk: str | None = None  # low / medium / high / critical
    requires_approval: bool = False


class AIAgentMcpServerCreate(BaseModel):
    """MCP 연결 등록.

    - 카탈로그 배포 선택: ``mcp_deployment_name`` (+ 선택 ``allowed_groups``/``allowed_tools``).
      name/url/auth_method 는 배포 값으로 채운다.
    - 직접 입력(하위 호환): ``name`` 필수, "미등록 MCP" 로 표시된다.
    """

    name: str | None = Field(None, max_length=200)
    url: str | None = None
    auth_method: str | None = None
    description: str | None = None
    mcp_deployment_name: str | None = None
    allowed_groups: list[str] | None = None
    allowed_tools: list[str] | None = None

    @model_validator(mode="after")
    def _need_name_or_deployment(self):
        if not self.mcp_deployment_name and not (self.name or "").strip():
            raise ValueError("name 또는 mcp_deployment_name 중 하나는 필요합니다")
        return self


class AIAgentMcpServerUpdate(BaseModel):
    """연결된 MCP 의 허용 범위 수정 / 카탈로그 배포로 승격."""

    mcp_deployment_name: str | None = None
    allowed_groups: list[str] | None = None
    allowed_tools: list[str] | None = None
    description: str | None = None


class AIAgentLineageCreate(BaseModel):
    target_type: str = Field("agent", max_length=20)
    target_ref: str = Field(..., min_length=1, max_length=500)
    relation: str = Field("depends_on", max_length=20)
    description: str | None = None


class AIAgentVersionCreate(BaseModel):
    version: str = Field(..., min_length=1, max_length=50)
    source: str | None = None
    system_prompt: str | None = None
    changelog: str | None = None


class AIAgentStatusHistoryResponse(BaseModel):
    id: int
    from_status: str | None = None
    to_status: str
    note: str | None = None
    changed_by: str | None = None
    changed_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


class NameCount(BaseModel):
    name: str
    count: int


class DepartmentQuality(BaseModel):
    """부서별 정보 품질(부서 없음은 department=None)."""

    department: str | None = None
    agents: int
    avg_completeness: int
    missing_required: int  # 소유자·부서·분류 중 빠진 에이전트 수
    owner_attention: int


class AIAgentStats(BaseModel):
    """대시보드 통계."""

    total_agents: int
    active_agents: int
    by_status: list[NameCount]
    by_framework: list[NameCount]
    by_category: list[NameCount]
    multi_agent_count: int
    hitl_required_count: int
    total_invocations: int
    # 목록 상단 지표용 (최근 24시간 호출 로그 기준 · 거버넌스 점검)
    invocations_24h: int = 0
    errors_24h: int = 0
    risky_tool_agents: int = 0          # 승인 없이 쓰는 high/critical 도구를 가진 에이전트
    unregistered_mcp_count: int = 0     # MCP Server 카탈로그에 연결되지 않은(직접 입력) MCP 연결
    unregistered_agent_count: int = 0   # 실행 기록에서 발견됐지만 카탈로그에 없는 에이전트(처리 전)
    owner_attention_count: int = 0      # 책임자 확인 필요(소유자 없음·사용자 목록에 없음·비활성)
    incomplete_count: int = 0           # 정보 완성도 100% 미만
    by_department: list[DepartmentQuality] = Field(default_factory=list)



# ---------------------------------------------------------------------------
# 미등록 에이전트 발견
# ---------------------------------------------------------------------------


class AIAgentDiscoveryResponse(BaseModel):
    id: int
    name: str
    first_seen_at: datetime
    last_seen_at: datetime
    seen_count: int
    invocation_count: int
    hook_event_count: int
    reported_by: str | None = None
    last_consumer: str | None = None
    status: str  # new / ignored / registered
    agent_id: int | None = None
    agent_name: str | None = None
    status_changed_by: str | None = None
    status_changed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedAIAgentDiscoveries(BaseModel):
    items: list[AIAgentDiscoveryResponse]
    total: int
    page: int
    page_size: int


class AIAgentDiscoveryRegister(BaseModel):
    """발견 기록 등록 — 새로 만들 때 필수 항목(화면 등록과 같은 규칙).

    소유자 이메일·부서를 비우면 로그인 사용자 값으로 채운다. 분류는 채울 기본값이 없어 직접 넣어야 한다.
    같은 이름의 에이전트가 이미 있어 연결만 할 때는 쓰지 않는다.
    """

    owner_email: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    category: str | None = Field(None, max_length=100)


class AIAgentDiscoveryRegisterResponse(BaseModel):
    discovery: AIAgentDiscoveryResponse
    agent: AIAgentSummary


class AIAgentCardImportRequest(BaseModel):
    """소개 정보(에이전트 카드) 읽어 들이기 — 주소와, 카드에 없는 필수 항목 보충값."""

    url: str = Field(..., min_length=1, max_length=2000, description="에이전트 주소 또는 소개 정보(.json) 주소")
    name: str | None = Field(None, max_length=255, description="카탈로그 이름(없으면 카드 이름에서 만듦)")
    owner: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    category: str | None = Field(None, max_length=100)


class AIAgentCardImportResponse(BaseModel):
    """정의 내용 미리 보기(저장하지 않음). 확인·보완한 뒤 ``POST /ai-agents/apply`` 로 반영한다."""

    source_url: str
    name: str
    exists: bool
    missing: list[str]
    notes: list[str]
    document: dict
    yaml: str


# ---------------------------------------------------------------------------
# Phase 2 — 평가 (Evaluation)
# ---------------------------------------------------------------------------


class AIAgentEvalCreate(BaseModel):
    eval_type: str = Field(..., min_length=1, max_length=50)
    metric_key: str = Field(..., min_length=1, max_length=100)
    metric_value: float
    version: str | None = None
    dataset_ref: str | None = None
    passed: bool | None = None
    notes: str | None = None


class AIAgentEvalResponse(BaseModel):
    id: int
    version: str | None = None
    eval_type: str
    metric_key: str
    metric_value: float
    dataset_ref: str | None = None
    passed: bool | None = None
    notes: str | None = None
    evaluated_at: datetime
    created_by: str | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Phase 2 — 미터링 (Metering / Invocation telemetry)
# ---------------------------------------------------------------------------


class AIAgentInvocationIngest(BaseModel):
    """외부 런타임이 호출마다 push 하는 텔레메트리."""

    status: str = Field("success", max_length=20)  # success / error
    consumer: str | None = None
    error_type: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    session_id: str | None = None


class DataPoint(BaseModel):
    date: str
    count: int


class AIAgentMeteringResponse(BaseModel):
    """집계된 미터링 지표."""

    total_invocations: int
    success_count: int
    error_count: int
    success_rate: float
    error_rate: float
    avg_latency_ms: float | None = None
    latency_p50: int | None = None
    latency_p95: int | None = None
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float
    reputation_score: float | None = None
    daily_invocations: list[DataPoint]
    by_consumer: list[NameCount]


# ---------------------------------------------------------------------------
# Phase 2 — 에이전트 카드 (A2A)
# ---------------------------------------------------------------------------


class AgentCardSkill(BaseModel):
    name: str
    description: str | None = None


class AgentCardProvider(BaseModel):
    organization: str | None = None
    contact: str | None = None


class AIAgentCard(BaseModel):
    """Google A2A 스타일 에이전트 카드(상호 검색·협업용).

    카탈로그가 보유한 메타데이터로부터 조립된다(별도 저장 없음).
    """

    name: str
    display_name: str | None = None
    description: str | None = None
    version: str
    url: str | None = None
    protocol: str | None = None
    auth_method: str | None = None
    provider: AgentCardProvider
    capabilities: list[str] = []
    supported_languages: list[str] = []
    skills: list[AgentCardSkill] = []
    streaming: bool = False


# ---------------------------------------------------------------------------
# Phase 3 연동 인터페이스 — 정책 번들 (Control → Execution Plane, pull)
# ---------------------------------------------------------------------------


class PolicyTool(BaseModel):
    """집행기가 허용해야 하는 도구 화이트리스트 항목."""

    name: str
    description: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    # MCP Server 카탈로그 배포에서 온 도구면 배포 이름
    mcp_deployment: str | None = None
    risk: str | None = None
    requires_approval: bool = False

    model_config = {"populate_by_name": True}


class PolicyMcpServer(BaseModel):
    name: str
    url: str | None = None
    auth_method: str | None = None
    # 카탈로그 등록 여부와 배포 식별자, 이 에이전트에 허용된 도구 이름
    registered: bool = False
    deployment: str | None = None
    urn: str | None = None
    allowed_tools: list[str] | None = None


class AIAgentPolicyBundle(BaseModel):
    """외부 Execution Plane이 런타임 집행을 위해 pull 하는 정책 번들.

    카탈로그(Control Plane)가 보유한 거버넌스 메타데이터를 집행기 친화적으로
    조립한 읽기 전용 계약. ``policy_version`` 으로 변경 감지(폴링/ETag)에 사용한다.
    카탈로그는 이 정책을 *정의*만 하며, 실제 강제는 런타임이 수행한다.
    """

    name: str
    urn: str
    version: str
    status: str
    # 변경 감지용 버전 토큰 — agent.updated_at 과 연결된 카탈로그 배포 updated_at 중 최신값(ISO),
    # 둘 다 없으면 agent.version
    policy_version: str

    # --- 실행 제약 ---
    max_steps: int | None = None
    budget_limit: float | None = None
    # 통신 허용 도메인 (egress 화이트리스트)
    network_allowlist: list[str] = []
    # 허용 도구/MCP (이외 호출 차단 권고)
    allowed_tools: list[PolicyTool] = []
    allowed_mcp_servers: list[PolicyMcpServer] = []

    # --- 데이터 보호 ---
    pii_handling: str | None = None
    data_residency: str | None = None
    dlp_policies: list[str] = []

    # --- 휴먼 인 더 루프 ---
    hitl_required: bool = False
    hitl_config: dict[str, Any] | None = None

    # --- 입출력 가드레일 ---
    guardrails: dict[str, Any] | None = None

    # --- 인증/호출 ---
    auth_method: str | None = None
    protocol: str | None = None
    endpoint: str | None = None


# ---------------------------------------------------------------------------
# Phase 3 연동 인터페이스 — 집행 훅 이벤트 (Execution → Control Plane, push)
# ---------------------------------------------------------------------------


class HookEventIngest(BaseModel):
    """외부 런타임의 3-단계 인라인 훅이 집행 결과를 보고하는 페이로드."""

    # 집행 단계 — 접근 판정 / 실행 전 / 실행 후. 그 밖의 값은 422.
    stage: Literal["access", "pre_exec", "post_exec"]
    # 판정 — 허용 / 차단 / 마스킹 / 승인 필요 / 승인됨 / 수정됨. 그 밖의 값은 422.
    decision: Literal["allow", "deny", "mask", "require_approval", "approved", "modified"]
    action_type: str | None = None
    target: str | None = None
    policy_ref: str | None = None
    reason: str | None = None
    session_id: str | None = None
    consumer: str | None = None
    metadata: dict[str, Any] | None = None


class HookEventResponse(BaseModel):
    id: int
    occurred_at: datetime
    stage: str
    decision: str
    action_type: str | None = None
    target: str | None = None
    policy_ref: str | None = None
    reason: str | None = None
    session_id: str | None = None
    consumer: str | None = None
    metadata: dict[str, Any] | None = Field(default=None, alias="event_metadata")

    model_config = {"from_attributes": True, "populate_by_name": True}
