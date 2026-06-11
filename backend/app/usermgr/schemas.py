# SPDX-License-Identifier: Apache-2.0
"""사용자 관리(User management) 스키마.

요청 검증과 응답 직렬화에 사용하는 Pydantic 모델을 모은다. 구성은 세 부분:

1. **Enum**: ``UserStatus`` (계정 상태), ``RoleName`` (역할 식별자).
2. **Role 스키마**: ``RoleResponse`` — 역할 목록 조회 응답.
3. **User 스키마**: 요청용(추가/수정/역할 변경)과 응답용(단건/페이지네이션).

요청 모델은 Pydantic ``Field`` 로 길이·이메일 형식 등 검증 제약을 명시하고,
응답 모델은 DB 컬럼명과 동일한 snake_case 필드명을 사용한다.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class UserStatus(str, Enum):
    """사용자 계정 상태.

    - ``ACTIVE``:   로그인하여 데이터 소스을 사용할 수 있는 상태.
    - ``INACTIVE``: 계정이 비활성화되어 로그인이 차단된 상태(데이터는 보존).
    """

    ACTIVE = "active"
    INACTIVE = "inactive"


class RoleName(str, Enum):
    """사용 가능한 역할 식별자.

    각 값은 ``argus_roles`` 테이블의 ``role_id`` 컬럼 값과 Keycloak realm
    role 이름 양쪽에 동일하게 매칭된다(로컬·Keycloak 모드 공통).
    """

    ADMIN = "argus-admin"
    SUPERUSER = "argus-superuser"
    USER = "argus-user"


# ---------------------------------------------------------------------------
# 역할 스키마
# ---------------------------------------------------------------------------

class RoleResponse(BaseModel):
    """클라이언트에 반환하는 역할 정보. ORM 객체에서 바로 변환 가능."""

    id: int
    role_id: str
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 사용자 요청 스키마
# ---------------------------------------------------------------------------

class UserAddRequest(BaseModel):
    """신규 사용자 생성 요청.

    ``phone_number`` 를 제외한 모든 필드는 필수다. ``username`` 과 ``email`` 은
    기존 사용자와 중복돼선 안 되며, 이는 서비스 계층(체크 후 인서트)과 DB 의
    UNIQUE 제약으로 이중 방어한다.

    필드:
        - ``username`` (1–100자): 고유 로그인 식별자
        - ``email``: 유효한 이메일 (Pydantic ``EmailStr`` 검증)
        - ``first_name`` / ``last_name`` (각 1–100자)
        - ``phone_number`` (≤30자, 선택): 연락처
        - ``password`` (4–128자): 평문. 저장 전 해시 처리.
        - ``role``: 부여할 역할 (미지정 시 ``User``)
    """

    username: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    organization: str | None = Field(None, max_length=100)  # 소속
    department: str | None = Field(None, max_length=100)     # 소속 부서
    phone_number: str | None = Field(None, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    role: RoleName = Field(RoleName.USER, description="역할 이름 (Admin/Superuser/User)")
    # True 면 최초 로그인 시 비밀번호 변경을 강제한다(LDAP 동기화 계정은 초기 비번이
    # 생년월일이므로 동기화 도구가 True 로 보낸다). 일반 생성은 기본 False.
    must_change_password: bool = Field(False, description="최초 로그인 시 비밀번호 강제 변경 여부")


class UserModifyRequest(BaseModel):
    """사용자 프로필 필드 수정 요청.

    ``None`` 이 아닌 필드만 갱신된다(부분 갱신). 사용자명과 역할은 이 엔드포인트로
    바꿀 수 없으며, 역할은 ``/users/{id}/role`` 엔드포인트를 따로 사용한다.

    필드:
        - ``first_name`` / ``last_name`` (각 1–100자)
        - ``email``
        - ``phone_number`` (≤30자)
    """

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    organization: str | None = Field(None, max_length=100)
    department: str | None = Field(None, max_length=100)
    phone_number: str | None = Field(None, max_length=30)


class UserChangeRoleRequest(BaseModel):
    """역할 변경 요청.

    ``role`` 값은 반드시 ``RoleName`` enum 의 한 값이어야 한다. 서비스 계층이
    이 이름을 DB ``role_id`` 로 변환해 사용자 행을 갱신한다.
    """

    role: RoleName


class UserSetPasswordRequest(BaseModel):
    """관리자에 의한 비밀번호 재설정 요청.

    본인 비밀번호 변경(``/auth/change-password``)과 달리 현재 비밀번호를 요구하지 않고,
    관리자가 대상 사용자의 비밀번호를 직접 설정한다. 로컬 인증 모드에서만 유효하다.
    ``password`` 는 평문이며 서비스 계층에서 해시 처리한다(프론트 검증과 동일하게 8자 이상).
    """

    password: str = Field(..., min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# 사용자 응답 스키마
# ---------------------------------------------------------------------------

class UserResponse(BaseModel):
    """클라이언트에 반환하는 사용자 정보.

    프로필 필드 + ``role_id`` 대신 resolve 된 역할 이름 문자열을 포함한다.
    ``status`` 는 타입 안전을 위해 ``UserStatus`` enum 으로 직렬화되고,
    timestamp 는 ISO 8601 문자열로 변환된다.

    필드:
        - ``id`` (int): DB primary key
        - ``username`` / ``email``
        - ``first_name`` / ``last_name``
        - ``phone_number`` (nullable)
        - ``status``: ``UserStatus``
        - ``role``: 역할 이름 (예: ``argus-admin``)
        - ``created_at`` / ``updated_at``: 타임스탬프
    """

    id: int
    username: str
    email: str
    first_name: str
    last_name: str
    organization: str | None = None
    department: str | None = None
    phone_number: str | None = None
    status: UserStatus
    role: str
    created_at: datetime
    updated_at: datetime


class PaginatedUserResponse(BaseModel):
    """사용자 목록의 페이지네이션 응답.

    ``GET /users`` 가 반환한다. 현재 페이지의 레코드와 페이지 컨트롤 구성에 필요한
    메타(총 건수, 현재 페이지, 페이지 크기)를 함께 담는다.

    필드:
        - ``items``: 현재 페이지의 ``UserResponse`` 배열
        - ``total``: 필터를 적용한 후의 총 건수
        - ``page`` (1-based)
        - ``page_size``
    """

    items: list[UserResponse]
    total: int
    page: int
    page_size: int
