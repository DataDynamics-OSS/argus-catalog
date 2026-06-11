# SPDX-License-Identifier: Apache-2.0
"""권한 매트릭스 모델.

행 의미: (kind, perm_key) 에 role 이 허용됨.
정책(open-by-default): 어떤 perm_key 에 대한 행이 **하나도 없으면 전 역할
허용** — 도입 시 기존 동작이 바뀌지 않고, 잠그고 싶은 항목만 명시 저장한다.
Admin(argus-admin)은 행과 무관하게 항상 허용 (저장도 하지 않음).
"""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from app.core.database import Base


class Permission(Base):
    """역할별 메뉴/기능 허용 행."""

    __tablename__ = "argus_permissions"
    __table_args__ = (UniqueConstraint("kind", "perm_key", "role_id", name="uq_permission"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(10), nullable=False)        # MENU | FEATURE
    perm_key = Column(String(100), nullable=False)   # 메뉴/기능 key (프런트 레지스트리와 일치)
    role_id = Column(String(50), nullable=False)     # argus-superuser | argus-user
    created_at = Column(DateTime(timezone=True), server_default=func.now())
