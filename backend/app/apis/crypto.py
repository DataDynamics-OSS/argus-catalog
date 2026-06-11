"""대칭 암호화 헬퍼(Fernet) — API 자격증명 시크릿 저장용.

키는 환경변수 ``ARGUS_SECRET_KEY`` (없으면 개발용 기본 패스프레이즈)에서 SHA-256
파생해 Fernet 키로 사용한다. 운영에서는 반드시 ARGUS_SECRET_KEY 를 설정할 것.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DEV_PASSPHRASE = "argus-catalog-dev-secret-change-me"


def _fernet() -> Fernet:
    passphrase = os.environ.get("ARGUS_SECRET_KEY") or _DEV_PASSPHRASE
    if passphrase == _DEV_PASSPHRASE:
        logger.warning("ARGUS_SECRET_KEY 미설정 — 개발용 기본 키 사용(운영 비권장).")
    key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode("utf-8")).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt((text or "").encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt((token or "").encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("자격증명 복호화 실패(키 불일치 가능)")
        raise
