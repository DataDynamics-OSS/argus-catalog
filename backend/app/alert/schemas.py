# SPDX-License-Identifier: Apache-2.0
"""리니지 변경 알림 Pydantic 스키마(요청·응답 DTO).

라우터(``app/alert/router.py``) 와 서비스(``app/alert/service.py``) 가 공유하는
규칙(scope/trigger/severity) · 알림(상태 전이) · 요약 DTO 와 열거형을 모아둔다.
"""

import json
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------

class AlertType(str, Enum):
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    QUALITY_FAILED = "QUALITY_FAILED"
    MCP_POLICY_DRIFT = "MCP_POLICY_DRIFT"
    MCP_SECURITY_WARNING = "MCP_SECURITY_WARNING"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BREAKING = "BREAKING"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class RuleScopeType(str, Enum):
    """감시 범위."""
    DATASET = "DATASET"
    TAG = "TAG"
    LINEAGE = "LINEAGE"
    DATASOURCE = "DATASOURCE"
    ALL = "ALL"


class RuleTriggerType(str, Enum):
    """트리거 조건.

    ``SYNC_FAILED``·``SYNC_STALE`` 은 데이터 소스 수집 실행 이력(``catalog_datasource_sync_runs``)으로 평가한다
    (``app.catalog.sync_runs_service``).
    """
    ANY = "ANY"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    COLUMN_WATCH = "COLUMN_WATCH"
    MAPPING_BROKEN = "MAPPING_BROKEN"
    QUALITY_FAILED = "QUALITY_FAILED"   # 품질 진단 실패 — evaluate_quality_alerts 가 평가
    MCP_POLICY_DRIFT = "MCP_POLICY_DRIFT"          # MCP 배포 승인 정책 위반·드리프트 — evaluate_entity_alerts
    MCP_SECURITY_WARNING = "MCP_SECURITY_WARNING"  # MCP 서버 보안 경고
    SYNC_FAILED = "SYNC_FAILED"    # 수집 실행이 실패(또는 일부 실패)로 끝남
    SYNC_STALE = "SYNC_STALE"      # 수집 대상 데이터 소스가 정해진 시간 안에 성공한 수집이 없음
    # AI-Ready (app.catalog.ai_ready_alerts) — 데이터셋 범위
    AI_READY_UNMET = "AI_READY_UNMET"    # 공유 최소 요건 미충족 (주기 점검)
    REPORT_RECEIVED = "REPORT_RECEIVED"  # 오류 신고·개선 요청 접수 (댓글 저장 때)
    REPORT_DUE = "REPORT_DUE"            # 신고 처리 기한 임박·넘김 (주기 점검)
    REPORT_CLOSED = "REPORT_CLOSED"      # 신고 처리 결과(해결·소명) — 신고한 사람에게 (상태 변경 때)


# 엔터티(MCP) 트리거 — 범위는 ALL / DATASOURCE 만 의미가 있다.
ENTITY_TRIGGERS = frozenset({"MCP_POLICY_DRIFT", "MCP_SECURITY_WARNING"})
# 데이터 소스 수집 트리거 — 범위는 ALL / DATASOURCE 만 의미가 있다.
SYNC_TRIGGERS = frozenset({"SYNC_FAILED", "SYNC_STALE"})
# 지원 채널. EMAIL 은 발송기가 없어 받지 않는다(저장해도 아무 데도 가지 않으므로).
SUPPORTED_CHANNELS = ("IN_APP", "WEBHOOK")
_CHANGE_TYPES = ("DROP", "MODIFY", "ADD")
_QUALITY_SEVERITIES = ("INFO", "WARNING", "BREAKING")
_MCP_SEVERITIES = ("low", "medium", "high", "critical")
_MCP_ENVIRONMENTS = ("DEV", "STG", "PROD")
_AI_READY_CONDITIONS = ("missing", "metadata", "freshness", "format")
_REPORT_CATEGORIES = ("bug", "suggestion")


