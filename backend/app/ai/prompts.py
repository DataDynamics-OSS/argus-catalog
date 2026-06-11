# SPDX-License-Identifier: Apache-2.0
"""AI 메타데이터 생성용 프롬프트 템플릿.

각 함수는 특정 생성 작업을 위해 사용 가능한 카탈로그 컨텍스트
(테이블 정보, 컬럼, DDL, 샘플 데이터)로부터 구조화된 프롬프트를 구성한다.

확장 컨텍스트 (선택):
- glossary: StandardWord 약어→이름 매핑 (도메인 특화 용어)
- lineage: 상류/하류 데이터셋 관계
- fewshot_examples: 이전에 승인된 AI 생성 결과를 참조로 사용
"""

SYSTEM_PROMPT = (
    "You are a data catalog assistant that generates metadata for database tables. "
    "Always respond with valid JSON only, no markdown fences or extra text."
)


# ---------------------------------------------------------------------------
# 공유 컨텍스트 빌더
# ---------------------------------------------------------------------------

def _build_glossary_section(glossary: list[dict] | None, columns: list[dict]) -> str:
    """컬럼명에서 발견된 약어를 매핑하는 용어집 섹션을 구성.

    실제 컬럼명에 약어가 등장하는 용어집 항목만 포함해, 프롬프트를 집중적이고
    토큰 효율적으로 유지한다.
    """
    if not glossary:
        return ""

    # 컬럼명에서 모든 토큰 수집 (_ 로 분할하고 소문자화)
    col_tokens = set()
    for c in columns:
        for token in c["field_path"].upper().replace("-", "_").split("_"):
            if token:
                col_tokens.add(token)

    # 컬럼 토큰과 일치하는 항목으로 용어집 필터링
    relevant = [g for g in glossary if g["abbr"].upper() in col_tokens]

    if not relevant:
        return ""

    lines = []
    for g in relevant[:50]:  # 최대 50개 항목으로 제한
        lines.append(f"  {g['abbr']}: {g['name']} ({g['english']})")

    return (
        "\n== Terminology glossary (abbreviation → meaning) ==\n"
        + "\n".join(lines)
        + "\n"
    )


def _build_lineage_section(lineage: dict[str, list[str]] | None) -> str:
    """데이터 흐름 관계를 보여주는 리니지 컨텍스트 섹션을 구성."""
    if not lineage:
        return ""

    upstream = lineage.get("upstream", [])
    downstream = lineage.get("downstream", [])

    if not upstream and not downstream:
        return ""

    section = "\n== Data lineage ==\n"
    if upstream:
        section += f"  Upstream (source tables): {', '.join(upstream)}\n"
    if downstream:
        section += f"  Downstream (consuming tables): {', '.join(downstream)}\n"
    return section


def _build_fewshot_section(
    examples: list[dict] | None, generation_type: str,
) -> str:
    """이전에 승인된 생성 결과로부터 few-shot 예시 섹션을 구성."""
    if not examples:
        return ""

    section = "\n== Reference examples (previously approved descriptions in this catalog) ==\n"
    for i, ex in enumerate(examples[:3], 1):
        name = ex.get("dataset_name", "")
        text = ex.get("generated_text", "")
        # 긴 텍스트는 잘라냄
        if len(text) > 200:
            text = text[:200] + "..."
        section += f"  {i}. {name}: {text}\n"

    section += "Use a similar style and level of detail.\n"
    return section


# ---------------------------------------------------------------------------
# 프롬프트 빌더
# ---------------------------------------------------------------------------

