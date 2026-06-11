"""사용자 관리(User management) 모듈의 SQLAlchemy ORM 모델.

테이블 구성:
    - ``ArgusRole``: 역할 정의. ``role_id`` 식별자(예: ``argus-admin``) 는 Keycloak
      realm role 이름과 동일하게 맞춰 두 인증 모드에서 동일하게 동작한다.
    - ``ArgusUser``: 로컬 인증용 사용자 계정. ``role_id`` 외래키로 역할에 연결.
    - ``ArgusUserPreference``: 두 인증 모드 모두에서 사용 가능한 UI 선호도
      (현재는 아바타 preset). 토큰의 ``sub`` 를 키로 사용한다.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func

from app.core.database import Base


class ArgusRole(Base):
    """역할 테이블.

    컬럼:
        - ``id``: auto-increment PK
        - ``role_id``: 역할 식별자(예: ``argus-admin``). Keycloak realm role 이름과
          1:1 매칭되어야 한다. UNIQUE.
        - ``name``: 화면 표시용 이름(예: ``Admin``, ``Superuser``, ``User``)
        - ``description``: 역할 설명(선택)
        - ``created_at`` / ``updated_at``: 생성·수정 타임스탬프
    """

    __tablename__ = "argus_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(String(50), nullable=False, unique=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ArgusUserPreference(Base):
    """사용자별 UI 선호도.

    토큰의 ``sub`` 를 PK 로 사용해 두 인증 모드에서 동일하게 동작한다:
        - 로컬 인증: ``sub = str(argus_users.id)`` (예: ``"42"``)
        - Keycloak 인증: ``sub = Keycloak user UUID``

    현재는 아바타 preset 만 저장하지만, 향후 다른 사용자 단위 UI 설정도 이
    테이블에 컬럼으로 추가 가능하다.
    """

    __tablename__ = "argus_user_preferences"

    sub = Column(String(100), primary_key=True)
    avatar_preset_id = Column(String(50))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ArgusUser(Base):
    """사용자 테이블. 정체성·자격 증명·상태를 저장.

    로컬 인증 모드에서만 사용된다. Keycloak 모드에서는 이 테이블에 행이
    없으며, 사용자 정보는 Keycloak 의 JWT claim 에서 얻는다.

    컬럼:
        - ``id``: auto-increment PK
        - ``username``: 로그인 식별자 (UNIQUE)
        - ``email``: 이메일 (UNIQUE)
        - ``first_name`` / ``last_name``
        - ``phone_number``: 연락처(선택)
        - ``password_hash``: SHA-256 해시(평문 저장 금지)
        - ``status``: ``"active"`` / ``"inactive"``
        - ``role_id``: ``argus_roles.id`` 참조 외래키
        - ``created_at`` / ``updated_at``
    """

    __tablename__ = "argus_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(255), nullable=False, unique=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    organization = Column(String(100))  # 소속 (예: 데이터다이나믹스)
    department = Column(String(100))     # 소속 부서 (예: 데이터 플랫폼팀)
    phone_number = Column(String(30))
    password_hash = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    # True 면 최초 로그인 시 비밀번호 변경을 강제한다(LDAP 동기화 계정은 초기 비번이
    # 생년월일이라 반드시 변경시킨다). 비밀번호 변경 성공 시 False 로 해제된다.
    must_change_password = Column(Boolean, nullable=False, default=False, server_default="false")
    role_id = Column(Integer, ForeignKey("argus_roles.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
