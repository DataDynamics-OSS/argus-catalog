# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 서술형 항목 초안(TODO-096 6단계) 테스트 — SQLite 메모리 DB, LLM 은 가짜.

- 법령 목록: 설정 문자열 해석 · 검증, LLM 이 고른 이름 중 목록에 있는 것만
- 프롬프트: 프로파일 통계는 싣고 샘플 행은 싣지 않음, PII 컬럼의 값(범위 · 상위 값)은 싣지 않음
- 생성: 빈 항목만(force 면 다시), 목록이 없으면 관련법령 건너뜀, 제안으로만 남음
- 개인정보 보호조치 · 허용 이용 유형: 정해진 코드만(모르는 값 버림), 개인정보 없음이면 보호조치 건너뜀, 근거는 제안 로그 reason
- 적용: 제안 목록의 현재 값, 적용하면 그 항목에 들어감
- 설정 API 의 법령 목록 검증

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_ai_ready_drafts.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ai import ai_ready_drafts, service
from app.ai.models import AIGenerationLog
from app.catalog.models import Dataset, DatasetLineage, DatasetSchema, DatasetTag, Datasource, Tag
from app.core.database import Base
from app.quality.models import DataProfile
from app.settings.models import CatalogConfiguration

LAWS = """# 기관이 쓰는 법령
개인정보 보호법 | 개인을 알아볼 수 있는 정보가 있을 때
공공데이터의 제공 및 이용 활성화에 관한 법률
「통계법」 | 통계 작성 · 공표
개인정보 보호법 | 중복
"""


class _Provider:
    def provider_name(self):
        return "fake"

    def model_name(self):
        return "fake-1"


@pytest.fixture
async def make(monkeypatch):
    from app.embedding import service as embedding_service

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(embedding_service, "embed_dataset_background", _noop)

    async def _provider():
        return _Provider()
    monkeypatch.setattr(ai_ready_drafts, "get_provider", _provider)

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            AIGenerationLog.__table__, Dataset.__table__, DatasetSchema.__table__, Datasource.__table__,
            DatasetLineage.__table__, DataProfile.__table__, CatalogConfiguration.__table__, Tag.__table__,
            DatasetTag.__table__,
        ])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(Datasource(id=1, datasource_id="pg-1", name="민원 DB", type="postgresql"))
        s.add(Dataset(id=1, urn="ds.civil.petition", name="civil.petition", datasource_id=1,
                      summary="민원 접수 내역", description="<p>시군구별 <b>민원</b> 접수</p>",
                      ingestion_frequency="DAILY", known_limitations="이미 쓴 한계"))
        s.add_all([
            DatasetSchema(id=11, dataset_id=1, field_path="sigungu", field_type="STRING", native_type="varchar",
                          nullable="true", ordinal=0),
            DatasetSchema(id=12, dataset_id=1, field_path="phone", field_type="STRING", native_type="varchar",
                          pii_type="PHONE", nullable="true", ordinal=1),
            DatasetSchema(id=13, dataset_id=1, field_path="received_at", field_type="DATE", native_type="date",
                          nullable="true", ordinal=2),
        ])
        s.add(DataProfile(dataset_id=1, row_count=1000, profiled_at=datetime(2026, 11, 1, tzinfo=UTC), profile_json=json.dumps([
            {"column_name": "sigungu", "column_type": "varchar", "total_count": 1000, "null_count": 0, "null_percent": 0,
             "unique_count": 3, "unique_percent": 0.3, "min_value": "강남구", "max_value": "종로구",
             "top_values": [{"value": "강남구", "count": 800}, {"value": "종로구", "count": 150}, {"value": "중구", "count": 50}]},
            {"column_name": "phone", "column_type": "varchar", "total_count": 1000, "null_count": 300, "null_percent": 30,
             "unique_count": 5, "unique_percent": 0.5, "min_value": "010-0000-0001", "max_value": "010-9999-9999",
             "top_values": [{"value": "010-1234-5678", "count": 10}]},
            {"column_name": "received_at", "column_type": "date", "total_count": 1000, "null_count": 0, "null_percent": 0,
             "unique_count": 300, "unique_percent": 30, "min_value": "2024-01-01", "max_value": "2024-12-31"},
        ])))
        s.add(CatalogConfiguration(category="ai_ready", config_key="ai_ready_laws", config_value=LAWS))
        await s.commit()
    yield maker
    await engine.dispose()


