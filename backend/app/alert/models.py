# SPDX-License-Identifier: Apache-2.0
"""리니지 변경 알림 ORM 모델.

주요 테이블:
- argus_alert_rule: 알림 규칙 정의 (감시 대상 + 트리거 조건 + 알림 설정)
- argus_lineage_alert: 스키마 변경 영향 분석 결과 알림
- argus_alert_notification: 알림 전달 기록 (IN_APP, WEBHOOK, EMAIL)
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class AlertRule(Base):
    """알림 규칙 정의.

    감시 대상(scope) + 트리거 조건(trigger) + 알림 설정(notify)을 하나로 통합.
    기존 AlertSubscription의 상위 호환.

    scope_type별 동작:
    - DATASET: 특정 데이터셋의 변경 감시 (scope_id = dataset_id)
    - TAG: 태그가 붙은 모든 데이터셋 감시 (scope_id = tag_id)
    - LINEAGE: 특정 리니지 관계의 양쪽 데이터셋 감시 (scope_id = lineage_id)
    - DATASOURCE: 특정 데이터 소스의 모든 데이터셋 감시 (scope_id = datasource_id)
    - ALL: 카탈로그 전체 감시 (scope_id = NULL)

    trigger_type별 동작:
    - ANY: 모든 스키마 변경 시 발동
    - SCHEMA_CHANGE: 특정 변경 유형(DROP/MODIFY/ADD) 필터
    - COLUMN_WATCH: 특정 컬럼만 감시
    - MAPPING_BROKEN: 컬럼 매핑된 컬럼이 변경될 때만 발동
    - SYNC_STALE: N시간 이상 sync 안 됨
    """

    __tablename__ = "argus_alert_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_name = Column(String(255), nullable=False)             # 규칙 이름
    description = Column(Text)                                  # 규칙 설명
    scope_type = Column(String(32), nullable=False)             # 감시 범위: DATASET, TAG, LINEAGE, DATASOURCE, ALL
    scope_id = Column(Integer)                                  # 범위 대상 ID (ALL이면 NULL)
    trigger_type = Column(String(64), nullable=False)           # 트리거 유형: ANY, SCHEMA_CHANGE, COLUMN_WATCH, MAPPING_BROKEN, SYNC_STALE, QUALITY_FAILED
    trigger_config = Column(Text, default="{}")                 # 트리거 조건 상세 (JSON)
    severity_override = Column(String(16))                      # 심각도 강제 지정 (NULL이면 자동 판정)
    channels = Column(String(200), nullable=False, default="IN_APP")  # 알림 채널 (콤마 구분)
    notify_owners = Column(String(5), nullable=False, default="true")  # 데이터셋 Owner 알림 여부
    webhook_url = Column(String(500))                           # WEBHOOK 전송 URL
    subscribers = Column(String(2000))                          # 구독자 목록 (콤마 구분)
    is_active = Column(String(5), nullable=False, default="true")  # 활성화 여부
    created_by = Column(String(200))                            # 생성자
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LineageAlert(Base):
    """리니지 변경 알림.

    스키마 변경이 리니지 관계에 영향을 미칠 때 생성되는 알림 이벤트.
    어떤 Rule에 의해 생성되었는지 rule_id로 추적.

    생명주기: OPEN → ACKNOWLEDGED → RESOLVED / DISMISSED
    """

    __tablename__ = "argus_lineage_alert"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String(32), nullable=False)             # SCHEMA_CHANGE, LINEAGE_BROKEN, SYNC_FAILED, QUALITY_FAILED
    severity = Column(String(16), nullable=False)               # INFO, WARNING, BREAKING
    source_dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False
    )
    affected_dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE")
    )
    lineage_id = Column(
        Integer, ForeignKey("argus_dataset_lineage.id", ondelete="SET NULL")
    )
    rule_id = Column(                                           # 이 알림을 생성한 Rule
        Integer, ForeignKey("argus_alert_rule.id", ondelete="SET NULL")
    )
    change_summary = Column(String(500), nullable=False)
    change_detail = Column(Text)
    status = Column(String(20), nullable=False, default="OPEN")
    resolved_by = Column(String(200))
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AlertNotification(Base):
    """알림 전달 기록."""

    __tablename__ = "argus_alert_notification"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(
        Integer, ForeignKey("argus_lineage_alert.id", ondelete="CASCADE"), nullable=False
    )
    channel = Column(String(32), nullable=False)
    recipient = Column(String(200), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), nullable=False, default="SENT")
