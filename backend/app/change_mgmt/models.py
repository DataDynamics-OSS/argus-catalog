"""변경 관리(Change Management) ORM 모델.

주요 테이블:
- argus_change_request          : 변경 요청(CR) — 결재 흐름의 마스터 엔티티
- argus_change_approval_step    : 결재 단계별 처리 이력
- argus_change_consumer         : 데이터셋 소비자(시스템/조직) 등록부
- argus_change_notification_log : 다운스트림 통지/ACK 기록
"""

from sqlalchemy import (
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


class ChangeRequest(Base):
    """변경 요청(CR).

    상태 전이:
        DRAFT → SUBMITTED → APPROVING → APPROVED → SCHEDULED → APPLIED → CLOSED
                                    ↓
                                REJECTED / CANCELLED / ROLLED_BACK

    workflow_id 는 Temporal 워크플로우 실행 ID. 결재/통지/검증 단계는 모두
    Temporal 워크플로우가 오케스트레이션하며 본 테이블은 결과만 저장한다.
    """

    __tablename__ = "argus_change_request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cr_code = Column(String(32), nullable=False, unique=True)        # 예: CR-2026-0001
    title = Column(String(500), nullable=False)
    description = Column(Text)
    dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False
    )
    change_type = Column(String(32), nullable=False)                 # BREAKING / NON_BREAKING / ADDITIVE / COSMETIC
    priority = Column(String(16), nullable=False, default="NORMAL")  # EMERGENCY / HIGH / NORMAL / LOW
    status = Column(String(24), nullable=False, default="DRAFT")
    schema_before = Column(Text)                                     # 변경 전 스키마(JSON)
    schema_after = Column(Text)                                      # 변경 후 스키마(JSON) — COSMETIC 변경은 생략 가능
    impact_report = Column(Text)                                     # 자동 생성된 영향 분석(JSON)
    rollback_plan = Column(Text, nullable=False)                     # 롤백 계획(필수)
    business_justification = Column(Text, nullable=False)
    scheduled_at = Column(DateTime(timezone=True))                   # 적용 예정 시각
    applied_at = Column(DateTime(timezone=True))                     # 실제 적용 시각
    workflow_id = Column(String(200))                                # Temporal Workflow ID
    requested_by = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApprovalStep(Base):
    """결재 단계.

    하나의 CR은 N단계의 결재(ApprovalStep)를 가진다.
    Temporal 워크플로우가 각 단계의 결재자에게 Signal 을 받기 위해 대기하며,
    결재 처리 시 본 테이블에 결과가 기록된다.
    """

    __tablename__ = "argus_change_approval_step"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cr_id = Column(
        Integer, ForeignKey("argus_change_request.id", ondelete="CASCADE"), nullable=False
    )
    step_order = Column(Integer, nullable=False)                     # 1부터 시작
    approver = Column(String(200), nullable=False)
    role = Column(String(64))                                        # OWNER / DOMAIN_LEAD / DG_COMMITTEE 등
    decision = Column(String(16))                                    # APPROVED / REJECTED / DELEGATED / PENDING
    comment = Column(Text)
    decided_at = Column(DateTime(timezone=True))
    delegated_to = Column(String(200))                               # 위임 결재 시 대결자
    due_at = Column(DateTime(timezone=True))                         # 처리 기한
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("cr_id", "step_order", name="uq_approval_step_order"),
    )


class Consumer(Base):
    """데이터셋 소비자(시스템/조직) 등록부.

    스키마 변경 시 누구에게 사전 통지해야 하는지 결정하는 기준 테이블.
    쿼리 로그 기반 자동 탐지로 등록되거나, 외부 시스템에 한해 수동 등록한다.
    """

    __tablename__ = "argus_change_consumer"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False
    )
    consumer_name = Column(String(200), nullable=False)              # 시스템 또는 조직 이름
    consumer_type = Column(String(32), nullable=False, default="SYSTEM")  # SYSTEM / ORGANIZATION / TEAM
    usage = Column(String(64))                                       # ETL / DASHBOARD / ML_TRAINING / REGULATORY 등
    criticality = Column(String(16), nullable=False, default="NORMAL")  # MISSION_CRITICAL / IMPORTANT / NORMAL
    contact_emails = Column(String(2000))                            # 콤마 구분
    webhook_url = Column(String(500))
    slack_channel = Column(String(200))
    auto_detected = Column(Boolean, nullable=False, default=False)   # 쿼리 로그 자동 탐지 여부
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("dataset_id", "consumer_name", name="uq_consumer_per_dataset"),
    )


class ChangeReferrer(Base):
    """변경 요청 참조자(CC) — 결재권 없이 통지만 받는 대상.

    결재선(ApprovalStep)과 분리. 참조자 추가 시 이메일(우선)·Slack/Mattermost 로 통지한다.
    """

    __tablename__ = "argus_change_referrer"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cr_id = Column(Integer, ForeignKey("argus_change_request.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200))                                       # 표시명(선택)
    email = Column(String(300))                                      # 이메일(EMAIL 채널)
    channel = Column(String(16), nullable=False, default="EMAIL")    # EMAIL / SLACK / MATTERMOST
    slack_target = Column(String(200))                               # Slack/Mattermost 채널 또는 멘션(선택)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    """다운스트림 통지/ACK 로그.

    각 CR에 대해 등록된 소비자별로 발송된 통지와 수신 확인(ACK)을 추적.
    상태:
        PENDING → SENT → DELIVERED → ACKED
                              ↓
                            FAILED / REJECTED / DEFERRED
    """

    __tablename__ = "argus_change_notification_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cr_id = Column(
        Integer, ForeignKey("argus_change_request.id", ondelete="CASCADE"), nullable=False
    )
    consumer_id = Column(
        Integer, ForeignKey("argus_change_consumer.id", ondelete="CASCADE"), nullable=True
    )
    # 참조자(CC) 대상 통지 추적 — 소비자가 아닌 경우
    referrer_id = Column(Integer, ForeignKey("argus_change_referrer.id", ondelete="CASCADE"), nullable=True)
    recipient = Column(String(300))                                  # 수신자(이메일/채널 대상)
    channel = Column(String(32), nullable=False)                     # EMAIL / SLACK / MATTERMOST / WEBHOOK / IN_APP
    stage = Column(String(16), nullable=False)                       # SUBMITTED / T_MINUS_30 / T_MINUS_7 / APPLIED 등
    status = Column(String(16), nullable=False, default="PENDING")
    sent_at = Column(DateTime(timezone=True))
    acked_at = Column(DateTime(timezone=True))
    ack_comment = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
