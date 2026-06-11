# SPDX-License-Identifier: Apache-2.0
"""프롬프트 빌더 — 서버(backend/app/ai/prompts.py)와 동일한 생성 시맨틱.

컨텍스트(스키마·샘플·용어집)를 한국어 프롬프트로 직렬화한다.
qwen2.5:7b 같은 소형 모델에서도 안정적으로 동작하도록 출력 형식을
명시적으로 지시하고, JSON 출력은 예시를 포함한다.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "당신은 데이터 카탈로그의 메타데이터 작성 전문가입니다. "
    "데이터셋 스키마와 샘플을 근거로 정확하고 간결한 한국어 메타데이터를 작성합니다. "
    "근거 없는 추측을 하지 않으며, 지시된 출력 형식을 엄격히 지킵니다."
)


def _schema_section(dataset: dict) -> str:
    """스키마를 '- 컬럼명 (타입): 설명' 목록으로 직렬화."""
    lines = []
    for f in dataset.get("schema_fields", []):
        desc = f" — {f['description']}" if f.get("description") else ""
        nullable = "" if f.get("nullable") == "false" else " (NULL 허용)"
        lines.append(f"- {f['field_path']} ({f.get('native_type') or f.get('field_type')}){nullable}{desc}")
    return "\n".join(lines) if lines else "(스키마 정보 없음)"


def _sample_section(sample: dict | None) -> str:
    """샘플 행을 간단한 표 텍스트로 — 너무 길면 프롬프트만 부풀므로 5행 제한."""
    if not sample or not sample.get("rows"):
        return ""
    cols = sample.get("columns", [])
    rows = sample["rows"][:5]
    lines = [" | ".join(cols)]
    for r in rows:
        lines.append(" | ".join("" if v is None else str(v)[:40] for v in r))
    return "\n\n샘플 데이터 (상위 5행):\n" + "\n".join(lines)


def _glossary_section(terms: list[dict]) -> str:
    """용어집 컨텍스트 — 설명에 표준 용어를 일관되게 쓰도록 유도."""
    if not terms:
        return ""
    names = ", ".join(t.get("name", "") for t in terms[:30] if t.get("name"))
    return f"\n\n조직 표준 용어집 (가능하면 이 용어를 사용): {names}"


def _context(dataset: dict, sample: dict | None, terms: list[dict]) -> str:
    """모든 생성 프롬프트가 공유하는 데이터셋 컨텍스트 블록."""
    return (
        f"데이터셋: {dataset.get('name')}\n"
        f"데이터 소스 타입: {(dataset.get('datasource') or {}).get('type', '알 수 없음')}\n"
        f"행 수: {dataset.get('row_count') or '알 수 없음'}\n\n"
        f"스키마:\n{_schema_section(dataset)}"
        f"{_sample_section(sample)}"
        f"{_glossary_section(terms)}"
    )


def build_description_prompt(dataset: dict, sample: dict | None, terms: list[dict]) -> str:
    """데이터셋 상세 설명 (markdown) 생성 프롬프트."""
    return (
        f"{_context(dataset, sample, terms)}\n\n"
        "위 데이터셋의 상세 설명을 한국어 markdown 으로 작성하세요.\n"
        "- 구성: 개요(2~3문장) → 주요 컬럼 설명 → 활용 예시\n"
        "- 스키마에 근거한 사실만 작성하고, 추측한 내용에는 '추정' 을 명시\n"
        "- 마크다운 본문만 출력 (코드펜스로 감싸지 말 것)"
    )


def build_summary_prompt(dataset: dict, sample: dict | None, terms: list[dict]) -> str:
    """한 줄 요약 생성 프롬프트."""
    return (
        f"{_context(dataset, sample, terms)}\n\n"
        "위 데이터셋을 한국어 한 문장(80자 이내)으로 요약하세요. "
        "문장만 출력하고 다른 텍스트는 붙이지 마세요."
    )


def build_columns_prompt(dataset: dict, sample: dict | None, terms: list[dict]) -> str:
    """컬럼별 설명 생성 — JSON 출력."""
    return (
        f"{_context(dataset, sample, terms)}\n\n"
        "각 컬럼의 설명을 한국어로 작성해 JSON 배열로만 출력하세요.\n"
        '형식: [{"name": "컬럼명", "description": "간결한 설명 (40자 내외)"}]\n'
        "이미 설명이 있는 컬럼도 포함해 전체를 출력하세요. JSON 외 텍스트 금지."
    )


def build_tags_prompt(dataset: dict, sample: dict | None, terms: list[dict]) -> str:
    """태그 추천 — JSON 출력."""
    return (
        f"{_context(dataset, sample, terms)}\n\n"
        "이 데이터셋에 적합한 분류 태그를 3~6개 추천해 JSON 배열로만 출력하세요.\n"
        '형식: ["태그1", "태그2"] — 한국어 또는 짧은 영문 소문자. JSON 외 텍스트 금지.'
    )


def build_pii_prompt(dataset: dict, sample: dict | None) -> str:
    """PII 컬럼 감지 — JSON 출력 (서버 apply 시맨틱과 동일한 형식).

    pii_type 은 서버가 schema.pii_type 에 그대로 저장하므로 통제된 값만 쓴다.
    """
    return (
        f"{_context(dataset, sample, [])}\n\n"
        "개인정보(PII)가 포함될 수 있는 컬럼을 찾아 JSON 배열로만 출력하세요.\n"
        '형식: [{"name": "컬럼명", "pii_type": "유형", "reason": "근거 (한국어)"}]\n'
        "pii_type 은 다음 중 하나만: EMAIL, PHONE, NAME, ADDRESS, ID_NUMBER, "
        "BIRTH_DATE, ACCOUNT, IP_ADDRESS, OTHER\n"
        "PII 가 없으면 [] 를 출력. JSON 외 텍스트 금지."
    )
