"""Temporal 클라이언트 헬퍼.

프로세스당 한 번만 연결을 맺어 재사용한다. FastAPI lifespan 에서
``connect()`` 를 호출하여 초기화하고, ``get_client()`` 로 핸들을 얻는다.

연결 대상/네임스페이스/태스크 큐는 ``app.core.config.settings`` 에서 읽으며,
환경변수(TEMPORAL_TARGET / TEMPORAL_NAMESPACE / TEMPORAL_TASK_QUEUE)로
덮어쓸 수 있다. 워커 프로세스(worker.py)와 API 서버가 동일한 값을 공유한다.
"""

from __future__ import annotations

import logging
import os

from temporalio.client import Client

from app.core.config import settings

logger = logging.getLogger(__name__)

# 워커와 API 서버가 같은 큐를 바라보도록 settings 를 기본값으로 사용 (env 로 덮어쓰기 가능).
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", settings.temporal_task_queue)

_client: Client | None = None


def _target() -> str:
    return os.getenv("TEMPORAL_TARGET", settings.temporal_target)


def _namespace() -> str:
    return os.getenv("TEMPORAL_NAMESPACE", settings.temporal_namespace)


async def connect() -> Client:
    """Temporal 서버 연결을 초기화하고 클라이언트를 반환."""
    global _client
    if _client is not None:
        return _client

    target = _target()
    namespace = _namespace()
    logger.info("Temporal 연결 중: target=%s namespace=%s", target, namespace)
    _client = await Client.connect(target, namespace=namespace)
    return _client


def get_client() -> Client:
    if _client is None:
        raise RuntimeError("Temporal client not initialized. Call connect() first.")
    return _client


def is_connected() -> bool:
    """Temporal 클라이언트가 초기화되어 사용 가능한지 여부."""
    return _client is not None
