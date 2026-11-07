# SPDX-License-Identifier: Apache-2.0
"""기본 임베딩 모델(다국어) 회귀 테스트 (in-memory SQLite).

- 새 설치 시드·설정 API 기본값·제공자 기본값이 다국어 모델이다.
- 임베딩을 한 번도 켜지 않은 기존 설치는 이전 기본값(all-MiniLM-L6-v2)에서 새 기본값으로 옮긴다.
- 임베딩을 켜서 쓰는 설치나 사용자가 고른 다른 모델은 건드리지 않는다.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.embedding.base import DEFAULT_EMBEDDING_MODEL
from app.embedding.registry import KNOWN_MODEL_DIMENSIONS
from app.settings import service
from app.settings.models import CatalogConfiguration
from app.settings.router import EmbeddingConfig

LEGACY = "all-MiniLM-L6-v2"


async def _session_with(enabled: str | None, model: str | None):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: CatalogConfiguration.__table__.create(sc))
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        if enabled is not None:
            s.add(CatalogConfiguration(category="embedding", config_key="embedding_enabled", config_value=enabled))
        if model is not None:
            s.add(CatalogConfiguration(category="embedding", config_key="embedding_model", config_value=model))
        await s.commit()
    return engine, make


async def _model(make) -> str | None:
    async with make() as s:
        row = (await s.execute(select(CatalogConfiguration).where(
            CatalogConfiguration.config_key == "embedding_model"))).scalars().first()
        return row.config_value if row else None


async def _run() -> None:
    assert DEFAULT_EMBEDDING_MODEL == "paraphrase-multilingual-MiniLM-L12-v2"
    assert KNOWN_MODEL_DIMENSIONS[("local", DEFAULT_EMBEDDING_MODEL)] == 384  # pgvector(384) 호환
    assert EmbeddingConfig().model == DEFAULT_EMBEDDING_MODEL
    assert dict((k, v) for k, v, _ in service._EMBEDDING_DEFAULTS)["embedding_model"] == DEFAULT_EMBEDDING_MODEL

    # 미사용 설치 + 이전 기본값 → 새 기본값 (두 번 실행해도 동일)
    engine, make = await _session_with("false", LEGACY)
    async with make() as s:
        assert await service.migrate_default_embedding_model(s) is True
    async with make() as s:
        assert await service.migrate_default_embedding_model(s) is False
    assert await _model(make) == DEFAULT_EMBEDDING_MODEL
    await engine.dispose()

    # 사용 중인 설치는 유지
    engine, make = await _session_with("true", LEGACY)
    async with make() as s:
        assert await service.migrate_default_embedding_model(s) is False
    assert await _model(make) == LEGACY
    await engine.dispose()

    # 사용자가 고른 다른 모델은 유지
    engine, make = await _session_with("false", "bge-small-en-v1.5")
    async with make() as s:
        assert await service.migrate_default_embedding_model(s) is False
    assert await _model(make) == "bge-small-en-v1.5"
    await engine.dispose()

    # 행이 없으면 아무 것도 하지 않음
    engine, make = await _session_with(None, None)
    async with make() as s:
        assert await service.migrate_default_embedding_model(s) is False
    await engine.dispose()


def test_embedding_default_model() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_embedding_default_model()
    print("test_embedding_default_model OK")
