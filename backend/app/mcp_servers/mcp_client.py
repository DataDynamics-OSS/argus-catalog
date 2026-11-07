# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 수집 클라이언트.

1. **표준 MCP (Streamable HTTP)** — 어떤 개발자가 만든 서버든 MCP 스펙만 따르면 수집 가능.
   ``initialize`` → ``notifications/initialized`` → ``tools/list`` · ``resources/list`` ·
   ``resources/templates/list`` · ``prompts/list`` (cursor 페이지네이션). 응답은
   ``application/json`` 또는 ``text/event-stream`` 모두 처리한다.
2. **Argus Forge 도구 REST** — ``GET {api}/tools`` (name, description, mutating, inputSchema).
3. **헬스** — Forge ``/readyz``, 그 밖은 MCP ``initialize`` 성공 여부.

백엔드 의존성은 고정 버전으로 폐쇄망 패키징되므로 공식 ``mcp`` SDK 대신 이미 쓰는
``httpx`` 로 필요한 JSON-RPC 호출만 구현한다(설계 결정 4 의 구현 선택).

4. **Forge manifest** — ``GET {api}/manifest`` (설계 §6.2, Forge 변경 후 제공).

안전장치:
  - http/https 만 허용, 리다이렉트는 따라가지 않는다.
  - 공용 SSRF 가드(``app.core.outbound``) — 루프백·링크로컬(클라우드 메타데이터)·카탈로그 내부
    인프라 차단, ``api_outbound.*`` 설정(사설망 차단·허용/차단 목록)을 그대로 따른다.
  - 환경변수 ``ARGUS_MCP_COLLECT_ALLOWED_HOSTS``(쉼표 구분 fnmatch)를 설정하면 그 호스트만
    수집할 수 있고, 목록에 든 호스트는 운영자가 명시 허용한 것으로 보고 SSRF 가드를 건너뛴다
    (예: 로컬 개발용 ``localhost``).
  - 타임아웃·응답 크기·페이지 수 상한.
stdio 서버는 카탈로그 서버에서 실행하지 않는다(임의 코드 실행 위험 — 설계 결정 2).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"  # initialize 때 제안하는 MCP 프로토콜 버전
CLIENT_INFO = {"name": "argus-catalog", "version": "1"}
MAX_PAGES = 50  # 목록 조회 cursor 페이지 상한
MAX_BYTES = 8 * 1024 * 1024  # 응답 본문 크기 상한(8MiB)
DEFAULT_TIMEOUT = 15.0  # 초


class CollectError(Exception):
    """수집 실패 (사용자에게 보여줄 메시지)."""


@dataclass
class Credential:
    """수집·헬스 요청에 붙일 자격증명. ``secret`` 이 비어 있으면 헤더를 붙이지 않는다."""

    kind: str = "bearer"  # bearer / api_key / basic
    secret: str = ""
    header_name: str | None = None
    username: str | None = None

    def headers(self) -> dict[str, str]:
        """요청 헤더 — api_key 는 ``header_name``(기본 X-API-Key), basic 은 Basic, 그 밖은 Bearer."""
        if not self.secret:
            return {}
        if self.kind == "api_key":
            return {self.header_name or "X-API-Key": self.secret}
        if self.kind == "basic":
            import base64

            raw = f"{self.username or ''}:{self.secret}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {"Authorization": f"Bearer {self.secret}"}


@dataclass
class McpSnapshot:
    """한 번의 수집 결과 — initialize 정보와 도구·리소스·프롬프트 목록. ``source`` 는 mcp/forge_rest."""

    server_info: dict = field(default_factory=dict)
    protocol_version: str | None = None
    capabilities: dict = field(default_factory=dict)
    instructions: str | None = None
    tools: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    resource_templates: list[dict] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=list)
    source: str = "mcp"
    latency_ms: int | None = None


