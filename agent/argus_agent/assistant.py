"""어시스턴트 tool-use 루프 — 질문 → 도구 선택·실행 반복 → 최종 답변.

이벤트 제너레이터로 구현되어 SSE 서버(server.py)가 그대로 흘려보낸다.
이벤트 규약은 프런트(use-assistant-stream)와 동일:
  tool_call / tool_result / text_delta / usage / done / error
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from argus_agent.llm import LLMClient
from argus_agent.tools import ToolContext, run_tool, tool_schemas

logger = logging.getLogger(__name__)

# 도구 호출 무한 루프 방지 — 일반 질문은 2~3회면 충분하다
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """당신은 Argus Catalog(데이터 카탈로그)의 AI 어시스턴트입니다. 항상 한국어로만 답합니다 — 중국어(한자 단어)·일본어·영어 문장을 섞지 않습니다 (테이블·컬럼명, SQL 키워드 등 식별자는 원문 유지).

원칙:
1. 데이터셋/테이블/품질/리니지 관련 질문은 반드시 도구로 실제 데이터를 조회해 근거를 갖고 답합니다.
   - 데이터셋 id 를 모르면 **반드시 먼저 search_datasets 로 찾습니다** — id 를 추측하지 마세요.
   - **검색은 짧은 핵심어 하나로** 합니다(예: "대여", "점포"). 한 질문에 대상이 여러 개면
     **각각 따로** 검색합니다(예: "점포별 월 대여" → "점포" 검색 + "대여" 검색).
   - 카탈로그 테이블은 **영문명**일 수 있으니, 한국어로 안 나오면 **영어로도 검색**합니다
     (대여→rental, 점포→store, 고객→customer, 결제→payment, 직원→staff). limit 은 지정하지 않습니다.
   - 테이블을 찾을 때: search_datasets → get_dataset_detail
   - SQL 작성: 필요한 테이블을 각각 찾아 get_dataset_detail(스키마) + 조인이 필요하면
     get_erd(FK 경로) → 작성 → validate_sql 로 자가 검증 후 제시. SQL 은 표준 SQL 코드블록으로,
     사용 테이블·조인 근거를 함께 설명합니다.
   - 품질 질문: get_quality (실패 규칙·위반 샘플 근거로 설명)
   - 품질 규칙 추천 질문("어떤 규칙을 걸까?"): get_quality_rule_recommendations (각 후보의 reason 으로 설명)
   - 출처/영향 질문: get_lineage
   - 용어의 뜻/사내 정의 질문: get_glossary_term
   - 표준 용어·명명 규칙 준수 질문: get_standard_compliance (준수율·비준수 컬럼 근거로 설명)
2. **사용자에게 테이블·컬럼 이름을 되묻지 않습니다** — 도구로 직접 찾습니다. 한 번에 못 찾으면
   검색어(한국어/영어)를 바꿔 다시 시도하고, 충분히 시도한 뒤에만 모른다고 답합니다.
3. 도구로 확인된 사실만 답하고, 확인되지 않은 값은 지어내지 않습니다.
4. SQL 은 조회(SELECT)만 작성합니다 — 변경 쿼리를 요청받으면 정중히 거절합니다.
5. 답변은 간결하게 — 표/코드블록을 적극 사용합니다."""


def _conversation_messages(history: list[dict], message: str) -> list[dict]:
    """이전 대화 + 새 질문으로 OpenAI messages 를 구성한다."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["text"]})
    messages.append({"role": "user", "content": message})
    return messages


def _clean_answer(text: str) -> str:
    """최종 답변에서 도구 호출 누수 흔적을 방어적으로 제거한다.

    ollama 비스트리밍은 도구 호출을 구조화해 주지만, 모델이 본문 끝에 빈
    ```json 코드펜스나 <tool_call> 잔재를 남기는 경우가 있어 가볍게 정리한다.
    """
    t = (text or "").strip()
    for tag in ("<tool_call>", "</tool_call>"):
        t = t.replace(tag, "")
    # 내용 없이 펜스만 남은 꼬리(```json / ```) 제거
    while True:
        stripped = t.rstrip()
        for fence in ("```json", "```"):
            if stripped.endswith(fence):
                t = stripped[: -len(fence)]
                break
        else:
            break
    return t.strip()


def _chunk(text: str, size: int = 30) -> Iterator[str]:
    """최종 답변을 작은 조각으로 나눠 점진 표시(text_delta)에 쓴다."""
    for i in range(0, len(text), size):
        yield text[i:i + size]


def _has_cjk(text: str, threshold: int = 4) -> bool:
    """답변에 중국어(한자)가 섞였는지 — CJK 통합 한자 글자 수가 임계 이상이면 True.

    현대 한국어 카탈로그 답변은 한자를 거의 쓰지 않으므로, 다국어 모델(qwen2.5)이
    간헐적으로 중국어로 드리프트하는 것을 감지하는 데 쓴다 (한글/가나는 제외).
    """
    n = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            n += 1
            if n >= threshold:
                return True
    return False