def _str_list(value, field: str, *, allowed: tuple[str, ...] | None = None,
              required: bool = False, max_items: int = 200) -> list[str] | None:
    """trigger_config 의 문자열 목록 항목 검증·정규화(공백 제거·중복 제거·순서 유지)."""
    if value is None:
        if required:
            raise ValueError(f"trigger_config.{field} 는 필수입니다.")
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"trigger_config.{field} 는 문자열 목록이어야 합니다.")
    out: list[str] = []
    for v in value:
        v = v.strip()
        if v and v not in out:
            out.append(v)
    if required and not out:
        raise ValueError(f"trigger_config.{field} 에 값이 하나 이상 있어야 합니다.")
    if len(out) > max_items:
        raise ValueError(f"trigger_config.{field} 는 최대 {max_items}개까지입니다.")
    if allowed:
        bad = [v for v in out if v not in allowed]
        if bad:
            raise ValueError(f"trigger_config.{field} 에 허용되지 않는 값: {', '.join(bad)} "
                             f"(허용: {', '.join(allowed)})")
    return out


def _change_types(cfg: dict) -> list[str] | None:
    """change_types 검증 — 키가 없으면 None(전체 유형), 빈 목록이면 ValueError."""
    ct = _str_list(cfg.get("change_types"), "change_types", allowed=_CHANGE_TYPES)
    if ct is not None and not ct:
        raise ValueError("변경 유형을 하나 이상 선택해야 합니다.")
    return ct


def normalize_trigger_config(trigger_type: str, raw: str | dict | None) -> str:
    """트리거 유형별 trigger_config 를 검증하고 정규화된 JSON 문자열로 돌려준다.

    잘못된 JSON·형태는 ``ValueError`` — 저장 시점에 막아 평가 시점에 다른 규칙까지
    멈추는 일을 없앤다. 트리거와 무관한 키는 버린다.
    """
    if raw is None or raw == "":
        cfg: dict = {}
    elif isinstance(raw, dict):
        cfg = raw
    else:
        try:
            cfg = json.loads(raw)
        except ValueError as e:
            raise ValueError("trigger_config 가 올바른 JSON 이 아닙니다.") from e
    if not isinstance(cfg, dict):
        raise ValueError("trigger_config 는 JSON 객체여야 합니다.")

    out: dict = {}
    if trigger_type == "SCHEMA_CHANGE":
        ct = _change_types(cfg)
        if ct is not None:
            out["change_types"] = ct
    elif trigger_type == "COLUMN_WATCH":
        cols = _str_list(cfg.get("columns"), "columns", required=True)
        if any(len(c) > 255 for c in cols):
            raise ValueError("감시 컬럼 이름은 255자 이하여야 합니다.")
        out["columns"] = cols
        ct = _change_types(cfg)
        if ct is not None:
            out["change_types"] = ct
    elif trigger_type == "QUALITY_FAILED":
        ms = cfg.get("min_severity")
        if ms is not None:
            if ms not in _QUALITY_SEVERITIES:
                raise ValueError(f"min_severity 는 {', '.join(_QUALITY_SEVERITIES)} 중 하나여야 합니다.")
            out["min_severity"] = ms
    elif trigger_type in ENTITY_TRIGGERS:
        ms = cfg.get("min_severity")
        if ms is not None:
            ms = str(ms).lower()
            if ms not in _MCP_SEVERITIES:
                raise ValueError(f"min_severity 는 {', '.join(_MCP_SEVERITIES)} 중 하나여야 합니다.")
            out["min_severity"] = ms
        envs = _str_list(cfg.get("environments"), "environments", allowed=_MCP_ENVIRONMENTS)
        if envs:
            out["environments"] = envs
        kinds = _str_list(cfg.get("kinds"), "kinds")
        if kinds:
            out["kinds"] = kinds
    elif trigger_type == "SYNC_FAILED":
        ip = cfg.get("include_partial")
        if ip is not None:
            if not isinstance(ip, bool):
                raise ValueError("include_partial 은 true/false 여야 합니다.")
            out["include_partial"] = ip
    elif trigger_type == "SYNC_STALE":
        # 생략하면 데이터 소스 예약 주기의 2배(최소 1시간)를 기준으로 본다
        hours = cfg.get("stale_hours")
        if hours is not None:
            if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 720:
                raise ValueError("stale_hours 는 1~720 사이의 정수(시간)여야 합니다.")
            out["stale_hours"] = hours
    elif trigger_type == "AI_READY_UNMET":
        conds = _str_list(cfg.get("conditions"), "conditions", allowed=_AI_READY_CONDITIONS)
        if conds is not None:
            if not conds:
                raise ValueError("최소 요건 조건을 하나 이상 선택해야 합니다.")
            out["conditions"] = conds
    elif trigger_type in ("REPORT_RECEIVED", "REPORT_CLOSED"):
        cats = _str_list(cfg.get("categories"), "categories", allowed=_REPORT_CATEGORIES)
        if cats is not None:
            if not cats:
                raise ValueError("분류(오류 신고·개선 요청)를 하나 이상 선택해야 합니다.")
            out["categories"] = cats
    elif trigger_type == "REPORT_DUE":
        # 생략하면 2일 전부터 임박으로 본다. 0 이면 기한을 넘겼을 때만
        days = cfg.get("days_before")
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 30:
                raise ValueError("days_before 는 0~30 사이의 정수(일)여야 합니다.")
            out["days_before"] = days
    # ANY / MAPPING_BROKEN 은 옵션 없음
    return json.dumps(out, ensure_ascii=False)


