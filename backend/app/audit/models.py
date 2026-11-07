# SPDX-License-Identifier: Apache-2.0
"""관리 감사 로그 ORM 모델 — 사용자 관리·설정·권한 관리 변경 기록."""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, func

from app.core.database import Base


class AdminAuditLog(Base):
    """관리 메뉴(사용자·설정·권한) 변경 1건.

    - ``category``: ``users`` / ``settings`` / ``permissions``
    - ``action``: 예) ``user.create``, ``user.role``, ``settings.llm``, ``permissions.menus``
    - ``target``: 대상(사용자 아이디, 설정 범주, 권한 종류 등)
    - ``detail``: 변경 내용 JSON(비밀값은 값 없이 "변경됨"만)
    """

    __tablename__ = "argus_admin_audit_log"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor = Column(String(100))
    category = Column(String(30), nullable=False)
    action = Column(String(60), nullable=False)
    target = Column(String(200))
    summary = Column(String(500))
    detail = Column(Text)
    client_ip = Column(String(64))

    __table_args__ = (
        Index("idx_admin_audit_created", "created_at"),
        Index("idx_admin_audit_category", "category", "created_at"),
    )
