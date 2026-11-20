# SPDX-License-Identifier: Apache-2.0
"""DEF-AUTH-001 — 사내 ``.local`` 도메인 이메일로 프로필을 저장할 수 있어야 한다.

기본 관리자 이메일이 ``admin@argus.local`` 이라, email-validator 의 특수 용도 도메인 거부가 남아 있으면
계정 설정에서 비밀번호를 바꾼 뒤 이어지는 프로필 저장(PUT /usermgr/users/{id})이 422 로 실패했다.
"""

import pytest
from pydantic import ValidationError

from app.usermgr.schemas import UserAddRequest, UserModifyRequest


@pytest.mark.parametrize("email", ["admin@argus.local", "kim@corp.example.local", "user@example.com"])
def test_modify_accepts_local_domain(email):
    assert UserModifyRequest(email=email).email == email


def test_create_accepts_default_admin_email():
    req = UserAddRequest(
        username="admin2", email="admin@argus.local", first_name="관", last_name="리자", password="admin1234",
    )
    assert req.email == "admin@argus.local"


@pytest.mark.parametrize("email", ["admin@argus.invalid", "admin@localhost", "not-an-email"])
def test_other_invalid_emails_still_rejected(email):
    with pytest.raises(ValidationError):
        UserModifyRequest(email=email)
