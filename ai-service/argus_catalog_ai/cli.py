# SPDX-License-Identifier: Apache-2.0
"""argus-catalog-ai CLI — Argus Catalog AI 서비스의 진입점.

두 가지 역할:
    assistant   AI 어시스턴트 서버 — 카탈로그 화면의 채팅을 처리 (tool-use, SSE, 기본 :8930)
    enrich      LLM 으로 메타데이터 생성 — 설명·요약·컬럼·태그·PII (배치 또는 worker 데몬)

사용 예:
    # 어시스턴트 서버
    argus-catalog-ai assistant --port 8930

    # 단일 데이터셋 설명 생성 (제안 모드 — UI 에서 승인)
    argus-catalog-ai enrich describe --urn sakila-mysql.sakila.film.dataset \\
        --api-url http://localhost:4600 --username admin --password '...'

    # 데이터 소스 전체에 모든 작업 일괄 실행
    argus-catalog-ai enrich all --datasource-id sakila-mysql ...

    # worker — 설명 없는 데이터셋을 주기 폴링해 자동 생성
    argus-catalog-ai enrich worker --datasource-id sakila-mysql --poll-interval 600 ...

이전 이름(``argus-agent``)과 이전 명령(``serve``, ``describe`` …, ``generate-all``, ``worker``)도 한 버전 동안
동작하며 경고를 남긴다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from argus_catalog_ai.catalog import CatalogClient
from argus_catalog_ai.config import AgentConfig, load_config
from argus_catalog_ai.llm import LLMClient
from argus_catalog_ai.tasks import TASK_NAMES, run_for_dataset

logger = logging.getLogger("argus_catalog_ai")

PROG = "argus-catalog-ai"
LEGACY_PROG = "argus-agent"
# 작업별 도움말 — enrich 하위 명령과 (deprecated) 최상위 명령이 같이 쓴다
ENRICH_TASKS = (
    ("describe", "데이터셋 상세 설명 생성"),
    ("summarize", "한 줄 요약 생성"),
    ("columns", "컬럼별 설명 생성"),
    ("tags", "태그 추천"),
    ("pii", "PII 컬럼 감지 (항상 제안 — 사람 승인 필요)"),
    ("all", "위 작업 전부 실행"),
)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    """모든 하위 명령이 공유하는 인자 — 미지정 시 환경변수로 폴백 (config.py)."""
    p.add_argument("--urn", help="대상 데이터셋 URN (단일)")
    p.add_argument("--datasource-id", help="데이터 소스 ID (등록된 전체 데이터셋 일괄)")
    p.add_argument("--api-url", default=None, help="카탈로그 API (env: ARGUS_API_URL)")
    p.add_argument("--username", default=None, help="관리자 계정 (env: ARGUS_USERNAME)")
    p.add_argument("--password", default=None, help="비밀번호 (env: ARGUS_PASSWORD)")
    p.add_argument("--llm-url", default=None, help="OpenAI 호환 LLM URL (env: ARGUS_AI_LLM_URL, 기본 ollama)")
    p.add_argument("--model", default=None, help="모델 (env: ARGUS_AI_MODEL, 기본 qwen2.5:7b)")
    p.add_argument("--llm-api-key", default=None, help="LLM API 키 (ollama 는 불필요)")
    p.add_argument("--mode", default=None, choices=["suggest", "apply"],
                   help="suggest=제안 반입(기본, 사람 승인) / apply=직접 적용 (PII 는 항상 제안)")
    p.add_argument("--temperature", default=None, type=float)
    p.add_argument("--max-tokens", default=None, type=int)


def _add_assistant_args(p: argparse.ArgumentParser) -> None:
    """assistant(구 serve) 명령 인자 — 공통 인자 + 바인드 주소·포트·텔레메트리."""
    _add_common_args(p)
    p.add_argument("--host", default="0.0.0.0", help="바인드 주소 (기본 0.0.0.0)")
    p.add_argument("--port", default=8930, type=int, help="포트 (기본 8930)")
    # 셀프-텔레메트리 (A6) — 미지정 시 환경변수로 폴백 (config.py)
    p.add_argument("--agent-name", default=None,
                   help="AI Agent 레지스트리 이름 (env: ARGUS_AI_AGENT_NAME, 기본 argus-catalog-assistant)")
    p.add_argument("--telemetry", default=None, choices=["on", "off"],
                   help="셀프-텔레메트리 등록/적재 on/off (env: ARGUS_AI_TELEMETRY, 기본 on)")


def _add_worker_args(p: argparse.ArgumentParser) -> None:
    """enrich worker 명령 인자 — 공통 인자 + 폴링 주기·대상 기준·1회 실행."""
    _add_common_args(p)
    p.add_argument("--poll-interval", default=None, type=int,
                   help="폴링 주기 초 (env: ARGUS_AI_POLL_INTERVAL, 기본 300)")
    p.add_argument("--worker-target", default=None, choices=["missing"],
                   help="대상 선정 기준 — missing: 설명이 비어 있는 데이터셋만 (기본)")
    p.add_argument("--once", action="store_true", help="1회만 폴링하고 종료 (검증/cron 용)")


def build_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성 — assistant / enrich {작업|worker} 와 숨김 처리된 이전 최상위 명령."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Argus Catalog AI 서비스 — AI 어시스턴트 서버와 LLM 메타데이터 생성",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{assistant,enrich}")

    # assistant — 어시스턴트 HTTP 서버 (백엔드가 채팅을 프록시)
    _add_assistant_args(sub.add_parser("assistant", help="AI 어시스턴트 서버 — tool-use 채팅 (SSE)"))

    # enrich — 메타데이터 생성 (배치 작업 + worker)
    en = sub.add_parser("enrich", help="LLM 으로 메타데이터 생성 (설명·요약·컬럼·태그·PII)")
    en_sub = en.add_subparsers(dest="task", required=True)
    for name, help_text in ENRICH_TASKS:
        _add_common_args(en_sub.add_parser(name, help=help_text))
    _add_worker_args(en_sub.add_parser(
        "worker", help="폴링 데몬 — 설명 없는 데이터셋을 주기적으로 자동 생성"))

    # (deprecated) 이전 최상위 명령 — 도움말에는 숨긴다
    _add_assistant_args(sub.add_parser("serve"))
    for name, _ in ENRICH_TASKS[:-1]:
        _add_common_args(sub.add_parser(name))
    _add_common_args(sub.add_parser("generate-all"))
    _add_worker_args(sub.add_parser("worker"))
    return parser


def _normalize(args) -> tuple[str, str | None]:
    """(역할, 작업) 으로 정규화 — 이전 명령이면 경고를 남긴다."""
    if args.command == "assistant":
        return "assistant", None
    if args.command == "enrich":
        return "enrich", args.task
    new = "assistant" if args.command == "serve" else \
        "enrich all" if args.command == "generate-all" else f"enrich {args.command}"
    logger.warning("'%s' 명령은 곧 없어집니다 — '%s %s' 를 쓰세요.", args.command, PROG, new)
    if args.command == "serve":
        return "assistant", None
    return "enrich", "all" if args.command == "generate-all" else args.command


def _resolve_targets(catalog: CatalogClient, args) -> list[dict]:
    """--urn 단일 / --datasource-id 일괄 대상 데이터셋 해석."""
    if args.urn:
        logger.debug("대상 해석: 단일 URN=%s", args.urn)
        return [catalog.get_dataset_by_urn(args.urn)]
    if args.datasource_id:
        # 목록 응답에는 스키마가 없으므로 상세를 다시 읽는다
        summaries = catalog.list_datasets_by_datasource(args.datasource_id)
        logger.debug("대상 해석: datasource=%s count=%d", args.datasource_id, len(summaries))
        return [catalog.get_dataset(s["id"]) for s in summaries]
    raise SystemExit("--urn 또는 --datasource-id 중 하나를 지정하세요.")


def _run_once(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig,
              datasets: list[dict], task_names: list[str]) -> int:
    """대상 데이터셋들에 작업 실행 — 실패 데이터셋 수를 반환 (exit code 용)."""
    failures = 0
    for ds in datasets:
        logger.info("─ %s (id=%d)", ds.get("name"), ds["id"])
        outcomes = run_for_dataset(catalog, llm, cfg, ds, task_names)
        if any(v.startswith("실패") for v in outcomes.values()):
            failures += 1
    return failures


def _worker_loop(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, args) -> None:
    """worker 모드 — 설명 없는 데이터셋을 폴링해 전체 작업(enrich all) 수행.

    이미 제안이 반입된 데이터셋도 description 이 채워지기 전까지 대상에
    남지만, 같은 내용의 제안이 중복 적재되는 것을 막기 위해 한 번 처리한
    데이터셋 id 는 프로세스 생존 동안 건너뛴다.
    """
    processed: set[int] = set()
    logger.info("worker 시작 — 주기 %ds, 대상 기준: %s", cfg.poll_interval, cfg.worker_target)
    while True:
        try:
            summaries = catalog.list_datasets_by_datasource(args.datasource_id)
            # 설명이 비어 있고 아직 처리하지 않은 데이터셋만
            targets = [s for s in summaries
                       if not (s.get("description") or "").strip() and s["id"] not in processed]
            if targets:
                logger.info("worker: 대상 %d건 발견", len(targets))
            else:
                logger.debug("worker: 대상 없음 — 전체 %d건, 처리됨 %d건", len(summaries), len(processed))
            for s in targets:
                ds = catalog.get_dataset(s["id"])
                run_for_dataset(catalog, llm, cfg, ds, list(TASK_NAMES))
                processed.add(s["id"])
        except Exception as e:  # noqa: BLE001 — 루프 보호
            logger.warning("worker tick 실패: %s", e)
        if args.once:
            logger.info("worker --once 완료")
            return
        time.sleep(cfg.poll_interval)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — 종료 코드 반환 (0 성공, 1 일부 데이터셋 실패, 2 필수 인자 누락)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.path.basename(sys.argv[0]) == LEGACY_PROG:
        logger.warning("'%s' 명령 이름은 곧 없어집니다 — '%s' 를 쓰세요.", LEGACY_PROG, PROG)
    args = build_parser().parse_args(argv)
    role, task = _normalize(args)
    cfg = load_config(args)

    # assistant 는 카탈로그 계정이 불필요 — 사용자의 토큰을 그대로 위임받는다
    if role == "assistant":
        from argus_catalog_ai.server import serve
        serve(cfg, host=args.host, port=args.port)
        return 0

    if not cfg.password:
        print("비밀번호가 필요합니다 — --password 또는 ARGUS_PASSWORD", file=sys.stderr)
        return 2

    catalog = CatalogClient(cfg.api_url, cfg.username, cfg.password)
    llm = LLMClient(cfg.llm_url, cfg.model, cfg.llm_api_key,
                    temperature=cfg.temperature, max_tokens=cfg.max_tokens)
    logger.info("LLM: %s @ %s / 모드: %s", cfg.model, cfg.llm_url, cfg.mode)

    if task == "worker":
        if not args.datasource_id:
            print("worker 모드는 --datasource-id 가 필요합니다.", file=sys.stderr)
            return 2
        _worker_loop(catalog, llm, cfg, args)
        return 0

    task_names = list(TASK_NAMES) if task == "all" else [task]
    datasets = _resolve_targets(catalog, args)
    failures = _run_once(catalog, llm, cfg, datasets, task_names)
    logger.info("완료 — 데이터셋 %d건 중 실패 %d건", len(datasets), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
