# SPDX-License-Identifier: Apache-2.0
"""AI 어시스턴트 내장 경로의 대화 이력 — 사용자별, LRU (DEF-002).

키는 (사용자, conversation_id) 다. 다른 사용자의 conversation_id 를 보내도 그 사용자의 대화가 아니라
빈 이력으로 시작한다. 대화 수가 상한을 넘으면 가장 오래 쓰지 않은 대화부터 지운다(예전: 전체 삭제).

프로세스 메모리에 두므로 워커·인스턴스마다 따로이고 재시작하면 사라진다(TODO-034).
백엔드는 asyncio 단일 스레드라 잠금이 필요 없다.
"""

from __future__ import annotations

from collections import OrderedDict


class ConversationStore:
    """(사용자, conversation_id) → [{"role": "user"|"assistant", "text": ...}]."""

    def __init__(self, max_conversations: int = 200, max_turns: int = 20) -> None:
        self.max_conversations = max_conversations
        self.max_turns = max_turns
        self._items: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, owner: str, conv_id: str) -> list[dict]:
        """이력 사본(없으면 빈 목록). 읽기도 최근 사용으로 친다."""
        key = (owner, conv_id)
        history = self._items.get(key)
        if history is None:
            return []
        self._items.move_to_end(key)
        return list(history)

    def append(self, owner: str, conv_id: str, user_text: str, assistant_text: str) -> None:
        """질문·답변 한 쌍을 더한다 — 최근 ``max_turns`` 개 메시지만 남기고, 대화 수 상한을 넘으면 LRU 로 지운다."""
        key = (owner, conv_id)
        history = (self._items.pop(key, []) + [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": assistant_text},
        ])[-self.max_turns:]
        self._items[key] = history
        while len(self._items) > self.max_conversations:
            self._items.popitem(last=False)

    def drop_last_exchange(self, owner: str, conv_id: str, question: str | None = None) -> bool:
        """마지막 질문·답변 한 쌍을 지운다(답변 다시 생성). 지웠으면 True.

        ``question`` 을 주면 마지막 질문이 그것과 같을 때만 지운다 — 오류·중단으로 이력에 남지 않은 질문을 다시 시도할 때
        앞의 정상 질문·답변을 지우지 않는다(DEF-076).
        """
        history = self._items.get((owner, conv_id))
        if not history or len(history) < 2 or history[-1]["role"] != "assistant" or history[-2]["role"] != "user":
            return False
        if question is not None and history[-2]["text"] != question:
            return False
        del history[-2:]
        return True

    def clear(self) -> None:
        self._items.clear()