def _llm(monkeypatch, data: dict, seen: list | None = None):
    async def fake(prompt, system_prompt=None, temperature=0.3, max_tokens=1024):
        if seen is not None:
            seen.append(prompt)
        return {"data": data, "prompt_tokens": 100, "completion_tokens": 50}
    monkeypatch.setattr(service, "_call_llm", fake)


def test_parse_and_pick_laws() -> None:
    laws = ai_ready_drafts.parse_laws(LAWS)
    assert laws == [("개인정보 보호법", "개인을 알아볼 수 있는 정보가 있을 때"),
                    ("공공데이터의 제공 및 이용 활성화에 관한 법률", ""), ("통계법", "통계 작성 · 공표")]
    pick = ai_ready_drafts.pick_laws
    assert pick(["「개인정보보호법」", "도로교통법", "통계법"], laws) == ["개인정보 보호법", "통계법"], "띄어쓰기 · 낫표 무시, 목록 밖은 버림"
    assert pick("통계법, 없는법", laws) == ["통계법"]
    assert pick(None, laws) == [] and pick(42, laws) == []
    assert ai_ready_drafts.check_laws(LAWS).splitlines()[0] == "개인정보 보호법 | 개인을 알아볼 수 있는 정보가 있을 때"
    with pytest.raises(ValueError):
        ai_ready_drafts.check_laws("가" * 201)


async def test_generate_drafts_only_empty_fields_and_laws_from_list(make, monkeypatch) -> None:
    prompts: list[str] = []
    _llm(monkeypatch, {
        "data_biases": ["강남구가 80% 로 치우침", "2024년 한 해만 있음"],
        "known_limitations": "덮어쓰면 안 됨",
        "legal_basis": ["개인정보 보호법", "도로교통법"],
        "legal_reason": "phone 컬럼이 전화번호(PII)",
        "confidence": 0.7,
    }, prompts)
    async with make() as s:
        res = await ai_ready_drafts.generate_drafts(s, 1)
    assert [d["field"] for d in res["drafts"]] == ["data_biases", "legal_basis"]
    assert res["drafts"][0]["text"] == "- 강남구가 80% 로 치우침\n- 2024년 한 해만 있음", "목록이면 한 줄씩"
    assert res["drafts"][1]["text"] == "개인정보 보호법", "목록 밖 법령(도로교통법)은 버린다"
    assert res["legal_reason"] == "phone 컬럼이 전화번호(PII)" and res["confidence"] == 0.7
    assert [s_["field"] for s_ in res["skipped"]] == ["known_limitations", "privacy_measures", "allowed_uses"], \
        "값이 있는 항목은 건너뜀 — 응답에 없는 항목(보호조치 · 이용 유형)은 쓰지 못함"

    (prompt,) = prompts
    assert "known_limitations" not in prompt.split("Respond in JSON")[1], "건너뛴 항목은 묻지 않는다"
    assert "강남구" in prompt and "80.0" in prompt, "범주 컬럼의 분포는 싣는다"
    assert "010-" not in prompt, "PII 컬럼의 값(범위 · 상위 값)은 싣지 않는다"
    assert '"null_pct": 30.0' in prompt and '"pii": "PHONE"' in prompt
    assert "<p>" not in prompt and "시군구별 민원 접수" in prompt
    assert "개인정보 보호법 — applies when: 개인을 알아볼 수 있는 정보가 있을 때" in prompt

    async with make() as s:
        ds = await s.get(Dataset, 1)
        assert ds.data_biases is None and ds.legal_basis is None, "초안은 제안으로만 — 데이터셋은 그대로"
        logs = (await s.execute(select(AIGenerationLog).order_by(AIGenerationLog.id))).scalars().all()
        assert [(lg.generation_type, lg.field_name, lg.applied) for lg in logs] == [
            ("ai_ready", "data_biases", False), ("ai_ready", "legal_basis", False)]
        assert [lg.prompt_tokens for lg in logs] == [100, None], "한 번의 호출 — 토큰은 첫 항목에만"


