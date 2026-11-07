# SPDX-License-Identifier: Apache-2.0
"""로컬 계정 비밀번호 해시·검증·정책.

- 저장 형식: ``pbkdf2_sha256$<반복수>$<salt b64>$<hash b64>`` (표준 라이브러리 PBKDF2-HMAC-SHA256,
  사용자별 무작위 salt). 예전 형식(솔트 없는 SHA-256 16진 64자)도 검증하며, 로그인·비밀번호 변경 때
  새 형식으로 다시 저장한다(``needs_rehash``) — 기존 계정이 별도 이관 없이 점진적으로 옮겨진다.
- 해시 계산은 의도적으로 느리므로(수백 ms) 비동기 경로에서는 ``hash_password_async`` /
  ``verify_password_async`` 로 스레드에서 돌린다.
- 정책(``password_problem``): 8자 이상, 영문 소문자·숫자 각 1자 이상 — 화면(사용자 생성·재설정·
  강제 변경 페이지)과 같은 기준을 서버에서도 강제한다.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re

logger = logging.getLogger(__name__)

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000  # OWASP 권장(PBKDF2-HMAC-SHA256)
_SALT_BYTES = 16
_LEGACY_SHA256 = re.compile(r"^[0-9a-f]{64}$")

MIN_LENGTH = 8


def _b64(data: bytes) -> str:
    """패딩(=) 없는 표준 base64 문자열."""
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    """패딩을 복원해 base64 를 디코딩한다."""
    return base64.b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """무작위 salt 로 PBKDF2-HMAC-SHA256 해시를 만들어 저장 형식 문자열로 반환한다(느림 — 동기)."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """비밀번호가 저장된 해시(새 형식 또는 예전 SHA-256)와 일치하는지 상수 시간 비교로 확인한다.

    저장값이 없거나 형식을 알 수 없으면 False.
    """
    if not stored:
        return False
    if _LEGACY_SHA256.match(stored):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored)
    try:
        algo, iters, salt, expected = stored.split("$")
        if algo != ALGORITHM:
            logger.warning("알 수 없는 비밀번호 해시 알고리즘 — 검증 실패 처리: algo=%s", algo)
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iters))
    except (ValueError, TypeError):
        # 저장 형식이 깨진 해시 — 값 자체는 남기지 않는다
        logger.warning("비밀번호 해시 형식이 올바르지 않음 — 검증 실패 처리")
        return False
    return hmac.compare_digest(_b64(dk), expected)


def needs_rehash(stored: str | None) -> bool:
    """예전 형식이거나 반복수가 현재보다 적으면 True — 검증 성공 직후 새 해시로 저장한다."""
    if not stored or _LEGACY_SHA256.match(stored):
        return True
    try:
        algo, iters, _, _ = stored.split("$")
        return algo != ALGORITHM or int(iters) < ITERATIONS
    except ValueError:
        return True


async def hash_password_async(password: str) -> str:
    """hash_password 를 스레드에서 실행한다(이벤트 루프 블로킹 방지)."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored: str | None) -> bool:
    """verify_password 를 스레드에서 실행한다."""
    return await asyncio.to_thread(verify_password, password, stored)


def password_problem(password: str) -> str | None:
    """정책 위반 사유(없으면 None)."""
    if len(password) < MIN_LENGTH:
        return f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다."
    if not re.search(r"[a-z]", password):
        return "비밀번호에 영문 소문자를 1자 이상 포함해야 합니다."
    if not re.search(r"[0-9]", password):
        return "비밀번호에 숫자를 1자 이상 포함해야 합니다."
    return None
