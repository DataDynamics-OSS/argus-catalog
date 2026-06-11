"""변경 관리 Pydantic 스키마.

라우터/서비스/Temporal 액티비티 간에 공유되는 DTO 와 열거형.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------

class ChangeType(str, Enum):
    BREAKING = "BREAKING"
    NON_BREAKING = "NON_BREAKING"
    ADDITIVE = "ADDITIVE"
    COSMETIC = "COSMETIC"


class Priority(str, Enum):
    EMERGENCY = "EMERGENCY"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class CRStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVING = "APPROVING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DELEGATED = "DELEGATED"
    PENDING = "PENDING"


class Channel(str, Enum):
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    MATTERMOST = "MATTERMOST"
    WEBHOOK = "WEBHOOK"
    SMS = "SMS"
    IN_APP = "IN_APP"


class NotificationStage(str, Enum):
    SUBMITTED = "SUBMITTED"
    T_MINUS_30 = "T_MINUS_30"
    T_MINUS_7 = "T_MINUS_7"
    T_MINUS_1 = "T_MINUS_1"
    T_MINUS_1H = "T_MINUS_1H"
    APPLIED = "APPLIED"


# ---------------------------------------------------------------------------
# Approval / Consumer / Notification DTO
# ---------------------------------------------------------------------------

class ApprovalStepInput(BaseModel):
    step_order: int = Field(..., ge=1)
    approver: str
    role: str | None = None
    due_at: datetime | None = None


class ApprovalStepResponse(BaseModel):
    id: int
    step_order: int
    approver: str
    role: str | None
    decision: Decision
    comment: str | None
    decided_at: datetime | None
    delegated_to: str | None
    due_at: datetime | None

    class Config:
        from_attributes = True


class ApprovalDecisionRequest(BaseModel):
    decision: Decision
    comment: str | None = None
    delegated_to: str | None = None


class ConsumerCreate(BaseModel):
    dataset_id: int
    consumer_name: str
    consumer_type: str = "SYSTEM"
    usage: str | None = None
    criticality: str = "NORMAL"
    contact_emails: str | None = None
    webhook_url: str | None = None
    slack_channel: str | None = None


class ConsumerResponse(ConsumerCreate):
    id: int
    auto_detected: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationLogResponse(BaseModel):
    id: int
    consumer_id: int | None = None
    referrer_id: int | None = None
    recipient: str | None = None
    channel: Channel
    stage: NotificationStage
    status: str
    sent_at: datetime | None
    acked_at: datetime | None
    ack_comment: str | None

    class Config:
        from_attributes = True


class ReferrerInput(BaseModel):
    """변경 요청 참조자(CC) 입력."""
    name: str | None = None
    email: str | None = None
    channel: str = "EMAIL"          # EMAIL / SLACK / MATTERMOST
    slack_target: str | None = None


class ReferrerResponse(ReferrerInput):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Change Request DTO
# ---------------------------------------------------------------------------

class ChangeRequestCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    dataset_id: int
    change_type: ChangeType
    priority: Priority = Priority.NORMAL
    schema_before: str | None = None
    schema_after: str | None = None  # COSMETIC 변경은 생략 가능(조건부 검증)
    rollback_plan: str = Field(..., min_length=1)
    business_justification: str = Field(..., min_length=1)
    scheduled_at: datetime | None = None
    approval_chain: list[ApprovalStepInput] = Field(default_factory=list)
    referrers: list[ReferrerInput] = Field(default_factory=list)


class ChangeRequestResponse(BaseModel):
    id: int
    cr_code: str
    title: str
    description: str | None
    dataset_id: int
    change_type: ChangeType
    priority: Priority
    status: CRStatus
    schema_before: str | None
    schema_after: str | None
    impact_report: str | None
    rollback_plan: str
    business_justification: str
    scheduled_at: datetime | None
    applied_at: datetime | None
    workflow_id: str | None
    requested_by: str
    created_at: datetime
    updated_at: datetime
    approval_steps: list[ApprovalStepResponse] = Field(default_factory=list)
    referrers: list[ReferrerResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ChangeRequestSubmitResponse(BaseModel):
    cr_id: int
    cr_code: str
    workflow_id: str
    status: CRStatus
