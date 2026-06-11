"""AI 메타데이터 생성 API 엔드포인트.

LLM 기반의 데이터셋/컬럼 설명 자동 생성, 태그 제안, PII 탐지
엔드포인트를 제공한다.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.schemas import (
    AIStatsResponse,
    ApplyRejectResponse,
    ApplySuggestionsRequest,
    ApplySuggestionsResponse,
    BulkGenerateRequest,
    BulkGenerateResponse,
    GenerateAllResponse,
    GenerateColumnsResponse,
    GenerateDescriptionResponse,
    GenerateRequest,
    GenerateSummaryResponse,
    PIIDetectionResponse,
    SuggestionItem,
    TagSuggestionResponse,
)
from app.ai.service import (
    apply_suggestion,
    apply_suggestions,
    bulk_generate,
    detect_pii,
    generate_all_for_dataset,
    generate_column_descriptions,
    generate_dataset_description,
    generate_dataset_summary,
    get_ai_stats,
    get_suggestions,
    reject_suggestion,
    suggest_tags,
)
from app.core.auth import AdminUser, CurrentUser
from app.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ---------------------------------------------------------------------------
# AI 어시스턴트 채팅 — SSE 스트리밍 (의사 스트리밍)
#
# provider 인터페이스가 단일 generate() 라서 전체 응답을 받은 뒤 청크로 흘린다.
# 프런트(useAssistantStream)는 text_delta/usage/done/error 이벤트를 소비하므로
# 진짜 토큰 스트리밍으로 교체해도 클라이언트 변경이 없다.
# 대화 이력은 인메모리(프로세스 로컬) — 서버 재시작 시 초기화되는 데모 수준.
# ---------------------------------------------------------------------------

_ASSISTANT_SYSTEM_PROMPT = (
    "당신은 Argus Catalog(데이터 카탈로그)의 AI 어시스턴트입니다. "
    "데이터셋·용어집·품질 관리·리니지 등 카탈로그 사용법과 데이터 관리 일반에 대해 "
    "한국어로 간결하고 정확하게 답합니다. 모르는 내용은 모른다고 답하세요."
)

# conversation_id → [{"role": "user"|"assistant", "text": ...}] (최근 20턴 유지)
_conversations: dict[str, list[dict]] = {}
_MAX_TURNS = 20
_MAX_CONVERSATIONS = 200


def _sse(event: dict) -> str:
    """SSE data 라인 직렬화 — 한 이벤트 = ``data: {...}\n\n``."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/assistant/chat")
