# SPDX-License-Identifier: Apache-2.0
"""AI 서비스 설정 — 우선순위: CLI 인자 > 환경변수 > 기본값.

Docker 에서는 환경변수(.env), 네이티브에서는 CLI 인자가 자연스럽도록
두 경로를 모두 지원한다. 모든 설정은 이 모듈을 통해서만 읽는다.

환경변수는 ``ARGUS_AI_*`` 를 쓴다. 이전 이름(``AGENT_*``, argus-agent 시절)도 한 버전 동안
읽으며, 그때는 경고를 남긴다 (새 이름이 있으면 새 이름이 우선).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("argus_catalog_ai")

# 새 이름 → 이전 이름 (argus-agent). 이전 이름은 deprecated — 다음 MINOR 에서 제거한다.
LEGACY_ENV = {
    "ARGUS_AI_LLM_URL": "AGENT_LLM_URL",
    "ARGUS_AI_MODEL": "AGENT_MODEL",
    "ARGUS_AI_LLM_API_KEY": "AGENT_LLM_API_KEY",
    "ARGUS_AI_MODE": "AGENT_MODE",
    "ARGUS_AI_TEMPERATURE": "AGENT_TEMPERATURE",
    "ARGUS_AI_MAX_TOKENS": "AGENT_MAX_TOKENS",
    "ARGUS_AI_POLL_INTERVAL": "AGENT_POLL_INTERVAL",
    "ARGUS_AI_WORKER_TARGET": "AGENT_WORKER_TARGET",
    "ARGUS_AI_AGENT_NAME": "AGENT_NAME",
    "ARGUS_AI_TELEMETRY": "AGENT_TELEMETRY",
}


def env(key: str, default: str) -> str:
    """환경변수 — 새 이름(ARGUS_AI_*) 우선, 없으면 이전 이름(AGENT_*)을 경고와 함께 읽는다."""
    if key in os.environ:
        return os.environ[key]
    legacy = LEGACY_ENV.get(key)
    if legacy and legacy in os.environ:
        logger.warning("환경변수 %s 는 곧 없어집니다 — %s 를 쓰세요.", legacy, key)
        return os.environ[legacy]
    return default


@dataclass
class AgentConfig:
    """AI 서비스 전역 설정."""

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
        return env(env_key, default)

    return AgentConfig(
        api_url=pick(getattr(args, "api_url", None), "ARGUS_API_URL", "http://localhost:4600"),
        username=pick(getattr(args, "username", None), "ARGUS_USERNAME", "admin"),
        password=pick(getattr(args, "password", None), "ARGUS_PASSWORD", ""),
        llm_url=pick(getattr(args, "llm_url", None), "ARGUS_AI_LLM_URL", "http://localhost:11434/v1"),
        model=pick(getattr(args, "model", None), "ARGUS_AI_MODEL", "qwen2.5:7b"),
        llm_api_key=pick(getattr(args, "llm_api_key", None), "ARGUS_AI_LLM_API_KEY", "ollama"),
        mode=pick(getattr(args, "mode", None), "ARGUS_AI_MODE", "suggest"),
        temperature=float(pick(getattr(args, "temperature", None), "ARGUS_AI_TEMPERATURE", "0.3")),
        max_tokens=int(pick(getattr(args, "max_tokens", None), "ARGUS_AI_MAX_TOKENS", "2048")),
        poll_interval=int(pick(getattr(args, "poll_interval", None), "ARGUS_AI_POLL_INTERVAL", "300")),
        worker_target=pick(getattr(args, "worker_target", None), "ARGUS_AI_WORKER_TARGET", "missing"),
        agent_name=pick(getattr(args, "agent_name", None), "ARGUS_AI_AGENT_NAME", "argus-catalog-assistant"),
        # 문자열 "false"/"0"/"no" 는 비활성으로 해석 (그 외에는 활성)
        telemetry_enabled=pick(
            getattr(args, "telemetry", None), "ARGUS_AI_TELEMETRY", "true"
        ).strip().lower() not in ("false", "0", "no", "off"),
    )
