"""AI 메타데이터 생성 서비스.

LLM 기반의 데이터셋 설명, 컬럼 설명, 태그 제안, PII 탐지 생성을 오케스트레이션한다.
컨텍스트 조립, LLM 호출, JSON 파싱, 로깅, 그리고 선택적으로 카탈로그 엔티티에
결과를 적용하는 과정을 처리한다.
"""

import json
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIGenerationLog
from app.ai.prompts import (
    SYSTEM_PROMPT,
    build_column_descriptions_prompt,
    build_dataset_description_prompt,
    build_dataset_summary_prompt,
    build_pii_detection_prompt,
    build_tag_suggestion_prompt,
)
from app.ai.registry import get_provider
from app.catalog.models import Dataset, DatasetLineage, DatasetSchema, DatasetTag, Datasource, Tag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 확장 컨텍스트 로더 (용어집, 리니지, few-shot)
# ---------------------------------------------------------------------------

async def _load_glossary_context(session: AsyncSession) -> list[dict]:
    """StandardWord 항목을 약어→이름 매핑으로 로드해 프롬프트 컨텍스트로 사용.

    Returns: [{"abbr": "EQP", "name": "장비", "english": "Equipment"}, ...]
    """
    from app.standard.models import StandardDictionary, StandardWord

    # 활성 사전 조회
    dict_result = await session.execute(
        select(StandardDictionary.id)
        .where(StandardDictionary.status == "ACTIVE")
        .order_by(StandardDictionary.id)
    )
    dict_ids = [r[0] for r in dict_result.all()]
    if not dict_ids:
        return []

    result = await session.execute(
        select(
            StandardWord.word_abbr,
            StandardWord.word_name,
            StandardWord.word_english,
            StandardWord.word_type,
        )
        .where(
            StandardWord.dictionary_id.in_(dict_ids),
            StandardWord.status == "ACTIVE",
        )
        .order_by(StandardWord.word_abbr)
    )

    return [
        {
            "abbr": r.word_abbr,
            "name": r.word_name,
            "english": r.word_english,
            "type": r.word_type,
        }
        for r in result.all()
    ]


async def _load_lineage_context(
    session: AsyncSession, dataset_id: int,
) -> dict[str, list[str]]:
    """리니지 컨텍스트용으로 상류/하류 데이터셋 이름을 로드.

    Returns: {"upstream": ["db.table1", ...], "downstream": ["db.table2", ...]}
    """
    # 상류: 이 데이터셋으로 흘러들어오는 데이터셋
    upstream_result = await session.execute(
        select(Dataset.name)
        .join(DatasetLineage, DatasetLineage.source_dataset_id == Dataset.id)
        .where(DatasetLineage.target_dataset_id == dataset_id)
        .limit(10)
    )
    upstream = [r[0] for r in upstream_result.all()]

    # 하류: 이 데이터셋을 소비하는 데이터셋
    downstream_result = await session.execute(
        select(Dataset.name)
        .join(DatasetLineage, DatasetLineage.target_dataset_id == Dataset.id)
        .where(DatasetLineage.source_dataset_id == dataset_id)
        .limit(10)
    )
    downstream = [r[0] for r in downstream_result.all()]

    return {"upstream": upstream, "downstream": downstream}


async def _load_fewshot_examples(
    session: AsyncSession,
    datasource_type: str,
    generation_type: str,
    exclude_dataset_id: int,
    limit: int = 3,
) -> list[dict]:
    """적용된 AI 생성 결과를 few-shot 예시로 로드.

    동일 데이터소스 타입에서 결과가 적용(승인)된 예시를 선택한다.
    Returns: [{"dataset_name": "...", "generated_text": "..."}, ...]
    """
    result = await session.execute(
        select(
            Dataset.name.label("dataset_name"),
            AIGenerationLog.generated_text,
        )
        .join(Dataset, AIGenerationLog.dataset_id == Dataset.id)
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(
            AIGenerationLog.applied == True,  # noqa: E712
            AIGenerationLog.generation_type == generation_type,
            AIGenerationLog.entity_type == "dataset",
            Datasource.type == datasource_type,
            AIGenerationLog.dataset_id != exclude_dataset_id,
        )
        .order_by(AIGenerationLog.created_at.desc())
        .limit(limit)
    )

    examples = []
    for r in result.all():
        examples.append({
            "dataset_name": r.dataset_name,
            "generated_text": r.generated_text,
        })

    # 적용된 AI 결과가 없으면, 수동으로 설명이 작성된 데이터셋으로 폴백
    if not examples and generation_type == "description":
        manual_result = await session.execute(
            select(Dataset.name, Dataset.description)
            .join(Datasource, Dataset.datasource_id == Datasource.id)
            .where(
                Datasource.type == datasource_type,
                Dataset.description.isnot(None),
                Dataset.description != "",
                Dataset.status != "removed",
                Dataset.id != exclude_dataset_id,
            )
            .order_by(func.length(Dataset.description).desc())
            .limit(limit)
        )
        for r in manual_result.all():
            examples.append({
                "dataset_name": r.name,
                "generated_text": r.description,
            })

    return examples


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict | None:
    """LLM 응답에서 JSON 을 추출 (마크다운 펜스 허용)."""
    # 직접 파싱 시도
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 마크다운 코드 펜스 제거
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 첫 { ... } 블록 탐색 시도
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