def build_dataset_description_prompt(
    table_name: str,
    database: str,
    datasource_type: str,
    columns: list[dict],
    ddl: str | None = None,
    sample_rows: list[dict] | None = None,
    row_count: int | None = None,
    language: str = "ko",
    glossary: list[dict] | None = None,
    lineage: dict[str, list[str]] | None = None,
    fewshot_examples: list[dict] | None = None,
) -> str:
    """테이블 설명 생성용 프롬프트를 구성."""
    col_lines = []
    for c in columns[:50]:  # 최대 50개 컬럼으로 제한
        parts = [c["field_path"], f"({c['field_type']})"]
        if c.get("is_primary_key") == "true":
            parts.append("PK")
        if c.get("nullable") == "false":
            parts.append("NOT NULL")
        if c.get("description"):
            parts.append(f"-- {c['description']}")
        col_lines.append("  " + " ".join(parts))

    prompt = f"""Generate a concise description for this database table.
{_build_glossary_section(glossary, columns)}{_build_fewshot_section(fewshot_examples, "description")}{_build_lineage_section(lineage)}
Table: {database}.{table_name}
Datasource: {datasource_type}
Columns:
{chr(10).join(col_lines)}
"""

    if ddl:
        prompt += f"\nDDL:\n{ddl[:2000]}\n"

    if sample_rows:
        rows_str = "\n".join(str(r) for r in sample_rows[:5])
        prompt += f"\nSample data (first rows):\n{rows_str}\n"

    if row_count is not None:
        prompt += f"\nEstimated row count: {row_count:,}\n"

    lang_name = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
        language, language
    )
    prompt += f"""
Respond in {lang_name}.
JSON format: {{"description": "...", "confidence": 0.0-1.0}}"""
    return prompt


def build_dataset_summary_prompt(
    table_name: str,
    database: str,
    datasource_type: str,
    columns: list[dict],
    description: str | None = None,
    sample_rows: list[dict] | None = None,
    row_count: int | None = None,
    language: str = "ko",
) -> str:
    """한 줄 요약(summary) 생성 프롬프트.

    description 보다 짧고, 카드/리스트에 들어가는 70자 안팎의 명사구.
    description 이 이미 있으면 그 의도를 한 줄로 압축하도록 유도.
    """
    col_lines = []
    for c in columns[:30]:
        parts = [c["field_path"], f"({c['field_type']})"]
        if c.get("is_primary_key") == "true":
            parts.append("PK")
        if c.get("description"):
            parts.append(f"-- {c['description']}")
        col_lines.append("  " + " ".join(parts))

    prompt = f"""Generate a ONE-LINE summary (max 70 characters, ideally 20-40) for this database table.

Goal: a short, noun-phrase label that fits in a list cell or card header.
Do NOT write a sentence. Do NOT explain. Just a concise label.

Examples (Korean):
- orders → "매장 주문 트랜잭션"
- users → "회원 계정 및 프로필"
- payment_logs → "결제 처리 로그"

Table: {database}.{table_name}
Datasource: {datasource_type}
Columns:
{chr(10).join(col_lines)}
"""

    if description:
        prompt += f"\nExisting detailed description (compress this into one line):\n{description[:1500]}\n"

    if sample_rows:
        rows_str = "\n".join(str(r) for r in sample_rows[:3])
        prompt += f"\nSample rows:\n{rows_str}\n"

    if row_count is not None:
        prompt += f"\nEstimated row count: {row_count:,}\n"

    lang_name = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
        language, language
    )
    prompt += f"""
Respond in {lang_name}.
JSON format: {{"summary": "...", "confidence": 0.0-1.0}}
Constraints: summary must be <= 70 characters, no trailing period, no quotes."""
    return prompt