def normalize_channels(value: str | list[str] | None) -> str:
    """채널 목록(콤마 문자열 또는 배열) → 검증된 콤마 문자열. EMAIL 등 미지원 채널은 거부."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for ch in items:
        ch = str(ch).strip().upper()
        if not ch:
            continue
        if ch == "EMAIL":
            raise ValueError("이메일 채널은 아직 지원하지 않습니다. 앱 내 또는 Webhook 을 사용하세요.")
        if ch not in SUPPORTED_CHANNELS:
            raise ValueError(f"지원하지 않는 채널: {ch} (허용: {', '.join(SUPPORTED_CHANNELS)})")
        if ch not in out:
            out.append(ch)
    if not out:
        raise ValueError("알림 채널을 하나 이상 선택해야 합니다.")
    return ",".join(out)


def normalize_subscribers(value: str | list[str] | None) -> str | None:
    """구독자 목록 정규화 → 저장 문자열(쉼표 구분 사용자 이름). 비면 None.

    화면은 목록(배지 입력, TODO-144)을 보내고, 예전 클라이언트는 쉼표 · 줄바꿈 문자열을 보낸다.
    사용자 이름에는 쉼표가 들어갈 수 없으므로 목록 항목에 쉼표가 있으면 거부한다."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
        if any("," in v for v in items):
            raise ValueError("구독자 이름에는 쉼표를 쓸 수 없습니다.")
    else:
        items = value.replace("\n", ",").split(",")
    out: list[str] = []
    for v in items:
        v = v.strip()
        if v and v not in out:
            if len(v) > 200:
                raise ValueError("구독자 한 명의 이름은 200자 이하여야 합니다.")
            out.append(v)
    joined = ",".join(out)
    if len(joined) > 2000:
        raise ValueError("구독자 목록이 너무 깁니다(최대 2000자).")
    return joined or None


def normalize_webhook_url(value: str | None) -> str | None:
    """Webhook URL — http(s) 절대 주소만. 공백이면 None."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > 500:
        raise ValueError("Webhook URL 은 500자 이하여야 합니다.")
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Webhook URL 은 http(s):// 로 시작하는 주소여야 합니다.")
    return v


def _bool_str(value) -> str | None:
    """bool 또는 'true'/'false' 문자열 → 'true'/'false' (DB 는 문자열 컬럼)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip().lower()
    if s in ("true", "1", "yes"):
        return "true"
    if s in ("false", "0", "no"):
        return "false"
    raise ValueError("true / false 값이어야 합니다.")


# ---------------------------------------------------------------------------
# Alert Rule 스키마
# ---------------------------------------------------------------------------

class _RuleFields(BaseModel):
    """생성·수정 공통 단일 필드 정규화. 조합 검증(범위·트리거·채널)은 서비스가 병합 후 수행."""

    @field_validator("rule_name", mode="before", check_fields=False)
    @classmethod
    def _strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("description", mode="before", check_fields=False)
    @classmethod
    def _strip_desc(cls, v):
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("channels", mode="before", check_fields=False)
    @classmethod
    def _channels(cls, v):
        return None if v is None else normalize_channels(v)

    @field_validator("notify_owners", "is_active", mode="before", check_fields=False)
    @classmethod
    def _bools(cls, v):
        return _bool_str(v)

    @field_validator("webhook_url", mode="before", check_fields=False)
    @classmethod
    def _webhook(cls, v):
        return normalize_webhook_url(v)

    @field_validator("subscribers", mode="before", check_fields=False)
    @classmethod
    def _subs(cls, v):
        return normalize_subscribers(v)

    @field_validator("trigger_config", mode="before", check_fields=False)
    @classmethod
    def _cfg(cls, v):
        # dict 도 받아 문자열로 — 형태 검증은 trigger_type 이 확정된 뒤
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return v