async def _call_llm(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> dict:
    """LLM 을 호출하고 JSON 응답을 파싱. 파싱 실패 시 1회 재시도.

    Returns: {"data": parsed_dict, "prompt_tokens": int, "completion_tokens": int}
    제공자가 없거나 재시도 후에도 JSON 파싱이 실패하면 ValueError 발생.
    """
    provider = await get_provider()
    if provider is None:
        raise ValueError("LLM 제공자가 초기화되지 않았습니다. 설정 > AI 에서 활성화하세요.")

    result = await provider.generate(prompt, system_prompt, temperature, max_tokens)
    parsed = _parse_json_response(result["text"])

    if parsed is None:
        # 교정 프롬프트로 재시도
        retry_prompt = (
            "Your previous response was not valid JSON. "
            "Please fix and respond with valid JSON only:\n\n" + result["text"]
        )
        result2 = await provider.generate(retry_prompt, system_prompt, temperature, max_tokens)
        parsed = _parse_json_response(result2["text"])
        if parsed is None:
            raise ValueError(f"LLM 응답을 JSON 으로 파싱하지 못했습니다(재시도 후): {result2['text'][:200]}")
        # 토큰 카운트 합산
        result["prompt_tokens"] = (result.get("prompt_tokens") or 0) + (
            result2.get("prompt_tokens") or 0
        )
        result["completion_tokens"] = (result.get("completion_tokens") or 0) + (
            result2.get("completion_tokens") or 0
        )

    return {
        "data": parsed,
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
    }


async def _get_llm_config(session: AsyncSession) -> dict[str, str]:
    """DB 에서 LLM 설정을 로드."""
    from app.settings.service import get_config_by_category
    return await get_config_by_category(session, "llm")


def _load_sample_data(
    datasource_name: str, dataset_name: str, max_rows: int = 5,
) -> tuple[list[dict] | None, dict[str, list] | None]:
    """디스크의 Parquet 파일에서 샘플 데이터를 로드.

    (sample_rows, sample_values) 를 반환하거나, 없으면 (None, None) 반환.
    """
    from pathlib import Path

    from app.core.config import settings

    parquet_path = (
        Path(settings.data_dir) / "samples" / datasource_name / dataset_name / "sample.parquet"
    )
    if not parquet_path.exists():
        return None, None

    try:
        import pyarrow.parquet as pq

        table = pq.read_table(str(parquet_path)).slice(0, max_rows)
        data = table.to_pydict()

        rows = [
            {col: str(data[col][i]) for col in table.column_names}
            for i in range(table.num_rows)
        ]

        values: dict[str, list] = {
            col: [str(v) for v in data[col]] for col in table.column_names
        }

        return rows, values
    except Exception as e:
        logger.warning("샘플 데이터 로드 실패: %s/%s: %s", datasource_name, dataset_name, e)
        return None, None


async def _get_dataset_context(session: AsyncSession, dataset_id: int) -> dict | None:
    """프롬프트 구성을 위해 데이터셋 + 데이터소스 + 스키마 + 샘플 데이터를 로드."""
    result = await session.execute(
        select(
            Dataset.id, Dataset.name, Dataset.summary, Dataset.description,
            Dataset.qualified_name, Dataset.datasource_properties,
            Datasource.type.label("datasource_type"), Datasource.name.label("datasource_name"),
            Datasource.datasource_id.label("datasource_id_str"),
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id == dataset_id)
    )
    row = result.first()
    if not row:
        return None

    # name 을 database.table 로 분해
    parts = row.name.split(".", 1) if row.name else ["", ""]
    database = parts[0] if len(parts) > 1 else ""
    table_name = parts[1] if len(parts) > 1 else row.name

    # 스키마 필드 로드
    schema_result = await session.execute(
        select(DatasetSchema)
        .where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )
    fields = schema_result.scalars().all()
    columns = [
        {
            "id": f.id,
            "field_path": f.field_path,
            "field_type": f.field_type,
            "native_type": f.native_type,
            "description": f.description,
            "nullable": f.nullable,
            "is_primary_key": f.is_primary_key,
            "is_unique": f.is_unique,
            "is_indexed": f.is_indexed,
            "ordinal": f.ordinal,
        }
        for f in fields
    ]

    # datasource_properties 에서 DDL 과 행 수 파싱
    props = {}
    if row.datasource_properties:
        try:
            props = json.loads(row.datasource_properties)
        except (json.JSONDecodeError, TypeError):
            pass

    # Parquet 파일에서 샘플 데이터 로드
    sample_rows, sample_values = _load_sample_data(
        row.datasource_id_str, row.name,
    )

    return {
        "dataset_id": row.id,
        "name": row.name,
        "table_name": table_name,
        "database": database,
        "summary": row.summary,
        "description": row.description,
        "datasource_type": row.datasource_type,
        "datasource_name": row.datasource_name,
        "columns": columns,
        "ddl": props.get("ddl"),
        "row_count": props.get("estimated_rows"),
        "sample_rows": sample_rows,
        "sample_values": sample_values,
    }


def _log_entry(
    entity_type: str,
    entity_id: int,
    dataset_id: int,
    generation_type: str,
    generated_text: str,
    applied: bool,
    provider_name: str,
    model_name: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    field_name: str | None = None,
) -> AIGenerationLog:
    """AIGenerationLog 항목을 생성."""
    return AIGenerationLog(
        entity_type=entity_type,
        entity_id=entity_id,
        dataset_id=dataset_id,
        field_name=field_name,
        generation_type=generation_type,
        generated_text=generated_text,
        applied=applied,
        provider=provider_name,
        model=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


# ---------------------------------------------------------------------------
# 핵심 생성 함수
# ---------------------------------------------------------------------------

async def generate_dataset_description(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    force: bool = False,
    language: str | None = None,
) -> dict:
    """LLM 을 사용해 데이터셋 설명을 생성.

    Returns: {"dataset_id", "description", "confidence", "applied", "log_id"}
    """
    ctx = await _get_dataset_context(session, dataset_id)
    if not ctx:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    # 이미 설명이 있으면 건너뜀 (force 가 아닌 경우)
    if ctx["description"] and not force:
        return {
            "dataset_id": dataset_id,
            "description": ctx["description"],
            "confidence": 1.0,
            "applied": False,
            "skipped": True,
            "reason": "이미 설명이 존재합니다.",
        }

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")

    # 확장 컨텍스트 로드
    glossary = await _load_glossary_context(session)
    lineage = await _load_lineage_context(session, dataset_id)
    fewshot = await _load_fewshot_examples(
        session, ctx["datasource_type"], "description", dataset_id,
    )

    prompt = build_dataset_description_prompt(
        table_name=ctx["table_name"],
        database=ctx["database"],
        datasource_type=ctx["datasource_type"],
        columns=ctx["columns"],
        ddl=ctx["ddl"],
        sample_rows=ctx.get("sample_rows"),
        row_count=ctx["row_count"],
        language=lang,
        glossary=glossary,
        lineage=lineage,
        fewshot_examples=fewshot,
    )

    llm_result = await _call_llm(prompt, max_tokens=int(cfg.get("llm_max_tokens", "1024")))
    data = llm_result["data"]
    description = data.get("description", "")
    confidence = data.get("confidence", 0.5)

    provider = await get_provider()

    # 생성 결과 로깅
    log = _log_entry(
        entity_type="dataset",
        entity_id=dataset_id,
        dataset_id=dataset_id,
        generation_type="description",
        generated_text=description,
        applied=apply,
        provider_name=provider.provider_name() if provider else "unknown",
        model_name=provider.model_name() if provider else "unknown",
        prompt_tokens=llm_result.get("prompt_tokens"),
        completion_tokens=llm_result.get("completion_tokens"),
    )
    session.add(log)

    # 요청된 경우 적용
    if apply and description:
        ds_result = await session.execute(
            select(Dataset).where(Dataset.id == dataset_id)
        )
        ds = ds_result.scalars().first()
        if ds:
            ds.description = description

    await session.commit()
    await session.refresh(log)

    # 설명이 적용된 경우 임베딩 갱신 트리거
    if apply and description:
        try:
            from app.embedding.service import embed_dataset_background
            await embed_dataset_background(dataset_id)
        except Exception as e:
            logger.warning("AI 설명 적용 후 임베딩 갱신 실패: %s", e)

    return {
        "dataset_id": dataset_id,
        "description": description,
        "confidence": confidence,
        "applied": apply,
        "log_id": log.id,
    }


async def generate_dataset_summary(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    force: bool = False,
    language: str | None = None,
) -> dict:
    """한 줄 요약(summary) 을 LLM 으로 생성.

    이미 summary 가 있고 force=False 면 건너뜀.
    description 이 존재하면 그 의미를 한 줄로 압축. 없으면 컬럼/샘플로 추론.
    Returns: {"dataset_id", "summary", "confidence", "applied", "log_id"}
    """
    ctx = await _get_dataset_context(session, dataset_id)
    if not ctx:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    if ctx.get("summary") and not force:
        return {
            "dataset_id": dataset_id,
            "summary": ctx["summary"],
            "confidence": 1.0,
            "applied": False,
            "skipped": True,
            "reason": "이미 요약이 존재합니다.",
        }

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")

    prompt = build_dataset_summary_prompt(
        table_name=ctx["table_name"],
        database=ctx["database"],
        datasource_type=ctx["datasource_type"],
        columns=ctx["columns"],
        description=ctx.get("description"),
        sample_rows=ctx.get("sample_rows"),
        row_count=ctx["row_count"],
        language=lang,
    )

    llm_result = await _call_llm(prompt, max_tokens=int(cfg.get("llm_max_tokens", "256")))
    data = llm_result["data"]
    summary = (data.get("summary") or "").strip().strip('"').strip("'")
    # 200자 제한 — DB 컬럼 길이를 초과하지 않도록 안전하게 자름
    if len(summary) > 200:
        summary = summary[:200]
    confidence = data.get("confidence", 0.5)

    provider = await get_provider()

    log = _log_entry(
        entity_type="dataset",
        entity_id=dataset_id,
        dataset_id=dataset_id,
        generation_type="summary",
        generated_text=summary,
        applied=apply,
        provider_name=provider.provider_name() if provider else "unknown",
        model_name=provider.model_name() if provider else "unknown",
        prompt_tokens=llm_result.get("prompt_tokens"),
        completion_tokens=llm_result.get("completion_tokens"),
    )
    session.add(log)

    if apply and summary:
        ds_result = await session.execute(
            select(Dataset).where(Dataset.id == dataset_id)
        )
        ds = ds_result.scalars().first()
        if ds:
            ds.summary = summary

    await session.commit()
    await session.refresh(log)

    return {
        "dataset_id": dataset_id,
        "summary": summary,
        "confidence": confidence,
        "applied": apply,
        "log_id": log.id,
    }


async def generate_column_descriptions(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    force: bool = False,
    language: str | None = None,
) -> dict:
    """데이터셋의 모든 컬럼에 대한 설명을 생성.

    Returns: {"dataset_id", "columns": [...], "applied"}
    """
    ctx = await _get_dataset_context(session, dataset_id)
    if not ctx:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    # 설명이 필요한 컬럼 필터링
    columns = ctx["columns"]
    if not force:
        target_columns = [c for c in columns if not c.get("description")]
        if not target_columns:
            return {
                "dataset_id": dataset_id,
                "columns": [],
                "applied": False,
                "skipped": True,
                "reason": "모든 컬럼에 이미 설명이 있습니다.",
            }
    else:
        target_columns = columns

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")

    # 확장 컨텍스트 로드
    glossary = await _load_glossary_context(session)
    lineage = await _load_lineage_context(session, dataset_id)

    prompt = build_column_descriptions_prompt(
        table_name=ctx["table_name"],
        database=ctx["database"],
        table_description=ctx["description"],
        columns=target_columns,
        sample_values=ctx.get("sample_values"),
        language=lang,
        glossary=glossary,
        lineage=lineage,
    )

    llm_result = await _call_llm(
        prompt, max_tokens=min(int(cfg.get("llm_max_tokens", "1024")), 4096)
    )
    data = llm_result["data"]
    col_results = data.get("columns", [])

    provider = await get_provider()
    results = []

    # 결과를 스키마 필드에 다시 매핑
    col_name_map = {c["field_path"].lower(): c for c in target_columns}

    for cr in col_results:
        name = cr.get("name", "")
        desc = cr.get("description", "")
        conf = cr.get("confidence", 0.5)

        col_info = col_name_map.get(name.lower())
        if not col_info or not desc:
            continue

        # 로깅
        log = _log_entry(
            entity_type="column",
            entity_id=col_info["id"],
            dataset_id=dataset_id,
            generation_type="description",
            generated_text=desc,
            applied=apply,
            provider_name=provider.provider_name() if provider else "unknown",
            model_name=provider.model_name() if provider else "unknown",
            prompt_tokens=llm_result.get("prompt_tokens") if not results else None,
            completion_tokens=llm_result.get("completion_tokens") if not results else None,
            field_name=name,
        )
        session.add(log)
        # log.id 확보 — 프론트가 미리보기 후 applySuggestions(log_id) 로 그대로 적용한다.
        await session.flush()

        # 적용
        if apply:
            schema_result = await session.execute(
                select(DatasetSchema).where(DatasetSchema.id == col_info["id"])
            )
            schema = schema_result.scalars().first()
            if schema:
                schema.description = desc

        results.append({
            "field_path": name,
            "description": desc,
            "confidence": conf,
            "had_existing": bool(col_info.get("description")),
            "log_id": log.id,
        })

    await session.commit()

    return {
        "dataset_id": dataset_id,
        "columns": results,
        "total_generated": len(results),
        "applied": apply,
    }


async def suggest_tags(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    language: str | None = None,
) -> dict:
    """데이터셋에 대한 태그를 제안.

    Returns: {"dataset_id", "tags": [...], "new_tags": [...], "applied"}
    """
    ctx = await _get_dataset_context(session, dataset_id)
    if not ctx:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    # 기존 카탈로그 태그 로드
    tag_result = await session.execute(select(Tag.name).order_by(Tag.name))
    existing_tags = [t[0] for t in tag_result.all()]

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")

    # 확장 컨텍스트 로드
    glossary = await _load_glossary_context(session)
    lineage = await _load_lineage_context(session, dataset_id)

    prompt = build_tag_suggestion_prompt(
        table_name=ctx["table_name"],
        database=ctx["database"],
        description=ctx["description"],
        columns=ctx["columns"],
        existing_tags=existing_tags,
        language=lang,
        glossary=glossary,
        lineage=lineage,
    )

    llm_result = await _call_llm(prompt, max_tokens=int(cfg.get("llm_max_tokens", "1024")))
    data = llm_result["data"]
    suggested_tags = data.get("tags", [])
    new_tags = data.get("new_tags", [])

    provider = await get_provider()

    # 로깅
    log = _log_entry(
        entity_type="dataset",
        entity_id=dataset_id,
        dataset_id=dataset_id,
        generation_type="tag_suggestion",
        generated_text=json.dumps({"tags": suggested_tags, "new_tags": new_tags},
                                  ensure_ascii=False),
        applied=apply,
        provider_name=provider.provider_name() if provider else "unknown",
        model_name=provider.model_name() if provider else "unknown",
        prompt_tokens=llm_result.get("prompt_tokens"),
        completion_tokens=llm_result.get("completion_tokens"),
    )
    session.add(log)

    applied_tags = []
    created_tags = []

    if apply:
        # 기존 태그 할당
        for tag_name in suggested_tags:
            tag_row = await session.execute(
                select(Tag).where(func.lower(Tag.name) == tag_name.lower())
            )
            tag = tag_row.scalars().first()
            if tag:
                # 이미 할당되어 있는지 확인
                existing = await session.execute(
                    select(DatasetTag).where(
                        DatasetTag.dataset_id == dataset_id,
                        DatasetTag.tag_id == tag.id,
                    )
                )
                if not existing.scalars().first():
                    session.add(DatasetTag(dataset_id=dataset_id, tag_id=tag.id))
                    applied_tags.append(tag_name)

        # 새 태그 생성 및 할당
        for nt in new_tags:
            name = nt.get("name", "")
            desc = nt.get("description", "")
            if not name:
                continue
            # 태그가 이미 존재하는지 확인
            existing_tag = await session.execute(
                select(Tag).where(func.lower(Tag.name) == name.lower())
            )
            tag = existing_tag.scalars().first()
            if not tag:
                tag = Tag(name=name, description=desc)
                session.add(tag)
                await session.flush()
                created_tags.append(name)
            # 할당
            existing_dt = await session.execute(
                select(DatasetTag).where(
                    DatasetTag.dataset_id == dataset_id,
                    DatasetTag.tag_id == tag.id,
                )
            )
            if not existing_dt.scalars().first():
                session.add(DatasetTag(dataset_id=dataset_id, tag_id=tag.id))
                if name not in applied_tags:
                    applied_tags.append(name)

    await session.commit()
    await session.refresh(log)

    return {
        "dataset_id": dataset_id,
        "suggested_tags": suggested_tags,
        "new_tags": new_tags,
        "applied_tags": applied_tags,
        "created_tags": created_tags,
        "applied": apply,
        "log_id": log.id,
    }


async def detect_pii(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    language: str | None = None,
) -> dict:
    """데이터셋에서 PII 컬럼을 탐지.

    Returns: {"dataset_id", "pii_columns": [...], "applied"}
    """
    ctx = await _get_dataset_context(session, dataset_id)
    if not ctx:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    cfg = await _get_llm_config(session)
    lang = language or cfg.get("llm_language", "ko")

    # 확장 컨텍스트 로드
    glossary = await _load_glossary_context(session)

    prompt = build_pii_detection_prompt(
        table_name=ctx["table_name"],
        database=ctx["database"],
        columns=ctx["columns"],
        sample_values=ctx.get("sample_values"),
        glossary=glossary,
        language=lang,
    )

    llm_result = await _call_llm(prompt, max_tokens=int(cfg.get("llm_max_tokens", "1024")))
    data = llm_result["data"]
    pii_columns = data.get("pii_columns", [])

    provider = await get_provider()

    # 로깅
    log = _log_entry(
        entity_type="dataset",
        entity_id=dataset_id,
        dataset_id=dataset_id,
        generation_type="pii_detection",
        generated_text=json.dumps(pii_columns, ensure_ascii=False),
        applied=apply,
        provider_name=provider.provider_name() if provider else "unknown",
        model_name=provider.model_name() if provider else "unknown",
        prompt_tokens=llm_result.get("prompt_tokens"),
        completion_tokens=llm_result.get("completion_tokens"),
    )
    session.add(log)

    if apply:
        col_name_map = {c["field_path"].lower(): c for c in ctx["columns"]}
        for pii in pii_columns:
            col_name = pii.get("name", "").lower()
            col_info = col_name_map.get(col_name)
            if col_info:
                schema_result = await session.execute(
                    select(DatasetSchema).where(DatasetSchema.id == col_info["id"])
                )
                schema = schema_result.scalars().first()
                if schema:
                    schema.pii_type = pii.get("pii_type")

    await session.commit()
    await session.refresh(log)

    return {
        "dataset_id": dataset_id,
        "pii_columns": pii_columns,
        "applied": apply,
        "log_id": log.id,
    }


async def generate_all_for_dataset(
    session: AsyncSession,
    dataset_id: int,
    apply: bool = False,
    force: bool = False,
    language: str | None = None,
) -> dict:
    """단일 데이터셋에 대해 모든 생성 작업을 실행.

    모든 작업의 결과를 합쳐서 반환한다.
    """
    desc_result = await generate_dataset_description(
        session, dataset_id, apply=apply, force=force, language=language
    )
    col_result = await generate_column_descriptions(
        session, dataset_id, apply=apply, force=force, language=language
    )
    tag_result = await suggest_tags(
        session, dataset_id, apply=apply, language=language
    )
    pii_result = await detect_pii(
        session, dataset_id, apply=apply
    )

    return {
        "dataset_id": dataset_id,
        "description": desc_result,
        "columns": col_result,
        "tags": tag_result,
        "pii": pii_result,
    }


async def bulk_generate(
    session: AsyncSession,
    generation_types: list[str] | None = None,
    apply: bool = False,
    language: str | None = None,
    datasource_id: int | None = None,
    empty_only: bool = True,
) -> dict:
    """여러 데이터셋에 대한 메타데이터를 일괄 생성.

    Args:
        generation_types: 생성할 타입 목록. 기본값: ["description"]
        apply: 결과를 직접 적용할지 여부.
        language: 대상 언어 오버라이드.
        datasource_id: 데이터소스로 필터링.
        empty_only: 설명이 비어 있는 데이터셋만 처리 (기본 True).

    Returns: {"total", "processed", "errors", "results": [...]}
    """
    types = generation_types or ["description"]

    query = select(Dataset.id).where(Dataset.status != "removed")
    if datasource_id:
        query = query.where(Dataset.datasource_id == datasource_id)
    if empty_only:
        query = query.where((Dataset.description.is_(None)) | (Dataset.description == ""))
    query = query.order_by(Dataset.id)

    result = await session.execute(query)
    dataset_ids = [r[0] for r in result.all()]

    total = len(dataset_ids)
    processed = 0
    errors = 0
    results = []

    for ds_id in dataset_ids:
        try:
            ds_result = {}
            if "description" in types:
                ds_result["description"] = await generate_dataset_description(
                    session, ds_id, apply=apply, language=language
                )
            if "columns" in types:
                ds_result["columns"] = await generate_column_descriptions(
                    session, ds_id, apply=apply, language=language
                )
            if "tags" in types:
                ds_result["tags"] = await suggest_tags(
                    session, ds_id, apply=apply, language=language
                )
            if "pii" in types:
                ds_result["pii"] = await detect_pii(
                    session, ds_id, apply=apply
                )
            results.append({"dataset_id": ds_id, **ds_result})
            processed += 1
        except Exception as e:
            errors += 1
            logger.warning("데이터셋 %d 일괄 생성 실패: %s", ds_id, e)
            results.append({"dataset_id": ds_id, "error": str(e)})

    return {
        "total": total,
        "processed": processed,
        "errors": errors,
        "results": results,
    }


# ---------------------------------------------------------------------------
# 제안 관리
# ---------------------------------------------------------------------------

async def get_suggestions(session: AsyncSession, dataset_id: int) -> list[dict]:
    """데이터셋의 미적용 AI 제안을 모두 조회."""
    result = await session.execute(
        select(AIGenerationLog)
        .where(
            AIGenerationLog.dataset_id == dataset_id,
            AIGenerationLog.applied == False,  # noqa: E712
        )
        .order_by(AIGenerationLog.created_at.desc())
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "field_name": log.field_name,
            "generation_type": log.generation_type,
            "generated_text": log.generated_text,
            "provider": log.provider,
            "model": log.model,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


async def _apply_log(session: AsyncSession, log: AIGenerationLog) -> bool:
    """단일 AIGenerationLog 를 실제 엔티티에 반영한다(커밋/임베딩 갱신은 호출자 책임).

    이미 적용된 로그는 건너뛰고 False 를 반환. 반영했으면 True.
    LLM 을 재호출하지 않고 로깅된 generated_text 를 그대로 적용하므로,
    사용자가 미리 본 결과가 그대로 반영된다.
    """
    if log.applied:
        return False

    if log.entity_type == "dataset" and log.generation_type == "description":
        ds_result = await session.execute(
            select(Dataset).where(Dataset.id == log.entity_id)
        )
        ds = ds_result.scalars().first()
        if ds:
            ds.description = log.generated_text

    elif log.entity_type == "column" and log.generation_type == "description":
        schema_result = await session.execute(
            select(DatasetSchema).where(DatasetSchema.id == log.entity_id)
        )
        schema = schema_result.scalars().first()
        if schema:
            schema.description = log.generated_text

    elif log.generation_type == "pii_detection":
        pii_data = json.loads(log.generated_text)
        ctx = await _get_dataset_context(session, log.dataset_id)
        if ctx:
            col_name_map = {c["field_path"].lower(): c for c in ctx["columns"]}
            for pii in (pii_data if isinstance(pii_data, list) else []):
                col_info = col_name_map.get(pii.get("name", "").lower())
                if col_info:
                    schema_result = await session.execute(
                        select(DatasetSchema).where(DatasetSchema.id == col_info["id"])
                    )
                    schema = schema_result.scalars().first()
                    if schema:
                        schema.pii_type = pii.get("pii_type")

    elif log.generation_type == "tag_suggestion":
        # 미리보기 때 로깅한 후보(tags/new_tags)를 그대로 적용 — suggest_tags(apply=True) 와
        # 동일한 할당 로직이지만 LLM 을 재호출하지 않아 미리 본 결과가 그대로 반영된다.
        tag_data = json.loads(log.generated_text)
        suggested_tags = tag_data.get("tags", []) if isinstance(tag_data, dict) else []
        new_tags = tag_data.get("new_tags", []) if isinstance(tag_data, dict) else []
        # 기존 태그 할당
        for tag_name in suggested_tags:
            tag_row = await session.execute(
                select(Tag).where(func.lower(Tag.name) == tag_name.lower())
            )
            tag = tag_row.scalars().first()
            if tag:
                existing = await session.execute(
                    select(DatasetTag).where(
                        DatasetTag.dataset_id == log.dataset_id,
                        DatasetTag.tag_id == tag.id,
                    )
                )
                if not existing.scalars().first():
                    session.add(DatasetTag(dataset_id=log.dataset_id, tag_id=tag.id))
        # 새 태그 생성 및 할당
        for nt in new_tags:
            name = nt.get("name", "")
            desc = nt.get("description", "")
            if not name:
                continue
            existing_tag = await session.execute(
                select(Tag).where(func.lower(Tag.name) == name.lower())
            )
            tag = existing_tag.scalars().first()
            if not tag:
                tag = Tag(name=name, description=desc)
                session.add(tag)
                await session.flush()
            existing_dt = await session.execute(
                select(DatasetTag).where(
                    DatasetTag.dataset_id == log.dataset_id,
                    DatasetTag.tag_id == tag.id,
                )
            )
            if not existing_dt.scalars().first():
                session.add(DatasetTag(dataset_id=log.dataset_id, tag_id=tag.id))

    log.applied = True
    return True


async def apply_suggestion(session: AsyncSession, suggestion_id: int) -> dict:
    """이전에 생성된 제안 1건을 적용."""
    result = await session.execute(
        select(AIGenerationLog).where(AIGenerationLog.id == suggestion_id)
    )
    log = result.scalars().first()
    if not log:
        raise ValueError(f"제안을 찾을 수 없습니다: id={suggestion_id}")

    if not await _apply_log(session, log):
        return {"id": log.id, "already_applied": True}
    await session.commit()

    # 임베딩 갱신 트리거
    try:
        from app.embedding.service import embed_dataset_background
        await embed_dataset_background(log.dataset_id)
    except Exception as e:
        logger.warning("임베딩 갱신 실패: %s", e)

    return {"id": log.id, "applied": True}


async def apply_suggestions(session: AsyncSession, suggestion_ids: list[int]) -> dict:
    """여러 제안을 한 번에 적용 — 컬럼 설명처럼 건별 로그가 다수일 때 사용.

    전부 반영한 뒤 한 번만 커밋하고, 영향받은 데이터셋별로 임베딩을 1회 갱신한다.
    """
    if not suggestion_ids:
        return {"applied_ids": [], "count": 0}

    result = await session.execute(
        select(AIGenerationLog).where(AIGenerationLog.id.in_(suggestion_ids))
    )
    logs = result.scalars().all()

    applied_ids: list[int] = []
    dataset_ids: set[int] = set()
    for log in logs:
        if await _apply_log(session, log):
            applied_ids.append(log.id)
            dataset_ids.add(log.dataset_id)

    await session.commit()

    # 데이터셋별 임베딩 1회 갱신
    if dataset_ids:
        try:
            from app.embedding.service import embed_dataset_background
            for did in dataset_ids:
                await embed_dataset_background(did)
        except Exception as e:
            logger.warning("임베딩 갱신 실패: %s", e)

    return {"applied_ids": applied_ids, "count": len(applied_ids)}


async def import_suggestions(
    session: AsyncSession, dataset_id: int, items: list[dict],
    provider: str, model: str,
) -> dict:
    """외부 에이전트(agent/)가 생성한 제안을 승인 워크플로에 반입한다.

    서버가 직접 생성한 제안과 동일하게 ``AIGenerationLog(applied=False)`` 로
    적재되어, 기존 GET suggestions → apply/reject 흐름을 그대로 탄다.
    ``entity_id`` 는 entity_type 에 따라 dataset.id 또는 schema_field.id —
    apply_suggestion 의 적용 시맨틱과 일치해야 하므로 여기서 존재를 검증한다.
    """
    ds = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalars().first()
    if not ds:
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    # 컬럼 제안 검증용 — field id 집합
    schema_ids = set((await session.execute(
        select(DatasetSchema.id).where(DatasetSchema.dataset_id == dataset_id)
    )).scalars().all())

    created = 0
    for item in items:
        entity_type = item.get("entity_type")
        generation_type = item.get("generation_type")
        text = item.get("generated_text") or ""
        if not text or entity_type not in ("dataset", "column", "tag", "pii"):
            continue
        entity_id = int(item.get("entity_id") or dataset_id)
        # 컬럼 제안은 entity_id 가 이 데이터셋의 schema field 여야 한다
        if entity_type == "column" and entity_id not in schema_ids:
            logger.warning("제안 반입 건너뜀 (알 수 없는 컬럼 id): dataset=%d, entity_id=%d",
                           dataset_id, entity_id)
            continue
        session.add(AIGenerationLog(
            entity_type=entity_type,
            entity_id=entity_id if entity_type == "column" else dataset_id,
            dataset_id=dataset_id,
            field_name=item.get("field_name"),
            generation_type=generation_type or "description",
            generated_text=text,
            applied=False,
            provider=provider,
            model=model,
            prompt_tokens=item.get("prompt_tokens"),
            completion_tokens=item.get("completion_tokens"),
        ))
        created += 1
    await session.commit()
    logger.info("제안 반입 완료 (외부 에이전트): dataset_id=%d, created=%d, provider=%s/%s",
                dataset_id, created, provider, model)
    return {"dataset_id": dataset_id, "created": created}


async def reject_suggestion(session: AsyncSession, suggestion_id: int) -> dict:
    """제안을 거절로 표시 (삭제)."""
    result = await session.execute(
        select(AIGenerationLog).where(AIGenerationLog.id == suggestion_id)
    )
    log = result.scalars().first()
    if not log:
        raise ValueError(f"제안을 찾을 수 없습니다: id={suggestion_id}")

    await session.delete(log)
    await session.commit()
    return {"id": suggestion_id, "rejected": True}


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

async def get_ai_stats(session: AsyncSession) -> dict:
    """AI 생성 통계를 반환."""
    total_generations = (await session.execute(
        select(func.count()).select_from(AIGenerationLog)
    )).scalar() or 0

    applied_count = (await session.execute(
        select(func.count()).select_from(AIGenerationLog)
        .where(AIGenerationLog.applied == True)  # noqa: E712
    )).scalar() or 0

    total_prompt_tokens = (await session.execute(
        select(func.coalesce(func.sum(AIGenerationLog.prompt_tokens), 0))
    )).scalar() or 0

    total_completion_tokens = (await session.execute(
        select(func.coalesce(func.sum(AIGenerationLog.completion_tokens), 0))
    )).scalar() or 0

    # 설명 커버리지
    total_datasets = (await session.execute(
        select(func.count()).select_from(Dataset).where(Dataset.status != "removed")
    )).scalar() or 0

    described_datasets = (await session.execute(
        select(func.count()).select_from(Dataset).where(
            Dataset.status != "removed",
            Dataset.description.isnot(None),
            Dataset.description != "",
        )
    )).scalar() or 0

    by_type = (await session.execute(
        select(AIGenerationLog.generation_type, func.count())
        .group_by(AIGenerationLog.generation_type)
    )).all()

    provider = await get_provider()

    return {
        "total_generations": total_generations,
        "applied_count": applied_count,
        "pending_count": total_generations - applied_count,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "description_coverage": {
            "total_datasets": total_datasets,
            "described_datasets": described_datasets,
            "coverage_pct": round(
                described_datasets / total_datasets * 100, 1
            ) if total_datasets > 0 else 0,
        },
        "by_type": {t: c for t, c in by_type},
        "provider": provider.provider_name() if provider else None,
        "model": provider.model_name() if provider else None,
    }


# ---------------------------------------------------------------------------
# 백그라운드 헬퍼 (sync 연동용)
# ---------------------------------------------------------------------------

async def generate_descriptions_post_sync(dataset_ids: list[int]) -> None:
    """백그라운드 작업: sync 후 데이터셋 설명을 생성.

    자체 DB 세션을 사용한다. 실패는 로깅되지만 전파되지 않는다.
    """
    if not dataset_ids:
        return

    from app.core.database import async_session

    async with async_session() as session:
        cfg = await _get_llm_config(session)
        enabled = cfg.get("llm_enabled", "false").lower() in ("true", "1", "yes")
        auto_sync = cfg.get("llm_auto_generate_on_sync", "false").lower() in ("true", "1", "yes")

        if not enabled or not auto_sync:
            return

        logger.info("sync 후 AI 생성 시작: 데이터셋 %d개", len(dataset_ids))
        for ds_id in dataset_ids:
            try:
                await generate_dataset_description(session, ds_id, apply=True)
            except Exception as e:
                logger.warning("데이터셋 %d sync 후 AI 생성 실패: %s", ds_id, e)
