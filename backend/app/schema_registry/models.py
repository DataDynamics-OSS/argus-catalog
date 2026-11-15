# SPDX-License-Identifier: Apache-2.0
"""스키마 레지스트리 ORM 모델."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class SchemaRegistryVersion(Base):
    """데이터셋 Avro 스키마의 등록 버전 — Avro 본문 지문마다 한 행.

    ``id`` 가 Confluent 스키마 id, ``version`` 이 subject(데이터셋) 안의 버전 번호(1부터)다.
    행은 고치지 않는다(같은 본문이면 같은 id). 스키마 변경 이력 스냅샷(``catalog_schema_snapshots``)과 달리
    오래된 행을 지우지 않아, 예전 id 로 쓴 데이터도 계속 읽을 수 있다. 데이터셋이 지워지면 함께 지운다.
    """

    __tablename__ = "argus_schema_registry_version"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_schema_registry_version"),
        UniqueConstraint("dataset_id", "fingerprint", name="uq_schema_registry_fingerprint"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=False)   # 키 정렬 JSON 의 SHA-256 16진
    schema_text = Column(Text, nullable=False)         # Confluent 응답의 "schema" 문자열(압축 JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