async def test_force_no_laws_and_nothing_to_do(make, monkeypatch) -> None:
    calls: list[str] = []
    _llm(monkeypatch, {"known_limitations": "- 결측 30%", "legal_basis": []}, calls)
    async with make() as s:
        res = await ai_ready_drafts.generate_drafts(s, 1, fields=["known_limitations", "legal_basis"], force=True)
    assert [d["field"] for d in res["drafts"]] == ["known_limitations"]
    assert res["skipped"] == [{"field": "legal_basis", "label": "관련법령",
                               "reason": "목록의 법령 가운데 해당하는 것을 찾지 못했습니다."}]

    async with make() as s:
        (await s.execute(select(CatalogConfiguration))).scalars().one().config_value = ""
        ds = await s.get(Dataset, 1)
        ds.data_biases = "있음"
        ds.privacy_measures = "가명처리"
        ds.allowed_uses = "VIEW"
        await s.commit()
        res = await ai_ready_drafts.generate_drafts(s, 1)
    assert res["drafts"] == [] and len(calls) == 1, "만들 항목이 없으면 LLM 을 부르지 않는다"
    reasons = {x["field"]: x["reason"] for x in res["skipped"]}
    assert "관련법령 목록이 없습니다" in reasons["legal_basis"] and "이미 값이 있습니다" in reasons["data_biases"]

    async with make() as s:
        with pytest.raises(ValueError, match="초안을 만들 수 없는 항목"):
            await ai_ready_drafts.generate_drafts(s, 1, fields=["description"])
        with pytest.raises(ValueError, match="데이터셋을 찾을 수 없습니다"):
            await ai_ready_drafts.generate_drafts(s, 99)


async def test_suggestion_current_value_and_apply(make, monkeypatch) -> None:
    _llm(monkeypatch, {"known_limitations": "- 새 한계", "data_biases": "- 편향", "legal_basis": ["통계법"]})
    async with make() as s:
        res = await ai_ready_drafts.generate_drafts(s, 1, force=True)
        ids = {d["field"]: d["log_id"] for d in res["drafts"]}
        items = {i["id"]: i for i in await service.get_suggestions(s, 1)}
        assert items[ids["known_limitations"]]["current_text"] == "이미 쓴 한계"
        assert items[ids["data_biases"]]["current_text"] is None
        await service.apply_suggestions(s, [ids["known_limitations"], ids["legal_basis"]])
    async with make() as s:
        ds = await s.get(Dataset, 1)
        assert (ds.known_limitations, ds.legal_basis, ds.data_biases) == ("- 새 한계", "통계법", None)


async def test_bulk_generate_ai_ready_targets_missing_fields(make, monkeypatch) -> None:
    _llm(monkeypatch, {"data_biases": "- 편향", "legal_basis": []})
    async with make() as s:
        s.add(Dataset(id=2, urn="ds.full", name="full", datasource_id=1, data_biases="a", known_limitations="b",
                      legal_basis="c", privacy_measures="d", allowed_uses="", description="설명 있음"))
        await s.commit()
        res = await service.bulk_generate(s, ["ai_ready"], apply=True)
    assert res["total"] == 1 and res["results"][0]["dataset_id"] == 1, \
        "초안 항목이 다 찬 데이터셋은 빠진다 — 허용 이용 유형의 빈 목록('모두 허용하지 않음')도 값"
    async with make() as s:
        assert (await s.get(Dataset, 1)).data_biases is None, "일괄 생성도 제안으로만"


def test_pick_uses() -> None:
    pick = ai_ready_drafts.pick_uses
    assert pick(["AI_TRAINING", "view", "판매", "분석"]) == ["VIEW", "ANALYSIS", "AI_TRAINING"], "정의 순서, 화면 이름도, 모르는 값은 버림"
    assert pick("ai-training, COMBINE") == ["COMBINE", "AI_TRAINING"]
    assert pick("AI 학습") == ["AI_TRAINING"]
    assert pick(None) == [] and pick(["모두"]) == [] and pick(3) == []


