"""셀프-텔레메트리 (A6) — 어시스턴트를 카탈로그 AI Agent 레지스트리에 등록하고
호출 지표를 push 한다.

목적: "카탈로그가 자기 AI 어시스턴트를 거버넌스한다." 어시스턴트 채팅마다
지연·토큰·성공 여부를 ``/ai-agents/{name}/invocations`` 로 보내 레지스트리의
미터링/평판 지표에 반영한다.

설계 원칙:
- **베스트에포트**: 모든 실패는 logger.warning 후 무시한다 — 텔레메트리가
  채팅 응답을 절대 막지 않는다 (레지스트리가 없는 환경에서도 안전).
- **권한 위임**: serve 모드는 사용자 토큰으로 동작하므로 등록/적재도 그
  토큰을 그대로 쓴다 (별도 관리자 계정 없음).
- **등록은 프로세스당 1회**: 이름 기준 idempotent — 이미 있으면(409) 정상으로
  보고 건너뛴다. 첫 채팅에서 사용자 토큰을 확보한 뒤 지연 등록한다.

표준 라이브러리만 사용한다 (외부 의존성 0).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _model_provider(llm_url: str) -> str:
    """LLM URL 로 제공자를 추정한다 (레지스트리 메타데이터용, 베스트에포트)."""
    u = llm_url.lower()
    if "openai.com" in u:
        return "openai"
    if "11434" in u or "ollama" in u:
        return "ollama"
    if "anthropic" in u:
        return "anthropic"
    return "custom"


class Telemetry:
    """어시스턴트 셀프-텔레메트리 클라이언트 (serve 모드 전용).

    serve 핸들러가 채팅 1건을 끝낼 때마다 :meth:`record` 를 호출한다.
    내부적으로 최초 1회 등록(:meth:`_ensure_registered`)을 보장한 뒤
    호출 텔레메트리를 push 한다. 모든 네트워크 작업은 베스트에포트다.
    """

    def __init__(self, api_url: str, agent_name: str, model: str, llm_url: str,
                 enabled: bool = True) -> None:
        self.base = api_url.rstrip("/") + "/api/v1"
        self.name = agent_name
        self.model = model
        self.provider = _model_provider(llm_url)
        self.enabled = enabled
        # 등록은 프로세스당 1회 — 동시 채팅 경쟁을 막기 위해 락으로 보호
        self._registered = False
        self._reg_lock = threading.Lock()

    # ------------------------------------------------------------------ 공통
    def _post(self, token: str, path: str, payload: dict) -> dict | None:
        """사용자 토큰으로 POST — 실패는 None 반환(호출부가 베스트에포트 처리)."""
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return json.loads(body) if body else {}

    # ------------------------------------------------------------- 등록
    def _registration_payload(self, tool_names: list[str]) -> dict:
        """레지스트리 create 페이로드 — 어시스턴트의 실제 성격을 그대로 기술."""
        return {
            "name": self.name,
            "display_name": "Argus Catalog 어시스턴트",
            "description": "카탈로그 도구를 호출해 근거 기반으로 답하는 tool-use 채팅 어시스턴트.",
            "status": "active",
            "category": "data-catalog",
            "framework": "argus-agent",
            "base_model": self.model,
            "model_provider": self.provider,
            "protocol": "sse",
            "streaming": True,
            "invocation_method": "http",
            # 거버넌스 — 도구는 읽기 전용, 사용자 권한 위임이라 별도 HITL 없음
            "pii_handling": "read-only",
            "hitl_required": False,
            "supported_languages": ["ko"],
            "capabilities": tool_names,
            "use_cases": ["메타데이터 Q&A", "Text-to-SQL", "품질·리니지 설명", "표준 용어 준수 점검"],
            "tags": ["assistant", "tool-use", "self-registered"],
        }

    def _ensure_registered(self, token: str, tool_names: list[str]) -> None:
        """최초 1회 레지스트리에 등록 — 이미 있으면(409) 정상으로 간주."""
        if self._registered:
            return
        with self._reg_lock:
            if self._registered:  # 락 대기 중 다른 스레드가 끝냈을 수 있다
                return
            try:
                self._post(token, "/ai-agents", self._registration_payload(tool_names))
                logger.info("셀프-등록 완료 — AI Agent 레지스트리: %s", self.name)
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    # 이미 등록됨 — 프로세스 재시작 시 정상 경로
                    logger.info("셀프-등록 생략 — 이미 등록됨: %s", self.name)
                else:
                    detail = e.read().decode(errors="replace")[:200]
                    logger.warning("셀프-등록 실패 %s: %s — 텔레메트리만 시도", e.code, detail)
            except (urllib.error.URLError, OSError) as e:
                logger.warning("셀프-등록 연결 실패 (%s): %s — 텔레메트리 비활성화", self.base, e)
                # 레지스트리에 닿지 못하면 이후 적재도 무의미 — 이 프로세스에선 끈다
                self.enabled = False
                return
            # 등록 시도가 끝났으면(성공/409/4xx) 재시도하지 않는다
            self._registered = True

    # ------------------------------------------------------------- 적재
    def record(self, token: str, *, tool_names: list[str], status: str,
               latency_ms: int, tokens_in: int, tokens_out: int,
               session_id: str | None = None) -> None:
        """채팅 1건의 호출 텔레메트리를 push — 베스트에포트(예외를 던지지 않음)."""
        if not self.enabled or not token:
            return
        try:
            self._ensure_registered(token, tool_names)
            if not self.enabled:  # 등록 단계에서 레지스트리 연결 실패로 꺼졌으면 중단
                return
            self._post(token, f"/ai-agents/{self.name}/invocations", {
                "status": status,
                "latency_ms": latency_ms,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "session_id": session_id,
                "consumer": "assistant-ui",
            })
            logger.info("텔레메트리 적재 — status=%s, latency=%dms, tokens=%d/%d",
                        status, latency_ms, tokens_in, tokens_out)
        except urllib.error.HTTPError as e:
            # 404(등록 실패 후) 등은 베스트에포트라 경고만
            logger.warning("텔레메트리 적재 실패 %s — 무시", e.code)
        except (urllib.error.URLError, OSError) as e:
            logger.warning("텔레메트리 적재 연결 실패: %s — 무시", e)
