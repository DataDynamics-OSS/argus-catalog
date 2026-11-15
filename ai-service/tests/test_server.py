# SPDX-License-Identifier: Apache-2.0
"""어시스턴트 서버(``POST /chat``) — 이력 소유자 검증·요청 검증·스트리밍 예외 (DEF-029~033).

가짜 카탈로그 API(``/api/v1/auth/me``)와 실제 핸들러를 임의 포트에 띄워 HTTP 로 확인한다. LLM 호출은
``run_assistant`` 를 바꿔 끼워 대신한다.

실행:
    cd ai-service
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_server.py
"""

from __future__ import annotations

import base64
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from argus_catalog_ai import server as srv
from argus_catalog_ai.config import AgentConfig

# 카탈로그가 인정하는 토큰 → sub
VALID = {"tok-alice": "uuid-alice", "tok-bob": "uuid-bob"}


class _Catalog(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):  # noqa: N802
        sub = VALID.get(self.headers.get("Authorization", "").removeprefix("Bearer "))
        if self.path != "/api/v1/auth/me" or sub is None:
            self.send_error(401)
            return
        body = json.dumps({"sub": sub, "username": sub}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(handler) -> ThreadingHTTPServer:
    s = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture()
def env(monkeypatch):
    srv._conversations.clear()
    catalog = _start(_Catalog)
    seen: dict = {"histories": [], "telemetry": []}
    behavior = {"raise": None}

    def fake_run(_llm, ctx, history, message):
        seen["histories"].append((ctx.token, list(history)))
        if behavior["raise"]:
            raise behavior["raise"]
        yield {"type": "token", "data": {"text": "ok"}}
        yield {"type": "_final", "data": {"text": f"답:{message}"}}

    class FakeTelemetry:
        def __init__(self, *a, **k):
            pass

        def record(self, token, **k):
            seen["telemetry"].append(k["status"])

    monkeypatch.setattr(srv, "run_assistant", fake_run)
    monkeypatch.setattr(srv, "Telemetry", FakeTelemetry)
    cfg = AgentConfig(api_url=f"http://127.0.0.1:{catalog.server_port}", username="", password="",
                      llm_url="http://127.0.0.1:1/v1", model="m", llm_api_key="x", mode="suggest",
                      temperature=0.0, max_tokens=10, poll_interval=60, worker_target="missing",
                      agent_name="t", telemetry_enabled=False)
    app = _start(srv.make_handler(cfg))

    def chat(body, auth="Bearer tok-alice"):
        c = http.client.HTTPConnection("127.0.0.1", app.server_port, timeout=10)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        c.request("POST", "/chat", data, {"Content-Type": "application/json", "Authorization": auth})
        r = c.getresponse()
        return r.status, r.read().decode()

    yield chat, seen, behavior
    app.shutdown()
    catalog.shutdown()


def _forged(sub: str) -> str:
    seg = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg({'sub': sub})}.x"


def test_forged_token_cannot_read_other_users_history(env) -> None:
    chat, seen, _ = env
    assert chat({"message": "비밀", "conversation_id": "c1"})[0] == 200
    # 피해자 sub 를 넣은 위조 토큰 — 예전에는 서명 검증 없이 sub 를 믿어 이력을 넘겼다(DEF-029)
    status, _ = chat({"message": "뭐라고 했지?", "conversation_id": "c1"}, auth=f"Bearer {_forged('uuid-alice')}")
    assert status == 401
    assert len(seen["histories"]) == 1


def test_history_is_per_verified_user(env) -> None:
    chat, seen, _ = env
    chat({"message": "하나", "conversation_id": "c1"})
    chat({"message": "둘", "conversation_id": "c1"})
    chat({"message": "남의 것", "conversation_id": "c1"}, auth="Bearer tok-bob")
    assert [len(h) for _t, h in seen["histories"]] == [0, 2, 0]
    assert srv._conversations.keys() == {("sub:uuid-alice", "c1"), ("sub:uuid-bob", "c1")}


def test_lowercase_bearer_scheme(env) -> None:
    chat, seen, _ = env
    assert chat({"message": "hi"}, auth="bearer tok-alice")[0] == 200
    assert seen["histories"][0][0] == "tok-alice"   # 접두어를 뗀 토큰이 도구 호출로 간다(DEF-033)


@pytest.mark.parametrize("body", [b"[]", b'"hi"', b"3"])
def test_non_object_body_is_400(env, body) -> None:
    chat, _, _ = env
    assert chat(body)[0] == 400


def test_catalog_unreachable_is_502(env, monkeypatch) -> None:
    chat, _, _ = env

    def boom(*_a, **_k):
        raise srv.OwnerLookupError("down")

    monkeypatch.setattr(srv, "_verify_owner", boom)
    assert chat({"message": "hi"})[0] == 502


def test_unexpected_error_sends_error_event_and_telemetry(env) -> None:
    chat, seen, behavior = env
    behavior["raise"] = RuntimeError("도구 응답 파싱 실패")
    status, text = chat({"message": "hi", "conversation_id": "c9"})
    assert status == 200 and '"type": "error"' in text      # 끊긴 스트림 대신 오류 안내(DEF-032)
    assert seen["telemetry"] == ["error"]
    assert ("sub:uuid-alice", "c9") not in srv._conversations


def test_bearer_token_parsing() -> None:
    assert srv._bearer_token("Bearer abc") == "abc"
    assert srv._bearer_token("BEARER  abc ") == "abc"
    assert srv._bearer_token("Basic abc") == ""
    assert srv._bearer_token("") == ""


def test_regenerate_drops_last_exchange(env) -> None:
    """다시 생성은 이력의 마지막 질문·답변을 지우고 같은 질문에 답한다(TODO-068)."""
    chat, seen, _ = env
    chat({"message": "하나", "conversation_id": "c1"})
    chat({"message": "둘", "conversation_id": "c1"})
    chat({"message": "둘", "conversation_id": "c1", "regenerate": True})
    # 세 번째 호출의 이력에는 "둘"·"답:둘" 이 없다
    assert [[t["text"] for t in h] for _t, h in seen["histories"]][2] == ["하나", "답:하나"]
    assert [t["text"] for t in srv._conversations[("sub:uuid-alice", "c1")]] == ["하나", "답:하나", "둘", "답:둘"]


def test_retry_after_error_keeps_previous_exchange(env) -> None:
    """오류로 이력에 남지 않은 질문을 regenerate 로 다시 보내도 앞의 정상 질문·답변은 지우지 않는다(DEF-076)."""
    chat, seen, _ = env
    chat({"message": "하나", "conversation_id": "c2"})
    chat({"message": "실패한 질문", "conversation_id": "c2", "regenerate": True})
    assert [[t["text"] for t in h] for _t, h in seen["histories"]][1] == ["하나", "답:하나"]
    assert [t["text"] for t in srv._conversations[("sub:uuid-alice", "c2")]] == ["하나", "답:하나", "실패한 질문", "답:실패한 질문"]
