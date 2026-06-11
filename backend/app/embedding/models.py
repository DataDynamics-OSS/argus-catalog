"""임베딩(pgvector) 용 SQLAlchemy ORM 모델.

활성 임베딩 제공자가 생성한 벡터 임베딩을 저장한다.
- ``DatasetEmbedding``: 데이터셋 1:1 (FK CASCADE).
- ``EntityEmbedding``: 용어집/AI Agent/API 등 비-데이터셋 엔티티용 다형 테이블.
"""

import logging

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base

logger = logging.getLogger(__name__)

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # pgvector 미설치 — 모듈을 그대로 임포트할 수 있도록 플레이스홀더를 정의한다.
    # 테이블은 생성되지 않으며, 임베딩 작업은 graceful 하게 실패한다.
    logger.warning("pgvector 가 설치되어 있지 않습니다 — 임베딩 테이블/기능이 비활성화됩니다.")
    Vector = None


class DatasetEmbedding(Base):
    """카탈로그 데이터셋 시맨틱 검색용 벡터 임베딩."""

    __tablename__ = "catalog_dataset_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(
        Integer,
        ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # pgvector 컬럼 — 차원은 테이블 생성 시점에 설정
    embedding = Column(Vector(384) if Vector else Text, nullable=False)
    # 임베딩된 원본 텍스트 (변경 감지용)
    source_text = Column(Text, nullable=False)
    # 출처(provenance) 추적
    model_name = Column(String(200), nullable=False)
    provider = Column(String(50), nullable=False)
    dimension = Column(Integer, nullable=False, default=384)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EntityEmbedding(Base):
    """비-데이터셋 카탈로그 엔티티의 시맨틱 검색용 임베딩 (다형).

    ``entity_type``: glossary_term / ai_agent / api.
    엔티티별로 FK 대상이 달라 DB CASCADE 를 걸 수 없으므로, 행 삭제는
    각 서비스의 delete 경로에서 ``delete_entity_embedding`` 으로 명시 수행한다.
    """

    __tablename__ = "catalog_entity_embeddings"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(Integer, nullable=False)
    embedding = Column(Vector(384) if Vector else Text, nullable=False)
    # 임베딩된 원본 텍스트 (변경 감지용)
    source_text = Column(Text, nullable=False)
    # 출처(provenance) 추적
    model_name = Column(String(200), nullable=False)
    provider = Column(String(50), nullable=False)
    dimension = Column(Integer, nullable=False, default=384)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
