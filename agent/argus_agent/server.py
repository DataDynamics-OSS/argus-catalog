"""어시스턴트 HTTP 서버 (serve 모드) — 표준 라이브러리 http.server 기반.

POST /chat  : SSE 스트리밍 채팅 (백엔드가 프록시하는 엔드포인트)
GET  /health: 헬스체크

인증 모델: 백엔드가 사용자의 Bearer 토큰을 그대로 전달하고, 이 서버는
그 토큰으로 카탈로그 API 를 호출한다 — 도구가 보는 데이터는 곧 사용자
권한으로 보이는 데이터다 (권한 위임 없음). 토큰 자체 검증은 카탈로그
API 가 수행하므로 여기서는 하지 않는다.

대화 이력은 인메모리(conversation_id) — 데모 수준. 멀티 인스턴스
확장 시 외부 저장소(redis 등)로 교체 지점.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from argus_agent.assistant import run_assistant
from argus_agent.config import AgentConfig
from argus_agent.llm import LLMClient
from argus_agent.telemetry import Telemetry
from argus_agent.tools import TOOLS, ToolContext

logger = logging.getLogger(__name__)

# conversation_id → [{"role", "text"}] — 최근 20턴 유지
_conversations: dict[str, list[dict]] = {}
_conv_lock = threading.Lock()
_MAX_TURNS = 20
_MAX_CONVERSATIONS = 500


def _get_history(conv_id: str) -> list[dict]:
    with _conv_lock:
        return list(_conversations.get(conv_id, []))


def _append_history(conv_id: str, user_text: str, assistant_text: str) -> None:
    with _conv_lock:
        if len(_conversations) > _MAX_CONVERSATIONS:
            _conversations.clear()  # 단순 용량 가드
        history = _conversations.get(conv_id, [])
        history = (history + [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": assistant_text},
        ])[-_MAX_TURNS:]
        _conversations[conv_id] = history


def make_handler(cfg: AgentConfig):
    """설정을 클로저로 캡처한 핸들러 클래스를 만든다."""

    llm = LLMClient(cfg.llm_url, cfg.model, cfg.llm_api_key,
                    temperature=cfg.temperature, max_tokens=cfg.max_tokens)
    # 셀프-텔레메트리 (A6) — 채팅마다 호출 지표를 레지스트리에 push (베스트에포트)
    telemetry = Telemetry(cfg.api_url, cfg.agent_name, cfg.model, cfg.llm_url,
                          enabled=cfg.telemetry_enabled)
    tool_names = list(TOOLS.keys())

    class Handler(BaseHTTPRequestHandler):
        # 기본 로그(stderr 한 줄)를 logging 으로 우회
        def log_message(self, fmt, *args):  # noqa: N802 — 표준 시그니처
            logger.debug("%s — %s", self.address_string(), fmt % args)

        def _sse_headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def _send_event(self, event: dict) -> None:
            self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                body = json.dumps({"status": "ok", "model": cfg.model}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self):  # noqa: N802
            if self.path != "/chat":
                self.send_error(404)
                return

            # 요청 body 를 먼저 소비한다 — 읽기 전에 에러를 보내면
            # 클라이언트 쪽에서 connection reset 으로 보일 수 있다.
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except (ValueError, json.JSONDecodeError):
                self.send_error(400, "invalid json body")
                return

            # 사용자 토큰 — 백엔드 프록시가 그대로 전달한다
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if not token:
                self.send_error(401, "authorization token required")
                return

            message = str(body.get("message") or "").strip()
            conv_id = body.get("conversation_id") or str(uuid.uuid4())

            self._sse_headers()
            if not message:
                self._send_event({"type": "error", "data": {"reason": "메시지가 비어 있습니다."}})
                return

            tool_ctx = ToolContext(cfg.api_url, token)
            final_text = ""
            # 텔레메트리용 — 지연/토큰/성공여부를 채팅 진행 중 누적한다
            started = time.monotonic()
            status = "success"
            tokens_in = tokens_out = 0
            try:
                for event in run_assistant(llm, tool_ctx, _get_history(conv_id), message):
                    if event["type"] == "_final":
                        final_text = event["data"]["text"]  # 내부용 — 전송하지 않음
                        continue
                    if event["type"] == "usage":
                        event["data"]["conversation_id"] = conv_id
                        tokens_in = event["data"].get("tokens_in") or 0
                        tokens_out = event["data"].get("tokens_out") or 0
                    elif event["type"] == "error":
                        status = "error"
                    self._send_event(event)
            except BrokenPipeError:
                logger.info("클라이언트 연결 종료 (conv=%s)", conv_id)
                return
            if final_text:
                _append_history(conv_id, message, final_text)
            logger.info("chat 완료: conv=%s, 답변 %d자", conv_id, len(final_text))

            # 셀프-텔레메트리 적재 (A6) — 베스트에포트, 채팅 응답에 영향 없음
            telemetry.record(
                token, tool_names=tool_names, status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
                tokens_in=tokens_in, tokens_out=tokens_out, session_id=conv_id,
            )

    return Handler


def serve(cfg: AgentConfig, host: str = "0.0.0.0", port: int = 8930) -> None:
    """어시스턴트 서버 기동 — 스레딩 서버라 동시 대화를 처리한다."""
    server = ThreadingHTTPServer((host, port), make_handler(cfg))
    logger.info("assistant 서버 시작 — http://%s:%d (LLM: %s @ %s)",
                host, port, cfg.model, cfg.llm_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("assistant 서버 종료")
        server.shutdown()