class AlertRuleCreate(_RuleFields):
    """알림 규칙 생성 요청. 생성자는 서버가 로그인 사용자로 기록한다."""
    rule_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    scope_type: RuleScopeType
    scope_id: int | None = None
    trigger_type: RuleTriggerType = RuleTriggerType.ANY
    trigger_config: str = Field("{}", max_length=10000)      # JSON 문자열
    severity_override: AlertSeverity | None = None
    channels: str = "IN_APP"
    notify_owners: str = "true"
    webhook_url: str | None = None
    subscribers: str | None = None                           # 사용자 이름 목록(배열 또는 예전 쉼표 문자열) → 저장은 쉼표 구분

    @model_validator(mode="after")
    def _check_config(self):
        self.trigger_config = normalize_trigger_config(self.trigger_type.value, self.trigger_config)
        return self


# 수정 시 명시적 null 을 허용하지 않는 필드(DB NOT NULL 또는 의미상 필수)
_NON_NULLABLE_UPDATE = ("rule_name", "scope_type", "trigger_type", "trigger_config",
                        "channels", "notify_owners", "is_active")


class AlertRuleUpdate(_RuleFields):
    """알림 규칙 수정 요청(부분). 조합 검증은 기존 값과 병합한 뒤 서비스에서 한다."""
    rule_name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    scope_type: RuleScopeType | None = None
    scope_id: int | None = None
    trigger_type: RuleTriggerType | None = None
    trigger_config: str | None = Field(None, max_length=10000)
    severity_override: AlertSeverity | None = None
    channels: str | None = None
    notify_owners: str | None = None
    webhook_url: str | None = None
    subscribers: str | None = None
    is_active: str | None = None

    @model_validator(mode="after")
    def _no_nulls(self):
        for f in _NON_NULLABLE_UPDATE:
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(f"{f} 는 null 로 바꿀 수 없습니다.")
        return self


class AlertRuleResponse(BaseModel):
    """알림 규칙 응답.

    ``webhook_url`` 은 규칙 관리 권한자에게만 내려간다(토큰이 URL 에 들어 있는 경우가 많다).
    그 외 사용자는 ``webhook_configured`` 로 설정 여부만 본다.
    """
    id: int
    rule_name: str
    description: str | None = None
    scope_type: str
    scope_id: int | None = None
    scope_name: str | None = None            # 범위 대상 이름 (JOIN 조회)
    scope_missing: bool = False              # 범위 대상이 삭제되어 더 이상 매칭되지 않음
    trigger_type: str
    trigger_config: str = "{}"
    severity_override: str | None = None
    channels: str
    notify_owners: str
    webhook_url: str | None = None
    webhook_configured: bool = False
    subscribers: str | None = None
    is_active: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    alert_count: int = 0                     # 이 Rule로 생성된 알림 수

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Alert 스키마
# ---------------------------------------------------------------------------

class AlertResponse(BaseModel):
    id: int
    alert_type: str
    severity: str
    source_dataset_id: int | None = None
    entity_type: str | None = None
    entity_ref: str | None = None
    source_dataset_name: str | None = None
    source_datasource_type: str | None = None
    affected_dataset_id: int | None = None
    affected_dataset_name: str | None = None
    affected_datasource_type: str | None = None
    lineage_id: int | None = None
    rule_id: int | None = None
    rule_name: str | None = None
    change_summary: str
    change_detail: str | None = None
    status: str
    # 마지막 상태 전이(확인·해결·무시) 처리자·시각 — 다시 열면 비워진다
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    can_update: bool = False                 # 현재 사용자가 상태를 바꿀 수 있는지


class AlertUpdateStatus(BaseModel):
    """상태 전이 요청. 처리자·시각은 서버가 로그인 사용자로 기록한다."""
    status: AlertStatus


class AlertBulkStatus(BaseModel):
    """여러 알림 상태 일괄 전이."""
    ids: list[int] = Field(..., min_length=1, max_length=200)
    status: AlertStatus


class AlertBulkResult(BaseModel):
    updated: list[int] = []
    skipped: list[dict] = []                 # [{"id": .., "reason": ..}]


class AlertPurgeResult(BaseModel):
    deleted: int = 0


class PaginatedAlerts(BaseModel):
    items: list[AlertResponse]
    total: int
    page: int
    page_size: int


class AlertSummary(BaseModel):
    """미해결 알림 건수 요약 (벨 배지용)."""
    total_open: int = 0
    breaking_count: int = 0
    warning_count: int = 0
    info_count: int = 0