async def test_privacy_and_allowed_uses_drafts_with_reason(make, monkeypatch) -> None:
    prompts: list[str] = []
    _llm(monkeypatch, {
        "privacy_measures": ["phone: 가명처리 또는 삭제 후 제공", "재식별 위험 검토 필요"],
        "allowed_uses": ["VIEW", "ANALYSIS", "SELL"],
        "allowed_uses_reason": "phone(PII) 때문에 AI 학습은 가명처리 뒤에",
        "legal_basis": ["개인정보 보호법"],
        "legal_reason": "phone 컬럼이 전화번호",
    }, prompts)
    async with make() as s:
        res = await ai_ready_drafts.generate_drafts(s, 1, fields=["privacy_measures", "allowed_uses", "legal_basis"])
    by = {d["field"]: d for d in res["drafts"]}
    assert by["privacy_measures"]["text"] == "- phone: 가명처리 또는 삭제 후 제공\n- 재식별 위험 검토 필요"
    assert by["allowed_uses"]["text"] == "VIEW,ANALYSIS", "정해진 코드만 — SELL 은 버림"
    assert by["allowed_uses"]["reason"] == "phone(PII) 때문에 AI 학습은 가명처리 뒤에"
    assert by["legal_basis"]["reason"] == "phone 컬럼이 전화번호" and by["privacy_measures"]["reason"] is None
    (prompt,) = prompts
    assert "AI_TRAINING (AI 학습)" in prompt and "allowed_uses_reason" in prompt and "privacy_measures" in prompt
    assert "010-" not in prompt, "샘플 · PII 값은 싣지 않는다"

    async with make() as s:
        items = {i["field_name"]: i for i in await service.get_suggestions(s, 1)}
        assert items["legal_basis"]["reason"] == "phone 컬럼이 전화번호", "근거는 제안 목록에 함께"
        assert items["allowed_uses"]["reason"].startswith("phone(PII)") and items["allowed_uses"]["current_text"] is None
        await service.apply_suggestions(s, [by["allowed_uses"]["log_id"], by["privacy_measures"]["log_id"]])
    async with make() as s:
        ds = await s.get(Dataset, 1)
        assert ds.allowed_uses == "VIEW,ANALYSIS" and ds.privacy_measures.startswith("- phone")


async def test_allowed_uses_invalid_and_privacy_skip(make, monkeypatch) -> None:
    _llm(monkeypatch, {"allowed_uses": ["SELL", "EVERYTHING"], "privacy_measures": "- 없음"})
    async with make() as s:
        ds = await s.get(Dataset, 1)
        ds.contains_pii = "false"
        await s.commit()
        res = await ai_ready_drafts.generate_drafts(s, 1, fields=["allowed_uses", "privacy_measures"])
    assert res["drafts"] == []
    reasons = {x["field"]: x["reason"] for x in res["skipped"]}
    assert "개인정보가 없다고" in reasons["privacy_measures"]
    assert "이용 유형을 고르지 못했습니다" in reasons["allowed_uses"]

    async with make() as s:
        ds = await s.get(Dataset, 1)
        ds.allowed_uses = ""  # '모두 허용하지 않음'도 정한 값
        await s.commit()
        res = await ai_ready_drafts.generate_drafts(s, 1, fields=["allowed_uses"])
    assert res["skipped"][0]["reason"] == "이미 값이 있습니다."

    ds = Dataset()
    with pytest.raises(ValueError, match="정해진 코드가 없습니다"):
        ai_ready_drafts.apply_draft(ds, "allowed_uses", "SELL")
    ai_ready_drafts.apply_draft(ds, "allowed_uses", "AI_TRAINING,VIEW")
    assert ds.allowed_uses == "VIEW,AI_TRAINING"


def test_settings_validate_laws() -> None:
    from app.settings.router import AiReadyConfig

    cfg = AiReadyConfig(laws="  통계법 |  통계 작성 \n\n통계법\n")
    assert cfg.laws == "통계법 | 통계 작성"
    with pytest.raises(ValueError):
        AiReadyConfig(laws="가" * 300)