def _recover_tool_calls(content: str, valid_names: set[str]) -> list[dict]:
    """ollama 가 구조화하지 못해 본문에 흘린 도구 호출 JSON 을 OpenAI tool_calls 로 복구.

    qwen2.5+ollama 는 도구 호출을 ``message.tool_calls`` 로 주지 못하고 본문에
    ``leton\\n{...}\\n</tool_call>`` / ```` ```json{...}``` ```` / ``<tool_call>{...}</tool_call>``
    처럼 흘리는데, 열림 마커가 환경마다 달라 신뢰할 수 없다. 그래서 마커가 아니라
    **본문 속 JSON 객체**를 직접 스캔하고, ``name`` 이 실제 등록된 도구일 때만
    복구한다 (정상 답변의 JSON 예시를 도구 호출로 오인하지 않도록 강한 가드).
    """
    if not content or '"name"' not in content:
        return []
    calls: list[dict] = []
    dec = json.JSONDecoder()
    i, n = 0, len(content)
    while i < n:
        brace = content.find("{", i)
        if brace == -1:
            break
        try:
            obj, end = dec.raw_decode(content, brace)
        except json.JSONDecodeError:
            i = brace + 1
            continue
        if isinstance(obj, dict) and obj.get("name") in valid_names:
            args = obj.get("arguments", obj.get("parameters", {}))
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            calls.append({"id": f"call_recovered_{len(calls)}", "type": "function",
                          "function": {"name": obj["name"], "arguments": args or "{}"}})
        i = end
    return calls


def run_assistant(
    llm: LLMClient,
    tool_ctx: ToolContext,
    history: list[dict],
    message: str,
) -> Iterator[dict]:
    """tool-use 루프 실행 — SSE 이벤트 dict 를 순서대로 yield 한다.

    마지막에 history 갱신용으로 {"type": "_final", "data": {"text": ...}} 를
    내보낸다 (서버 내부용 — 클라이언트로는 전송하지 않음).
    """
    messages = _conversation_messages(history, message)
    tools = tool_schemas()
    valid_tool_names = {t["function"]["name"] for t in tools}
    total_in = total_out = 0

    for round_no in range(MAX_TOOL_ROUNDS):
        # 도구 판단은 '비스트리밍'으로 한다 — ollama 는 스트리밍에서 도구 호출을
        # 구조화(delta.tool_calls)하지 못하고 Hermes 태그/JSON 을 본문에 흘리는
        # 버그가 있어 채팅이 멈추거나 원본 JSON 이 노출된다 (대신 최종 답변은
        # 아래에서 점진 표시). 비스트리밍도 가끔 구조화에 실패하므로, 그때는
        # 본문에 흘러나온 도구 호출 JSON 을 복구한다(_recover_tool_calls).
        try:
            result = llm.chat(messages, tools=tools)
        except RuntimeError as e:
            yield {"type": "error", "data": {"reason": str(e)}}
            return
        msg = result["message"]
        usage = result["usage"]

        total_in += usage.get("prompt_tokens") or 0
        total_out += usage.get("completion_tokens") or 0

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            recovered = _recover_tool_calls(msg.get("content") or "", valid_tool_names)
            if recovered:
                # 본문 누수 도구 호출 복구 — 구조화 메시지로 교체(누수 JSON 본문은 버림)
                tool_calls = recovered
                msg = {"role": "assistant", "content": None, "tool_calls": recovered}
                logger.info("본문 누수 tool_call %d개 복구 — %s", len(recovered),
                            ", ".join(tc["function"]["name"] for tc in recovered))

        if not tool_calls:
            # 도구 호출 없음 = 최종 답변. 본문을 정리해 조각으로 흘려 점진 표시한다.
            text = _clean_answer(msg.get("content") or "")
            # 한국어 최우선 — 중국어 등이 섞였으면 한국어로 1회 재작성한다
            # (qwen2.5 다국어 누수 방어, 프롬프트만으로는 간헐 누수를 100% 못 막음).
            if _has_cjk(text):
                retry_msgs = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "방금 답변에 한국어가 아닌 글자가 섞였습니다. "
                     "같은 내용을 100% 한국어로만 다시 작성해 주세요. 중국어·일본어 금지 "
                     "(테이블·컬럼명, SQL 키워드 등 식별자만 원문 유지)."},
                ]
                try:
                    retry = llm.chat(retry_msgs, tools=None)
                    retry_text = _clean_answer(retry["message"].get("content") or "")
                    ru = retry["usage"]
                    total_in += ru.get("prompt_tokens") or 0
                    total_out += ru.get("completion_tokens") or 0
                    if retry_text and not _has_cjk(retry_text):
                        text = retry_text
                    logger.info("한국어 재작성 수행 — 중국어 누수 감지")
                except RuntimeError:
                    pass  # 재작성 실패 시 원문 유지(베스트에포트)
            for piece in _chunk(text):
                yield {"type": "text_delta", "data": {"text": piece}}
            yield {"type": "usage", "data": {"tokens_in": total_in, "tokens_out": total_out}}
            yield {"type": "done", "data": {}}
            yield {"type": "_final", "data": {"text": text}}
            return

        # 도구 실행 라운드 — assistant 메시지(tool_calls 포함)를 대화에 추가
        messages.append(msg)
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            try:
                args = json.loads(tc.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            call_id = tc.get("id") or f"call_{round_no}_{name}"

            yield {"type": "tool_call", "data": {"id": call_id, "name": name, "args": args}}
            tool_result = run_tool(tool_ctx, name, args)
            yield {"type": "tool_result", "data": {"id": call_id, "name": name, "result": tool_result}}

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })
        logger.info("도구 라운드 %d — %s", round_no + 1,
                    ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls))

    # 라운드 초과 — 도구만 반복하고 답을 못 만든 경우
    yield {"type": "error", "data": {
        "reason": f"도구 호출이 {MAX_TOOL_ROUNDS}회를 초과했습니다 — 질문을 좁혀서 다시 시도해 주세요."}}
