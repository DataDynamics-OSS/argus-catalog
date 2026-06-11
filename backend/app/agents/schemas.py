"""AI Agent 카탈로그의 Pydantic 스키마(요청·응답 DTO).

``/api/v1/ai-agents`` 라우터와 서비스 레이어가 사용한다. JSON 컬럼은
list/dict 로 그대로 노출되며 ``from_attributes`` 로 ORM → 응답 변환된다.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------


class AgentStatus(str, Enum):
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
    max_steps: int | None = None
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
    budget_limit: float | None = None
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
    max_steps: int | None = None
    memory_type: str | None = None
    is_multi_agent: bool | None = None
    endpoint: str | None = None
    protocol: str | None = None
    streaming: bool | None = None
    invocation_method: str | None = None
    auth_method: str | None = None
    pii_handling: str | None = None
    data_residency: str | None = None
    budget_limit: float | None = None
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
    name: str = Field(..., min_length=1, max_length=200)
    url: str | None = None
    auth_method: str | None = None
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

    model_config = {"populate_by_name": True}


class PolicyMcpServer(BaseModel):
    name: str
    url: str | None = None
    auth_method: str | None = None


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
    # 변경 감지용 버전 토큰 (agent.updated_at 기반)
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

    # access / pre_exec / post_exec
    stage: str = Field(..., max_length=20)
    # allow / deny / mask / require_approval / approved / modified
    decision: str = Field(..., max_length=30)
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