def check_url(url: str) -> bool:
    """정적 검사. 수집 허용 목록에 명시된 호스트면 True (SSRF 가드 생략 대상)."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logger.warning("MCP 수집 URL 거부(http(s) 아님 또는 호스트 없음): scheme=%s", parts.scheme)
        raise CollectError(f"http(s) URL 만 수집할 수 있습니다: {url!r}")
    allowed = [p.strip() for p in os.environ.get("ARGUS_MCP_COLLECT_ALLOWED_HOSTS", "").split(",") if p.strip()]
    if not allowed:
        return False
    if not any(fnmatch.fnmatch(parts.hostname.lower(), p.lower()) for p in allowed):
        logger.warning("MCP 수집 허용 호스트 목록에 없는 대상 거부: host=%s", parts.hostname)
        raise CollectError(
            f"수집 허용 호스트(ARGUS_MCP_COLLECT_ALLOWED_HOSTS)에 없는 대상입니다: {parts.hostname}"
        )
    logger.debug("MCP 수집 허용 호스트 일치 — SSRF 가드 생략: host=%s", parts.hostname)
    return True


async def guard_url(url: str) -> None:
    """접속 전 검사: 정적 검사 + 공용 SSRF 가드."""
    if check_url(url):
        return
    from app.core.outbound import OutboundBlockedError, OutboundResolveError
    from app.core.outbound import check_url as outbound_check

    try:
        await outbound_check(url)
    except OutboundBlockedError as exc:
        logger.warning("MCP 수집 대상이 SSRF 가드로 차단됨: host=%s reason=%s", urlsplit(url).hostname, exc)
        raise CollectError(f"보안 정책으로 차단된 대상입니다: {exc}") from exc
    except OutboundResolveError as exc:
        logger.warning("MCP 수집 대상 호스트 해석 실패: host=%s reason=%s", urlsplit(url).hostname, exc)
        raise CollectError(str(exc)) from exc


def origin_of(url: str) -> str:
    """URL 의 scheme://netloc 부분(경로·쿼리 제거)."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def _parse_body(resp: httpx.Response, request_id: int) -> dict:
    """JSON-RPC 응답(JSON 단건·배치 또는 SSE)에서 ``request_id`` 의 result 를 꺼낸다.

    SSE 는 빈 줄로 구분된 이벤트의 ``data:`` 줄을 합쳐 JSON 으로 해석하고, 해석 안 되는 이벤트는 건너뛴다.

    Raises:
        CollectError: 크기 초과·JSON 아님·JSON-RPC error·해당 id 응답 없음.
    """
    if len(resp.content) > MAX_BYTES:
        raise CollectError("응답이 너무 큽니다")
    ctype = resp.headers.get("content-type", "")
    messages: list[dict] = []
    if "text/event-stream" in ctype:
        data_lines: list[str] = []
        for line in resp.text.splitlines() + [""]:
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "" and data_lines:
                try:
                    messages.append(json.loads("\n".join(data_lines)))
                except ValueError:
                    logger.debug("SSE 이벤트 JSON 해석 실패 — 건너뜀: request_id=%d", request_id)
                data_lines = []
    else:
        try:
            body = resp.json()
        except ValueError as exc:
            raise CollectError("MCP 응답이 JSON 이 아닙니다") from exc
        messages = body if isinstance(body, list) else [body]
    for msg in messages:
        if isinstance(msg, dict) and msg.get("id") == request_id:
            if "error" in msg:
                err = msg["error"] or {}
                logger.warning("MCP JSON-RPC 오류 응답: code=%s message=%s", err.get("code"), err.get("message"))
                raise CollectError(f"MCP 오류 {err.get('code')}: {err.get('message')}")
            return msg.get("result") or {}
    raise CollectError("MCP 응답에서 요청 결과를 찾지 못했습니다")


