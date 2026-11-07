# SPDX-License-Identifier: Apache-2.0
"""파일 다운로드용 짧은 토큰 — 브라우저가 링크로 바로 내려받게 한다.

다운로드 API 는 로그인(``Authorization`` 헤더)이 필요한데, 브라우저의 일반 링크(``<a href>``)로는
헤더를 실을 수 없다. 그래서 화면은 파일 전체를 메모리에 받아 저장해야 했고, 수 GB 모델 파일은
멈추거나 실패했다. 로그인한 사용자가 파일 하나에 대해 이 토큰을 받아 주소에 붙이면, 브라우저가
스트리밍으로 바로 저장한다.

- 대상(``kind``)과 경로(``path``)가 토큰에 묶여 다른 파일에는 쓸 수 없다.
- 유효 시간은 짧다(기본 120초). 다운로드 시작에만 쓰이므로 큰 파일도 문제없다.
- 일반 접근 토큰과 ``typ`` 이 달라 API 인증에는 쓸 수 없다(``typ=download``).
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

from fastapi import HTTPException
from jose import JWTError, jwt

from app.core.auth import LOCAL_JWT_ALGORITHM, LOCAL_JWT_SECRET_KEY

logger = logging.getLogger(__name__)

TOKEN_TYPE_DOWNLOAD = "download"
DEFAULT_TTL_SECONDS = 120


def issue(kind: str, path: str, sub: str, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """``kind``(예: ``fs:model-artifacts``, ``s3``)·``path`` 에 묶인 다운로드 토큰을 만든다."""
    now = int(time.time())
    logger.debug("다운로드 토큰 발급: kind=%s path=%s sub=%s ttl=%d", kind, path, sub, ttl)
    payload = {"typ": TOKEN_TYPE_DOWNLOAD, "knd": kind, "pth": path, "sub": sub, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, LOCAL_JWT_SECRET_KEY, algorithm=LOCAL_JWT_ALGORITHM)


def verify(token: str, kind: str, path: str) -> dict:
    """토큰이 이 대상·경로용이고 아직 유효한지 확인한다. 아니면 HTTP 401/403."""
    try:
        payload = jwt.decode(token, LOCAL_JWT_SECRET_KEY, algorithms=[LOCAL_JWT_ALGORITHM],
                             options={"verify_aud": False})
    except JWTError as e:
        logger.warning("다운로드 토큰 검증 실패(만료·위조): kind=%s path=%s: %s", kind, path, e)
        raise HTTPException(status_code=401, detail=f"다운로드 링크가 만료되었거나 올바르지 않습니다: {e}") from e
    if payload.get("typ") != TOKEN_TYPE_DOWNLOAD:
        logger.warning("다운로드 토큰 용도 불일치: typ=%s sub=%s", payload.get("typ"), payload.get("sub"))
        raise HTTPException(status_code=401, detail="다운로드 링크가 올바르지 않습니다.")
    if payload.get("knd") != kind or payload.get("pth") != path:
        logger.warning("다운로드 토큰 대상 불일치: sub=%s kind=%s path=%s", payload.get("sub"), kind, path)
        raise HTTPException(status_code=403, detail="이 파일의 다운로드 링크가 아닙니다.")
    return payload


def content_disposition(filename: str, inline: bool = False) -> str:
    """한글 등 비 ASCII 파일 이름도 깨지지 않는 ``Content-Disposition`` (RFC 6266/5987)."""
    kind = "inline" if inline else "attachment"
    # 구형 클라이언트용 filename 은 ASCII 로(비 ASCII·따옴표·? 는 _ 로), filename* 에 UTF-8 원본
    fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("?", "_")
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
