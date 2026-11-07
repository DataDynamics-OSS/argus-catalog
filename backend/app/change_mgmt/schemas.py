# SPDX-License-Identifier: Apache-2.0
"""변경 관리 Pydantic 스키마.

라우터/서비스/Temporal 액티비티 간에 공유되는 DTO 와 열거형.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def _naive_as_utc(v: datetime | None) -> datetime | None:
    """시간대 없는(naive) 일시는 UTC 로 본다 — 저장(timestamptz)·워크플로우(``workflow.py``)와 같은 규칙.

    클라이언트는 시간대를 붙여(ISO 오프셋, 예: ``…Z`` / ``+09:00``) 보내야 한다. 예전 UI 는
    datetime-local 값을 그대로 보내 KST 입력이 UTC 로 저장·적용(9시간 늦음)됐다(IT CHG-T25).
    """
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v

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


class DecisionInput(str, Enum):
    """결재자가 보낼 수 있는 결정값 — ``PENDING`` 은 '아직 결정 안 함' 상태값이라 받지 않는다.

    예전에는 ``Decision`` 전체를 받아 ``PENDING`` 이 202 로 접수됐고, 워크플로우는 반려가 아니면
    다음 단계로 넘어가는데 DB 단계는 PENDING 으로 남아 결재가 영영 멈췄다.
    """
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DELEGATED = "DELEGATED"


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
    approver: str = Field(..., min_length=1, max_length=200)
    role: str | None = None
    due_at: datetime | None = None

    _due_at_tz = field_validator("due_at")(_naive_as_utc)


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
    decision: DecisionInput
    comment: str | None = None
    delegated_to: str | None = None

    @model_validator(mode="after")
    def _normalize_delegated_to(self):
        """대결자 ``delegated_to`` 정리 — 앞뒤 공백 제거(빈 값은 None), 위임이 아닌 결정이면 버린다.

        위임(DELEGATED)인데 대결자가 없으면 라우터가 422 로 거부한다(``router.submit_decision``).
        예전에는 요청값을 버리고 결정자 본인을 ``delegated_to`` 에 저장했다(IT CHG-T14).
        """
        if self.decision == DecisionInput.DELEGATED:
            self.delegated_to = (self.delegated_to or "").strip() or None
        else:
            self.delegated_to = None
        return self


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

    _scheduled_at_tz = field_validator("scheduled_at")(_naive_as_utc)

    @model_validator(mode="after")
    def _check_approval_chain(self):
        """결재선 단계 번호는 1..N 이 빠짐·중복 없이 — 워크플로우는 결재자를 순서대로 1, 2, … 로
        기다리고 결재 API 는 실제 ``step_order`` 로 Signal 을 보내므로, 번호가 건너뛰면(1, 3) CR 이
        결재 중에서 멈춘다. 중복은 DB UNIQUE 위반(409)으로 늦게 걸리던 것을 422 로 앞당긴다.
        결재자 이름은 앞뒤 공백을 지우고 비어 있으면 거부한다.
        """
        orders = [s.step_order for s in self.approval_chain]
        dup = sorted({o for o in orders if orders.count(o) > 1})
        if dup:
            raise ValueError(f"결재선 단계 번호가 중복됩니다: {', '.join(map(str, dup))}")
        if sorted(orders) != list(range(1, len(orders) + 1)):
            got = ", ".join(map(str, sorted(orders)))
            raise ValueError(f"결재선 단계 번호는 1부터 빠짐없이 이어져야 합니다 (받은 값: {got})")
        for s in self.approval_chain:
            s.approver = s.approver.strip()
            if not s.approver:
                raise ValueError("결재자 이름이 비어 있습니다.")
        return self


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
