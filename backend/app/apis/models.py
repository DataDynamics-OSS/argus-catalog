# SPDX-License-Identifier: Apache-2.0
"""API Catalog 의 SQLAlchemy ORM 모델.

데이터셋/모델/AI 에이전트와 동급의 1급 엔티티로 API 를 등록·문서화·호출·
거버넌스한다. OpenAPI(Swagger) 스펙을 업로드/URL 로 등록하면 파서가 엔드포인트·
서버·보안 스킴을 추출해 함께 저장한다.

테이블:
  - ``catalog_apis``                  : 메인 엔티티(식별/소유/분류/상태/버전)
  - ``catalog_api_specs``             : 스펙 버전 이력(원본 raw + 파싱 결과)
  - ``catalog_api_endpoints``         : 엔드포인트(메서드/경로/요청·응답 스키마/보안)
  - ``catalog_api_servers``           : 서버/환경(base URL)
  - ``catalog_api_security_schemes``  : 인증 스킴(apiKey/oauth2/http/mutualTLS…)
  - ``catalog_api_status_history``    : 상태 변경 이력(자동 기록)
  - ``catalog_api_alerts``            : 스펙 Breaking 변경 알림(자동 생성)
  - ``catalog_api_lineage``           : provides/consumes 리니지(관계 엣지)
  - ``catalog_api_invocations``       : Try-it 호출 로그(사용량 관측·미터링·입력 이력)
  - ``catalog_api_favorites``         : 사용자별 엔드포인트 즐겨찾기
"""

from sqlalchemy import (
    JSON,
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


class CatalogApi(Base):
    """등록된 API (메인 엔티티)."""

    __tablename__ = "catalog_apis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    urn = Column(String(500), nullable=False, unique=True)
    display_name = Column(String(255))
    description = Column(Text)
    version = Column(String(50), nullable=False, default="1.0.0")
    # draft / published / deprecated / retired
    status = Column(String(20), nullable=False, default="draft")
    owner_email = Column(String(200))
    department = Column(String(200))
    category = Column(String(100))
    # REST / GraphQL / gRPC / AsyncAPI
    protocol = Column(String(30), default="REST")
    # 엔드포인트 출처: spec(스펙 파싱) / manual(수동 등록·엔드포인트 직접 관리)
    source = Column(String(10), nullable=False, default="spec")
    # openapi2 / openapi3 / asyncapi / ...
    spec_format = Column(String(20))
    # 기본 base URL (대표 서버)
    base_url = Column(String(1000))
    # 사용자가 Base URL 을 수동 지정했는지(true 면 스펙 업로드가 덮어쓰지 않음)
    base_url_overridden = Column(String(5), default="false")
    # 계약/스키마 문서(비REST 프로토콜의 SDL/WSDL/.proto/AsyncAPI 등) — 원문/URL
    contract_text = Column(Text)
    contract_url = Column(String(1000))
    # 인증 상태 / 등급
    certification = Column(String(20))
    tier = Column(String(20))
    tags = Column(JSON)  # list[str]
    note = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))


