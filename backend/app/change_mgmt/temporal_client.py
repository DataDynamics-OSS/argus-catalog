# SPDX-License-Identifier: Apache-2.0
"""Temporal 클라이언트 헬퍼.

프로세스당 한 번만 연결을 맺어 재사용한다. FastAPI lifespan 에서
``connect()`` 를 호출하여 초기화하고, ``get_client()`` 로 핸들을 얻는다.

연결 대상/네임스페이스/태스크 큐는 ``app.core.config.settings`` 에서 읽으며,
환경변수(TEMPORAL_TARGET / TEMPORAL_NAMESPACE / TEMPORAL_TASK_QUEUE)로
덮어쓸 수 있다. 워커 프로세스(worker.py)와 API 서버가 동일한 값을 공유한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from temporalio.client import Client

from app.core.config import settings

logger = logging.getLogger(__name__)

# 워커와 API 서버가 같은 큐를 바라보도록 settings 를 기본값으로 사용 (env 로 덮어쓰기 가능).
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", settings.temporal_task_queue)

_client: Client | None = None

# 지연 재연결 — 백엔드가 Temporal 없이 기동된 뒤 Temporal 이 올라오면 다음 요청에서 연결한다.
# 요청마다 연결을 시도해 응답이 늘어지지 않도록 간격·시간 제한을 둔다.
RECONNECT_INTERVAL_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 5.0
_last_attempt: float | None = None
_connect_lock = asyncio.Lock()


def _target() -> str:
    """Temporal 서버 주소 — TEMPORAL_TARGET 환경변수 우선, 없으면 settings."""
    return os.getenv("TEMPORAL_TARGET", settings.temporal_target)


def _namespace() -> str:
    """Temporal 네임스페이스 — TEMPORAL_NAMESPACE 환경변수 우선, 없으면 settings."""
    return os.getenv("TEMPORAL_NAMESPACE", settings.temporal_namespace)


async def connect() -> Client:
    """Temporal 서버 연결을 초기화하고 클라이언트를 반환(이미 연결돼 있으면 재사용).

    연결 실패 시 예외가 그대로 전파된다.
    """
    global _client
    if _client is not None:
        return _client

    target = _target()
    namespace = _namespace()
    logger.info("Temporal 연결 중: target=%s namespace=%s", target, namespace)
    _client = await Client.connect(target, namespace=namespace)
    logger.info("Temporal 연결 완료: target=%s namespace=%s", target, namespace)
    return _client


async def ensure_connected() -> bool:
    """연결돼 있으면 True. 아니면 (temporal.enabled 이고 마지막 시도 후 간격이 지났을 때) 다시 연결해 본다.

    예전에는 기동 시 한 번만 연결해, Temporal 없이 기동되면 백엔드를 재시작할 때까지 503 이었다.
    """
    global _last_attempt
    if is_connected():
        return True
    if not settings.temporal_enabled:
        return False
    async with _connect_lock:
        if is_connected():
            return True
        now = time.monotonic()
        if _last_attempt is not None and now - _last_attempt < RECONNECT_INTERVAL_SECONDS:
            logger.debug("Temporal 재연결 대기 중(간격 제한): %.1fs 경과", now - _last_attempt)
            return False
        _last_attempt = now
        try:
            await asyncio.wait_for(connect(), timeout=CONNECT_TIMEOUT_SECONDS)
            logger.info("Temporal 재연결 성공")
        except Exception as e:  # noqa: BLE001 — 미가용이면 다음 간격에 다시 시도
            logger.warning("Temporal 재연결 실패: %s", e)
            return False
    return is_connected()


def get_client() -> Client:
    """초기화된 클라이언트를 반환한다. 미연결이면 RuntimeError."""
    if _client is None:
        raise RuntimeError("Temporal client not initialized. Call connect() first.")
    return _client


def is_connected() -> bool:
    """Temporal 클라이언트가 초기화되어 사용 가능한지 여부."""
    return _client is not None
