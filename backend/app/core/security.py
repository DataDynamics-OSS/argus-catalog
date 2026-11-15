# SPDX-License-Identifier: Apache-2.0
"""보안 유틸리티 및 미들웨어."""

import logging

from starlette.datastructures import MutableHeaders

logger = logging.getLogger(__name__)


_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("X-XSS-Protection", "1; mode=block"),
)


class SecurityHeadersMiddleware:
    """모든 응답에 보안 헤더를 추가한다(순수 ASGI — 응답 시작 메시지의 헤더만 고친다, TODO-086).

    nosniff(MIME 추측 금지)·DENY(iframe 삽입 금지)·X-XSS-Protection(구형 브라우저용).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in _SECURITY_HEADERS:
                    headers[k] = v
            await send(message)

        await self.app(scope, receive, send_with_headers)
