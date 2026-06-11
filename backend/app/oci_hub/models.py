# SPDX-License-Identifier: Apache-2.0
"""OCI 모델 허브 SQLAlchemy ORM 정의.

주요 테이블:
  - ``catalog_oci_models``                : 모델 메타데이터 + README + 분류 + 라이프사이클
  - ``catalog_oci_model_versions``        : 버전별 아티팩트 (OCI ``manifest.json`` 포함)
  - ``catalog_oci_model_tags``            : 모델 ↔ 태그 매핑 (``catalog_tags`` 재사용)
  - ``catalog_oci_model_lineage``         : 학습 데이터 / 부모 모델 등 외부 자원 관계
  - ``catalog_oci_model_download_log``    : OCI 모델 다운로드 이벤트 로그
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class OciModel(Base):
    """OCI 모델 허브 — README 와 라이프사이클을 가진 등록 모델."""

    __tablename__ = "catalog_oci_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    display_name = Column(String(255))
    description = Column(Text)
    readme = Column(Text)

    # 분류
    task = Column(String(50))
    framework = Column(String(50))
    language = Column(String(50))
    license = Column(String(100))

    # 출처
    source_type = Column(String(50))
    source_id = Column(String(500))
    source_revision = Column(String(100))

    # 저장소
    bucket = Column(String(255))
    storage_prefix = Column(String(500))

    # 소유권
    owner = Column(String(200))
    created_by = Column(String(200))  # 행 단위 소유권 — 생성자

    # 비정규화 통계
    version_count = Column(Integer, nullable=False, default=0)
    total_size = Column(BigInteger, default=0)
    download_count = Column(Integer, nullable=False, default=0)

    # 라이프사이클: draft → review → approved → production → deprecated → archived
    status = Column(String(20), nullable=False, default="draft")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OciModelVersion(Base):
    """OCI manifest 를 포함한 버전별 모델 아티팩트."""

    __tablename__ = "catalog_oci_model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_oci_models.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)

    manifest = Column(Text)
    content_digest = Column(String(100))

    file_count = Column(Integer, default=0)
    total_size = Column(BigInteger, default=0)

    extra_metadata = Column("metadata", JSONB)

    status = Column(String(20), nullable=False, default="ready")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OciModelTag(Base):
    """OCI 모델의 태그 연결 (catalog_tags 재사용)."""

    __tablename__ = "catalog_oci_model_tags"
    __table_args__ = (UniqueConstraint("model_id", "tag_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_oci_models.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(Integer, ForeignKey("catalog_tags.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OciModelLineage(Base):
    """모델 리니지 — 학습 데이터 및 베이스 모델 관계."""

    __tablename__ = "catalog_oci_model_lineage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_oci_models.id", ondelete="CASCADE"), nullable=False)

    source_type = Column(String(20), nullable=False)
    source_id = Column(String(255), nullable=False)
    source_name = Column(String(255))

    relation_type = Column(String(30), nullable=False)
    description = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OciModelDownloadLog(Base):
    """OCI 모델 다운로드 이벤트 로그.

    허브별로 독립적인 다운로드 추이 분석이 가능하도록 MLflow 의
    catalog_model_download_log 와 분리한다.
    """

    __tablename__ = "catalog_oci_model_download_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(255), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    download_type = Column(String(20), nullable=False)
    client_ip = Column(String(45))
    user_agent = Column(String(500))
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
