# SPDX-License-Identifier: Apache-2.0
"""대칭 암호화 헬퍼(Fernet) — API 자격증명 시크릿 저장용.

키는 환경변수 ``ARGUS_SECRET_KEY`` (없으면 개발용 기본 패스프레이즈)에서 SHA-256
파생해 Fernet 키로 사용한다. 운영에서는 반드시 ARGUS_SECRET_KEY 를 설정할 것 —
``ARGUS_REQUIRE_SECRETS=true`` 이면 미설정·기본값·짧은 값으로는 암복호화하지 않는다(app.core.secrets).
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.secrets import DEV_SECRET_KEY, resolve_secret

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    """``ARGUS_SECRET_KEY`` 의 SHA-256 다이제스트를 urlsafe-base64 로 인코딩한 Fernet 키를 만든다.

    ``resolve_secret`` 은 ``ARGUS_REQUIRE_SECRETS=true`` 에서 키가 부적절하면 ``InsecureSecretError`` 를 낸다.
    """
    passphrase = resolve_secret("ARGUS_SECRET_KEY")
    if passphrase == DEV_SECRET_KEY:
        logger.warning("ARGUS_SECRET_KEY 미설정 — 개발용 기본 키 사용(운영 비권장).")
    key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode("utf-8")).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    """평문을 Fernet 토큰(문자열)으로 암호화한다. ``None``/빈 값은 빈 문자열로 취급."""
    return _fernet().encrypt((text or "").encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Fernet 토큰을 복호화한다. 키가 다르거나 토큰이 손상되면 ``InvalidToken`` 을 다시 올린다."""
    try:
        return _fernet().decrypt((token or "").encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("자격증명 복호화 실패(키 불일치 가능)")
        raise
