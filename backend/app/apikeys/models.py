# SPDX-License-Identifier: Apache-2.0
"""API 키 ORM 모델.

평문 키(``argus_sk_…``)는 발급·재발급 응답에서 한 번만 보여 주고 DB 에는 SHA-256 해시만 둔다.
권한은 역할(``role_id`` — 사용자와 같은 역할 식별자)과 범위(``scopes`` — 부를 수 있는 API 묶음)를 함께 본다.
폐기한 키는 지우지 않고 ``status='revoked'`` 로 남겨 이력(누가 언제 만들고 폐기했는지)을 유지한다.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.core.database import Base


class ArgusApiKey(Base):
    __tablename__ = "argus_api_key"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500))
    # 표시용 앞부분(예: argus_sk_AbCdEfG) — 어떤 키인지 알아보는 용도, 복원 불가
    key_prefix = Column(String(20), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    role_id = Column(String(50), nullable=False)
    # 쉼표로 구분한 범위 코드 (app.apikeys.service.SCOPES)
    scopes = Column(String(200), nullable=False)
    # active | disabled | revoked
    status = Column(String(20), nullable=False, default="active")
    expires_at = Column(DateTime(timezone=True))
    last_used_at = Column(DateTime(timezone=True))
    last_used_ip = Column(String(64))
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    rotated_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by = Column(String(100))

    __table_args__ = (Index("idx_api_key_status", "status"),)
