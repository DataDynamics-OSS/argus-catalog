# SPDX-License-Identifier: Apache-2.0
"""07-search 통합 테스트에서 찾은 임베딩 결함 회귀 테스트.

- SRCH-T15: 모델을 바꾸면 원문이 같아도 백필이 다시 임베딩한다.
- SRCH-T12: removed 데이터셋의 임베딩 행은 커버리지(embedded_datasets)에서 뺀다.

SRCH-T15 상세:

같은 384차원 모델끼리(all-MiniLM-L6-v2 → paraphrase-multilingual-MiniLM-L12-v2) 바꾸면
벡터 공간이 달라 옛 벡터를 새 질의 벡터와 비교할 수 없다. 예전에는 원문(source_text)만 비교해
백필이 전부 "건너뜀"이 되고 옛 모델 벡터가 그대로 남았다.

모델 전환 테스트는 세션을 가짜 객체로 대신한다(행 조회·갱신만 확인).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_search_embedding_it07.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.embedding import service as es
from app.embedding.base import EmbeddingProvider


class _Provider(EmbeddingProvider):
    def __init__(self, model: str, provider: str = "local") -> None:
        self._model = model
        self._provider = provider
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.5] * 384 for _ in texts]

    def dimension(self) -> int:
        return 384

    def model_name(self) -> str:
        return self._model

    def provider_name(self) -> str:
        return self._provider


class _Session:
    """execute() 가 미리 정한 행을 돌려주는 가짜 세션."""

    def __init__(self, row) -> None:
        self.row = row
        self.added: list = []
        self.commits = 0

    async def execute(self, _stmt):
        row = self.row
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: row))

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


def _row(text: str, model: str, provider: str = "local"):
    return SimpleNamespace(source_text=text, model_name=model, provider=provider,
                           embedding=[0.1] * 384, dimension=384)


def _patch(monkeypatch, provider, text: str = "sakila.film | 영화") -> None:
    async def _get_provider():
        return provider

    async def _src(_session, *_args):
        return text

    monkeypatch.setattr(es, "get_provider", _get_provider)
    monkeypatch.setattr(es, "build_source_text", _src)
    monkeypatch.setattr(es, "build_entity_source_text", _src)


def test_is_up_to_date_requires_same_text_and_model() -> None:
    p = _Provider("all-MiniLM-L6-v2")
    assert es._is_up_to_date(_row("a", "all-MiniLM-L6-v2"), "a", p)
    assert not es._is_up_to_date(None, "a", p)
    assert not es._is_up_to_date(_row("a", "all-MiniLM-L6-v2"), "b", p)
    assert not es._is_up_to_date(_row("a", "paraphrase-multilingual-MiniLM-L12-v2"), "a", p)
    assert not es._is_up_to_date(_row("a", "all-MiniLM-L6-v2", provider="ollama"), "a", p)


def test_dataset_same_model_same_text_is_skipped(monkeypatch) -> None:
    p = _Provider("all-MiniLM-L6-v2")
    _patch(monkeypatch, p)
    session = _Session(_row("sakila.film | 영화", "all-MiniLM-L6-v2"))
    assert asyncio.run(es.embed_dataset(session, 1)) is False
    assert p.calls == 0 and session.commits == 0


def test_dataset_reembedded_after_model_switch(monkeypatch) -> None:
    p = _Provider("paraphrase-multilingual-MiniLM-L12-v2")
    _patch(monkeypatch, p)
    row = _row("sakila.film | 영화", "all-MiniLM-L6-v2")
    session = _Session(row)
    assert asyncio.run(es.embed_dataset(session, 1)) is True
    assert p.calls == 1 and session.commits == 1
    assert row.model_name == "paraphrase-multilingual-MiniLM-L12-v2"
    assert row.embedding == [0.5] * 384
    assert session.added == []  # 기존 행 갱신(새 행 아님)


def test_entity_reembedded_after_model_switch(monkeypatch) -> None:
    p = _Provider("paraphrase-multilingual-MiniLM-L12-v2")
    _patch(monkeypatch, p, text="대여 | 고객이 DVD 를 빌려가는 트랜잭션")
    row = _row("대여 | 고객이 DVD 를 빌려가는 트랜잭션", "all-MiniLM-L6-v2")
    session = _Session(row)
    assert asyncio.run(es.embed_entity(session, "glossary_term", 7)) is True
    assert row.model_name == "paraphrase-multilingual-MiniLM-L12-v2"
    # 같은 모델로 다시 돌리면 건너뜀
    assert asyncio.run(es.embed_entity(session, "glossary_term", 7)) is False
    assert p.calls == 1


# ---------------------------------------------------------------------------
# SRCH-T12 — removed 데이터셋 임베딩은 커버리지에서 뺀다 (100% 초과 방지)
# ---------------------------------------------------------------------------

async def _run_stats_excludes_removed(monkeypatch) -> None:
    import app.catalog.models as cm
    from app.catalog import service
    from app.catalog.schemas import DatasourceCreate
    from app.embedding.models import DatasetEmbedding, EntityEmbedding
    from app.settings.models import CatalogConfiguration
    from tests.test_datasource_stage2 import _make_session

    async def _no_provider():
        return None

    monkeypatch.setattr(es, "get_provider", _no_provider)

    make_db = await _make_session()
    async with make_db() as db:
        conn = await db.connection()
        for t in (CatalogConfiguration, DatasetEmbedding, EntityEmbedding):
            await conn.run_sync(lambda sc, t=t: t.__table__.create(sc, checkfirst=True))
        await db.commit()

        src = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        ids = []
        for name, status in (("db.a", "active"), ("db.b", "active"), ("db.gone", "removed")):
            d = cm.Dataset(urn=f"{src.datasource_id}.{name}", name=name, datasource_id=src.id,
                           origin="DEV", qualified_name=f"{src.datasource_id}.{name}", status=status)
            db.add(d)
            await db.flush()
            ids.append(d.id)
        # a 와 removed 인 gone 만 임베딩이 있다
        for ds_id in (ids[0], ids[2]):
            db.add(DatasetEmbedding(dataset_id=ds_id, embedding=[0.1] * 384, source_text="x",
                                    model_name="m", provider="local", dimension=384))
        await db.commit()

        stats = await es.get_embedding_stats(db)
        assert stats["total_datasets"] == 2
        assert stats["embedded_datasets"] == 1
        assert stats["coverage_pct"] == 50.0


def test_stats_excludes_removed_dataset_embeddings(monkeypatch) -> None:
    asyncio.run(_run_stats_excludes_removed(monkeypatch))
