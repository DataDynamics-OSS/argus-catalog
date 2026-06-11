# SPDX-License-Identifier: Apache-2.0
"""생성 작업 5종 — describe / summarize / columns / tags / pii.

모든 작업은 같은 흐름을 공유한다:
  컨텍스트 수집(스키마·샘플·용어집) → LLM 생성 → 결과 처리(제안 반입 or 직접 적용)

결과 처리 모드:
  - suggest(기본): suggestions/import 로 반입 → UI 에서 사람이 승인/거절.
  - apply: 데이터셋에 직접 PUT — 신뢰 환경 전용. 단 PII 는 민감 정보라
    apply 모드에서도 항상 제안으로만 남긴다 (사람 승인 강제).
"""

from __future__ import annotations

import json
import logging

from argus_agent.catalog import CatalogClient
from argus_agent.config import AgentConfig
from argus_agent.llm import LLMClient
from argus_agent.prompts import (
    SYSTEM_PROMPT,
    build_columns_prompt,
    build_description_prompt,
    build_pii_prompt,
    build_summary_prompt,
    build_tags_prompt,
)

logger = logging.getLogger(__name__)

# CLI 명령 이름 → 실행할 작업 목록 (generate-all 은 전체)
TASK_NAMES = ("describe", "summarize", "columns", "tags", "pii")


def _gather_context(catalog: CatalogClient, dataset: dict) -> tuple[dict | None, list[dict]]:
    """샘플·용어집 컨텍스트 수집 — 실패해도 생성은 계속한다."""
    sample = catalog.get_sample_rows(dataset["id"])
    terms = catalog.get_glossary_terms()
    return sample, terms


def _submit(catalog: CatalogClient, cfg: AgentConfig, dataset: dict,
            items: list[dict], apply_fields: dict | None = None) -> str:
    """결과 처리 — suggest 면 반입, apply 면 직접 수정 (PII 제외)."""
    if cfg.mode == "apply" and apply_fields:
        catalog.update_dataset(dataset["id"], apply_fields)
        # 직접 적용한 항목은 제안으로 중복 반입하지 않는다
        items = [i for i in items if i.get("_apply_field") is None]
    if items:
        # 내부 표시용 키 제거 후 반입
        clean = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]
        catalog.import_suggestions(dataset["id"], clean, provider="agent", model=cfg.model)
    applied = "직접 적용" if (cfg.mode == "apply" and apply_fields) else ""
    suggested = f"제안 {len(items)}건" if items else ""
    return " + ".join(x for x in (applied, suggested) if x) or "결과 없음"


# ---------------------------------------------------------------------------
# 개별 작업
# ---------------------------------------------------------------------------

def run_describe(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, dataset: dict) -> str:
    """데이터셋 상세 설명 생성."""
    sample, terms = _gather_context(catalog, dataset)
    result = llm.generate(build_description_prompt(dataset, sample, terms), SYSTEM_PROMPT)
    item = {
        "entity_type": "dataset", "generation_type": "description",
        "generated_text": result["text"],
        "prompt_tokens": result["prompt_tokens"], "completion_tokens": result["completion_tokens"],
        "_apply_field": "description" if cfg.mode == "apply" else None,
    }
    return _submit(catalog, cfg, dataset, [item],
                   apply_fields={"description": result["text"]} if cfg.mode == "apply" else None)


def run_summarize(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, dataset: dict) -> str:
    """한 줄 요약 생성 — 서버와 동일하게 summary generation_type 으로 제안."""
    sample, terms = _gather_context(catalog, dataset)
    result = llm.generate(build_summary_prompt(dataset, sample, terms), SYSTEM_PROMPT)
    item = {
        "entity_type": "dataset", "generation_type": "summary",
        "generated_text": result["text"],
        "prompt_tokens": result["prompt_tokens"], "completion_tokens": result["completion_tokens"],
    }
    return _submit(catalog, cfg, dataset, [item])


