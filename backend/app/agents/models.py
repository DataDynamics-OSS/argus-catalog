"""AI Agent 카탈로그의 SQLAlchemy ORM 모델 정의.

데이터셋/모델 카탈로그와 동급의 1급 엔티티로 AI 에이전트를 등록·관리한다.
기존 메타데이터 위에 "에이전트 고유" 속성(tools, MCP, 실행정책, 가드레일,
거버넌스, 평가/관측)을 얹는 형태로 설계되었다. 설계 근거는
``design/ai-agent-catalog.md`` 의 통합 메타데이터 모델(G1~G12) 참조.

주요 테이블:
  - ``catalog_ai_agents``              : 메인 엔티티 (식별/모델/실행/인터페이스/거버넌스/관측)
  - ``catalog_ai_agent_versions``      : 사양 버전 이력 (system_prompt 스냅샷 포함)
  - ``catalog_ai_agent_tools``         : 호출 가능 도구 + 스키마
  - ``catalog_ai_agent_mcp_servers``   : 연결된 MCP 서버
  - ``catalog_ai_agent_lineage``       : 에이전트 ↔ 에이전트/모델/데이터셋 의존 관계

이름/버전 규칙:
  - ``name``    : 전사 고유 식별자 (예: ``cs.payment-refund-assistant``)
  - ``urn``     : ``{name}.{ENV}.agent`` (예: ``...PROD.agent``)
  - ``version`` : SemVer 문자열 (예: ``1.3.0-release``)

상태(``status``) 라이프사이클:
  ``draft`` → ``staging`` → ``active`` → ``blocked`` / ``deprecated`` / ``retired``
  소프트 삭제는 ``status='retired'`` 로 표현하지 않고 별도 처리 없이 목록에서
  필터링한다(모델 레지스트리의 ``deleted`` 와 동일한 컨벤션).

JSON 컬럼:
  capabilities/input_schema/output_schema/guardrails 등 구조화 메타데이터는
  SQLAlchemy 제네릭 ``JSON`` 타입으로 저장한다(PostgreSQL JSON / MariaDB JSON).
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


class AIAgent(Base):
    """등록된 AI 에이전트 (메인 엔티티).

    스칼라 컬럼은 검색·필터·정렬 대상 핵심 속성을, JSON 컬럼은 구조화된
    에이전트 고유 메타데이터(입출력 계약, 가드레일, RAG 설정 등)를 담는다.
    """

    __tablename__ = "catalog_ai_agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 전사 고유 식별자 (display 가 아닌 머신용 키)
    name = Column(String(255), nullable=False, unique=True)
    # URN: {name}.{ENV}.agent
    urn = Column(String(500), nullable=False, unique=True)
    # UI 노출명
    display_name = Column(String(255))
    # LLM 자동생성 + 휴먼 승인 대상
    description = Column(Text)
    # SemVer 문자열 (결정 2)
    version = Column(String(50), nullable=False, default="0.1.0")
    # draft / staging / active / blocked / deprecated / retired
    status = Column(String(20), nullable=False, default="draft")
    owner_email = Column(String(200))
    department = Column(String(200))
    # 데이터분석 / 고객지원 / 코드생성 등
    category = Column(String(100))

    # --- 모델 정보 (G3): 자유 문자열 + 선택적 사내 레지스트리 FK (결정 4) ---
    base_model = Column(String(255))
    base_model_ref = Column(
        Integer,
        ForeignKey("catalog_registered_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_provider = Column(String(100))

    # --- 아키텍처 / 실행 (G5) ---
    framework = Column(String(100))
    # ReAct / Plan-Execute / Sequential / Reflection
    execution_policy = Column(String(50))
    max_steps = Column(Integer)
    # stateless / short-term / long-term
    memory_type = Column(String(30))
    is_multi_agent = Column(Boolean, nullable=False, default=False)

    # --- 인터페이스 / 호출 (G7) ---
    endpoint = Column(String(1000))
    # REST / MCP / A2A
    protocol = Column(String(30))
    # sync / async / streaming
    invocation_method = Column(String(30))
    # 스트리밍 응답 지원 여부 (A2A 카드 streaming)
    streaming = Column(Boolean, nullable=False, server_default="false")
    # API key / OAuth2.1(PKCE) / SSO
    auth_method = Column(String(50))

    # --- 거버넌스 / 보안 (G8) — "그림자 액션" 방지 1차 필수 ---
    pii_handling = Column(String(30))
    data_residency = Column(String(100))
    budget_limit = Column(Numeric(12, 4))
    hitl_required = Column(Boolean, nullable=False, default=False)
    audit_log_ref = Column(String(500))

    # --- 관측 / 성능 (G9) — 미터링 집계 캐시 ---
    latency_p50 = Column(Integer)
    latency_p95 = Column(Integer)
    error_rate = Column(Numeric(5, 4))
    avg_token_usage = Column(Integer)
    cost_per_call = Column(Numeric(12, 6))
    # 성공률·평점·에러빈도 종합 신뢰등급 (파생, G10)
    reputation_score = Column(Numeric(5, 2))

    # --- 구조화 메타데이터 (JSON) ---
    capabilities = Column(JSON)  # list[str]
    input_schema = Column(JSON)  # JSON Schema dict
    output_schema = Column(JSON)  # JSON Schema dict
    supported_languages = Column(JSON)  # list[str] e.g. ["ko","en"]
    use_cases = Column(JSON)  # list[str]
    limitations = Column(JSON)  # list[str]
    inference_params = Column(JSON)  # dict (temperature, top_p ...)
    guardrails = Column(JSON)  # dict (입출력 필터, 금지 행위)
    rag_config = Column(JSON)  # dict (vectorstore, retriever, top_k)
    network_allowlist = Column(JSON)  # list[str] egress 허용 도메인
    dlp_policies = Column(JSON)  # list[str]
    hitl_config = Column(JSON)  # dict (중요 액션 사람 서명 정책)
    sub_agents = Column(JSON)  # list[dict] 하위 에이전트 구성
    tags = Column(JSON)  # list[str] (MVP: 단순 문자열 태그)

    # --- 라이프사이클 / 사용 통계 (G12) ---
    usage_count = Column(Integer, nullable=False, default=0)
    last_invoked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))


class AIAgentVersion(Base):
    """에이전트 사양 버전 이력 (system_prompt 스냅샷 포함).

    SemVer 문자열을 키로 사용한다. 새 버전 생성 시 당시의 system_prompt 와
    변경 사유(changelog)를 함께 보관해 롤백/감사를 지원한다.
    """

    __tablename__ = "catalog_ai_agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    # SemVer 문자열
    version = Column(String(50), nullable=False)
    # Git repo, 아티팩트 경로 등 사양 원본 위치
    source = Column(String(1000))
    # 해당 버전의 system_prompt 스냅샷 (버전 관리 대상)
    system_prompt = Column(Text)
    changelog = Column(Text)
    status = Column(String(30), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200))


class AIAgentTool(Base):
    """에이전트가 자율 판단 하에 호출할 수 있는 도구 + 스키마 (G4)."""

    __tablename__ = "catalog_ai_agent_tools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(200), nullable=False)
    description = Column(Text)
    # 함수 호출 파라미터 스키마 (JSON Schema)
    tool_schema = Column(JSON)
    # 위험도(low/medium/high/critical) 및 실행 전 승인 필요 여부
    risk = Column(String(20))
    requires_approval = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AIAgentMcpServer(Base):
    """에이전트가 연결한 Model Context Protocol(MCP) 서버 (G4)."""

    __tablename__ = "catalog_ai_agent_mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(200), nullable=False)
    url = Column(String(1000))
    # API key / OAuth2.1 / none
    auth_method = Column(String(50))
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AIAgentLineage(Base):
    """에이전트 ↔ 다른 에이전트/모델/데이터셋 의존 관계 (G11).

    ``relation`` 으로 방향/성격을 구분한다:
      - ``depends_on``  : 이 에이전트가 의존하는 대상
      - ``consumed_by`` : 이 에이전트를 호출하는 주체
      - ``related``     : 연관 에이전트
    ``target_type`` 은 ``agent`` / ``model`` / ``dataset``, ``target_ref`` 는
    대상의 name 또는 URN(외부 대상도 자유 문자열로 허용).
    """

    __tablename__ = "catalog_ai_agent_lineage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    # agent / model / dataset
    target_type = Column(String(20), nullable=False, default="agent")
    # 대상 name 또는 URN
    target_ref = Column(String(500), nullable=False)
    # depends_on / consumed_by / related
    relation = Column(String(20), nullable=False, default="depends_on")
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AIAgentEval(Base):
    """에이전트 평가 결과 (G10).

    판독전용 LLM/벤치마크/휴먼 피드백이 산출한 정성·정량 점수를 적재한다.
    실제 채점은 외부 Execution Plane(또는 평가 잡)이 수행하고, 카탈로그는
    결과만 수신·집계한다(Control/Execution Plane 분리 — 결정 3).

    ``eval_type`` 예시: accuracy / task_success / hallucination / safety /
    user_rating. ``metric_value`` 는 0~1 정규화 권장(평판 점수 산출에 사용).
    """

    __tablename__ = "catalog_ai_agent_evals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    # 평가 대상 사양 버전 (SemVer, nullable)
    version = Column(String(50))
    # accuracy / task_success / hallucination / safety / user_rating ...
    eval_type = Column(String(50), nullable=False)
    metric_key = Column(String(100), nullable=False)
    metric_value = Column(Numeric(10, 4), nullable=False)
    # 평가 데이터셋/케이스 참조
    dataset_ref = Column(String(500))
    passed = Column(Boolean)
    notes = Column(Text)
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_by = Column(String(200))


class AIAgentInvocationLog(Base):
    """에이전트 호출 미터링 원장 (G9·G12).

    외부 런타임이 호출마다 텔레메트리(토큰·비용·지연·성공여부·소비자)를
    push 한다. 집계 결과는 메인 엔티티의 관측 컬럼(latency_p50/p95,
    error_rate, avg_token_usage, cost_per_call, usage_count, reputation_score)에
    캐시된다.
    """

    __tablename__ = "catalog_ai_agent_invocation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoked_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    # 호출 주체 (사용자/팀/에이전트 식별자)
    consumer = Column(String(200))
    # success / error
    status = Column(String(20), nullable=False, default="success")
    error_type = Column(String(100))
    latency_ms = Column(Integer)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cost = Column(Numeric(12, 6))
    session_id = Column(String(200))


class AIAgentHookEvent(Base):
    """집행(Enforcement) 훅 이벤트 — 외부 Execution Plane이 push 하는 감사 기록.

    Phase 3 연동 인터페이스(Control/Execution Plane 분리 — 결정 3)의 일부.
    카탈로그는 정책을 *정의*하고(``GET /policy``), 외부 런타임은 3-단계 인라인
    훅(Access / Pre-Execution / Post-Execution)에서 정책을 *집행*한 뒤 그 결과를
    이 테이블로 보고한다. 카탈로그는 집행을 수행하지 않고 결과만 수신·보관한다.

    예) egress 차단, PII 마스킹, HITL 승인 요청/승인, 예산 초과 거부.
    """

    __tablename__ = "catalog_ai_agent_hook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    # access / pre_exec / post_exec
    stage = Column(String(20), nullable=False)
    # allow / deny / mask / require_approval / approved / modified
    decision = Column(String(30), nullable=False)
    # tool_call / network_egress / data_write / budget_spend / browse ...
    action_type = Column(String(50))
    # 대상(도메인/도구명/데이터셋 등)
    target = Column(String(500))
    # 발동된 정책 키 (network_allowlist / dlp_policies / hitl_config / guardrails ...)
    policy_ref = Column(String(100))
    reason = Column(Text)
    session_id = Column(String(200))
    consumer = Column(String(200))
    event_metadata = Column(JSON)


class AIAgentStatusHistory(Base):
    """에이전트 상태 변경 이력 (자동 기록)."""

    __tablename__ = "catalog_ai_agent_status_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("catalog_ai_agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status = Column(String(20))
    to_status = Column(String(20), nullable=False)
    note = Column(Text)
    changed_by = Column(String(200))
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
