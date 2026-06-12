# SPDX-License-Identifier: Apache-2.0
"""페더레이션 export API 의 서비스 토큰 인증.

이 인스턴스가 peer 로서 노출하는 ``/federation/export/*`` 호출을 검증한다.
``settings.federation_export_token`` 이 설정돼 있으면 ``Authorization: Bearer <token>``
이 일치해야 하고, 비어 있으면(개발 기본값) 인증을 강제하지 않는다(/external 과 동일).
"""

import hmac
import logging

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


async def verify_export_token(request: Request) -> None:
    """export 호출의 Bearer 토큰을 검증한다(FastAPI 의존성).

    토큰 미설정 시 통과. 설정 시 불일치/누락이면 401.
    타이밍 공격 완화를 위해 ``hmac.compare_digest`` 로 상수시간 비교한다.
    """
    expected = settings.federation_export_token
    if not expected:
        return  # 토큰 비강제(개발)

    from fastapi import HTTPException

    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    presented = header[len(prefix):] if header.startswith(prefix) else ""
    if not presented or not hmac.compare_digest(presented, expected):
        # 토큰값은 로그에 남기지 않고, 거부 사실/경로/호출자만 기록한다(보안 진단용).
        logger.warning(
            "페더레이션 export 토큰 거부: path=%s client=%s",
            request.url.path,
            request.client.host if request.client else "?",
        )
        raise HTTPException(status_code=401, detail="페더레이션 export 토큰이 유효하지 않습니다")
