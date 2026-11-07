"""사용자가 지정한 URL 로 나가는 서버측 HTTP 요청의 SSRF 방어.

API 카탈로그의 스펙 URL 가져오기·Try-it 프록시(invoke)처럼 **사용자가 입력한 URL 을
서버가 대신 호출**하는 경로에서 사용한다. 정책:

1. 스킴은 http/https 만 허용.
2. 항상 차단 — 루프백, 링크로컬(클라우드 메타데이터 169.254.169.254 포함), 미지정(0.0.0.0),
   멀티캐스트, 예약 대역. IPv4-mapped IPv6 도 원래 IPv4 로 판정한다.
3. 카탈로그 자체 인프라 차단 — DB·오브젝트 스토리지·Temporal·Keycloak·metadata-sync 의
   (해석된 IP, 포트) 와 일치하면 차단. 같은 호스트의 다른 포트(업무 API)는 막지 않는다.
4. 사설망(RFC1918·ULA·CGNAT)은 기본 허용 — 사내 API 등록이 API 카탈로그의 주 용도이므로.
   ``api_outbound.block_private: true`` 로 차단할 수 있다.
5. ``api_outbound.denied_hosts`` (호스트명/IP/CIDR) 는 추가 차단,
   ``api_outbound.allowed_hosts`` 는 위 2~5 규칙보다 우선하는 예외 허용.
6. 리다이렉트는 자동으로 따라가지 않고, 매 hop 의 Location 을 같은 규칙으로 다시 검사한다.

한계: 검사 시점과 실제 연결 시점의 DNS 결과가 다를 수 있다(DNS rebinding). 짧은 TTL 로
결과를 바꾸는 공격까지 막으려면 egress 프록시/네트워크 정책을 함께 쓴다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_MAX_REDIRECTS = 5

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class OutboundBlockedError(Exception):
    """보안 정책으로 차단된 외부 호출."""


class OutboundResolveError(OSError):
    """호스트 이름을 해석할 수 없음 — 보안 차단이 아니라 일반 연결 오류로 취급한다."""


# ---------------------------------------------------------------------------
# 규칙
# ---------------------------------------------------------------------------

def _normalize(ip: IPAddress) -> IPAddress:
    """IPv4-mapped IPv6(::ffff:a.b.c.d)는 원래 IPv4 로 바꿔 규칙을 우회하지 못하게 한다."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _always_blocked_reason(ip: IPAddress) -> str | None:
    """항상 차단하는 대역이면 사유 문자열, 아니면 None."""
    if ip.is_loopback:
        return "루프백 주소"
    if ip.is_link_local:
        return "링크로컬 주소(클라우드 메타데이터 포함)"
    if ip.is_unspecified:
        return "미지정 주소"
    if ip.is_multicast:
        return "멀티캐스트 주소"
    if ip.is_reserved:
        return "예약 대역 주소"
    return None


def _is_private(ip: IPAddress) -> bool:
    """사설망(RFC1918·ULA 등 ``is_private``) 또는 CGNAT(100.64.0.0/10) 여부."""
    return ip.is_private or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)


def _host_matches(host: str, ips: list[IPAddress], entries: list[str]) -> bool:
    """host 가 목록(호스트명·IP·CIDR) 중 하나와 일치하는지.

    IP/CIDR 항목은 해석된 IP 중 하나라도 포함되면 일치, 그 밖의 항목은 호스트명 정확 일치
    (대소문자·끝의 점 무시, 와일드카드 없음).
    """
    host_l = host.lower().rstrip(".")
    for entry in entries:
        e = entry.strip().lower().rstrip(".")
        if not e:
            continue
        try:
            net = ipaddress.ip_network(e, strict=False)
        except ValueError:
            if host_l == e:
                return True
            continue
        if any(ip in net for ip in ips):
            return True
    return False


def _infra_endpoints() -> list[tuple[str, int]]:
    """카탈로그가 의존하는 내부 인프라의 (호스트, 포트) 목록."""
    out: list[tuple[str, int]] = [(settings.db_host, int(settings.db_port))]
    for url in (settings.os_endpoint, settings.auth_keycloak_server_url, settings.metadata_sync_base_url):
        parts = urlsplit(url or "")
        if parts.hostname:
            out.append((parts.hostname, parts.port or _DEFAULT_PORTS.get(parts.scheme, 80)))
    host, _, port = (settings.temporal_target or "").rpartition(":")
    if host and port.isdigit():
        out.append((host, int(port)))
    return out