class CatalogApiSpec(Base):
    """API 스펙 버전 이력 — 원본(raw) + 파싱 결과(parsed) 보관."""

    __tablename__ = "catalog_api_specs"
    __table_args__ = (UniqueConstraint("api_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    format = Column(String(20))  # openapi2 / openapi3 / ...
    raw = Column(Text)  # 원본 스펙 텍스트(JSON/YAML 정규화 JSON)
    parsed = Column(JSON)  # 파싱 요약(info/servers/securitySchemes 등)
    source_url = Column(String(1000))
    is_current = Column(String(5), nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200))


class CatalogApiEndpoint(Base):
    """API 엔드포인트(오퍼레이션)."""

    __tablename__ = "catalog_api_endpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    # 오퍼레이션 유형 — REST: GET/POST… / GraphQL: query·mutation / gRPC: unary·server-streaming / SOAP·Webhook 등
    method = Column(String(40), nullable=False)
    # 오퍼레이션 식별자 — REST: 경로 / GraphQL: 이름 / gRPC: Service.Method / Webhook: 채널·이벤트
    path = Column(String(1000), nullable=False)
    operation_id = Column(String(255))
    summary = Column(Text)
    description = Column(Text)
    tags = Column(JSON)  # list[str]
    parameters = Column(JSON)  # list[dict] (인자/파라미터)
    request_body = Column(JSON)  # dict (요청/입력 스키마)
    responses = Column(JSON)  # dict (응답/출력)
    security = Column(JSON)  # list (보안 요구)
    extra = Column(JSON)  # 프로토콜별 추가 속성(soap_action, grpc 메시지, graphql 반환타입 등)
    sort_order = Column(Integer, default=0)


class CatalogApiServer(Base):
    """API 서버/환경(base URL)."""

    __tablename__ = "catalog_api_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(1000), nullable=False)
    description = Column(String(500))
    env = Column(String(20))  # PROD/STAGING/DEV (선택)


class CatalogApiSecurityScheme(Base):
    """API 인증 스킴."""

    __tablename__ = "catalog_api_security_schemes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_name = Column(String(100), nullable=False)  # 스펙상 키
    type = Column(String(30))  # apiKey/oauth2/http/openIdConnect/mutualTLS
    config = Column(JSON)  # 상세(in/name/scheme/flows…)


class CatalogApiCredential(Base):
    """API 호출용 자격증명 — 시크릿은 암호화(Fernet)하여 저장.

    Try-it 콘솔에서 선택 시 서버가 복호화해 요청에 주입한다(브라우저 미노출).
    """

    __tablename__ = "catalog_api_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_name = Column(String(100))  # 연결된 보안 스킴 키(있으면)
    label = Column(String(200), nullable=False)
    # apiKey / bearer / basic / oauth2
    type = Column(String(30), nullable=False)
    secret = Column(Text, nullable=False)  # 암호화된 JSON(values)
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CatalogApiStatusHistory(Base):
    """API 상태 변경 이력(자동 기록)."""

    __tablename__ = "catalog_api_status_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = Column(String(20))
    to_status = Column(String(20), nullable=False)
    note = Column(Text)
    changed_by = Column(String(200))
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class CatalogApiAlert(Base):
    """스펙 버전 업로드 시 Breaking 변경이 감지되면 자동 생성되는 알림.

    생명주기: OPEN → ACKNOWLEDGED. 소비자/소유자가 영향 파악 후 확인 처리한다.
    """

    __tablename__ = "catalog_api_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    from_spec_id = Column(Integer)  # 이전 current 스펙
    to_spec_id = Column(Integer)    # 새로 업로드된 스펙
    from_version = Column(String(50))
    to_version = Column(String(50))
    severity = Column(String(16), nullable=False, default="BREAKING")  # BREAKING
    breaking_count = Column(Integer, nullable=False, default=0)
    summary = Column(String(500), nullable=False)
    detail = Column(Text)  # 변경 상세(JSON: removed/changed)
    status = Column(String(20), nullable=False, default="OPEN")  # OPEN / ACKNOWLEDGED
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    acknowledged_by = Column(String(200))
    acknowledged_at = Column(DateTime(timezone=True))


class CatalogApiLineage(Base):
    """API 의 provides/consumes 리니지(관계 엣지) — Backstage 스타일.

    어떤 시스템/에이전트/데이터셋/모델/API 가 이 API 를 제공·소비·의존하는지 기록한다.
    대상은 카탈로그 내부 엔티티의 참조(이름/URN) 또는 외부 시스템을 자유 입력으로 표현한다.
    """

    __tablename__ = "catalog_api_lineage"
    __table_args__ = (
        UniqueConstraint("api_id", "relation", "target_type", "target_ref", name="uq_api_lineage_edge"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    relation = Column(String(20), nullable=False)   # provides / consumes / depends_on
    target_type = Column(String(20), nullable=False)  # api / dataset / model / agent / system
    target_ref = Column(String(300), nullable=False)  # 대상 식별자(이름/URN/외부 참조)
    target_label = Column(String(300))                # 표시명(선택)
    note = Column(Text)
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CatalogApiInvocation(Base):
    """Try-it 콘솔(프록시) 호출 로그 — 사용량 관측·미터링 기반 데이터.

    호출이 알려진 API 로 식별되는 경우에만 기록한다(임의 URL 호출은 제외).
    """

    __tablename__ = "catalog_api_invocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    method = Column(String(10), nullable=False)
    url = Column(String(2000), nullable=False)
    status_code = Column(Integer, nullable=False, default=0)  # 0 = 네트워크/예외
    ok = Column(String(5), nullable=False, default="false")   # 2xx/3xx 여부
    latency_ms = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    called_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    # 입력 파라미터 이력(호출 탭 "불러오기"용) — 엔드포인트 식별 + 입력값.
    endpoint_method = Column(String(10))    # 템플릿 메서드
    endpoint_path = Column(String(2000))    # 템플릿 경로(파라미터 치환 전)
    request_input = Column(JSON)            # {path_params, query_params, headers(마스킹), body}


class CatalogApiFavorite(Base):
    """사용자별 엔드포인트 즐겨찾기. 스펙 재업로드로 endpoint id 가 바뀌어도 유지되도록
    (method, path) 로 식별한다."""

    __tablename__ = "catalog_api_favorites"
    __table_args__ = (
        UniqueConstraint("user_key", "api_id", "method", "path", name="uq_api_favorite"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_key = Column(String(200), nullable=False, index=True)  # username 또는 email
    api_id = Column(Integer, ForeignKey("catalog_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    method = Column(String(10), nullable=False)
    path = Column(String(2000), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
