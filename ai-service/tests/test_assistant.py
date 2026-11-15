# SPDX-License-Identifier: Apache-2.0
"""어시스턴트 tool-use 루프 — 참조 데이터셋 유지(TODO-036)·라운드 초과 답변(TODO-037)·도구 호출 복구·한자 재작성.

LLM 은 정해 둔 응답을 차례로 돌려주는 가짜로, 도구는 ``run_tool`` 을 바꿔 끼워 대신한다.

실행:
    cd ai-service
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_assistant.py
"""

from __future__ import annotations

import json

import pytest

from argus_catalog_ai import assistant as asst


class FakeLLM:
    """``chat`` 호출마다 미리 정한 응답을 하나씩 돌려준다. 받은 messages·tools 를 기록한다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"message": reply, "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def _tool_call(name, args, cid="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _answer(text):
    return {"role": "assistant", "content": text}


@pytest.fixture()
def tools(monkeypatch):
    """도구 실행 대체 — get_dataset_detail 은 이름을, 나머지는 빈 성공 결과를 돌려준다."""
    calls = []

    def fake_run_tool(_ctx, name, args):
        calls.append((name, args))
        if name == "get_dataset_detail":
            return {"id": args["dataset_id"], "name": f"ds{args['dataset_id']}"}
        if args.get("dataset_id") == 404:
            return {"error": "도구 실행 실패: HTTP 404"}
        return {"ok": True}

    monkeypatch.setattr(asst, "run_tool", fake_run_tool)
    return calls


def _run(llm, history=None, message="질문"):
    return list(asst.run_assistant(llm, None, history or [], message))


def _final(events):
    return next(e["data"] for e in events if e["type"] == "_final")


def _text(events):
    return "".join(e["data"]["text"] for e in events if e["type"] == "text_delta")


def test_final_reports_datasets_confirmed_by_tools(tools):
    llm = FakeLLM([
        _tool_call("get_dataset_detail", {"dataset_id": 7}),
        _tool_call("get_quality", {"dataset_id": 9}, "c2"),
        _tool_call("get_quality", {"dataset_id": 404}, "c3"),   # 실패한 도구는 넣지 않는다
        _answer("답변"),
    ])
    final = _final(_run(llm))
    assert final["text"] == "답변"
    assert final["datasets"] == [{"id": 7, "name": "ds7"}, {"id": 9, "name": None}]


def test_history_datasets_are_given_as_context(tools):
    history = [
        {"role": "user", "text": "대여 테이블 찾아줘"},
        {"role": "assistant", "text": "rental 입니다", "datasets": [{"id": 7, "name": "rental"}]},
    ]
    llm = FakeLLM([_answer("품질 답변")])
    _run(llm, history, "그 테이블 품질은?")
    msgs = llm.calls[0]["messages"]
    assert msgs[-1] == {"role": "user", "content": "그 테이블 품질은?"}
    note = msgs[-2]
    assert note["role"] == "system" and "rental(id=7)" in note["content"]


def test_no_context_note_without_history_datasets(tools):
    llm = FakeLLM([_answer("안녕하세요")])
    _run(llm, [{"role": "user", "text": "안녕"}, {"role": "assistant", "text": "네"}])
    assert [m["role"] for m in llm.calls[0]["messages"]] == ["system", "user", "assistant", "user"]


def test_history_datasets_latest_first_and_capped():
    history = [{"role": "assistant", "text": "x", "datasets": [{"id": i, "name": f"t{i}"}]}
               for i in range(15)]
    history.append({"role": "assistant", "text": "y", "datasets": [{"id": 3, "name": "t3-new"}]})
    got = asst._history_datasets(history)
    assert len(got) == asst.MAX_CONTEXT_DATASETS
    assert got[0] == {"id": 3, "name": "t3-new"}
    assert got[1]["id"] == 14


def test_round_limit_answers_from_gathered_evidence(tools):
    rounds = [_tool_call("get_quality", {"dataset_id": 1}, f"c{i}") for i in range(asst.MAX_TOOL_ROUNDS)]
    llm = FakeLLM(rounds + [_answer("확인한 범위의 답변")])
    events = _run(llm)
    assert _text(events) == "확인한 범위의 답변"
    assert not any(e["type"] == "error" for e in events)
    last = llm.calls[-1]
    assert last["tools"] is None
    assert last["messages"][-1]["content"] == asst.ROUND_LIMIT_PROMPT
    usage = next(e["data"] for e in events if e["type"] == "usage")
    assert usage["tokens_in"] == 10 * (asst.MAX_TOOL_ROUNDS + 1)


def test_round_limit_error_when_wrap_up_fails(tools):
    rounds = [_tool_call("get_quality", {"dataset_id": 1}, f"c{i}") for i in range(asst.MAX_TOOL_ROUNDS)]
    llm = FakeLLM(rounds + [RuntimeError("LLM down")])
    events = _run(llm)
    assert events[-1]["type"] == "error"
    assert "초과" in events[-1]["data"]["reason"]
    assert not any(e["type"] == "_final" for e in events)


def test_leaked_tool_call_is_recovered(tools):
    leaked = _answer('<tool_call>\n{"name": "get_dataset_detail", "arguments": {"dataset_id": 5}}\n</tool_call>')
    llm = FakeLLM([leaked, _answer("ds5 설명")])
    events = _run(llm)
    assert tools == [("get_dataset_detail", {"dataset_id": 5})]
    assert _text(events) == "ds5 설명"


def test_json_example_in_answer_is_not_a_tool_call(tools):
    llm = FakeLLM([_answer('예시: {"name": "홍길동", "age": 3}')])
    events = _run(llm)
    assert tools == []
    assert "홍길동" in _text(events)


def test_chinese_drift_is_rewritten(tools):
    llm = FakeLLM([_answer("这是数据表说明"), _answer("이것은 테이블 설명입니다")])
    events = _run(llm)
    assert _text(events) == "이것은 테이블 설명입니다"
    assert llm.calls[1]["tools"] is None


def test_non_object_tool_arguments_become_empty(tools):
    bad = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_quality", "arguments": "[1]"}}]}
    llm = FakeLLM([bad, _answer("끝")])
    _run(llm)
    assert tools == [("get_quality", {})]


def test_get_lineage_returns_ids_for_graph() -> None:
    """리니지 결과에 데이터셋 id 를 넣어 화면이 미니 그래프·링크를 그린다(TODO-065)."""
    from argus_catalog_ai.tools import get_lineage

    class Ctx:
        def get(self, path):
            assert path == "/catalog/datasets/2/lineage?depth=1"
            return {"nodes": [{"id": 1, "name": "src"}, {"id": 2, "name": "me"},
                              {"id": 3, "name": "dst", "qualityStatus": "WARN"}],
                    "edges": [{"source": 1, "target": 2, "relationType": "ETL"},
                              {"source": 2, "target": 3, "relationType": "VIEW"}]}

    r = get_lineage(Ctx(), 2)
    assert r["dataset"] == {"id": 2, "name": "me"}
    assert r["upstream"] == [{"id": 1, "name": "src", "relation": "ETL"}]
    assert r["downstream"] == [{"id": 3, "name": "dst", "relation": "VIEW"}]
    assert r["quality_warnings"] == [{"name": "dst", "status": "WARN"}]


def test_tool_http_errors_become_actionable_messages() -> None:
    """도구가 없는 데이터셋 id(모델이 지어낸 값)로 부르면 404 원문 대신 다시 검색하라는 문구를 돌려준다.
    권한 거부 · 그 밖의 오류도 원문 대신 설명한다."""
    import urllib.error

    from argus_catalog_ai.tools import run_tool

    class Ctx:
        def __init__(self, code):
            self.code = code

        def get(self, path):
            raise urllib.error.HTTPError("http://x" + path, self.code, "x", {}, None)

    r = run_tool(Ctx(404), "get_dataset_detail", {"dataset_id": 1024})
    assert "1024" in r["error"] and "search_datasets" in r["error"] and "HTTP Error" not in r["error"]
    assert "404" in run_tool(Ctx(404), "get_glossary_term", {"term_name": "x"})["error"]
    assert "권한" in run_tool(Ctx(403), "get_dataset_detail", {"dataset_id": 1})["error"]
    assert "500" in run_tool(Ctx(500), "get_dataset_detail", {"dataset_id": 1})["error"]
