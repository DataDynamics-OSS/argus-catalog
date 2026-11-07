# SPDX-License-Identifier: Apache-2.0
"""AI 제안 검토(승인·거절 화면) 보강 테스트 (자기완결형, SQLite 메모리 DB).

확인하는 규칙:
- 제안 목록은 비교용 현재 값(current_text)을 함께 준다 — 설명·요약·컬럼 설명·태그·PII.
- 요약(summary) 제안을 승인하면 데이터셋 요약이 실제로 바뀐다(이전엔 표시만 적용됨으로 바뀌었다).
- AI 서비스(enrich)가 남기는 태그 이름 배열 형식도 승인하면 태그가 붙는다(없으면 만든다).

실행:
    cd backend
    pytest tests/test_ai_suggestions_review.py
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ai import service
from app.ai.models import AIGenerationLog
from app.catalog.models import Dataset, DatasetSchema, DatasetTag, Tag
from app.core.database import Base


@pytest.fixture
async def session(monkeypatch):
    # 임베딩 갱신은 이 테스트의 관심사가 아니다
    from app.embedding import service as embedding_service

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(embedding_service, "embed_dataset_background", _noop)

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            AIGenerationLog.__table__, Dataset.__table__, DatasetSchema.__table__,
            Tag.__table__, DatasetTag.__table__,
        ])
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as s:
        s.add(Dataset(id=1, urn="ds.db.orders.dataset", name="orders", datasource_id=1,
                      summary="옛 요약", description="옛 설명"))
        s.add_all([
            DatasetSchema(id=11, dataset_id=1, field_path="email", field_type="STRING",
                          description="옛 컬럼 설명", pii_type="EMAIL", nullable="true", ordinal=0),
            DatasetSchema(id=12, dataset_id=1, field_path="amount", field_type="NUMBER",
                          nullable="true", ordinal=1),
        ])
        s.add(Tag(id=100, name="sales", color="#000000"))
        s.add(DatasetTag(dataset_id=1, tag_id=100))
        s.add_all([
            AIGenerationLog(id=1, entity_type="dataset", entity_id=1, dataset_id=1, generation_type="summary",
                            generated_text="새 요약", provider="agent", model="m"),
            AIGenerationLog(id=2, entity_type="dataset", entity_id=1, dataset_id=1, generation_type="description",
                            generated_text="새 설명", provider="openai", model="m"),
            AIGenerationLog(id=3, entity_type="column", entity_id=11, dataset_id=1, field_name="email",
                            generation_type="description", generated_text="새 컬럼 설명", provider="agent", model="m"),
            AIGenerationLog(id=4, entity_type="tag", entity_id=1, dataset_id=1, generation_type="tag_suggestion",
                            generated_text=json.dumps(["sales", "finance"]), provider="agent", model="m"),
            AIGenerationLog(id=5, entity_type="pii", entity_id=1, dataset_id=1, generation_type="pii_detection",
                            generated_text=json.dumps([{"name": "email", "pii_type": "EMAIL"},
                                                       {"name": "amount", "pii_type": "FINANCIAL"}]),
                            provider="agent", model="m"),
        ])
        await s.commit()
    async with make() as s:
        yield s
    await engine.dispose()


async def test_suggestions_carry_current_value(session):
    items = {i["id"]: i for i in await service.get_suggestions(session, 1)}
    assert items[1]["current_text"] == "옛 요약"
    assert items[2]["current_text"] == "옛 설명"
    assert items[3]["current_text"] == "옛 컬럼 설명"
    assert json.loads(items[4]["current_text"]) == ["sales"]
    assert json.loads(items[5]["current_text"]) == [
        {"name": "email", "pii_type": "EMAIL"}, {"name": "amount", "pii_type": None},
    ]


async def test_apply_summary_updates_dataset(session):
    assert (await service.apply_suggestion(session, 1))["applied"] is True
    ds = (await session.execute(select(Dataset).where(Dataset.id == 1))).scalars().one()
    await session.refresh(ds)
    assert ds.summary == "새 요약"
    assert 1 not in {i["id"] for i in await service.get_suggestions(session, 1)}


async def test_apply_agent_tag_list_assigns_and_creates(session):
    res = await service.apply_suggestions(session, [4])
    assert res["applied_ids"] == [4]
    names = (await session.execute(
        select(Tag.name).join(DatasetTag, DatasetTag.tag_id == Tag.id).where(DatasetTag.dataset_id == 1)
    )).scalars().all()
    assert sorted(names) == ["finance", "sales"]


async def test_reject_removes_from_pending(session):
    await service.reject_suggestion(session, 2)
    assert 2 not in {i["id"] for i in await service.get_suggestions(session, 1)}
