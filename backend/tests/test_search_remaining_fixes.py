# SPDX-License-Identifier: Apache-2.0
"""전역 검색·임베딩 잔여 결함 회귀 테스트 (자기완결형, in-memory SQLite, 임베딩은 stub).

- SRCH-T09: 임베딩 비활성화 저장 시 이미 로드된 provider 를 해제 → semantic 503, hybrid 키워드 폴백.
- SRCH-T03: 임베딩 연결 테스트는 임시 인스턴스만 쓰고 전역 provider 를 교체하지 않는다.
- (관찰) 데이터셋 키워드 검색이 display_name·summary 도 매칭.
- (관찰) pgvector 컬럼(384) 과 다른 차원은 저장 시 422, 테스트 시 success:false.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_search_remaining_fixes
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException

import app.catalog.models as cm
from app.embedding import registry
from app.embedding.base import EmbeddingProvider
from app.embedding.models import VECTOR_DIMENSION
from app.settings.models import CatalogConfiguration
from tests.test_datasource_stage2 import _make_session


class _StubProvider(EmbeddingProvider):
    """실제 모델/외부 API 를 부르지 않는 stub."""

    def __init__(self, dim: int = VECTOR_DIMENSION, name: str = "stub") -> None:
        self._dim = dim
        self._name = name
        self.closed = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dim for _ in texts]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._name

    def provider_name(self) -> str:
        return "stub"

    async def close(self) -> None:
        self.closed = True


async def _make_db():
    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sc: CatalogConfiguration.__table__.create(sc, checkfirst=True))
        await db.commit()
    return make_db


async def _seed_dataset(db, **kw) -> int:
    from app.catalog import service
    from app.catalog.schemas import DatasourceCreate
    ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
    d = cm.Dataset(urn=f"{ds.datasource_id}.db.film.dataset", name="db.film", datasource_id=ds.id,
                   origin="DEV", qualified_name=f"{ds.datasource_id}.db.film", status="active", **kw)
    db.add(d)
    await db.commit()
    return d.id


# ---------------------------------------------------------------------------
# SRCH-T09
# ---------------------------------------------------------------------------

async def _run_disable_releases_provider() -> None:
    from app.search import router as search_router
    from app.search.service import hybrid_search
    from app.settings.service import load_embedding_settings, update_config

    make_db = await _make_db()
    stub = _StubProvider()
    registry._current_provider = stub
    try:
        async with make_db() as db:
            ds_id = await _seed_dataset(db, display_name="영화")
            await update_config(db, "embedding", {"embedding_enabled": "false"})
            await load_embedding_settings(db)

            assert await registry.get_provider() is None
            assert stub.closed

            # semantic → 503
            try:
                await search_router.semantic_search(q="film", limit=5, threshold=0.3, session=db)
            except HTTPException as e:
                assert e.status_code == 503, e
            else:
                raise AssertionError("provider 해제 후 semantic 은 503 이어야 한다")

            # hybrid → 키워드 폴백
            res = await hybrid_search(db, "film", limit=5)
            assert res == [(ds_id, 1.0, "keyword")], res
    finally:
        registry._current_provider = None


def test_disable_embedding_releases_provider() -> None:
    asyncio.run(_run_disable_releases_provider())


# ---------------------------------------------------------------------------
# SRCH-T03 — 연결 테스트가 전역 provider 를 건드리지 않음
# ---------------------------------------------------------------------------

async def _run_test_does_not_touch_global() -> None:
    from app.settings.router import EmbeddingConfig, test_embedding

    created: list[_StubProvider] = []
    orig = registry.create_provider

    def _fake_create(config, dim=VECTOR_DIMENSION):
        p = _StubProvider(dim=dim, name=config.get("embedding_model", ""))
        created.append(p)
        return p

    try:
        # 1) 전역 provider 없음(비활성) 상태에서 테스트 → 여전히 None
        registry._current_provider = None
        registry.create_provider = _fake_create
        r = await test_embedding(None, EmbeddingConfig(provider="local", model="m1"))
        assert r.success, r
        assert await registry.get_provider() is None
        assert created[-1].closed  # 임시 인스턴스는 닫힌다

        # 2) 활성 provider 가 있을 때 다른 모델 테스트 → 전역은 그대로
        active = _StubProvider(name="active")
        registry._current_provider = active
        r = await test_embedding(None, EmbeddingConfig(provider="local", model="m2"))
        assert r.success, r
        assert await registry.get_provider() is active and not active.closed

        # 3) 실패하는 테스트도 전역에 영향 없음
        def _boom(config):
            raise ValueError("Unknown embedding provider: nope")
        registry.create_provider = _boom
        r = await test_embedding(None, EmbeddingConfig(provider="nope", model="x"))
        assert not r.success and "nope" in r.message, r
        assert await registry.get_provider() is active

        # 4) 384 가 아닌 벡터를 돌려주면 success:false (저장 불가 안내)
        registry.create_provider = lambda config: _fake_create(config, dim=1536)
        r = await test_embedding(None, EmbeddingConfig(provider="openai", model="text-embedding-3-small",
                                                       api_key="sk-x"))
        assert not r.success and "1536" in r.message and str(VECTOR_DIMENSION) in r.message, r
        assert created[-1].closed
        assert await registry.get_provider() is active
    finally:
        registry.create_provider = orig
        registry._current_provider = None


def test_embedding_test_does_not_replace_global_provider() -> None:
    asyncio.run(_run_test_does_not_touch_global())


# ---------------------------------------------------------------------------
# (관찰) 차원 검증 — 저장 시 422
# ---------------------------------------------------------------------------

async def _run_save_rejects_dimension() -> None:
    from app.settings.router import EmbeddingConfig, update_embedding_config

    bad = [
        EmbeddingConfig(provider="openai", model="text-embedding-3-small", dimension=1536),
        # 알려진 모델은 요청 dimension 과 무관하게 모델 차원으로 판단
        EmbeddingConfig(provider="openai", model="text-embedding-3-large", dimension=384),
        EmbeddingConfig(provider="ollama", model="nomic-embed-text", dimension=768),
        # 알려지지 않은 모델은 요청 dimension 기준
        EmbeddingConfig(provider="ollama", model="custom-model", dimension=1024),
    ]
    for body in bad:
        try:
            # 검증은 DB 접근 전에 일어나므로 session 없이 호출
            await update_embedding_config(None, body, session=None)
        except HTTPException as e:
            assert e.status_code == 422, (body, e)
            assert str(VECTOR_DIMENSION) in e.detail, e.detail
        else:
            raise AssertionError(f"422 이어야 한다: {body}")

    # 384 차원 모델은 통과(저장까지 수행)
    make_db = await _make_db()
    try:
        async with make_db() as db:
            for body in (
                EmbeddingConfig(provider="local", model="paraphrase-multilingual-MiniLM-L12-v2"),
                EmbeddingConfig(provider="ollama", model="all-minilm", dimension=384),
                EmbeddingConfig(provider="ollama", model="custom-384", dimension=384),
            ):
                r = await update_embedding_config(None, body, session=db)
                assert r["status"] == "ok", r
    finally:
        registry._current_provider = None


def test_save_rejects_unsupported_dimension() -> None:
    asyncio.run(_run_save_rejects_dimension())


def test_known_model_dimensions_match_ui() -> None:
    """설정 화면 선택지의 차원과 백엔드 표가 어긋나지 않는지."""
    import pathlib
    import re
    ui = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/apps/web/features/settings/embedding-settings.tsx")
    if not ui.exists():
        return
    found = re.findall(r'\{ id: "([^"]+)", label: "[^"]*", dim: (\d+) \}', ui.read_text(encoding="utf-8"))
    assert found
    known = {m: d for (_p, m), d in registry.KNOWN_MODEL_DIMENSIONS.items()}
    for model, dim in found:
        assert known.get(model) == int(dim), (model, dim)
    assert f"SUPPORTED_DIMENSION = {VECTOR_DIMENSION}" in ui.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (관찰) 데이터셋 키워드 검색 — display_name·summary 매칭
# ---------------------------------------------------------------------------

async def _run_keyword_display_name_summary() -> None:
    from app.search.service import keyword_search_ids

    make_db = await _make_db()
    async with make_db() as db:
        ds_id = await _seed_dataset(db, display_name="영화", summary="대여 가능한 작품 목록")
        assert await keyword_search_ids(db, "영화") == {ds_id}
        assert await keyword_search_ids(db, "작품") == {ds_id}
        assert await keyword_search_ids(db, "film") == {ds_id}
        assert await keyword_search_ids(db, "없는말") == set()
        # LIKE 와일드카드는 글자로 매칭(이스케이프 유지)
        assert await keyword_search_ids(db, "%") == set()


def test_keyword_search_matches_display_name_and_summary() -> None:
    asyncio.run(_run_keyword_display_name_summary())


if __name__ == "__main__":
    asyncio.run(_run_disable_releases_provider())
    asyncio.run(_run_test_does_not_touch_global())
    asyncio.run(_run_save_rejects_dimension())
    test_known_model_dimensions_match_ui()
    asyncio.run(_run_keyword_display_name_summary())
    print("test_search_remaining_fixes OK")
