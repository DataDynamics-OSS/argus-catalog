# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 서술형 항목 초안 (TODO-096 6단계).

편향(``data_biases``) · 한계(``known_limitations``) · 관련법령(``legal_basis``)은 사람이 글로 적어야 하는 AI-Ready 항목이다.
LLM 이 데이터셋의 메타데이터와 프로파일 통계를 근거로 초안을 쓰고, 기존 AI 제안 흐름(``AIGenerationLog``,
``generation_type="ai_ready"``, ``field_name`` 이 항목)으로 남긴다. 담당자가 제안 목록에서 적용해야 데이터셋에 반영된다.

- 근거는 메타데이터 · 스키마 · 최신 프로파일 통계 · PII 분류 · 리니지 이름뿐이다. **샘플 행은 넣지 않는다.** 값 분포(상위 값)는
  PII 로 분류되지 않은 범주 컬럼(고유값이 적은 컬럼)만 넣는다.
- 관련법령은 설정 › AI-Ready 의 **법령 목록** 안에서만 고른다(``ai_ready_laws``, 한 줄에 하나 — ``법령명 | 해당하는 경우``).
  목록이 비어 있으면 관련법령은 만들지 않는다. LLM 이 목록에 없는 이름을 내면 버린다 — 없는 법령을 지어내지 못하게.
- 이미 값이 있는 항목은 ``force`` 가 아니면 건너뛴다.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIGenerationLog
from app.ai.registry import get_provider
from app.catalog.models import Dataset, DatasetSchema, Datasource

logger = logging.getLogger(__name__)

GENERATION_TYPE = "ai_ready"
# 초안을 만드는 항목 — (데이터셋 컬럼, 화면 이름)
FIELDS: dict[str, str] = {
    "data_biases": "알려진 편향",
    "known_limitations": "한계 · 유의사항",
    "legal_basis": "관련법령",
}
SETTING_LAWS = "ai_ready_laws"
MAX_LAWS = 100
_MAX_TEXT = 2000             # 편향 · 한계 초안 길이 상한
_MAX_COLUMNS = 80            # 프롬프트에 싣는 컬럼 수 상한
_CATEGORY_MAX_DISTINCT = 30  # 상위 값을 싣는 범주 컬럼의 고유값 수 상한
_TOP_VALUES = 5

SYSTEM_PROMPT = (
    "You help public-sector data stewards document datasets for AI use, following the Korean "
    "'AI-friendly public data management guideline'. You only state what the given metadata and statistics support. "
    "Always respond with valid JSON only, no markdown fences or extra text."
)


