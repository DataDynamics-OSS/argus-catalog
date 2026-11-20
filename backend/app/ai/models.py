# SPDX-License-Identifier: Apache-2.0
"""AI 생성 ORM 모델.

감사(audit), 되돌리기, 비용 추적을 위해 AI 가 생성한 모든 메타데이터 제안을 추적한다.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint, func

from app.core.database import Base


class AIGenerationLog(Base):
    """AI 가 생성한 메타데이터 제안 로그.

    감사 추적, 미리보기/적용 워크플로, 토큰 사용량 추적을 위해
    모든 생성 결과를 기록한다.
    """

    __tablename__ = "catalog_ai_generation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String(20), nullable=False)       # dataset, column, tag, pii
    entity_id = Column(Integer, nullable=False)             # dataset_id 또는 schema_field_id
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    field_name = Column(String(500))                        # 컬럼명 (컬럼 단위인 경우)
    generation_type = Column(String(30), nullable=False)    # description, tag_suggestion, pii_detection
    generated_text = Column(Text, nullable=False)
    applied = Column(Boolean, default=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    # 제안 근거 — AI-Ready 초안의 관련법령 · 허용 이용 유형을 고른 이유(TODO-096). 제안 창에 보여 준다.
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AssistantFeedback(Base):
    """AI 어시스턴트 답변 평가(👍/👎) — 답변 하나에 사용자당 한 건, 다시 누르면 바꾼다 (TODO-068).

    ``answer_id`` 는 화면이 답변마다 만든 식별자다. 질문·답변 원문은 나중에 무엇을 평가했는지 보려고
    앞부분만 남긴다(질문 2,000자·답변 8,000자).
    """

    __tablename__ = "catalog_ai_assistant_feedback"
    __table_args__ = (UniqueConstraint("user_sub", "answer_id", name="uq_ai_assistant_feedback_answer"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_sub = Column(String(200), nullable=False)
    username = Column(String(200))
    conversation_id = Column(String(100))
    answer_id = Column(String(64), nullable=False)
    rating = Column(SmallInteger, nullable=False)   # 1 = 👍, -1 = 👎
    comment = Column(Text)
    question = Column(Text)
    answer = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
