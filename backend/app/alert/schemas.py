"""리니지 변경 알림 Pydantic 스키마(요청·응답 DTO).

라우터(``app/alert/router.py``) 와 서비스(``app/alert/service.py``) 가 공유하는
규칙(scope/trigger/severity) · 알림(상태 전이) · 요약 DTO 와 열거형을 모아둔다.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------

class AlertType(str, Enum):
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    LINEAGE_BROKEN = "LINEAGE_BROKEN"
    SYNC_FAILED = "SYNC_FAILED"


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
    """트리거 조건."""
    ANY = "ANY"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    COLUMN_WATCH = "COLUMN_WATCH"
    MAPPING_BROKEN = "MAPPING_BROKEN"
    SYNC_STALE = "SYNC_STALE"


# ---------------------------------------------------------------------------
# Alert Rule 스키마
# ---------------------------------------------------------------------------

class AlertRuleCreate(BaseModel):
    """알림 규칙 생성 요청."""
    rule_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    scope_type: RuleScopeType
    scope_id: int | None = None
    trigger_type: RuleTriggerType = RuleTriggerType.ANY
    trigger_config: str = "{}"                               # JSON 문자열
    severity_override: AlertSeverity | None = None
    channels: str = "IN_APP"
    notify_owners: str = "true"
    webhook_url: str | None = None
    subscribers: str | None = None                           # 콤마 구분
    created_by: str | None = None


class AlertRuleUpdate(BaseModel):
    """알림 규칙 수정 요청."""
    rule_name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    scope_type: RuleScopeType | None = None
    scope_id: int | None = None
    trigger_type: RuleTriggerType | None = None
    trigger_config: str | None = None
    severity_override: AlertSeverity | None = None
    channels: str | None = None
    notify_owners: str | None = None
    webhook_url: str | None = None
    subscribers: str | None = None
    is_active: str | None = None


class AlertRuleResponse(BaseModel):
    """알림 규칙 응답."""
    id: int
    rule_name: str
    description: str | None = None
    scope_type: str
    scope_id: int | None = None
    scope_name: str | None = None            # 범위 대상 이름 (JOIN 조회)
    trigger_type: str
    trigger_config: str = "{}"
    severity_override: str | None = None
    channels: str
    notify_owners: str
    webhook_url: str | None = None
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
    source_dataset_id: int
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
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class AlertUpdateStatus(BaseModel):
    status: AlertStatus
    resolved_by: str | None = None


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