class StreamableHttpClient:
    """MCP Streamable HTTP 최소 클라이언트 (목록 조회 전용)."""

    def __init__(self, endpoint: str, credential: Credential | None = None,
                 timeout: float = DEFAULT_TIMEOUT, transport: httpx.AsyncBaseTransport | None = None):
        check_url(endpoint)
        self.endpoint = endpoint
        self.headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **(credential.headers() if credential else {}),
        }
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._id = 0
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    async def __aenter__(self) -> StreamableHttpClient:
        """접속 전 SSRF 가드를 통과해야 진입한다(실패 시 클라이언트를 닫고 CollectError)."""
        try:
            await guard_url(self.endpoint)
        except CollectError:
            await self._client.aclose()
            raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """세션이 열렸으면 DELETE 로 종료를 알리고(실패 무시) HTTP 클라이언트를 닫는다."""
        if self.session_id:
            try:
                await self._client.delete(self.endpoint, headers=self._headers())
            except httpx.HTTPError:
                logger.debug("MCP 세션 종료(DELETE) 실패 — 무시")
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        """기본 헤더 + 협상된 세션 id·프로토콜 버전 헤더."""
        h = dict(self.headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    async def request(self, method: str, params: dict | None = None) -> dict:
        """JSON-RPC 요청 한 건 → result. 응답의 ``Mcp-Session-Id`` 를 이후 요청에 쓴다.

        Raises:
            CollectError: 접속 실패·401/403·그 밖의 4xx/5xx·응답 해석 실패.
        """
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        logger.debug("MCP 요청: method=%s id=%d", method, self._id)
        try:
            resp = await self._client.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("MCP 서버 접속 실패: method=%s error=%s", method, exc)
            raise CollectError(f"MCP 서버 접속 실패: {exc}") from exc
        if resp.status_code in (401, 403):
            logger.warning("MCP 서버 인증 실패: method=%s status=%d", method, resp.status_code)
            raise CollectError(f"MCP 서버 인증 실패 (HTTP {resp.status_code}) — 자격증명을 확인하세요")
        if resp.status_code >= 400:
            logger.warning("MCP 서버 HTTP 오류: method=%s status=%d", method, resp.status_code)
            raise CollectError(f"MCP 서버 HTTP {resp.status_code}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return _parse_body(resp, self._id)

    async def notify(self, method: str) -> None:
        """JSON-RPC 알림(id 없음) 전송 — 응답·실패는 무시한다."""
        try:
            await self._client.post(
                self.endpoint, headers=self._headers(), json={"jsonrpc": "2.0", "method": method}
            )
        except httpx.HTTPError:
            logger.debug("MCP 알림 전송 실패 — 무시: method=%s", method)

    async def initialize(self) -> dict:
        """``initialize`` 후 ``notifications/initialized`` 를 보내 세션을 연다. initialize result 반환."""
        result = await self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        await self.notify("notifications/initialized")
        return result

    async def list_all(self, method: str, key: str) -> list[dict]:
        """cursor 페이지네이션 목록을 ``MAX_PAGES`` 까지 모아 ``key`` 항목(dict 만)을 돌려준다."""
        items: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            result = await self.request(method, {"cursor": cursor} if cursor else {})
            items.extend(x for x in (result.get(key) or []) if isinstance(x, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            logger.warning("MCP 목록 페이지 상한 도달 — 일부만 수집: method=%s pages=%d items=%d",
                           method, MAX_PAGES, len(items))
        return items


async def collect_mcp(endpoint: str, credential: Credential | None = None,
                      transport: httpx.AsyncBaseTransport | None = None) -> McpSnapshot:
    """표준 MCP(Streamable HTTP) 서버에서 initialize 정보와 capabilities 에 있는 목록을 수집한다.

    resources/templates/list 실패는 빈 목록으로 대체한다(지원하지 않는 서버가 있음).

    Raises:
        CollectError: URL 검사·접속·인증·응답 해석 실패.
    """
    started = time.monotonic()
    logger.debug("표준 MCP 수집 시작: host=%s", urlsplit(endpoint).hostname)
    async with StreamableHttpClient(endpoint, credential, transport=transport) as client:
        init = await client.initialize()
        snap = McpSnapshot(
            server_info=init.get("serverInfo") or {},
            protocol_version=init.get("protocolVersion"),
            capabilities=init.get("capabilities") or {},
            instructions=init.get("instructions"),
        )
        caps = snap.capabilities
        if "tools" in caps:
            snap.tools = await client.list_all("tools/list", "tools")
        if "resources" in caps:
            snap.resources = await client.list_all("resources/list", "resources")
            try:
                snap.resource_templates = await client.list_all(
                    "resources/templates/list", "resourceTemplates"
                )
            except CollectError as exc:
                logger.warning("resources/templates/list 수집 실패 — 빈 목록으로 대체: %s", exc)
                snap.resource_templates = []
        if "prompts" in caps:
            snap.prompts = await client.list_all("prompts/list", "prompts")
    snap.latency_ms = int((time.monotonic() - started) * 1000)
    logger.debug("표준 MCP 수집 완료: host=%s tools=%d resources=%d prompts=%d latency_ms=%d",
                 urlsplit(endpoint).hostname, len(snap.tools), len(snap.resources), len(snap.prompts),
                 snap.latency_ms)
    return snap


async def collect_forge_rest(api_base: str, credential: Credential | None = None,
                             transport: httpx.AsyncBaseTransport | None = None) -> McpSnapshot:
    """Forge ``GET {api_base}/tools`` → 도구 목록 (mutating 플래그 포함)."""
    url = api_base.rstrip("/") + "/tools"
    await guard_url(url)
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport) as client:
        try:
            resp = await client.get(url, headers=credential.headers() if credential else {})
        except httpx.HTTPError as exc:
            logger.warning("Forge 도구 REST 접속 실패: host=%s error=%s", urlsplit(url).hostname, exc)
            raise CollectError(f"도구 REST 접속 실패: {exc}") from exc
    if resp.status_code in (401, 403):
        logger.warning("Forge 도구 REST 인증 실패: host=%s status=%d", urlsplit(url).hostname, resp.status_code)
        raise CollectError(f"도구 REST 인증 실패 (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        logger.warning("Forge 도구 REST HTTP 오류: host=%s status=%d", urlsplit(url).hostname, resp.status_code)
        raise CollectError(f"도구 REST HTTP {resp.status_code}")
    if len(resp.content) > MAX_BYTES:
        raise CollectError("응답이 너무 큽니다")
    try:
        body = resp.json()
    except ValueError as exc:
        raise CollectError("도구 REST 응답이 JSON 이 아닙니다") from exc
    tools = []
    for t in body.get("tools") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        mutating = bool(t.get("mutating"))
        tools.append(
            {
                "name": t["name"],
                "description": t.get("description"),
                "inputSchema": t.get("inputSchema"),
                "annotations": {"readOnlyHint": not mutating, "destructiveHint": mutating},
                "_mutating": mutating,
            }
        )
    logger.debug("Forge 도구 REST 수집 완료: host=%s tools=%d", urlsplit(url).hostname, len(tools))
    return McpSnapshot(tools=tools, source="forge_rest",
                       latency_ms=int((time.monotonic() - started) * 1000))


class ManifestNotAvailableError(CollectError):
    """서버가 manifest 엔드포인트를 제공하지 않음 (404/405) — 다른 수집 경로로 폴백."""


async def fetch_forge_manifest(api_base: str, credential: Credential | None = None,
                               transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict, int]:
    """Forge ``GET {api_base}/manifest`` (설계 §6.2). (manifest, latency_ms) 반환."""
    url = api_base.rstrip("/") + "/manifest"
    await guard_url(url)
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport) as client:
        try:
            resp = await client.get(url, headers=credential.headers() if credential else {})
        except httpx.HTTPError as exc:
            logger.warning("Forge manifest 접속 실패: host=%s error=%s", urlsplit(url).hostname, exc)
            raise CollectError(f"manifest 접속 실패: {exc}") from exc
    if resp.status_code in (404, 405):
        logger.debug("Forge manifest 엔드포인트 없음 — 다른 경로로 폴백: host=%s status=%d",
                     urlsplit(url).hostname, resp.status_code)
        raise ManifestNotAvailableError(f"manifest 엔드포인트 없음 (HTTP {resp.status_code})")
    if resp.status_code in (401, 403):
        logger.warning("Forge manifest 인증 실패: host=%s status=%d", urlsplit(url).hostname, resp.status_code)
        raise CollectError(f"manifest 인증 실패 (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        logger.warning("Forge manifest HTTP 오류: host=%s status=%d", urlsplit(url).hostname, resp.status_code)
        raise CollectError(f"manifest HTTP {resp.status_code}")
    if len(resp.content) > MAX_BYTES:
        raise CollectError("응답이 너무 큽니다")
    try:
        body = resp.json()
    except ValueError as exc:
        raise CollectError("manifest 응답이 JSON 이 아닙니다") from exc
    if not isinstance(body, dict):
        raise CollectError("manifest 형식이 올바르지 않습니다 (JSON 객체 아님)")
    return body, int((time.monotonic() - started) * 1000)


@dataclass
class HealthResult:
    """헬스 체크 결과."""

    status: str  # healthy / degraded / down
    latency_ms: int | None = None
    instance_id: str | None = None
    message: str | None = None


async def check_health(endpoint: str, framework: str, credential: Credential | None = None,
                       transport: httpx.AsyncBaseTransport | None = None) -> HealthResult:
    """배포 헬스 체크 — 예외를 내지 않고 결과로 돌려준다.

    Forge 는 ``{origin}/readyz`` 가 200 이면 healthy, 그 밖의 상태 코드는 degraded.
    그 밖의 서버는 MCP ``initialize`` 성공이면 healthy. 접속·검사 실패는 down.
    """
    started = time.monotonic()
    try:
        if framework == "argus-forge":
            url = origin_of(endpoint) + "/readyz"
            await guard_url(url)
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport) as client:
                resp = await client.get(url)
            ms = int((time.monotonic() - started) * 1000)
            instance_id = None
            try:
                body = resp.json()
                instance_id = body.get("instance_id") if isinstance(body, dict) else None
            except ValueError:
                pass
            if resp.status_code == 200:
                logger.debug("Forge 헬스 정상: host=%s latency_ms=%d", urlsplit(url).hostname, ms)
                return HealthResult("healthy", ms, instance_id)
            logger.warning("Forge 헬스 저하: host=%s status=%d", urlsplit(url).hostname, resp.status_code)
            return HealthResult("degraded", ms, instance_id, f"/readyz HTTP {resp.status_code}")
        async with StreamableHttpClient(endpoint, credential, transport=transport) as client:
            await client.initialize()
        return HealthResult("healthy", int((time.monotonic() - started) * 1000))
    except CollectError as exc:
        logger.warning("MCP 헬스 체크 실패(down): host=%s reason=%s", urlsplit(endpoint).hostname, exc)
        return HealthResult("down", int((time.monotonic() - started) * 1000), message=str(exc))
    except httpx.HTTPError as exc:
        logger.warning("MCP 헬스 체크 접속 실패(down): host=%s error=%s", urlsplit(endpoint).hostname, exc)
        return HealthResult("down", int((time.monotonic() - started) * 1000), message=str(exc))
