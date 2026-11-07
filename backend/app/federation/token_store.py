# SPDX-License-Identifier: Apache-2.0
"""peer 서비스 토큰 저장 형식 — Fernet 암호화(``ARGUS_SECRET_KEY`` 파생 키).

DB(``federation_instances.auth_token``)에는 ``enc:`` 접두사가 붙은 암호문만 저장한다.
접두사가 없는 값은 암호화 도입 전 평문 레거시로 보고 그대로 사용한다(하위 호환) —
해당 peer 를 수정·저장하면 암호문으로 바뀐다.
"""

from __future__ import annotations

import logging

from cryptography.fernet import InvalidToken

from app.apis.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

_PREFIX = "enc:"


class PeerTokenDecryptError(RuntimeError):
    """저장된 peer 토큰을 복호화하지 못함(ARGUS_SECRET_KEY 변경 등)."""


def seal(token: str | None) -> str | None:
    """평문 토큰 → 저장용 암호문. 빈 값은 None(토큰 없음)."""
    if not token:
        return None
    return _PREFIX + encrypt(token)


def unseal(stored: str | None) -> str | None:
    """저장값 → 평문 토큰. 레거시 평문·등록 전 임시 객체(평문)는 그대로 반환."""
    if not stored:
        return None
    if not stored.startswith(_PREFIX):
        return stored
    try:
        return decrypt(stored[len(_PREFIX):])
    except InvalidToken as e:
        # 토큰 값·암호문은 남기지 않는다.
        logger.warning("peer 서비스 토큰 복호화 실패 — ARGUS_SECRET_KEY 변경 여부 확인 필요")
        raise PeerTokenDecryptError(
            "peer 서비스 토큰을 복호화하지 못했습니다(ARGUS_SECRET_KEY 확인 후 토큰을 다시 저장하세요)"
        ) from e


def is_sealed(stored: str | None) -> bool:
    """저장값이 암호문(``enc:`` 접두사) 형식인지."""
    return bool(stored) and stored.startswith(_PREFIX)
