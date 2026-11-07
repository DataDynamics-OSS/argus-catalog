# SPDX-License-Identifier: Apache-2.0
"""Circuit breaker — LIVE peer 호출 내결함성.

peer 단위(instance_key)로 연속 실패를 집계해, threshold 도달 시 cooldown 동안 회로를
연다(open). 회로가 열린 동안의 호출은 네트워크를 타지 않고 즉시 실패시켜, 죽은 peer 가
연합 검색·드릴다운 전체를 느리게 만드는 것을 막는다.

인메모리 상태(프로세스 로컬). 다중 워커에서는 워커별로 독립 동작하지만, cooldown 이
짧아 실용상 충분하다. 영속 상태가 필요하면 후속 단계에서 외부 저장소로 확장한다.
"""

import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)


class _BreakerState:
    """peer 1곳의 breaker 상태 — 연속 실패 수, 회로 재개(open 해제) 시각, half-open 프로브 시작 시각."""

    __slots__ = ("failures", "open_until", "probe_started")

    def __init__(self) -> None:
        self.failures: int = 0
        self.open_until: float = 0.0
        # half-open 프로브 진행 시작 시각(0=진행 중인 프로브 없음)
        self.probe_started: float = 0.0


_states: dict[str, _BreakerState] = {}


def _state(key: str) -> _BreakerState:
    """key 의 상태를 반환하고, 없으면 새로 만들어 등록한다."""
    st = _states.get(key)
    if st is None:
        st = _BreakerState()
        _states[key] = st
    return st


def is_open(key: str, now: float | None = None) -> bool:
    """회로가 열려 있으면(호출 차단해야 하면) True."""
    st = _states.get(key)
    if st is None:
        return False
    now = time.monotonic() if now is None else now
    if not st.open_until:
        return False
    if now < st.open_until:
        return True
    # cooldown 경과 → half-open: 프로브 1건만 통과시키고 결과가 나올 때까지 나머지는 차단.
    # 프로브가 취소·유실돼 결과가 기록되지 않아도 cooldown 이 지나면 새 프로브를 허용한다.
    cooldown = settings.federation_breaker_cooldown_seconds
    if st.probe_started and now - st.probe_started < cooldown:
        return True
    st.probe_started = now
    logger.debug("circuit breaker HALF-OPEN: 프로브 1건 허용 key=%s", key)
    return False


def record_success(key: str) -> None:
    """성공 시 실패 카운트와 open 상태를 초기화한다."""
    st = _states.get(key)
    if st is not None:
        # 직전까지 실패가 쌓였거나 회로가 열려 있었다면 '복구' 전이로 한 줄 남긴다.
        if st.failures or st.open_until:
            logger.info("circuit breaker CLOSED [%s]: peer 호출 복구됨", key)
        st.failures = 0
        st.open_until = 0.0
        st.probe_started = 0.0


def record_failure(key: str, now: float | None = None) -> None:
    """실패를 집계하고 threshold 도달 시 회로를 연다."""
    st = _state(key)
    now = time.monotonic() if now is None else now
    st.failures += 1
    st.probe_started = 0.0
    logger.debug("peer 호출 실패 집계: key=%s failures=%d", key, st.failures)
    if st.failures >= settings.federation_breaker_threshold:
        st.open_until = now + settings.federation_breaker_cooldown_seconds
        logger.warning(
            "circuit breaker OPEN [%s]: %d 연속 실패, %ds 동안 차단",
            key, st.failures, settings.federation_breaker_cooldown_seconds,
        )


def reset(key: str | None = None) -> None:
    """상태 초기화(테스트/운영용). key 가 None 이면 전체."""
    if key is None:
        _states.clear()
    else:
        _states.pop(key, None)


def snapshot(now: float | None = None) -> dict[str, dict]:
    """현재 breaker 상태를 instance_key 별로 반환(관측성용).

    {key: {"failures": int, "open": bool, "reopen_in_sec": int}}
    """
    now = time.monotonic() if now is None else now
    out: dict[str, dict] = {}
    for key, st in _states.items():
        is_open_now = bool(st.open_until and now < st.open_until)
        out[key] = {
            "failures": st.failures,
            "open": is_open_now,
            "reopen_in_sec": max(0, int(st.open_until - now)) if is_open_now else 0,
        }
    return out


def is_peer_fault(exc: BaseException) -> bool:
    """breaker 실패로 셀 예외인지 — peer 장애(연결·타임아웃·5xx·429)만 센다.

    404(샘플 없음 등)·401/403 같은 4xx 는 peer 가 정상 응답한 것이므로 회로를 열지 않는다.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return True


class CircuitOpenError(Exception):
    """회로가 열려 있어 호출을 차단했음을 나타낸다."""

    def __init__(self, key: str) -> None:
        super().__init__(f"circuit open for peer '{key}'")
        self.key = key
