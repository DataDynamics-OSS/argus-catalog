# SPDX-License-Identifier: Apache-2.0
"""AI 어시스턴트 대화 이력 — 사용자별 분리와 LRU 정리 (DEF-002).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest tests/test_assistant_conversations.py
"""

from __future__ import annotations

import json

from app.ai import router as ai_router
from app.ai.conversations import ConversationStore
from app.core.auth import TokenUser


def test_store_is_scoped_by_owner():
    st = ConversationStore()
    st.append("alice", "c1", "비밀 질문", "비밀 답변")
    assert st.get("alice", "c1")[0]["text"] == "비밀 질문"
    assert st.get("bob", "c1") == []  # 같은 conversation_id 라도 다른 사용자의 이력은 안 보인다


def test_store_evicts_least_recently_used_only():
    st = ConversationStore(max_conversations=2)
    st.append("u", "a", "q", "r")
    st.append("u", "b", "q", "r")
    st.get("u", "a")               # a 를 최근 사용으로
    st.append("u", "c", "q", "r")  # 상한 초과 → 가장 오래 쓰지 않은 b 만 지운다
    assert len(st) == 2
    assert st.get("u", "a") and st.get("u", "c")
    assert st.get("u", "b") == []


def test_store_keeps_last_turns():
    st = ConversationStore(max_turns=4)
    for i in range(5):
        st.append("u", "a", f"q{i}", f"r{i}")
    assert [t["text"] for t in st.get("u", "a")] == ["q3", "r3", "q4", "r4"]


class _FakeProvider:
    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, prompt, **_kw):
        self.prompts.append(prompt)
        return {"text": "답변", "prompt_tokens": 1, "completion_tokens": 1}


class _Req:
    headers: dict = {}


async def _chat(user: TokenUser, message: str, conv_id: str | None) -> str:
    resp = await ai_router.assistant_chat({"message": message, "conversation_id": conv_id}, user, _Req(), None)
    chunks = [c async for c in resp.body_iterator]
    events = [json.loads(line[len("data: "):]) for c in chunks for line in c.splitlines() if line.startswith("data: ")]
    return next(e["data"]["conversation_id"] for e in events if e["type"] == "usage")


async def test_chat_does_not_leak_other_users_history(monkeypatch):
    fake = _FakeProvider()

    async def get_provider():
        return fake

    async def no_agent(_session, _category):
        return {}

    monkeypatch.setattr("app.ai.registry.get_provider", get_provider)
    monkeypatch.setattr("app.settings.service.get_config_by_category", no_agent)
    monkeypatch.setattr(ai_router, "_conversations", ConversationStore())

    alice = TokenUser(sub="alice-sub", username="alice")
    bob = TokenUser(sub="bob-sub", username="bob")
    conv = await _chat(alice, "내 급여 테이블 이름은 hr.salary_secret 이야", None)
    await _chat(bob, "앞에서 말한 테이블 이름이 뭐였지?", conv)  # alice 의 conversation_id 를 재사용
    assert "salary_secret" not in fake.prompts[-1]

    await _chat(alice, "아까 그 테이블은?", conv)
    assert "salary_secret" in fake.prompts[-1]  # 본인은 이어서 대화된다


async def test_proxy_forwards_token_and_backend_conversation_id(monkeypatch):
    """외부 어시스턴트 프록시 — 백엔드가 정한 conversation_id 를 넘기고(DEF-034), 소문자 bearer 도 토큰만 넘긴다(DEF-033)."""
    import httpx

    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text='data: {"type": "token", "data": {"text": "ok"}}\n\n')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **{**k, "timeout": 5}))

    async def agent_on(_session, _category):
        return {"assistant_enabled": "true", "assistant_agent_url": "http://ai:8930"}

    monkeypatch.setattr("app.settings.service.get_config_by_category", agent_on)

    class Req:
        headers = {"Authorization": "bearer tok-123"}

    logged: list[str] = []
    monkeypatch.setattr(ai_router.logger, "debug", lambda msg, *a: logged.append(msg % a))
    resp = await ai_router.assistant_chat({"message": "hi"}, TokenUser(sub="s", username="u"), Req(), None)
    _ = [c async for c in resp.body_iterator]
    assert seen["auth"] == "Bearer tok-123"
    conv = seen["body"]["conversation_id"]
    assert conv and any(f"conv={conv}" in m for m in logged)   # 예전: null 을 넘겨 ai-service 가 다른 id 를 만들었다


