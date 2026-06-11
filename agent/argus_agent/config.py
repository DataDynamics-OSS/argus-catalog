"""에이전트 설정 — 우선순위: CLI 인자 > 환경변수 > 기본값.

Docker 에서는 환경변수(.env), 네이티브에서는 CLI 인자가 자연스럽도록
두 경로를 모두 지원한다. 모든 설정은 이 모듈을 통해서만 읽는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AgentConfig:
    """에이전트 전역 설정."""

    # 카탈로그 API
    api_url: str
    username: str
    password: str

    # LLM (OpenAI 호환 — ollama/vLLM/OpenAI/LiteLLM 모두 동일 인터페이스)
    llm_url: str
    model: str
    llm_api_key: str  # ollama 는 임의 문자열이면 됨

    # 동작
    mode: str          # suggest(제안 반입, 사람 승인) | apply(직접 적용)
    temperature: float
    max_tokens: int

    # worker 모드
    poll_interval: int      # 폴링 주기 (초)
    worker_target: str      # 생성 대상 판단: missing(설명 없는 데이터셋만)

    # serve 모드 셀프-텔레메트리 (A6) — 카탈로그의 AI Agent 레지스트리에
    # 자신을 등록하고 호출 지표를 push 한다 (관측/평판). 베스트에포트.
    agent_name: str           # 레지스트리상의 고유 이름
    telemetry_enabled: bool   # 등록/적재 비활성화 스위치 (레지스트리 없는 환경)


def load_config(args) -> AgentConfig:
    """argparse 결과와 환경변수를 병합해 설정을 만든다."""

    def pick(cli_value, env_key: str, default: str) -> str:
        # CLI 인자가 명시됐으면 최우선, 없으면 환경변수, 그래도 없으면 기본값
        if cli_value is not None:
            return str(cli_value)
        return os.environ.get(env_key, default)

    return AgentConfig(
        api_url=pick(getattr(args, "api_url", None), "ARGUS_API_URL", "http://localhost:4600"),
        username=pick(getattr(args, "username", None), "ARGUS_USERNAME", "admin"),
        password=pick(getattr(args, "password", None), "ARGUS_PASSWORD", ""),
        llm_url=pick(getattr(args, "llm_url", None), "AGENT_LLM_URL", "http://localhost:11434/v1"),
        model=pick(getattr(args, "model", None), "AGENT_MODEL", "qwen2.5:7b"),
        llm_api_key=pick(getattr(args, "llm_api_key", None), "AGENT_LLM_API_KEY", "ollama"),
        mode=pick(getattr(args, "mode", None), "AGENT_MODE", "suggest"),
        temperature=float(pick(getattr(args, "temperature", None), "AGENT_TEMPERATURE", "0.3")),
        max_tokens=int(pick(getattr(args, "max_tokens", None), "AGENT_MAX_TOKENS", "2048")),
        poll_interval=int(pick(getattr(args, "poll_interval", None), "AGENT_POLL_INTERVAL", "300")),
        worker_target=pick(getattr(args, "worker_target", None), "AGENT_WORKER_TARGET", "missing"),
        agent_name=pick(getattr(args, "agent_name", None), "AGENT_NAME", "argus-catalog-assistant"),
        # 문자열 "false"/"0"/"no" 는 비활성으로 해석 (그 외에는 활성)
        telemetry_enabled=pick(
            getattr(args, "telemetry", None), "AGENT_TELEMETRY", "true"
        ).strip().lower() not in ("false", "0", "no", "off"),
    )
