# SPDX-License-Identifier: Apache-2.0
"""어시스턴트 HTTP 서버 (assistant 명령) — 표준 라이브러리 http.server 기반.

POST /chat  : SSE 스트리밍 채팅 (백엔드가 프록시하는 엔드포인트)
GET  /health: 헬스체크

인증 모델: 백엔드가 사용자의 Bearer 토큰을 그대로 전달하고, 이 서버는
그 토큰으로 카탈로그 API 를 호출한다 — 도구가 보는 데이터는 곧 사용자
권한으로 보이는 데이터다 (권한 위임 없음). 채팅마다 카탈로그 API
``GET /auth/me`` 로 토큰을 검증하고, 검증된 사용자(``sub``)를 대화 이력의
소유자로 쓴다. 검증에 실패한 토큰은 401 (DEF-029). 예전에는 서명을 검증하지
않은 JWT payload 의 ``sub`` 를 믿어, 이 서버에 직접 닿으면 토큰을 꾸며
다른 사용자의 이력을 볼 수 있었다.

대화 이력은 인메모리, 키는 (검증된 사용자, conversation_id) — 다른 사용자의 conversation_id 로는
이력을 볼 수 없다. 대화 수가 상한을 넘으면 가장 오래 쓰지 않은 대화부터 지운다(DEF-002).
멀티 인스턴스 확장 시 외부 저장소(redis 등)로 교체 지점(TODO-034).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from argus_catalog_ai.assistant import run_assistant
from argus_catalog_ai.config import AgentConfig
from argus_catalog_ai.llm import LLMClient
from argus_catalog_ai.telemetry import Telemetry
from argus_catalog_ai.tools import TOOLS, ToolContext

logger = logging.getLogger(__name__)

# (사용자, conversation_id) → [{"role", "text"}] — 최근 20개 메시지(질문·답변 10쌍) 유지, LRU
_conversations: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
_conv_lock = threading.Lock()
_MAX_TURNS = 20
_MAX_CONVERSATIONS = 500


class OwnerLookupError(Exception):
    """카탈로그 API 에 닿지 못해 토큰의 사용자를 확인하지 못함(토큰이 틀린 것과 구분)."""


def _bearer_token(header: str) -> str:
    """``Authorization`` 헤더에서 Bearer 토큰을 꺼낸다 — 스킴 이름은 대소문자를 구분하지 않는다(DEF-033)."""
    scheme, _, value = (header or "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _verify_owner(api_url: str, token: str, timeout: float = 10) -> str | None:
    """카탈로그 API ``GET /auth/me`` 로 토큰을 검증하고 대화 이력 소유자 키를 돌려준다.

    검증된 사용자의 ``sub`` 만 쓴다 — 서명을 검증하지 않은 payload 나 토큰 해시를 쓰면 위조할 수 있고(DEF-029),
    토큰이 갱신될 때 이력이 끊기며(DEF-030), 사용자명과 ``sub`` 가 섞인다(DEF-031).
    토큰이 거부되면(401·403) ``None``, API 에 닿지 못하면 :class:`OwnerLookupError`.
    """
    req = urllib.request.Request(api_url.rstrip("/") + "/api/v1/auth/me",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sub = (json.loads(resp.read()) or {}).get("sub")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None
        raise OwnerLookupError(f"카탈로그 API 응답 {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OwnerLookupError(str(e)) from e
    return f"sub:{sub}" if sub else None


def _get_history(owner: str, conv_id: str) -> list[dict]:
    """대화 이력 사본을 반환한다 (없으면 빈 목록). 읽기도 최근 사용으로 친다."""
    with _conv_lock:
        key = (owner, conv_id)
        if key not in _conversations:
            return []
        _conversations.move_to_end(key)
        return list(_conversations[key])


def _append_history(owner: str, conv_id: str, user_text: str, assistant_text: str,
                    datasets: list[dict] | None = None) -> None:
    """질문·답변 한 쌍을 이력에 추가 — 대화 수가 상한을 넘으면 가장 오래 쓰지 않은 대화부터 지운다.

    ``datasets`` 는 그 답변에서 도구로 확인한 데이터셋 [{"id", "name"}] — 이어지는 질문의 맥락으로 쓴다(TODO-036).
    """
    with _conv_lock:
        key = (owner, conv_id)
        history = (_conversations.pop(key, []) + [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": assistant_text, "datasets": list(datasets or [])},
        ])[-_MAX_TURNS:]
        _conversations[key] = history
        while len(_conversations) > _MAX_CONVERSATIONS:
            _conversations.popitem(last=False)


def _drop_last_exchange(owner: str, conv_id: str, question: str | None = None) -> bool:
    """마지막 질문·답변 한 쌍을 지운다 — 답변 다시 생성(TODO-068). 지웠으면 True.

    ``question`` 을 주면 마지막 질문이 그것과 같을 때만 지운다. 오류·중단으로 끝난 답변은 이력에 없으므로,
    그 질문을 다시 시도하며 regenerate 를 보내도 앞의 정상 질문·답변을 지우지 않는다(DEF-076).
    """
    with _conv_lock:
        history = _conversations.get((owner, conv_id))
        if not history or len(history) < 2 or history[-1]["role"] != "assistant" or history[-2]["role"] != "user":
            return False
        if question is not None and history[-2]["text"] != question:
            return False
        del history[-2:]
        return True


def make_handler(cfg: AgentConfig):
    """설정을 클로저로 캡처한 핸들러 클래스를 만든다."""

    llm = LLMClient(cfg.llm_url, cfg.model, cfg.llm_api_key,
                    temperature=cfg.temperature, max_tokens=cfg.max_tokens)
    # 셀프-텔레메트리 (A6) — 채팅마다 호출 지표를 레지스트리에 push (베스트에포트)
    telemetry = Telemetry(cfg.api_url, cfg.agent_name, cfg.model, cfg.llm_url,
                          enabled=cfg.telemetry_enabled,
                          owner_email=cfg.agent_owner_email, department=cfg.agent_department)
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
                logger.warning("잘못된 요청 본문(JSON 아님) — 400 반환: client=%s", self.address_string())
                self.send_error(400, "invalid json body")
                return
            if not isinstance(body, dict):   # [] · "hi" 같은 JSON 은 객체가 아니다(DEF-033)
                logger.warning("잘못된 요청 본문(객체 아님) — 400 반환: client=%s", self.address_string())
                self.send_error(400, "json body must be an object")
                return

            # 사용자 토큰 — 백엔드 프록시가 그대로 전달한다
            token = _bearer_token(self.headers.get("Authorization", ""))
            if not token:
                logger.warning("인증 토큰 누락 — 401 반환: client=%s", self.address_string())
                self.send_error(401, "authorization token required")
                return
            try:
                owner = _verify_owner(cfg.api_url, token)
            except OwnerLookupError as e:
                logger.warning("토큰 사용자 확인 실패(카탈로그 API) — 502 반환: %s", e)
                self.send_error(502, "cannot verify token with catalog api")
                return
            if owner is None:
                logger.warning("카탈로그 API 가 토큰을 거부 — 401 반환: client=%s", self.address_string())
                self.send_error(401, "invalid token")
                return

            message = str(body.get("message") or "").strip()
            conv_id = str(body.get("conversation_id") or uuid.uuid4())
            if body.get("regenerate") is True:
                dropped = _drop_last_exchange(owner, conv_id, message)
                logger.info("답변 다시 생성: conv=%s 이전 답변 제거=%s", conv_id, dropped)

            self._sse_headers()
            if not message:
                logger.warning("빈 메시지 요청 — 오류 이벤트 반환: conv=%s", conv_id)
                self._send_event({"type": "error", "data": {"reason": "메시지가 비어 있습니다."}})
                return

            tool_ctx = ToolContext(cfg.api_url, token)
            final_text = ""
            final_datasets: list[dict] = []
            # 텔레메트리용 — 지연/토큰/성공여부를 채팅 진행 중 누적한다
            started = time.monotonic()
            status = "success"
            tokens_in = tokens_out = 0
            try:
                for event in run_assistant(llm, tool_ctx, _get_history(owner, conv_id), message):
                    if event["type"] == "_final":
                        final_text = event["data"]["text"]  # 내부용 — 전송하지 않음
                        final_datasets = event["data"].get("datasets") or []
                        continue
                    if event["type"] == "usage":
                        event["data"]["conversation_id"] = conv_id
                        tokens_in = event["data"].get("tokens_in") or 0
                        tokens_out = event["data"].get("tokens_out") or 0
                    elif event["type"] == "error":
                        status = "error"
                    self._send_event(event)
            except ConnectionError:   # BrokenPipe·ConnectionReset 등 — 클라이언트가 끊음(DEF-032)
                logger.info("클라이언트 연결 종료 (conv=%s)", conv_id)
                return
            except Exception:   # noqa: BLE001 — 처리하지 못한 예외도 안내·텔레메트리를 남긴다(DEF-032)
                logger.exception("채팅 처리 중 오류 (conv=%s)", conv_id)
                status, final_text = "error", ""
                try:
                    self._send_event({"type": "error", "data": {"reason": "답변을 만드는 중 오류가 났습니다."}})
                except ConnectionError:
                    pass
            if final_text:
                _append_history(owner, conv_id, message, final_text, final_datasets)
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
