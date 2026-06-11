"""외부 API 용 TTL 만료 기능을 갖춘 스레드 안전 LRU 캐시.

특징:
- 최대 크기 설정 가능(가득 차면 LRU 방식으로 제거)
- 엔트리별 TTL 만료(monotonic 클럭 사용)
- asyncio.Lock 으로 비동기 안전
- 다목적 캐싱을 위한 문자열 키(예: "metadata:42", "avro:42")
- 모니터링용 hit/miss 통계
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """생성 시각과 적중 카운터를 가진 단일 캐시 항목."""

    data: dict
    created_at: float  # time.monotonic()
    hit_count: int = 0


class MetadataCache:
    """TTL 을 지원하는 비동기 안전 LRU 캐시.

    여러 데이터 타입을 지원하기 위해 키는 문자열을 사용한다:
    - "metadata:{dataset_id}": dataset 메타데이터
    - "avro:{dataset_id}": Avro 스키마
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def get(self, key: str) -> dict | None:
        """캐시된 데이터를 반환. miss 또는 만료 시 None 반환."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            elapsed = time.monotonic() - entry.created_at
            if elapsed > self._ttl_seconds:
                del self._cache[key]
                self._misses += 1
                logger.debug("캐시 만료: key=%s (%.1fs)", key, elapsed)
                return None

            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._hits += 1
            return entry.data

    async def put(self, key: str, data: dict) -> None:
        """데이터를 캐시에 저장. 용량이 가득 차면 LRU 항목을 제거한다."""
        async with self._lock:
            if key in self._cache:
                self._cache[key] = CacheEntry(data=data, created_at=time.monotonic())
                self._cache.move_to_end(key)
                return

            while len(self._cache) >= self._max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("캐시 LRU 제거: key=%s", evicted_key)

            self._cache[key] = CacheEntry(data=data, created_at=time.monotonic())

    async def invalidate(self, key: str) -> bool:
        """특정 항목을 제거. 찾아서 제거했으면 True 반환."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug("캐시 무효화: key=%s", key)
                return True
            return False

    async def invalidate_prefix(self, prefix: str) -> int:
        """키가 prefix 로 시작하는 모든 항목을 제거. 제거 개수를 반환."""
        async with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            if keys_to_remove:
                logger.debug("캐시 무효화 %d 개 키 prefix=%s", len(keys_to_remove), prefix)
            return len(keys_to_remove)

    async def clear(self) -> int:
        """모든 항목을 비운다. 제거된 항목 개수를 반환."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            logger.info("캐시 비움: %d 개 항목 제거", count)
            return count

    async def stats(self) -> dict:
        """캐시 통계를 반환."""
        async with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0,
                "total_requests": total,
            }

    async def reconfigure(
        self,
        max_size: int | None = None,
        ttl_seconds: int | None = None,
    ) -> dict:
        """캐시 설정을 갱신. 새 max_size 가 더 작으면 초과 항목을 제거한다."""
        async with self._lock:
            if max_size is not None:
                self._max_size = max_size
                while len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)

            if ttl_seconds is not None:
                self._ttl_seconds = ttl_seconds

            return {
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
                "current_size": len(self._cache),
            }


# ---------------------------------------------------------------------------
# 싱글턴 인스턴스
# ---------------------------------------------------------------------------

_cache: MetadataCache | None = None


def get_cache() -> MetadataCache:
    """전역 캐시 인스턴스를 반환."""
    global _cache
    if _cache is None:
        from app.core.config import settings

        _cache = MetadataCache(
            max_size=settings.cache_max_size,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        logger.info(
            "MetadataCache 초기화: max_size=%d, ttl=%ds",
            settings.cache_max_size,
            settings.cache_ttl_seconds,
        )
    return _cache


def reset_cache() -> None:
    """싱글턴을 초기화(재설정용)."""
    global _cache
    _cache = None
