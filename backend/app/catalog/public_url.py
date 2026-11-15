# SPDX-License-Identifier: Apache-2.0
"""외부 공개 주소 — DCAT · Croissant · 데이터 카드에 넣는 이 서버의 주소.

내보내기 결과는 다른 시스템(포털 · AI 학습 도구)이 읽으므로 그쪽에서 닿는 주소여야 한다.
요청 주소는 웹 서버(Next) 프록시를 거치면 ``localhost:4600`` 이 되기 쉬워, 관리자가
설정 › AI-Ready 에서 방식을 고른다.

- ``REQUEST`` 요청 주소(기본) — 프록시가 넘긴 ``X-Forwarded-Host`` · ``X-Forwarded-Proto`` 를 먼저 본다
- ``FQDN`` 이 서버의 정규 호스트 이름(``socket.getfqdn()``)
- ``IP`` 바인딩 IP — ``server.host`` 가 0.0.0.0 · :: 이면 외부로 나가는 기본 인터페이스의 IP
- ``CUSTOM`` 직접 입력한 URL(예: ``https://catalog.example.go.kr``)

FQDN · IP 는 스킴과 포트를 붙인다. 포트를 비우면 백엔드 포트(``server.port``)다.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

MODES = ("REQUEST", "FQDN", "IP", "CUSTOM")
SCHEMES = ("http", "https")

KEY_MODE = "ai_ready_public_mode"
KEY_SCHEME = "ai_ready_public_scheme"
KEY_PORT = "ai_ready_public_port"
KEY_URL = "ai_ready_public_url"

_ANY = {"", "0.0.0.0", "::", "[::]"}


def fqdn() -> str:
    return socket.getfqdn() or socket.gethostname() or "localhost"


def binding_ip() -> str:
    """바인딩 주소가 특정 IP 면 그 값, 모든 인터페이스면 기본 경로의 IP(패킷은 보내지 않음)."""
    host = (settings.host or "").strip()
    if host not in _ANY:
        return host
    for family, target in ((socket.AF_INET, ("10.254.254.254", 1)), (socket.AF_INET6, ("fd00::1", 1))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect(target)
                ip = s.getsockname()[0]
                if ip and not ipaddress.ip_address(ip).is_unspecified:
                    return ip
        except OSError:
            continue
    return "127.0.0.1"


def _host_part(host: str) -> str:
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


def compose(host: str, scheme: str = "http", port: int | None = None) -> str:
    """``scheme://host[:port]/`` — 스킴 기본 포트(80 · 443)는 생략."""
    scheme = scheme if scheme in SCHEMES else "http"
    port = port or settings.port
    default = 443 if scheme == "https" else 80
    suffix = "" if port == default else f":{port}"
    return f"{scheme}://{_host_part(host)}{suffix}/"


def request_base(request: Request) -> str:
    """요청 주소 — 프록시 헤더가 있으면 그 호스트 · 스킴(여러 단계면 첫 값)."""
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if not host:
        return str(request.base_url)
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto not in SCHEMES:
        proto = request.url.scheme
    return f"{proto}://{host}/"


def candidates(scheme: str = "http", port: int | None = None) -> dict[str, str]:
    """설정 화면 안내용 — FQDN · 바인딩 IP 방식이면 어떤 주소가 되는지."""
    return {"FQDN": compose(fqdn(), scheme, port), "IP": compose(binding_ip(), scheme, port)}


def resolve(cfg: dict[str, str], request: Request | None) -> str:
    mode = (cfg.get(KEY_MODE) or "REQUEST").upper()
    scheme = cfg.get(KEY_SCHEME) or "http"
    try:
        port = int(cfg.get(KEY_PORT) or 0) or None
    except ValueError:
        port = None
    if mode == "CUSTOM" and (url := (cfg.get(KEY_URL) or "").strip()):
        return url.rstrip("/") + "/"
    if mode == "FQDN":
        return compose(fqdn(), scheme, port)
    if mode == "IP":
        return compose(binding_ip(), scheme, port)
    if request is not None:
        return request_base(request)
    return compose(binding_ip(), scheme, port)


async def public_base(session: AsyncSession, request: Request | None) -> str:
    """설정에 따른 공개 주소(끝에 ``/``)."""
    from app.settings.service import get_config_by_category

    return resolve(await get_config_by_category(session, "ai_ready"), request)
