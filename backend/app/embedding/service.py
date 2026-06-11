"""임베딩 서비스 — 임베딩 생성·저장·관리.

임베딩의 생명주기를 처리한다: 엔티티 메타데이터로부터 원본 텍스트를 구성하고,
활성 제공자를 호출해 벡터를 생성한 뒤, pgvector 테이블에 upsert 한다.

대상 엔티티:
- dataset (``catalog_dataset_embeddings``, 1:1 FK CASCADE)
- glossary_term / ai_agent / api (``catalog_entity_embeddings``, 다형)
"""

import asyncio
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetSchema, Datasource, GlossaryTerm
from app.embedding.models import DatasetEmbedding, EntityEmbedding
from app.embedding.registry import get_provider

logger = logging.getLogger(__name__)

# 다형 임베딩이 지원하는 엔티티 타입 (dataset 은 전용 테이블 사용)
ENTITY_TYPES = ("glossary_term", "ai_agent", "api")

# ---------------------------------------------------------------------------
# 자동 임베딩(embed-on-write) 정책
#
# 기본은 OFF — 생성/수정/sync 같은 쓰기마다 임베딩을 돌리지 않는다(모델 로드+추론
# 부하가 bulk sync 에서 큼). 임베딩은 백필(``embed_all_*``)/스케줄로만 생성하고,
# 검색은 쿼리 임베딩만 lazy 로드한다. ``embedding_auto_on_write`` 설정으로 켤 수 있다.
# ---------------------------------------------------------------------------
_auto_on_write: bool = False

# dataset/entity 단위 디바운스 — 연속 쓰기(예: update_dataset + update_schema_fields)를
# 코얼레싱해 "최종 상태"로 1회만 임베딩한다(이중 트리거 제거).
_pending_embed: dict[str, "asyncio.Task"] = {}
_DEBOUNCE_SECONDS = 0.5


def set_auto_on_write(value: bool) -> None:
    """embed-on-write 활성 여부를 설정한다(설정 로딩 시 호출)."""
    global _auto_on_write
    _auto_on_write = value


def is_auto_on_write() -> bool:
    return _auto_on_write


def _schedule_debounced(key: str, do_embed) -> None:
    """같은 key 의 대기 중 임베딩이 있으면 취소하고 재예약(코얼레싱).

    짧은 디바운스 후 실행하므로, 한 요청 시퀀스의 여러 쓰기가 1회 임베딩으로 합쳐진다.
    """
    existing = _pending_embed.get(key)
    if existing is not None and not existing.done():
        existing.cancel()

    async def _runner():
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            await do_embed()
        except asyncio.CancelledError:
            pass
        finally:
            if _pending_embed.get(key) is asyncio.current_task():
                _pending_embed.pop(key, None)

    _pending_embed[key] = asyncio.create_task(_runner())