def parse_laws(text: str | None) -> list[tuple[str, str]]:
    """설정의 법령 목록 → [(법령명, 해당하는 경우)]. 한 줄에 하나, ``|`` 뒤는 언제 해당하는지(선택). ``#`` 로 시작하면 주석.

    같은 이름은 한 번만, 최대 ``MAX_LAWS`` 개.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, when = line.partition("|")
        name, when = _norm(name), when.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, when))
        if len(out) >= MAX_LAWS:
            break
    return out


def check_laws(text: str | None) -> str:
    """설정 저장 전 검증 — 정리한 목록 문자열. 법령명이 200자를 넘으면 ``ValueError``."""
    laws = parse_laws(text)
    for name, _ in laws:
        if len(name) > 200:
            raise ValueError(f"법령명이 너무 깁니다(200자까지): {name[:40]}…")
    return "\n".join(f"{n} | {w}" if w else n for n, w in laws)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().strip("「」『』\"'")


def pick_laws(chosen, laws: list[tuple[str, str]]) -> list[str]:
    """LLM 이 고른 이름 → 목록에 있는 법령명(목록 순서, 중복 없음). 띄어쓰기 · 낫표 차이는 같은 이름으로 본다."""
    if isinstance(chosen, str):
        chosen = [c for c in re.split(r"[,\n]", chosen)]
    if not isinstance(chosen, list):
        return []
    want = {_norm(c).replace(" ", "") for c in chosen if isinstance(c, str)}
    return [name for name, _ in laws if name.replace(" ", "") in want]


def _plain(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&nbsp;", " ").strip()


async def _context(session: AsyncSession, dataset_id: int) -> dict | None:
    """프롬프트 재료 — 메타데이터, 컬럼(타입 · PII 분류 · 결측률 · 고유값 · 범위 · 범주 컬럼의 상위 값), 리니지 이름."""
    row = (await session.execute(
        select(Dataset, Datasource.type, Datasource.name)
        .outerjoin(Datasource, Datasource.id == Dataset.datasource_id)
        .where(Dataset.id == dataset_id)
    )).first()
    if row is None:
        return None
    ds, source_type, source_name = row
    fields = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.ordinal)
    )).scalars().all()

    from app.quality.service import get_latest_profile
    profile = await get_latest_profile(session, dataset_id)
    stats = {c.column_name: c for c in (profile.columns if profile else [])}

    columns = []
    for f in fields[:_MAX_COLUMNS]:
        col = {"name": f.field_path, "type": f.native_type or f.field_type}
        if f.description:
            col["description"] = _plain(f.description)[:200]
        if f.pii_type:
            col["pii"] = f.pii_type
        s = stats.get(f.field_path)
        if s is not None and s.total_count:
            col["null_pct"] = round(s.null_percent, 1)
            col["distinct"] = s.unique_count
            if s.min_value is not None and not f.pii_type:
                col["range"] = [str(s.min_value)[:40], str(s.max_value)[:40]]
            # 범주 분포 — PII 가 아니고 고유값이 적은 컬럼만(값 자체가 개인을 가리키지 않게)
            if not f.pii_type and s.top_values and 0 < s.unique_count <= _CATEGORY_MAX_DISTINCT:
                col["top_values"] = [
                    {"value": str(t.get("value"))[:40], "pct": round(int(t.get("count") or 0) / s.total_count * 100, 1)}
                    for t in s.top_values[:_TOP_VALUES]
                ]
        columns.append(col)

    from app.ai.service import _load_lineage_context
    lineage = await _load_lineage_context(session, dataset_id)
    meta = {
        "name": ds.name,
        "source": f"{source_name} ({source_type})" if source_name else None,
        "summary": ds.summary,
        "description": _plain(ds.description)[:1500] or None,
        "row_count": profile.row_count if profile else None,
        "profiled_at": profile.profiled_at.date().isoformat() if profile else None,
        "update_frequency": ds.ingestion_frequency,
        "temporal_coverage": [str(ds.temporal_start) if ds.temporal_start else None,
                              str(ds.temporal_end) if ds.temporal_end else None],
        "spatial_coverage": ds.spatial_coverage,
        "missing_data_info": ds.missing_data_info,
        "privacy_measures": ds.privacy_measures,
        "allowed_uses": ds.allowed_uses,
        "contains_synthetic": ds.contains_synthetic,
        "contains_machine_annotation": ds.contains_machine_annotation,
        "upstream": lineage.get("upstream") or None,
    }
    return {
        "dataset": ds,
        "meta": {k: v for k, v in meta.items() if v not in (None, "", [None, None])},
        "columns": columns,
        "columns_omitted": max(0, len(fields) - _MAX_COLUMNS),
        "profiled": profile is not None,
    }


def build_prompt(ctx: dict, fields: list[str], laws: list[tuple[str, str]], language: str) -> str:
    """초안 프롬프트. 결과 JSON 키는 요청한 항목만."""
    lang = "Korean" if language.startswith("ko") else language
    parts = [
        "Draft AI-Ready documentation for the dataset below.",
        f"Write in {lang}. Be concrete and short (2-5 bullet-like sentences per field, each on its own line starting with '- ').",
        "Base every statement on the metadata or column statistics given. Do not invent facts about how the data was "
        "collected; if something cannot be told from the evidence, say what the user should check instead.",
        "",
        "== Dataset metadata ==",
        json.dumps(ctx["meta"], ensure_ascii=False, default=str),
        "",
        "== Columns (null_pct = % missing, distinct = distinct values, top_values = share of most frequent values) ==",
        json.dumps(ctx["columns"], ensure_ascii=False, default=str),
    ]
    if ctx["columns_omitted"]:
        parts.append(f"({ctx['columns_omitted']} more columns omitted)")
    if not ctx["profiled"]:
        parts.append("(No profile statistics yet — rely on the schema and metadata only.)")
    parts += ["", "== Fields to write =="]
    keys: dict[str, str] = {}
    if "data_biases" in fields:
        parts.append(
            "data_biases: known or likely biases — skewed category shares, groups/regions/periods that are over- or "
            "under-represented, gaps in the time range, columns whose missing values are concentrated. Mention the column "
            "and the number (e.g. share %) behind each point.")
        keys["data_biases"] = "string"
    if "known_limitations" in fields:
        parts.append(
            "known_limitations: what an AI/ML user must watch out for — high missing rates, coarse granularity, update "
            "frequency, aggregated or sampled data, personal data columns that must be removed or pseudonymized, synthetic "
            "or machine-annotated content, uses the data is not suitable for.")
        keys["known_limitations"] = "string"
    if "legal_basis" in fields:
        parts.append(
            "legal_basis: choose ONLY from the list below the laws that apply to this dataset (by exact name), and give "
            "the reason. Choose nothing if none clearly applies. Never name a law that is not in the list.")
        for name, when in laws:
            parts.append(f"  - {name}" + (f" — applies when: {when}" if when else ""))
        keys["legal_basis"] = "array of law names from the list"
        keys["legal_reason"] = "string"
    keys["confidence"] = "number 0.0-1.0"
    parts += ["", "Respond in JSON: " + json.dumps(keys, ensure_ascii=False)]
    return "\n".join(parts)


def _text(v) -> str:
    if isinstance(v, list):
        v = "\n".join(f"- {str(x).strip().lstrip('- ').strip()}" for x in v if str(x).strip())
    return str(v or "").strip()[:_MAX_TEXT]


async def generate_drafts(
    session: AsyncSession,
    dataset_id: int,
    fields: list[str] | None = None,
    force: bool = False,
    language: str | None = None,
) -> dict:
    """편향 · 한계 · 관련법령 초안을 만들어 적용 대기 제안으로 남긴다(데이터셋에는 반영하지 않음).

    Returns: ``{"dataset_id", "drafts": [{field, label, text, log_id}], "skipped": [{field, label, reason}],
    "legal_reason", "confidence"}``. 만들 항목이 없으면 LLM 을 부르지 않는다.
    Raises: ``ValueError`` — 데이터셋이 없거나 모르는 항목, LLM 호출 · 파싱 실패.
    """
    from app.ai.service import _call_llm, _get_llm_config, _log_entry
    from app.settings.service import get_config_by_category

    wanted = list(dict.fromkeys(fields or FIELDS))
    unknown = [f for f in wanted if f not in FIELDS]
    if unknown:
        raise ValueError(f"초안을 만들 수 없는 항목입니다: {', '.join(unknown)} ({', '.join(FIELDS)})")
    ctx = await _context(session, dataset_id)
    if ctx is None:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")
    ds: Dataset = ctx["dataset"]

    laws = parse_laws((await get_config_by_category(session, "ai_ready")).get(SETTING_LAWS))
    skipped: list[dict] = []
    todo: list[str] = []
    for f in wanted:
        if (getattr(ds, f) or "").strip() and not force:
            skipped.append({"field": f, "label": FIELDS[f], "reason": "이미 값이 있습니다."})
        elif f == "legal_basis" and not laws:
            skipped.append({"field": f, "label": FIELDS[f],
                            "reason": "설정 › AI-Ready 에 관련법령 목록이 없습니다 — 기관이 쓰는 법령을 먼저 적어 주세요."})
        else:
            todo.append(f)
    result = {"dataset_id": dataset_id, "drafts": [], "skipped": skipped, "legal_reason": None, "confidence": None}
    if not todo:
        return result

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")
    prompt = build_prompt(ctx, todo, laws, lang)
    llm = await _call_llm(prompt, system_prompt=SYSTEM_PROMPT, max_tokens=max(int(cfg.get("llm_max_tokens", "1024")), 1500))
    data = llm["data"]
    provider = await get_provider()
    pname = provider.provider_name() if provider else "unknown"
    mname = provider.model_name() if provider else "unknown"

    logs: list[tuple[str, AIGenerationLog]] = []
    for f in todo:
        if f == "legal_basis":
            names = pick_laws(data.get("legal_basis"), laws)
            dropped = data.get("legal_basis")
            if not names:
                reason = "목록의 법령 가운데 해당하는 것을 찾지 못했습니다."
                if dropped:
                    logger.info("AI-Ready 초안: 목록에 없는 법령을 버림 dataset_id=%d chosen=%s", dataset_id, dropped)
                skipped.append({"field": f, "label": FIELDS[f], "reason": reason})
                continue
            text = ", ".join(names)
            result["legal_reason"] = _text(data.get("legal_reason")) or None
        else:
            text = _text(data.get(f))
            if not text:
                skipped.append({"field": f, "label": FIELDS[f], "reason": "AI 가 초안을 쓰지 못했습니다."})
                continue
        log = _log_entry(
            entity_type="dataset", entity_id=dataset_id, dataset_id=dataset_id, generation_type=GENERATION_TYPE,
            generated_text=text, applied=False, provider_name=pname, model_name=mname, field_name=f,
            # 토큰은 한 번의 호출 — 첫 항목에만 적어 통계가 겹치지 않게
            prompt_tokens=None if logs else llm.get("prompt_tokens"),
            completion_tokens=None if logs else llm.get("completion_tokens"),
        )
        session.add(log)
        logs.append((f, log))
    await session.commit()
    for f, log in logs:
        result["drafts"].append({"field": f, "label": FIELDS[f], "text": log.generated_text, "log_id": log.id})
    try:
        result["confidence"] = float(data.get("confidence")) if data.get("confidence") is not None else None
    except (TypeError, ValueError):
        result["confidence"] = None
    logger.info("AI-Ready 초안 생성: dataset_id=%d drafts=%s skipped=%s", dataset_id,
                [d["field"] for d in result["drafts"]], [s["field"] for s in skipped])
    return result


def apply_draft(ds: Dataset, field: str | None, text: str) -> None:
    """제안 적용 — 그 항목에 초안을 그대로 넣는다(사람이 제안 목록에서 고른 것)."""
    if field not in FIELDS:
        raise ValueError(f"AI-Ready 초안 항목이 아닙니다: {field}")
    setattr(ds, field, text)


def missing_filter():
    """일괄 생성 대상 — 세 항목 가운데 하나라도 빈 데이터셋."""
    from sqlalchemy import or_
    return or_(*[(getattr(Dataset, f).is_(None)) | (getattr(Dataset, f) == "") for f in FIELDS])
