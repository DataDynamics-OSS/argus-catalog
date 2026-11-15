# SPDX-License-Identifier: Apache-2.0
"""Trino REST(``/v1/statement``) 클라이언트 — 품질 워커가 진단 쿼리를 실행할 때 쓴다.

드라이버를 따로 두지 않고 Trino 의 문장 프로토콜을 직접 따른다: ``POST /v1/statement`` 로 쿼리를 내고
응답의 ``nextUri`` 를 끝날 때까지 따라가며 ``data`` 를 모은다. 취소는 ``nextUri`` 에 ``DELETE``.

- ``source``·``client_tags`` 는 Trino 리소스 그룹 선택자가 보는 값이다. 진단 쿼리를 전용 그룹에 묶어
  동시 실행 수·메모리를 제한하려면 Trino 쪽에서 이 값으로 선택자를 만든다.
- ``session`` 은 세션 속성(``query_max_execution_time`` 등).
- 폴링할 때마다 ``on_poll`` 을 부른다 — 워커가 heartbeat 를 남기고 취소 요청을 확인하는 자리다.
  ``on_poll`` 이 ``True`` 를 돌려주면 쿼리를 취소하고 ``TrinoQueryCancelled`` 를 던진다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8443
_TRUE = ("true", "1", "yes", "on")
# 응답이 끝나지 않는 서버를 무한히 따라가지 않도록 — 폴링 간격 최대 1초 기준 넉넉한 상한
_MAX_POLLS = 200_000


class TrinoQueryError(RuntimeError):
    """Trino 가 쿼리 오류를 돌려줬다. ``query_id`` 는 Trino UI 에서 찾을 때 쓴다."""

    def __init__(self, message: str, query_id: str | None = None, error_name: str | None = None):
        super().__init__(message)
        self.query_id = query_id
        self.error_name = error_name


class TrinoQueryCancelled(RuntimeError):
    """``on_poll`` 의 요청으로 쿼리를 취소했다."""

    def __init__(self, query_id: str | None = None):
        super().__init__("쿼리를 중지했습니다.")
        self.query_id = query_id


@dataclass
class TrinoResult:
    """쿼리 결과 — 컬럼 이름과 행(리스트의 리스트)."""

    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    query_id: str | None = None


@dataclass(frozen=True)
class TrinoEndpoint:
    """접속 정보 — 데이터 소스 설정(``host``·``port``·``use_ssl``·``username``·``password``)에서 만든다."""

    base_url: str
    user: str
    password: str | None = None

    @classmethod
    def from_config(cls, config: dict) -> "TrinoEndpoint":
        """데이터 소스 접속 설정 → 엔드포인트. 호스트·포트 형식 오류는 ``ValueError``."""
        from app.catalog.service import validate_connection_target

        host, port = validate_connection_target(config, DEFAULT_PORT)
        use_ssl = str(config.get("use_ssl", True)).strip().lower() in _TRUE
        return cls(
            base_url=f"{'https' if use_ssl else 'http'}://{host}:{port}",
            user=str(config.get("username") or "argus"),
            password=config.get("password") or None,
        )


PollHook = Callable[[str | None], Awaitable[bool | None]]


async def execute(
    endpoint: TrinoEndpoint,
    sql: str,
    *,
    source: str | None = None,
    client_tags: tuple[str, ...] = (),
    session: dict[str, str] | None = None,
    on_poll: PollHook | None = None,
    request_timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> TrinoResult:
    """쿼리 하나를 끝까지 실행해 결과를 돌려준다.

    오류는 ``TrinoQueryError``, ``on_poll`` 이 취소를 요청하면 ``TrinoQueryCancelled``.
    호출한 태스크가 취소되면(``asyncio.CancelledError``) Trino 쿼리도 취소를 시도한 뒤 다시 던진다.
    """
    headers = {"X-Trino-User": endpoint.user}
    if source:
        headers["X-Trino-Source"] = source
    if client_tags:
        headers["X-Trino-Client-Tags"] = ",".join(client_tags)
    if session:
        headers["X-Trino-Session"] = ",".join(f"{k}={v}" for k, v in session.items())
    auth = (endpoint.user, endpoint.password) if endpoint.password else None

    own_client = client is None
    if own_client:
        # 내부망 Trino 는 사설 인증서가 흔하다 — 접속 테스트(connection_ssl)와 같은 수준(암호화, 미검증)
        client = httpx.AsyncClient(timeout=request_timeout, verify=False, auth=auth)
    result = TrinoResult()
    next_uri: str | None = None
    try:
        resp = await client.post(f"{endpoint.base_url}/v1/statement", content=sql.encode("utf-8"), headers=headers)
        delay = 0.05
        for _ in range(_MAX_POLLS):
            resp.raise_for_status()
            body = resp.json()
            result.query_id = body.get("id") or result.query_id
            error = body.get("error")
            if error:
                raise TrinoQueryError(
                    error.get("message") or "Trino 쿼리가 실패했습니다.",
                    query_id=result.query_id, error_name=error.get("errorName"),
                )
            if body.get("columns") and not result.columns:
                result.columns = [c.get("name") for c in body["columns"]]
            result.rows.extend(body.get("data") or [])
            next_uri = body.get("nextUri")
            if not next_uri:
                return result
            if on_poll is not None and await on_poll(result.query_id):
                await _cancel(client, next_uri, headers)
                raise TrinoQueryCancelled(result.query_id)
            # 데이터가 오는 동안은 바로, 대기 중이면 점점 늦춰(최대 1초) 폴링한다
            if not body.get("data"):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 1.0)
            resp = await client.get(next_uri, headers=headers)
        raise TrinoQueryError("Trino 응답이 끝나지 않았습니다.", query_id=result.query_id)
    except asyncio.CancelledError:
        if next_uri:
            await _cancel(client, next_uri, headers)
        raise
    finally:
        if own_client:
            await client.aclose()


async def _cancel(client: httpx.AsyncClient, next_uri: str, headers: dict[str, str]) -> None:
    """실행 중인 쿼리를 취소한다. 실패해도 조용히 넘어간다(이미 끝났을 수 있다)."""
    try:
        await client.delete(next_uri, headers=headers)
    except Exception as e:  # noqa: BLE001 — 취소는 최선 노력
        logger.warning("Trino 쿼리 취소 요청 실패: %r", e)
