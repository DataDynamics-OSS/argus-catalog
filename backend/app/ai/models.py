"""AI 생성 ORM 모델.

감사(audit), 되돌리기, 비용 추적을 위해 AI 가 생성한 모든 메타데이터 제안을 추적한다.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func

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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