def run_columns(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, dataset: dict) -> str:
    """컬럼별 설명 생성 — 컬럼명을 schema field id 로 매핑해 제안."""
    sample, terms = _gather_context(catalog, dataset)
    parsed, result = llm.generate_json(build_columns_prompt(dataset, sample, terms), SYSTEM_PROMPT)
    if not isinstance(parsed, list):
        return "컬럼 설명 JSON 파싱 실패 — 생략"

    # 컬럼명 → schema field id (반입 시 entity_id 로 필요)
    field_ids = {f["field_path"].lower(): f["id"] for f in dataset.get("schema_fields", [])}
    items = []
    for col in parsed:
        fid = field_ids.get(str(col.get("name", "")).lower())
        desc = str(col.get("description") or "").strip()
        if not fid or not desc:
            continue
        items.append({
            "entity_type": "column", "entity_id": fid,
            "field_name": col["name"], "generation_type": "description",
            "generated_text": desc,
        })
    return _submit(catalog, cfg, dataset, items)


def run_tags(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, dataset: dict) -> str:
    """태그 추천 — 항상 제안 (태그 체계는 사람이 관리)."""
    sample, terms = _gather_context(catalog, dataset)
    parsed, result = llm.generate_json(build_tags_prompt(dataset, sample, terms), SYSTEM_PROMPT)
    if not isinstance(parsed, list):
        return "태그 JSON 파싱 실패 — 생략"
    tags = [str(t).strip() for t in parsed if str(t).strip()][:6]
    if not tags:
        return "추천 태그 없음"
    item = {
        "entity_type": "tag", "generation_type": "tag_suggestion",
        "generated_text": json.dumps(tags, ensure_ascii=False),
    }
    return _submit(catalog, cfg, dataset, [item])


def run_pii(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig, dataset: dict) -> str:
    """PII 컬럼 감지 — 민감 정보라 모드와 무관하게 항상 제안만 (사람 승인 강제)."""
    sample, _ = _gather_context(catalog, dataset)
    parsed, result = llm.generate_json(build_pii_prompt(dataset, sample), SYSTEM_PROMPT)
    if not isinstance(parsed, list):
        return "PII JSON 파싱 실패 — 생략"
    # 실제 존재하는 컬럼만 통과 (모델 환각 방어)
    known = {f["field_path"].lower() for f in dataset.get("schema_fields", [])}
    findings = [p for p in parsed
                if isinstance(p, dict) and str(p.get("name", "")).lower() in known]
    if not findings:
        return "PII 후보 없음"
    item = {
        "entity_type": "pii", "generation_type": "pii_detection",
        # 서버 apply_suggestion 이 그대로 파싱하는 형식 — [{"name", "pii_type", ...}]
        "generated_text": json.dumps(findings, ensure_ascii=False),
    }
    # suggest 강제 — apply 모드여도 직접 적용하지 않는다
    catalog.import_suggestions(dataset["id"], [item], provider="agent", model=cfg.model)
    return f"PII 제안 {len(findings)}건 (사람 승인 필요)"


TASKS = {
    "describe": run_describe,
    "summarize": run_summarize,
    "columns": run_columns,
    "tags": run_tags,
    "pii": run_pii,
}


def run_for_dataset(catalog: CatalogClient, llm: LLMClient, cfg: AgentConfig,
                    dataset: dict, task_names: list[str]) -> dict:
    """한 데이터셋에 대해 지정한 작업들을 순차 실행 — 실패는 작업 단위로 격리."""
    outcomes: dict[str, str] = {}
    for name in task_names:
        try:
            outcomes[name] = TASKS[name](catalog, llm, cfg, dataset)
            logger.info("  [%s] %s — %s", name, dataset.get("name"), outcomes[name])
        except Exception as e:  # noqa: BLE001 — 한 작업 실패가 나머지를 막지 않도록
            outcomes[name] = f"실패: {e}"
            logger.warning("  [%s] %s — 실패: %s", name, dataset.get("name"), e)
    return outcomes
