# SPDX-License-Identifier: Apache-2.0
"""MCP Server 카탈로그 ORM 모델.

설계: ``design/mcp-server-catalog.md`` §3·§4. MCP 서버를 **정의(Server)** 와
**배포 인스턴스(Deployment)** 2계층으로 관리한다.

  - ``catalog_mcp_servers``                 : 서버 정의 (제품/패키지 단위)
  - ``catalog_mcp_server_tool_groups``      : 도구 그룹 (read / write / dml / destructive …)
  - ``catalog_mcp_server_tools``            : 전체 도구 목록
  - ``catalog_mcp_server_resources``        : MCP resources / resource templates
  - ``catalog_mcp_server_prompts``          : MCP prompts
  - ``catalog_mcp_deployments``             : 배포 인스턴스 (endpoint·환경·실행 계정·헬스)
  - ``catalog_mcp_deployment_credentials``  : 배포 접속 자격증명 (Fernet 암호화)
  - ``catalog_mcp_deployment_group_states`` : 인스턴스별 도구 그룹 on/off (observed / approved)
  - ``catalog_mcp_deployment_tool_states``  : 인스턴스별 도구 노출 상태 (observed / approved)
  - ``catalog_mcp_resource_scopes``         : 리소스 범위 규칙 (정규화, observed / approved)
  - ``catalog_mcp_status_history``          : 정의·배포 상태 변경 이력
  - ``catalog_mcp_sync_log``                : 수집·헬스체크·가져오기 실행 이력
  - ``catalog_mcp_deployment_findings``     : 드리프트·보안 진단 (Phase 2)
  - ``catalog_mcp_policy_history``          : 승인 정책 저장 이력 (Phase 2)
  - ``catalog_mcp_audit_events``            : 변경 도구 호출 감사 이벤트 (Phase 2)
  - ``catalog_mcp_server_versions``         : capability 버전 스냅숏 (Phase 2)

정보 출처(origin) 구분:
  각 필드가 MCP 표준(서버가 보고 / Registry server.json)에서 온 값인지, 서버 설정에서
  수집한 값인지, **Argus Catalog 에서만 관리하는 값**인지는 ``origins.py`` 의 레지스트리로
  선언하고 API(`/mcp-servers/field-origins`)와 UI 배지로 노출한다. 도구·리소스·프롬프트
  행 단위 출처는 ``source`` 컬럼(manual / mcp / forge_rest / manifest / forge_seed / config 등)으로 남긴다.

상태(``status``) 라이프사이클 (AI Agent 와 동일):
  ``draft`` → ``staging`` → ``active`` → ``blocked`` / ``deprecated`` / ``retired``
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base

# ---------------------------------------------------------------------------
# 정의
# ---------------------------------------------------------------------------


class McpServer(Base):
    """MCP 서버 정의 (제품/패키지 단위)."""

    __tablename__ = "catalog_mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # --- Argus 식별 ---
    name = Column(String(255), nullable=False, unique=True)
    urn = Column(String(500), nullable=False, unique=True)
    display_name = Column(String(255))
    description = Column(Text)
    status = Column(String(20), nullable=False, default="draft")
    version = Column(String(50), nullable=False, default="0.1.0")
    owner_email = Column(String(200))
    department = Column(String(200))
    category = Column(String(100))
    tags = Column(JSON)
    provider_type = Column(String(30), nullable=False, default="community")
    vendor = Column(String(200))
    # argus-forge / generic — 수집 경로·설정 매핑 선택에 사용
    framework = Column(String(50), nullable=False, default="generic")
    target_platform = Column(String(100))
    source_repo = Column(String(500))
    source_path = Column(String(500))
    # --- MCP Registry server.json 표준 ---
    registry_name = Column(String(255))
    title = Column(String(255))
    website_url = Column(String(1000))
    repository_url = Column(String(1000))
    license = Column(String(100))
    packages = Column(JSON)
    remotes = Column(JSON)
    # --- 배포 산출물(Argus) ---
    package_checksum = Column(String(200))
    oci_artifact_ref = Column(String(500))
    supported_transports = Column(JSON)
    launch_config = Column(JSON)
    # --- MCP 서버 보고값(initialize) ---
    server_info = Column(JSON)
    protocol_version = Column(String(30))
    capabilities = Column(JSON)
    instructions = Column(Text)
    # --- 동기화 ---
    capability_hash = Column(String(64))
    last_synced_at = Column(DateTime(timezone=True))
    # --- 감사 ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))


class McpServerToolGroup(Base):
    """도구 그룹 — Forge ``allow.<그룹>`` 또는 annotation 기반 자동 그룹."""

    __tablename__ = "catalog_mcp_server_tool_groups"
    __table_args__ = (UniqueConstraint("mcp_server_id", "group_key", name="uq_mcp_tool_group_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    key = Column("group_key", String(100), nullable=False)
    label = Column(String(200))
    description = Column(Text)
    # 서버 설정 키 (예: "hive.allow.dml") — 설정 가져오기·드리프트 매핑
    config_key = Column(String(300))
    default_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    default_risk = Column(String(20), nullable=False, default="medium")
    mutating = Column(Boolean, nullable=False, default=False, server_default="false")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class McpServerTool(Base):
    """서버가 제공하는 도구 (MCP ``tools/list`` 항목 + Argus 거버넌스 속성)."""

    __tablename__ = "catalog_mcp_server_tools"
    __table_args__ = (UniqueConstraint("mcp_server_id", "name", name="uq_mcp_tool_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    group_id = Column(
        Integer, ForeignKey("catalog_mcp_server_tool_groups.id", ondelete="SET NULL")
    )
    # --- MCP 표준 ---
    name = Column(String(200), nullable=False)
    title = Column(String(300))
    description = Column(Text)
    input_schema = Column(JSON)
    output_schema = Column(JSON)
    annotations = Column(JSON)
    meta = Column(JSON)
    # --- Argus ---
    mutating = Column(Boolean, nullable=False, default=False, server_default="false")
    risk = Column(String(20))
    risk_confirmed = Column(Boolean, nullable=False, default=False, server_default="false")
    requires_approval = Column(Boolean, nullable=False, default=False, server_default="false")
    resource_types = Column(JSON)
    notes = Column(Text)
    source = Column(String(30), nullable=False, default="manual")
    removed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class McpServerResource(Base):
    """MCP resources / resource templates."""

    __tablename__ = "catalog_mcp_server_resources"
    __table_args__ = (UniqueConstraint("mcp_server_id", "uri", name="uq_mcp_resource_uri"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    # uri 또는 uriTemplate
    uri = Column(String(1000), nullable=False)
    is_template = Column(Boolean, nullable=False, default=False, server_default="false")
    name = Column(String(300))
    title = Column(String(300))
    description = Column(Text)
    mime_type = Column(String(200))
    annotations = Column(JSON)
    source = Column(String(30), nullable=False, default="manual")
    removed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class McpServerPrompt(Base):
    """MCP prompts."""

    __tablename__ = "catalog_mcp_server_prompts"
    __table_args__ = (UniqueConstraint("mcp_server_id", "name", name="uq_mcp_prompt_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(300), nullable=False)
    title = Column(String(300))
    description = Column(Text)
    arguments = Column(JSON)
    source = Column(String(30), nullable=False, default="manual")
    removed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 배포
# ---------------------------------------------------------------------------


class McpDeployment(Base):
    """MCP 서버 배포 인스턴스 (예: hive-mcp @ 운영 클러스터)."""

    __tablename__ = "catalog_mcp_deployments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False, unique=True)
    urn = Column(String(500), nullable=False, unique=True)
    display_name = Column(String(255))
    description = Column(Text)
    environment = Column(String(20), nullable=False, default="DEV")
    status = Column(String(20), nullable=False, default="draft")
    owner_email = Column(String(200))
    department = Column(String(200))
    tags = Column(JSON)
    # --- 연결 ---
    transport = Column(String(30), nullable=False, default="streamable_http")
    endpoint = Column(String(1000))
    rest_api_base = Column(String(1000))
    auth_method = Column(String(30), nullable=False, default="none")
    auth_config = Column(JSON)
    deployment_scope = Column(String(30), nullable=False, default="shared")
    host = Column(String(300))
    # --- 대상 시스템 / 실행 계정 (서버 설정에서 수집 또는 입력) ---
    target_endpoint = Column(String(1000))
    datasource_id = Column(
        Integer, ForeignKey("catalog_datasources.id", ondelete="SET NULL")
    )
    service_principal = Column(String(300))
    principal_auth = Column(String(50))
    limits = Column(JSON)
    security_warnings = Column(JSON)
    # --- MCP 서버 보고값 ---
    deployed_version = Column(String(100))
    server_info = Column(JSON)
    protocol_version = Column(String(30))
    # --- 통제(Argus) ---
    policy_mode = Column(String(20), nullable=False, default="observe")
    policy_version = Column(String(100))
    collect_interval_minutes = Column(Integer)
    # --- 승인 정책(Argus, Phase 2) — 그룹·도구·범위 승인값은 state/scope 테이블의 approved 쪽 ---
    approved_limits = Column(JSON)
    approved_version = Column(String(100))
    policy_updated_at = Column(DateTime(timezone=True))
    policy_updated_by = Column(String(200))
    # --- 감사 이벤트 수집 토큰 (sha256 해시만 저장) ---
    ingest_token_hash = Column(String(64))
    ingest_token_hint = Column(String(20))  # 화면 표시용 앞뒤 일부(mcpi_xxxx…yyyy)
    ingest_token_created_at = Column(DateTime(timezone=True))
    audit_retention_days = Column(Integer, nullable=False, default=180, server_default="180")
    # --- 정책 번들 pull 기록 (Phase 3, enforce) ---
    policy_pulled_at = Column(DateTime(timezone=True))
    policy_pulled_version = Column(String(100))
    policy_pull_instance = Column(String(200))
    # 연속 헬스 체크 실패 횟수 — 임계값 이상이면 unhealthy finding
    health_fail_count = Column(Integer, nullable=False, default=0, server_default="0")
    # --- 헬스 / 수집 ---
    health_status = Column(String(20), nullable=False, default="unknown")
    last_health_check_at = Column(DateTime(timezone=True))
    latency_ms = Column(Integer)
    instance_id = Column(String(200))
    observed_hash = Column(String(64))
    last_observed_at = Column(DateTime(timezone=True))
    observed_source = Column(String(30))
    # --- 감사 ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))


class McpDeploymentCredential(Base):
    """배포 접속 자격증명 — 값은 Fernet 암호문으로만 저장한다."""

    __tablename__ = "catalog_mcp_deployment_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer,
        ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # bearer / api_key / basic
    kind = Column(String(30), nullable=False, default="bearer")
    # api_key 일 때 헤더 이름 (기본 X-API-Key)
    header_name = Column(String(100))
    username = Column(String(200))
    secret_encrypted = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(200))


class McpDeploymentGroupState(Base):
    """인스턴스별 도구 그룹 on/off."""

    __tablename__ = "catalog_mcp_deployment_group_states"
    __table_args__ = (
        UniqueConstraint("deployment_id", "group_id", name="uq_mcp_dep_group_state"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    group_id = Column(
        Integer,
        ForeignKey("catalog_mcp_server_tool_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 관측값 (NULL = 모름)
    observed_enabled = Column(Boolean)
    # 관측 근거: config(설정 파일) / manifest / inferred(노출 도구로 추론)
    observed_basis = Column(String(20))
    # 승인값 (Phase 2)
    approved_enabled = Column(Boolean)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class McpDeploymentToolState(Base):
    """인스턴스별 도구 노출 상태."""

    __tablename__ = "catalog_mcp_deployment_tool_states"
    __table_args__ = (
        UniqueConstraint("deployment_id", "tool_id", name="uq_mcp_dep_tool_state"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    tool_id = Column(
        Integer, ForeignKey("catalog_mcp_server_tools.id", ondelete="CASCADE"), nullable=False
    )
    observed_exposed = Column(Boolean)
    observed_at = Column(DateTime(timezone=True))
    # 승인값 (Phase 2/3)
    approved_blocked = Column(Boolean, nullable=False, default=False, server_default="false")
    approved_requires_approval = Column(Boolean)


class McpResourceScope(Base):
    """리소스 범위 규칙 — 서버별 설정 키를 "종류 × 동작 × 효과 × 패턴" 으로 정규화."""

    __tablename__ = "catalog_mcp_resource_scopes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    # 에이전트별 추가 제한 (catalog_ai_agent_mcp_servers.id) — NULL = 인스턴스 전체
    agent_link_id = Column(
        Integer, ForeignKey("catalog_ai_agent_mcp_servers.id", ondelete="CASCADE")
    )
    # observed(서버 설정·manifest 관측) / approved(카탈로그 승인)
    layer = Column(String(20), nullable=False, default="observed")
    resource_type = Column(String(50), nullable=False)
    # read / write / delete / any
    action = Column(String(20), nullable=False, default="any")
    # allow / deny / protect / mask
    effect = Column(String(20), nullable=False)
    patterns = Column(JSON, nullable=False)  # 패턴 문자열 목록
    match = Column("match_type", String(20), nullable=False, default="fnmatch")
    config_key = Column(String(300))
    description = Column(Text)
    source = Column(String(30), nullable=False, default="manual")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 이력
# ---------------------------------------------------------------------------


class McpStatusHistory(Base):
    """정의·배포 상태 변경 이력."""

    __tablename__ = "catalog_mcp_status_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # server / deployment
    entity_type = Column(String(20), nullable=False)
    entity_id = Column(Integer, nullable=False)
    from_status = Column(String(20))
    to_status = Column(String(20), nullable=False)
    note = Column(Text)
    changed_by = Column(String(200))
    changed_at = Column(DateTime(timezone=True), server_default=func.now())


class McpDeploymentFinding(Base):
    """드리프트·보안 진단 결과 (설계 §5.4).

    ``fingerprint`` = kind + 대상(subject) — 같은 문제는 한 행으로 유지하고
    ``last_seen_at`` 만 갱신한다. 조건이 사라지면 ``resolved`` 로 닫고, 다시 생기면 재오픈한다.
    """

    __tablename__ = "catalog_mcp_deployment_findings"
    __table_args__ = (
        UniqueConstraint("deployment_id", "fingerprint", name="uq_mcp_finding_fp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    kind = Column(String(50), nullable=False)
    fingerprint = Column(String(300), nullable=False)
    subject = Column(String(500))
    severity = Column(String(20), nullable=False, default="medium")
    title = Column(String(500), nullable=False)
    detail = Column(JSON)
    # open / acknowledged / resolved
    status = Column(String(20), nullable=False, default="open")
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
    acknowledged_by = Column(String(200))
    acknowledged_at = Column(DateTime(timezone=True))
    ack_note = Column(Text)
    # 이 finding 으로 발행된 마지막 알림 (argus_lineage_alert.id)
    alert_id = Column(Integer)


class McpPolicyHistory(Base):
    """승인 정책 저장 이력 — 저장 시점의 승인 정책 전체 스냅숏."""

    __tablename__ = "catalog_mcp_policy_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    policy_version = Column(String(100), nullable=False)
    snapshot = Column(JSON, nullable=False)
    note = Column(Text)
    changed_by = Column(String(200))
    changed_at = Column(DateTime(timezone=True), server_default=func.now())


class McpAuditEvent(Base):
    """변경 도구 호출 감사 이벤트 (Forge ``Audit`` JSONL 한 줄)."""

    __tablename__ = "catalog_mcp_audit_events"
    __table_args__ = (
        UniqueConstraint("deployment_id", "event_hash", name="uq_mcp_audit_event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE"), nullable=False
    )
    ts = Column(DateTime(timezone=True), nullable=False)
    session_id = Column(String(200))
    tool_name = Column(String(200), nullable=False)
    tool_id = Column(Integer, ForeignKey("catalog_mcp_server_tools.id", ondelete="SET NULL"))
    group_key = Column(String(100))
    risk = Column(String(20))
    user_name = Column(String(200))
    args = Column(JSON)
    ok = Column(Boolean, nullable=False, default=True)
    error = Column(String(500))
    result_id = Column(String(300))
    # 인자에서 추출한 리소스 [{"type": "topic", "value": "orders"}]
    resources = Column(JSON)
    # 호출한 에이전트 (세션 토큰 agent 클레임 — Phase 3)
    agent_name = Column(String(300))
    # 서버가 카탈로그 정책으로 실제 거부했으면 "denied" (enforce 모드, Phase 3)
    server_decision = Column(String(20))
    # 승인 정책 기준 판정: allow / deny / unknown / blocked(차단 도구 호출)
    policy_decision = Column(String(20))
    policy_reason = Column(String(500))
    event_hash = Column(String(64), nullable=False)
    received_via = Column(String(20))  # token / upload
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class McpServerVersion(Base):
    """서버 capability 스냅숏 — 도구·리소스·프롬프트 구성이 바뀔 때마다 한 행."""

    __tablename__ = "catalog_mcp_server_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    capability_hash = Column(String(64), nullable=False)
    # 서버가 보고한 버전 (serverInfo.version / manifest server.version)
    reported_version = Column(String(100))
    tool_count = Column(Integer, nullable=False, default=0)
    # {"tools": {name: hash}, "resources": [...], "prompts": [...]}
    snapshot = Column(JSON, nullable=False)
    # 직전 스냅숏 대비 {"tools": {"added","removed","changed"}, ...}
    diff = Column(JSON)
    source = Column(String(30))
    deployment_id = Column(Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="SET NULL"))
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class McpSyncLog(Base):
    """수집·헬스체크·가져오기 실행 이력."""

    __tablename__ = "catalog_mcp_sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id = Column(
        Integer, ForeignKey("catalog_mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id = Column(
        Integer, ForeignKey("catalog_mcp_deployments.id", ondelete="CASCADE")
    )
    # collect / health / config_import / manifest_import
    kind = Column(String(30), nullable=False)
    # mcp / forge_rest / manifest / config (health 는 NULL)
    source = Column(String(30))
    ok = Column(Boolean, nullable=False)
    message = Column(Text)
    # {"tools": {"added": [...], "removed": [...], "changed": [...]}, ...}
    diff = Column(JSON)
    duration_ms = Column(Integer)
    triggered_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