async def _resolve(host: str, port: int) -> list[IPAddress]:
    """host 가 IP 리터럴이면 그대로, 아니면 DNS 로 해석한 IP 목록(중복 제거). 실패 시 OutboundResolveError."""
    try:
        return [_normalize(ipaddress.ip_address(host))]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise OutboundResolveError(f"호스트 '{host}' 를 해석할 수 없습니다: {e}") from e
    ips: list[IPAddress] = []
    for info in infos:
        ip = _normalize(ipaddress.ip_address(info[4][0]))
        if ip not in ips:
            ips.append(ip)
    return ips


async def _infra_targets() -> set[tuple[IPAddress, int]]:
    """내부 인프라 엔드포인트를 해석한 (IP, 포트) 집합 — 호출마다 다시 해석한다."""
    targets: set[tuple[IPAddress, int]] = set()
    for host, port in _infra_endpoints():
        try:
            for ip in await _resolve(host, port):
                targets.add((ip, port))
        except OutboundResolveError:
            continue  # 해석 안 되는 인프라 호스트는 비교 대상에서 제외
    return targets


async def check_url(url: str) -> None:
    """URL 이 정책상 호출 가능한지 검사한다. 불가하면 ``OutboundBlockedError``."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise OutboundBlockedError(f"허용되지 않는 스킴입니다: '{parts.scheme or '(없음)'}' (http/https 만 허용)")
    host = parts.hostname
    if not host:
        raise OutboundBlockedError("URL 에 호스트가 없습니다.")
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError as e:
        raise OutboundBlockedError(f"잘못된 포트입니다: {e}") from e

    ips = await _resolve(host, port)
    if _host_matches(host, ips, settings.api_outbound_allowed_hosts):
        logger.debug("외부 호출 허용 목록 일치 — 검사 생략: host=%s", host)
        return
    if _host_matches(host, ips, settings.api_outbound_denied_hosts):
        logger.warning("외부 호출 차단(차단 목록): host=%s", host)
        raise OutboundBlockedError(f"'{host}' 는 호출 차단 목록(api_outbound.denied_hosts)에 있습니다.")

    infra = await _infra_targets()
    for ip in ips:
        reason = _always_blocked_reason(ip)
        if reason:
            logger.warning("외부 호출 차단(%s): host=%s ip=%s", reason, host, ip)
            raise OutboundBlockedError(f"'{host}' ({ip}) — {reason}로의 호출은 차단됩니다.")
        if (ip, port) in infra:
            logger.warning("외부 호출 차단(내부 인프라): host=%s port=%d", host, port)
            raise OutboundBlockedError(f"'{host}:{port}' 는 카탈로그 내부 인프라라 호출할 수 없습니다.")
        if settings.api_outbound_block_private and _is_private(ip):
            logger.warning("외부 호출 차단(사설망): host=%s ip=%s", host, ip)
            raise OutboundBlockedError(
                f"'{host}' ({ip}) — 사설망 호출이 차단되어 있습니다(api_outbound.block_private)."
            )


async def guarded_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs,
) -> httpx.Response:
    """정책 검사 후 요청한다. 리다이렉트는 hop 마다 다시 검사하며 최대 5회 따라간다.

    ``client`` 는 ``follow_redirects=False`` 여야 한다(여기서 직접 처리).
    """
    for _ in range(_MAX_REDIRECTS + 1):
        await check_url(url)
        resp = await client.request(method, url, follow_redirects=False, **kwargs)
        location = resp.headers.get("location")
        if not (resp.is_redirect and location):
            return resp
        url = urljoin(str(resp.url), location)
        # 303, 그리고 POST 의 301/302 는 브라우저와 같이 GET(본문 없음)으로 바꾼다.
        if resp.status_code == 303 or (resp.status_code in (301, 302) and method.upper() == "POST"):
            method = "GET"
            kwargs.pop("content", None)
            kwargs.pop("json", None)
            kwargs.pop("data", None)
        logger.info("외부 호출 리다이렉트 → %s", url)
    logger.warning("외부 호출 리다이렉트 횟수 초과: max=%d", _MAX_REDIRECTS)
    raise OutboundBlockedError(f"리다이렉트가 너무 많습니다(최대 {_MAX_REDIRECTS}회).")
