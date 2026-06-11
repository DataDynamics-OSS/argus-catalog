# SPDX-License-Identifier: Apache-2.0
"""설정(Settings) ORM 모델."""

from sqlalchemy import Column, DateTime, Integer, String, func

from app.core.database import Base


class CatalogConfiguration(Base):
    """키-값 설정 저장소.

    Settings API 를 통해 런타임에 변경 가능한 동적 설정을 저장한다.
    오브젝트 스토리지(MinIO/S3) 설정 등에 사용된다.
    """

    __tablename__ = "catalog_configuration"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(50), nullable=False)
    config_key = Column(String(100), nullable=False, unique=True)
    config_value = Column(String(500), nullable=False, default="")
    description = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