# ---------------------------------------------------------------------------
# 답변 다시 생성·평가 (TODO-068)
# ---------------------------------------------------------------------------

def test_drop_last_exchange():
    st = ConversationStore()
    assert st.drop_last_exchange("u", "a") is False   # 이력 없음
    st.append("u", "a", "q1", "r1")
    st.append("u", "a", "q2", "r2")
    # 마지막 질문이 다른 질문이면 지우지 않는다 — 오류·중단으로 이력에 없는 질문의 다시 시도(DEF-076)
    assert st.drop_last_exchange("u", "a", "q3") is False
    assert [t["text"] for t in st.get("u", "a")] == ["q1", "r1", "q2", "r2"]
    assert st.drop_last_exchange("u", "a", "q2") is True
    assert [t["text"] for t in st.get("u", "a")] == ["q1", "r1"]
    st.append("u", "a", "q2", "r2")
    assert st.drop_last_exchange("u", "a") is True    # 질문을 주지 않으면 예전처럼 마지막 쌍


async def test_regenerate_drops_previous_answer_from_prompt(monkeypatch):
    fake = _FakeProvider()

    async def get_provider():
        return fake

    async def no_agent(_session, _category):
        return {}

    monkeypatch.setattr("app.ai.registry.get_provider", get_provider)
    monkeypatch.setattr("app.settings.service.get_config_by_category", no_agent)
    store = ConversationStore()
    monkeypatch.setattr(ai_router, "_conversations", store)
    user = TokenUser(sub="u-sub", username="u")
    conv = await _chat(user, "첫 질문", None)
    resp = await ai_router.assistant_chat(
        {"message": "첫 질문", "conversation_id": conv, "regenerate": True}, user, _Req(), None)
    _ = [c async for c in resp.body_iterator]
    # 다시 생성할 때는 이전 답변("답변")이 프롬프트에 남지 않고, 이력에는 질문·답변 한 쌍만 남는다
    assert fake.prompts[-1] == "사용자: 첫 질문"
    assert [t["text"] for t in store.get("u-sub", conv)] == ["첫 질문", "답변"]


async def test_feedback_upsert_and_clear():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.ai.models import AssistantFeedback
    from app.ai.schemas import AssistantFeedbackRequest
    from app.core.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AssistantFeedback.__table__])
    make = async_sessionmaker(engine, expire_on_commit=False)
    alice = TokenUser(sub="alice-sub", username="alice")
    bob = TokenUser(sub="bob-sub", username="bob")

    async def send(user, **kw):
        async with make() as s:
            return await ai_router.assistant_feedback(AssistantFeedbackRequest(**kw), user, s)

    r = await send(alice, answer_id="a1", conversation_id="c1", rating="up", question="q" * 3000, answer="답")
    assert r.rating == "up"
    await send(alice, answer_id="a1", conversation_id="c1", rating="down", comment="틀림")   # 같은 답변은 바꾼다
    await send(bob, answer_id="a1", conversation_id="c1", rating="up")                       # 다른 사용자는 따로
    async with make() as s:
        rows = {(f.user_sub, f.rating): f for f in (await s.execute(select(AssistantFeedback))).scalars()}
    assert set(rows) == {("alice-sub", -1), ("bob-sub", 1)}
    a = rows[("alice-sub", -1)]
    assert a.comment == "틀림" and len(a.question) == 2000 and a.answer == "답" and a.username == "alice"
    r = await send(alice, answer_id="a1", rating=None)                                       # 평가 취소
    assert r.rating is None
    async with make() as s:
        left = [f.user_sub for f in (await s.execute(select(AssistantFeedback))).scalars()]
    assert left == ["bob-sub"]
    await engine.dispose()