async def assistant_chat(body: dict, _user: CurrentUser, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """어시스턴트 채팅 — SSE 스트리밍.

    설정 > AI 어시스턴트에서 활성화 + 에이전트 URL 이 지정돼 있으면 외부
    에이전트(agent/ serve)로 프록시한다 — tool-use(카탈로그 검색·스키마·ERD·
    품질·리니지) 답변. 사용자의 Bearer 토큰을 그대로 전달해 도구가 사용자
    권한으로 동작한다. 비활성/미설정 시 기존 내장 경로(단순 대화) 폴백.
    """
    import asyncio as _asyncio
    import uuid

    from fastapi.responses import StreamingResponse

    from app.ai.registry import get_provider
    from app.core.config import settings as app_settings
    from app.settings.service import get_config_by_category

    message = str(body.get("message") or "").strip()
    conv_id = body.get("conversation_id") or str(uuid.uuid4())

    # ── 에이전트 프록시 경로 ──
    # DB 설정(설정 > AI 어시스턴트)을 우선 적용, URL 이 비어 있으면 config.properties 로 폴백.
    acfg = await get_config_by_category(session, "assistant")
    a_enabled = acfg.get("assistant_enabled", "false").lower() in ("true", "1", "yes")
    agent_url = (acfg.get("assistant_agent_url", "") or "").strip() \
        or (app_settings.assistant_agent_url or "").strip()
    if a_enabled and agent_url:
        import httpx

        token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()

        async def proxy_stream():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                    async with client.stream(
                        "POST", agent_url.rstrip("/") + "/chat",
                        json={"message": message, "conversation_id": body.get("conversation_id")},
                        headers={"Authorization": f"Bearer {token}"},
                    ) as resp:
                        if resp.status_code != 200:
                            detail = (await resp.aread()).decode(errors="replace")[:200]
                            yield _sse({"type": "error", "data": {
                                "reason": f"어시스턴트 에이전트 오류 {resp.status_code}: {detail}"}})
                            return
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            except httpx.HTTPError as e:
                logger.warning("어시스턴트 에이전트 프록시 실패: %s", e)
                yield _sse({"type": "error", "data": {
                    "reason": f"어시스턴트 에이전트에 연결할 수 없습니다 ({agent_url}): {e}"}})

        return StreamingResponse(proxy_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stream():
        if not message:
            yield _sse({"type": "error", "data": {"reason": "메시지가 비어 있습니다."}})
            return
        provider = await get_provider()
        if provider is None:
            yield _sse({"type": "error", "data": {
                "reason": "LLM 제공자가 활성화되어 있지 않습니다 — 설정 > AI 에서 제공자를 구성하세요."}})
            return

        # 대화 이력을 프롬프트로 직렬화 (generate 는 단일 프롬프트 인터페이스)
        history = _conversations.get(conv_id, [])
        lines = [f"{'사용자' if t['role'] == 'user' else '어시스턴트'}: {t['text']}" for t in history]
        lines.append(f"사용자: {message}")
        prompt = "\n".join(lines)

        try:
            result = await provider.generate(
                prompt, system_prompt=_ASSISTANT_SYSTEM_PROMPT,
                temperature=0.5, max_tokens=2048,
            )
        except Exception as e:  # noqa: BLE001 — 호출 실패를 이벤트로 보고
            logger.warning("어시스턴트 채팅 실패: conv=%s, %s", conv_id, e)
            yield _sse({"type": "error", "data": {"reason": f"LLM 호출 실패: {e}"}})
            return

        text = result.get("text") or ""
        # 이력 갱신 (간단한 용량 가드 포함)
        if len(_conversations) > _MAX_CONVERSATIONS:
            _conversations.clear()
        history = (history + [
            {"role": "user", "text": message},
            {"role": "assistant", "text": text},
        ])[-_MAX_TURNS:]
        _conversations[conv_id] = history

        # 의사 스트리밍 — 80자 단위로 끊어 점진 표시
        for i in range(0, len(text), 80):
            yield _sse({"type": "text_delta", "data": {"text": text[i:i + 80]}})
            await _asyncio.sleep(0.02)
        yield _sse({"type": "usage", "data": {
            "tokens_in": result.get("prompt_tokens"),
            "tokens_out": result.get("completion_tokens"),
            "conversation_id": conv_id,
        }})
        yield _sse({"type": "done", "data": {}})
        logger.info("어시스턴트 채팅: conv=%s, in=%s, out=%s",
                    conv_id, result.get("prompt_tokens"), result.get("completion_tokens"))

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# 상태 — LLM 제공자 활성화 여부 (UI 의 AI 메뉴 활성/비활성 판단용)
# ---------------------------------------------------------------------------

@router.get("/status")
async def ai_status(session: AsyncSession = Depends(get_session)):
    """LLM(AI 메타데이터 생성) 활성화 상태를 반환한다.

    설정의 ``llm_enabled`` 와 provider 초기화 여부를 함께 본다 — 설정은 켜져
    있지만 초기화에 실패한 경우(enabled=true, provider=None)도 비활성으로 취급.
    """
    from app.ai.registry import get_provider
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, "llm")
    enabled_flag = cfg.get("llm_enabled", "false").lower() in ("true", "1", "yes")
    provider = await get_provider()
    # AI 어시스턴트(플로팅 챗) 활성화 여부 — 플로팅 챗 노출 게이팅에 사용.
    acfg = await get_config_by_category(session, "assistant")
    assistant_enabled = acfg.get("assistant_enabled", "false").lower() in ("true", "1", "yes")
    return {
        "enabled": enabled_flag and provider is not None,
        "provider": cfg.get("llm_provider") if enabled_flag else None,
        "model": cfg.get("llm_model") if enabled_flag else None,
        "assistant_enabled": assistant_enabled,
    }


# ---------------------------------------------------------------------------
# 데이터셋 단위 생성
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/describe", response_model=GenerateDescriptionResponse)
async def api_generate_description(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """AI 로 테이블 설명을 생성."""
    try:
        result = await generate_dataset_description(
            session, dataset_id,
            apply=body.apply, force=body.force, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/datasets/{dataset_id}/summarize", response_model=GenerateSummaryResponse)
async def api_generate_summary(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """한 줄 요약(summary) AI 생성."""
    try:
        result = await generate_dataset_summary(
            session, dataset_id,
            apply=body.apply, force=body.force, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/datasets/{dataset_id}/describe-columns", response_model=GenerateColumnsResponse)
async def api_generate_column_descriptions(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """AI 로 컬럼 설명을 생성."""
    try:
        result = await generate_column_descriptions(
            session, dataset_id,
            apply=body.apply, force=body.force, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/datasets/{dataset_id}/suggest-tags", response_model=TagSuggestionResponse)
async def api_suggest_tags(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """AI 로 데이터셋의 태그를 제안."""
    try:
        result = await suggest_tags(
            session, dataset_id,
            apply=body.apply, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/datasets/{dataset_id}/detect-pii", response_model=PIIDetectionResponse)
async def api_detect_pii(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """AI 로 데이터셋의 PII 컬럼을 탐지."""
    try:
        result = await detect_pii(
            session, dataset_id, apply=body.apply, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/datasets/{dataset_id}/generate-all", response_model=GenerateAllResponse)
async def api_generate_all(_guard: CurrentUser,
    dataset_id: int,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋에 대한 모든 AI 생성 작업을 실행."""
    try:
        result = await generate_all_for_dataset(
            session, dataset_id,
            apply=body.apply, force=body.force, language=body.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# 일괄 생성
# ---------------------------------------------------------------------------

@router.post("/bulk-generate", response_model=BulkGenerateResponse)
async def api_bulk_generate(_guard: CurrentUser,
    body: BulkGenerateRequest,
    session: AsyncSession = Depends(get_session),
):
    """여러 데이터셋에 대한 메타데이터를 일괄 생성."""
    try:
        result = await bulk_generate(
            session,
            generation_types=body.generation_types,
            apply=body.apply,
            language=body.language,
            datasource_id=body.datasource_id,
            empty_only=body.empty_only,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# 제안 관리
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/suggestions/import")
async def import_suggestions(
    dataset_id: int,
    body: dict,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_session),
):
    """외부 에이전트가 생성한 제안 반입 (admin 전용).

    body: {"provider": "...", "model": "...", "items": [
        {"entity_type": "dataset|column|tag|pii", "entity_id": <id>,
         "field_name": "...", "generation_type": "...", "generated_text": "..."}]}
    반입된 제안은 UI 의 AI 제안 목록에서 사람이 승인/거절한다.
    """
    try:
        return await service.import_suggestions(
            session, dataset_id,
            items=body.get("items") or [],
            provider=str(body.get("provider") or "external"),
            model=str(body.get("model") or "unknown"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/datasets/{dataset_id}/suggestions", response_model=list[SuggestionItem])
async def api_get_suggestions(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 미적용 AI 제안을 조회."""
    return await get_suggestions(session, dataset_id)


@router.post("/suggestions/{suggestion_id}/apply", response_model=ApplyRejectResponse)
async def api_apply_suggestion(_guard: CurrentUser,
    suggestion_id: int,
    session: AsyncSession = Depends(get_session),
):
    """특정 AI 제안을 적용."""
    try:
        return await apply_suggestion(session, suggestion_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/suggestions/apply-batch", response_model=ApplySuggestionsResponse)
async def api_apply_suggestions(_guard: CurrentUser,
    body: ApplySuggestionsRequest,
    session: AsyncSession = Depends(get_session),
):
    """여러 AI 제안을 한 번에 적용 — 컬럼 설명 일괄 적용 등."""
    return await apply_suggestions(session, body.suggestion_ids)


@router.post("/suggestions/{suggestion_id}/reject", response_model=ApplyRejectResponse)
async def api_reject_suggestion(_guard: CurrentUser,
    suggestion_id: int,
    session: AsyncSession = Depends(get_session),
):
    """특정 AI 제안을 거절하고 삭제."""
    try:
        return await reject_suggestion(session, suggestion_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=AIStatsResponse)
async def api_get_ai_stats(session: AsyncSession = Depends(get_session)):
    """AI 생성 통계와 커버리지 지표를 반환."""
    return await get_ai_stats(session)