async def build_source_text(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋 메타데이터로부터 임베딩할 텍스트를 구성.

    연결 순서: name | summary | description | qualified_name | datasource_name
                  | datasource_type | schema(컬럼 시그니처)
    태그/소유자는 의미 신호로 약해 임베딩 입력에서 제외.
    데이터셋이 존재하지 않으면 None 을 반환.
    """
    result = await session.execute(
        select(Dataset.name, Dataset.summary, Dataset.description, Dataset.qualified_name,
               Datasource.name.label("datasource_name"), Datasource.type.label("datasource_type"))
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(Dataset.id == dataset_id)
    )
    row = result.first()
    if not row:
        return None

    parts = [row.name]
    # ``summary`` 는 한 줄 요약. 검색어와 의미적으로 가장 가까운 신호라서 description 보다 앞.
    if row.summary:
        parts.append(row.summary)
    if row.description:
        parts.append(row.description)
    if row.qualified_name:
        parts.append(row.qualified_name)
    parts.append(row.datasource_name)
    parts.append(row.datasource_type)

    # 스키마 컬럼 시그니처 — 검색어가 컬럼명일 때 매칭이 크게 향상된다.
    # ordinal 순서를 유지하고, 각 행은 ``name TYPE [-- 설명]`` 형태로 평면화.
    schema_result = await session.execute(
        select(DatasetSchema.field_path, DatasetSchema.field_type, DatasetSchema.description)
        .where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )
    field_lines: list[str] = []
    for row_s in schema_result.all():
        line = f"{row_s.field_path} {row_s.field_type}"
        if row_s.description:
            line += f" -- {row_s.description}"
        field_lines.append(line)
    if field_lines:
        parts.append("schema: " + "; ".join(field_lines))

    return " | ".join(parts)


async def embed_dataset(session: AsyncSession, dataset_id: int) -> bool:
    """단일 데이터셋의 임베딩을 생성하고 저장.

    임베딩이 생성/갱신되면 True, 건너뛰면 False 를 반환.
    """
    provider = await get_provider()
    if provider is None:
        return False

    source_text = await build_source_text(session, dataset_id)
    if not source_text:
        logger.warning("데이터셋 %d 임베딩 불가: 찾을 수 없음", dataset_id)
        return False

    # 기존 임베딩이 동일한 원본 텍스트인지 확인 (변경 없으면 건너뜀)
    existing = await session.execute(
        select(DatasetEmbedding.source_text)
        .where(DatasetEmbedding.dataset_id == dataset_id)
    )
    existing_text = existing.scalar()
    if existing_text == source_text:
        return False  # 변경 없음

    # 임베딩 생성
    vectors = await provider.embed([source_text])
    vector = vectors[0]

    # Upsert
    existing_row = await session.execute(
        select(DatasetEmbedding).where(DatasetEmbedding.dataset_id == dataset_id)
    )
    row = existing_row.scalars().first()

    if row:
        row.embedding = vector
        row.source_text = source_text
        row.model_name = provider.model_name()
        row.provider = provider.provider_name()
        row.dimension = provider.dimension()
    else:
        session.add(DatasetEmbedding(
            dataset_id=dataset_id,
            embedding=vector,
            source_text=source_text,
            model_name=provider.model_name(),
            provider=provider.provider_name(),
            dimension=provider.dimension(),
        ))

    await session.commit()
    logger.info("데이터셋 %d 임베딩 완료 (%s, %d 차원)", dataset_id, provider.model_name(), provider.dimension())
    return True


async def embed_dataset_background(dataset_id: int) -> None:
    """쓰기 시 자동 임베딩(embed-on-write) — 정책이 켜져 있을 때만, 디바운스로 1회.

    ``embedding_auto_on_write`` 가 off 면 아무것도 하지 않는다(임베딩은 백필로).
    켜져 있으면 dataset 단위 디바운스로 연속 쓰기를 코얼레싱한다.
    """
    if not _auto_on_write:
        return

    async def _do_embed():
        from app.core.database import async_session
        async with async_session() as session:
            try:
                await embed_dataset(session, dataset_id)
            except Exception as e:
                logger.warning("데이터셋 %d 백그라운드 임베딩 실패: %s", dataset_id, e)

    _schedule_debounced(f"dataset:{dataset_id}", _do_embed)


async def embed_all_datasets(session: AsyncSession) -> dict:
    """모든 활성 데이터셋의 임베딩을 백필(backfill).

    Returns: {"total": N, "embedded": M, "skipped": S, "errors": E}
    """
    provider = await get_provider()
    if provider is None:
        return {"total": 0, "embedded": 0, "skipped": 0, "errors": 0, "error": "No provider"}

    result = await session.execute(
        select(Dataset.id).where(Dataset.status != "removed").order_by(Dataset.id)
    )
    dataset_ids = [r[0] for r in result.all()]
    total = len(dataset_ids)
    embedded = 0
    skipped = 0
    errors = 0

    logger.info("데이터셋 %d개 임베딩 백필 시작", total)

    for ds_id in dataset_ids:
        try:
            if await embed_dataset(session, ds_id):
                embedded += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            logger.warning("데이터셋 %d 임베딩 실패: %s", ds_id, e)

    logger.info("임베딩 백필 완료: total=%d, embedded=%d, skipped=%d, errors=%d",
                total, embedded, skipped, errors)
    return {"total": total, "embedded": embedded, "skipped": skipped, "errors": errors}


async def delete_embedding(session: AsyncSession, dataset_id: int) -> None:
    """데이터셋의 임베딩을 제거."""
    await session.execute(
        delete(DatasetEmbedding).where(DatasetEmbedding.dataset_id == dataset_id)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 비-데이터셋 엔티티 임베딩 (glossary_term / ai_agent / api)
# ---------------------------------------------------------------------------

async def _build_glossary_term_text(session: AsyncSession, term_id: int) -> str | None:
    """용어집 용어: name | description | 상위 분류명."""
    result = await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    )
    term = result.scalars().first()
    if not term:
        return None

    parts = [term.name]
    if term.description:
        parts.append(term.description)
    if term.parent_id:
        parent_name = (await session.execute(
            select(GlossaryTerm.name).where(GlossaryTerm.id == term.parent_id)
        )).scalar()
        if parent_name:
            parts.append(f"상위 분류: {parent_name}")
    return " | ".join(parts)


async def _build_ai_agent_text(session: AsyncSession, agent_id: int) -> str | None:
    """AI Agent: 이름/설명/분류/모델/기능/유스케이스 + 도구(스킬) 시그니처."""
    from app.agents.models import AIAgent, AIAgentTool

    result = await session.execute(select(AIAgent).where(AIAgent.id == agent_id))
    agent = result.scalars().first()
    if not agent:
        return None

    parts = [agent.name]
    if agent.display_name:
        parts.append(agent.display_name)
    if agent.description:
        parts.append(agent.description)
    if agent.category:
        parts.append(agent.category)
    if agent.framework:
        parts.append(agent.framework)
    if agent.base_model:
        parts.append(agent.base_model)
    if agent.capabilities:
        parts.append("capabilities: " + ", ".join(str(c) for c in agent.capabilities))
    if agent.use_cases:
        parts.append("use cases: " + "; ".join(str(u) for u in agent.use_cases))

    # 도구(스킬) 시그니처 — 데이터셋의 스키마 컬럼과 같은 역할의 매칭 신호.
    tools_result = await session.execute(
        select(AIAgentTool.name, AIAgentTool.description)
        .where(AIAgentTool.agent_id == agent_id)
        .order_by(AIAgentTool.id)
    )
    tool_lines = []
    for name, desc in tools_result.all():
        tool_lines.append(f"{name} -- {desc}" if desc else name)
    if tool_lines:
        parts.append("tools: " + "; ".join(tool_lines))

    return " | ".join(parts)


async def _build_api_text(session: AsyncSession, api_id: int) -> str | None:
    """API: 이름/설명/분류/프로토콜/태그 + 엔드포인트 시그니처."""
    from app.apis.models import CatalogApi, CatalogApiEndpoint

    result = await session.execute(select(CatalogApi).where(CatalogApi.id == api_id))
    api = result.scalars().first()
    if not api:
        return None

    parts = [api.name]
    if api.display_name:
        parts.append(api.display_name)
    if api.description:
        parts.append(api.description)
    if api.category:
        parts.append(api.category)
    if api.protocol:
        parts.append(api.protocol)
    if api.tags:
        parts.append("tags: " + ", ".join(str(t) for t in api.tags))

    # 엔드포인트 시그니처 — 검색어가 경로/오퍼레이션명일 때 매칭 향상.
    ep_result = await session.execute(
        select(CatalogApiEndpoint.method, CatalogApiEndpoint.path, CatalogApiEndpoint.summary)
        .where(CatalogApiEndpoint.api_id == api_id)
        .order_by(CatalogApiEndpoint.sort_order, CatalogApiEndpoint.id)
    )
    ep_lines = []
    for method, path, summary in ep_result.all():
        line = f"{method} {path}"
        if summary:
            line += f" -- {summary}"
        ep_lines.append(line)
    if ep_lines:
        parts.append("endpoints: " + "; ".join(ep_lines))

    return " | ".join(parts)


_ENTITY_TEXT_BUILDERS = {
    "glossary_term": _build_glossary_term_text,
    "ai_agent": _build_ai_agent_text,
    "api": _build_api_text,
}


async def build_entity_source_text(
    session: AsyncSession, entity_type: str, entity_id: int,
) -> str | None:
    """엔티티 타입별 임베딩 입력 텍스트 생성. 미지원 타입이면 ValueError."""
    builder = _ENTITY_TEXT_BUILDERS.get(entity_type)
    if builder is None:
        raise ValueError(f"Unsupported entity type for embedding: {entity_type}")
    return await builder(session, entity_id)


async def embed_entity(session: AsyncSession, entity_type: str, entity_id: int) -> bool:
    """단일 엔티티 임베딩 생성/갱신. 변경 없으면 False."""
    provider = await get_provider()
    if provider is None:
        return False

    source_text = await build_entity_source_text(session, entity_type, entity_id)
    if not source_text:
        logger.warning("%s %d 임베딩 불가: 찾을 수 없음", entity_type, entity_id)
        return False

    existing_text = (await session.execute(
        select(EntityEmbedding.source_text).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
        )
    )).scalar()
    if existing_text == source_text:
        return False  # 변경 없음

    vectors = await provider.embed([source_text])
    vector = vectors[0]

    row = (await session.execute(
        select(EntityEmbedding).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
        )
    )).scalars().first()

    if row:
        row.embedding = vector
        row.source_text = source_text
        row.model_name = provider.model_name()
        row.provider = provider.provider_name()
        row.dimension = provider.dimension()
    else:
        session.add(EntityEmbedding(
            entity_type=entity_type,
            entity_id=entity_id,
            embedding=vector,
            source_text=source_text,
            model_name=provider.model_name(),
            provider=provider.provider_name(),
            dimension=provider.dimension(),
        ))

    await session.commit()
    logger.info("%s %d 임베딩 완료 (%s, %d 차원)",
                entity_type, entity_id, provider.model_name(), provider.dimension())
    return True


async def embed_entity_background(entity_type: str, entity_id: int) -> None:
    """엔티티 쓰기 시 자동 임베딩 — 정책이 켜져 있을 때만, 디바운스로 1회."""
    if not _auto_on_write:
        return

    async def _do_embed():
        from app.core.database import async_session
        async with async_session() as session:
            try:
                await embed_entity(session, entity_type, entity_id)
            except Exception as e:
                logger.warning("%s %d 백그라운드 임베딩 실패: %s",
                               entity_type, entity_id, e)

    _schedule_debounced(f"{entity_type}:{entity_id}", _do_embed)


async def delete_entity_embedding(
    session: AsyncSession, entity_type: str, entity_id: int,
) -> None:
    """엔티티 임베딩 행 삭제 (FK CASCADE 가 없으므로 서비스 delete 경로에서 호출).

    호출 측 트랜잭션에 합류한다 — commit 은 호출자가 수행.
    """
    await session.execute(
        delete(EntityEmbedding).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
        )
    )


async def embed_all_entities(session: AsyncSession) -> dict:
    """용어집/AI Agent/API 전체 임베딩 백필.

    Returns: {"<entity_type>": {"total": N, "embedded": M, "skipped": S, "errors": E}, ...}
    """
    provider = await get_provider()
    if provider is None:
        return {t: {"total": 0, "embedded": 0, "skipped": 0, "errors": 0} for t in ENTITY_TYPES}

    from app.agents.models import AIAgent
    from app.apis.models import CatalogApi

    id_queries = {
        "glossary_term": select(GlossaryTerm.id).order_by(GlossaryTerm.id),
        "ai_agent": select(AIAgent.id).where(AIAgent.status != "deleted").order_by(AIAgent.id),
        "api": select(CatalogApi.id).where(CatalogApi.status != "deleted").order_by(CatalogApi.id),
    }

    summary: dict[str, dict] = {}
    for entity_type, query in id_queries.items():
        ids = [r[0] for r in (await session.execute(query)).all()]
        counts = {"total": len(ids), "embedded": 0, "skipped": 0, "errors": 0}
        for eid in ids:
            try:
                if await embed_entity(session, entity_type, eid):
                    counts["embedded"] += 1
                else:
                    counts["skipped"] += 1
            except Exception as e:
                counts["errors"] += 1
                logger.warning("%s %d 임베딩 실패: %s", entity_type, eid, e)
        summary[entity_type] = counts
        logger.info("엔티티 임베딩 백필 (%s): %s", entity_type, counts)

    return summary


async def clear_all_embeddings(session: AsyncSession) -> int:
    """모든 임베딩을 삭제 (예: 제공자 교체 전). 삭제된 행 수를 반환."""
    result = await session.execute(delete(DatasetEmbedding))
    entity_result = await session.execute(delete(EntityEmbedding))
    await session.commit()
    count = result.rowcount + entity_result.rowcount
    logger.info("모든 임베딩 삭제 완료: %d개 행 (dataset=%d, entity=%d)",
                count, result.rowcount, entity_result.rowcount)
    return count


async def get_embedding_stats(session: AsyncSession) -> dict:
    """임베딩 커버리지 통계를 반환."""
    total_datasets = (await session.execute(
        select(func.count()).select_from(Dataset).where(Dataset.status != "removed")
    )).scalar() or 0

    total_embeddings = (await session.execute(
        select(func.count()).select_from(DatasetEmbedding)
    )).scalar() or 0

    provider = await get_provider()

    # 통계 조회 시 모델 로드가 트리거되지 않도록 provider.dimension() 대신
    # DB 설정에서 차원을 읽는다
    from app.settings.service import get_config_by_category
    emb_cfg = await get_config_by_category(session, "embedding")
    dim = int(emb_cfg.get("embedding_dimension", "384")) if emb_cfg else None

    # 엔티티 타입별 임베딩 수 (glossary_term / ai_agent / api)
    entity_counts_result = await session.execute(
        select(EntityEmbedding.entity_type, func.count())
        .group_by(EntityEmbedding.entity_type)
    )
    entity_counts = {t: 0 for t in ENTITY_TYPES}
    entity_counts.update({row[0]: row[1] for row in entity_counts_result.all()})

    return {
        "total_datasets": total_datasets,
        "embedded_datasets": total_embeddings,
        "coverage_pct": round(total_embeddings / total_datasets * 100, 1) if total_datasets > 0 else 0,
        "embedded_entities": entity_counts,
        "provider": provider.provider_name() if provider else None,
        "model": provider.model_name() if provider else None,
        "dimension": dim,
    }