def build_column_descriptions_prompt(
    table_name: str,
    database: str,
    table_description: str | None,
    columns: list[dict],
    sample_values: dict[str, list] | None = None,
    language: str = "ko",
    glossary: list[dict] | None = None,
    lineage: dict[str, list[str]] | None = None,
    fewshot_examples: list[dict] | None = None,
) -> str:
    """컬럼 설명을 일괄 생성하는 프롬프트를 구성."""
    col_lines = []
    for i, c in enumerate(columns[:80], 1):  # 최대 80개 컬럼으로 제한
        parts = [f"{i}. {c['field_path']} ({c['field_type']}"]
        if c.get("native_type"):
            parts[0] += f", {c['native_type']}"
        parts[0] += ")"
        attrs = []
        if c.get("is_primary_key") == "true":
            attrs.append("PK")
        if c.get("is_unique") == "true":
            attrs.append("UNIQUE")
        if c.get("is_indexed") == "true":
            attrs.append("INDEX")
        if c.get("nullable") == "false":
            attrs.append("NOT NULL")
        if attrs:
            parts.append(", ".join(attrs))
        col_lines.append(" ".join(parts))

    prompt = f"""Generate descriptions for all columns in this table.
{_build_glossary_section(glossary, columns)}{_build_lineage_section(lineage)}
Table: {database}.{table_name}
"""
    if table_description:
        prompt += f"Table purpose: {table_description}\n"

    prompt += f"""
Columns:
{chr(10).join(col_lines)}
"""

    if sample_values:
        prompt += "\nSample values per column:\n"
        for col_name, values in list(sample_values.items())[:30]:
            vals_str = ", ".join(str(v) for v in values[:5])
            prompt += f"  {col_name}: [{vals_str}]\n"

    lang_name = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
        language, language
    )
    prompt += f"""
Respond in {lang_name}.
JSON format: {{"columns": [{{"name": "col_name", "description": "...", "confidence": 0.0-1.0}}, ...]}}"""
    return prompt


def build_tag_suggestion_prompt(
    table_name: str,
    database: str,
    description: str | None,
    columns: list[dict],
    existing_tags: list[str],
    language: str = "ko",
    glossary: list[dict] | None = None,
    lineage: dict[str, list[str]] | None = None,
) -> str:
    """데이터셋에 대한 태그 제안용 프롬프트를 구성."""
    col_names = [c["field_path"] for c in columns[:50]]

    prompt = f"""Suggest relevant classification tags for this database table.
{_build_glossary_section(glossary, columns)}{_build_lineage_section(lineage)}
Table: {database}.{table_name}
"""
    if description:
        prompt += f"Description: {description}\n"

    prompt += f"Columns: {', '.join(col_names)}\n"

    if existing_tags:
        prompt += f"\nExisting tags in catalog (prefer these): {', '.join(existing_tags)}\n"

    lang_name = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
        language, language
    )
    prompt += f"""
Suggest 2-5 tags. Prefer existing tags when appropriate.
Respond in {lang_name}.
JSON format: {{"tags": ["existing_tag1", ...], "new_tags": [{{"name": "...", "description": "..."}}, ...]}}"""
    return prompt


def build_pii_detection_prompt(
    table_name: str,
    database: str,
    columns: list[dict],
    sample_values: dict[str, list] | None = None,
    glossary: list[dict] | None = None,
    language: str = "ko",
) -> str:
    """PII 컬럼 탐지용 프롬프트를 구성."""
    col_lines = []
    for c in columns[:80]:
        col_lines.append(f"  {c['field_path']} ({c['field_type']})")

    prompt = f"""Analyze columns for Personally Identifiable Information (PII).
{_build_glossary_section(glossary, columns)}
Table: {database}.{table_name}
Columns:
{chr(10).join(col_lines)}
"""

    if sample_values:
        prompt += "\nSample values:\n"
        for col_name, values in list(sample_values.items())[:30]:
            vals_str = ", ".join(str(v) for v in values[:5])
            prompt += f"  {col_name}: [{vals_str}]\n"

    lang_name = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}.get(
        language, language
    )
    prompt += f"""
PII types: EMAIL, PHONE, SSN, NAME, ADDRESS, CREDIT_CARD, IP_ADDRESS, DATE_OF_BIRTH, NATIONAL_ID, OTHER
Only flag columns with high confidence.
Write the "reason" field in {lang_name} (keep "pii_type" as the English enum above).
JSON format: {{"pii_columns": [{{"name": "col_name", "pii_type": "EMAIL", "confidence": 0.0-1.0, "reason": "..."}}]}}
If no PII detected, return: {{"pii_columns": []}}"""
    return prompt
