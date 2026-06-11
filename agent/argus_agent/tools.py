"""어시스턴트 도구 — 카탈로그 API 를 LLM function calling 도구로 노출.

각 도구는 (OpenAI tools 스키마, 실행 함수) 쌍으로 정의된다. 실행은
**사용자의 토큰**으로 카탈로그 API 를 호출하므로 사용자가 볼 수 없는
데이터는 도구로도 볼 수 없다 (권한 위임 없음).

결과는 LLM 컨텍스트에 들어가므로 토큰 낭비를 막기 위해 필요한 필드만
추려서 반환한다.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


class ToolContext:
    """도구 실행 컨텍스트 — 카탈로그 API 주소와 사용자 토큰."""

    def __init__(self, api_url: str, user_token: str) -> None:
        self.base = api_url.rstrip("/") + "/api/v1"
        self.token = user_token

    def get(self, path: str):
        """사용자 토큰으로 GET — 실패는 도구 결과(오류 메시지)로 보고."""
        req = urllib.request.Request(
            self.base + path,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())


# ---------------------------------------------------------------------------
# 도구 구현 — 반환 dict 는 그대로 LLM 에 들어간다 (간결하게 유지)
# ---------------------------------------------------------------------------

def search_datasets(ctx: ToolContext, query: str, limit: int = 5) -> dict:
    """시맨틱 통합 검색 — 데이터셋·용어집·Agent·API 를 한 번에 찾는다."""
    data = ctx.get(f"/catalog/search/unified?q={urllib.parse.quote(query)}&limit={limit}")
    # 응답 구조: datasets = [{"dataset": {...}, "score": ...}], glossary_terms = [{...}]
    datasets = []
    for hit in (data.get("datasets") or [])[:limit]:
        d = hit.get("dataset") or hit  # 래핑 유무 양쪽 수용
        datasets.append({
            "id": d.get("id"), "name": d.get("name"), "urn": d.get("urn"),
            "description": (d.get("summary") or d.get("description") or "")[:200],
        })
    glossary = [
        {"name": g.get("name"), "description": (g.get("description") or "")[:150]}
        for g in (data.get("glossary_terms") or [])[:3]
    ]
    return {"datasets": datasets, "glossary_terms": glossary}


def get_dataset_detail(ctx: ToolContext, dataset_id: int) -> dict:
    """데이터셋 상세 — 스키마(컬럼·타입·PK·PII)·설명·태그·행 수."""
    d = ctx.get(f"/catalog/datasets/{dataset_id}")
    return {
        "id": d.get("id"), "name": d.get("name"), "urn": d.get("urn"),
        "description": (d.get("description") or "")[:600],
        "row_count": d.get("row_count"),
        "quality_status": d.get("quality_status"),
        "tags": [t.get("name") for t in (d.get("tags") or [])],
        "columns": [
            {"name": f.get("field_path"),
             "type": f.get("native_type") or f.get("field_type"),
             "nullable": f.get("nullable") == "true",
             "pk": f.get("is_primary_key") == "true",
             "pii": f.get("pii_type"),
             "description": (f.get("description") or "")[:80]}
            for f in (d.get("schema_fields") or [])
        ],
    }


def get_erd(ctx: ToolContext, dataset_id: int) -> dict:
    """ER 관계 — FK 조인 경로 (SQL 작성 시 조인 근거로 사용)."""
    d = ctx.get(f"/catalog/datasets/{dataset_id}/erd")
    names = {t["dataset_id"]: t["name"] for t in d.get("tables", [])}
    return {
        "tables": [{"dataset_id": t["dataset_id"], "name": t["name"]} for t in d.get("tables", [])],
        "relations": [
            {"from_table": names.get(r["source_dataset_id"]),
             "from_columns": r.get("source_columns"),
             "to_table": names.get(r["target_dataset_id"]),
             "to_columns": r.get("target_columns"),
             "cardinality": r.get("cardinality")}
            for r in d.get("relations", [])
        ],
    }


def get_quality(ctx: ToolContext, dataset_id: int) -> dict:
    """품질 상태 — 점수·실패 규칙·위반 샘플 (왜 나쁜지 설명용)."""
    try:
        score = ctx.get(f"/quality/datasets/{dataset_id}/score")
    except Exception:  # noqa: BLE001 — 점수 미산출은 정상
        score = None
    results = ctx.get(f"/quality/datasets/{dataset_id}/results")
    failed = [
        {"rule": r.get("rule_name"), "type": r.get("check_type"),
         "column": r.get("column_name"), "severity": r.get("severity"),
         "detail": r.get("detail"),
         # 위반 샘플은 2행만 — 컨텍스트 절약
         "samples": (r.get("failed_samples") or [])[:2] or None}
        for r in results if r.get("passed") == "false"
    ]
    return {
        "score": score.get("score") if score else None,
        "passed": score.get("passed_rules") if score else None,
        "total": score.get("total_rules") if score else None,
        "failed_rules": failed,
    }


def get_lineage(ctx: ToolContext, dataset_id: int) -> dict:
    """리니지 — 업스트림(원천)/다운스트림(영향 범위) 관계."""
    d = ctx.get(f"/catalog/datasets/{dataset_id}/lineage?depth=1")
    names = {n["id"]: n["name"] for n in d.get("nodes", [])}
    upstream, downstream = [], []
    for e in d.get("edges", []):
        rel = e.get("relationType") or ""
        if e.get("target") == dataset_id:
            upstream.append({"name": names.get(e.get("source")), "relation": rel})
        elif e.get("source") == dataset_id:
            downstream.append({"name": names.get(e.get("target")), "relation": rel})
    quality_warn = [
        {"name": n["name"], "status": n.get("qualityStatus")}
        for n in d.get("nodes", [])
        if n.get("qualityStatus") in ("WARN", "BAD")
    ]
    return {"upstream": upstream, "downstream": downstream,
            "quality_warnings": quality_warn}


def get_glossary_term(ctx: ToolContext, term_name: str) -> dict:
    """용어집(글로서리)에서 비즈니스 용어의 정의를 찾는다.

    사용자가 "X 가 무슨 뜻이야?"·"우리 회사에서 Y 는 어떻게 정의돼 있어?"
    처럼 용어의 의미를 물으면 사용한다. 글로서리는 보통 수십~수백 건이라
    전체를 받아 이름 부분일치로 상위 3건만 추려 컨텍스트를 작게 유지한다.
    """
    data = ctx.get("/catalog/glossary")
    needle = term_name.strip().lower()
    hits = [
        g for g in (data or [])
        if (g.get("term_type") or "TERM") == "TERM"
        and needle in (g.get("name") or "").lower()
    ][:3]
    if not hits:
        # 용어 부재는 정상 — 모델이 "정의 없음"을 답하도록 note 로 알린다
        logger.info("글로서리 용어 없음: %s", term_name)
        return {"terms": [], "note": f"'{term_name}' 에 해당하는 용어가 글로서리에 없습니다."}
    return {"terms": [
        {"name": g.get("name"), "description": (g.get("description") or "")[:300]}
        for g in hits
    ]}


def get_standard_compliance(ctx: ToolContext, dataset_id: int) -> dict:
    """데이터셋의 표준 용어 준수 현황 — 준수율 + 표준에 어긋난 컬럼 목록.

    "이 테이블이 표준 용어 규칙을 잘 따르나?"·"표준에 안 맞는 컬럼이 뭐야?"
    같은 거버넌스 질문에 사용한다. 데이터셋에 지정된 기본 표준사전을 쓰고,
    없으면 첫 번째 사전으로 폴백한다.

    주의: 매핑 현황은 표준 용어 화면에서 자동 매핑을 한 번 수행한 결과를
    반영하므로, 매핑을 돌린 적이 없으면 모든 컬럼이 UNMAPPED 로 나온다 —
    이 경우 "아직 표준 매핑이 수행되지 않았다"고 답한다.
    """
    # 1) 데이터셋의 기본 표준사전 — 없으면 첫 사전으로 폴백
    meta = ctx.get(f"/standards/datasets/{dataset_id}/dictionary") or {}
    dict_id = meta.get("dictionary_id")
    if not dict_id:
        dicts = ctx.get("/standards/dictionaries") or []
        if not dicts:
            logger.warning("표준사전 미등록 — 준수율 계산 불가 (dataset=%s)", dataset_id)
            return {"error": "등록된 표준 사전이 없습니다 — 표준 용어 관리에서 사전을 먼저 생성하세요."}
        dict_id = dicts[0]["id"]

    # 2) 컬럼별 매핑 현황 + 준수율
    m = ctx.get(f"/standards/mappings/dataset?dictionary_id={dict_id}&dataset_id={dataset_id}")
    comp = m.get("compliance") or {}
    # MATCHED(준수) 컬럼은 설명이 불필요하므로 비준수 컬럼만 추려 컨텍스트 절약
    issues = [
        {"column": c.get("column_name"),
         "status": c.get("mapping_type") or "UNMAPPED",
         "mapped_term": c.get("term_name"),
         "standard_physical_name": c.get("term_physical_name")}
        for c in (m.get("columns") or [])
        if (c.get("mapping_type") or "UNMAPPED") != "MATCHED"
    ]
    return {
        "dictionary_id": dict_id,
        "compliance_rate": comp.get("compliance_rate"),
        "matched": comp.get("matched"),
        "violation": comp.get("violation"),
        "unmapped": comp.get("unmapped"),
        "total_columns": comp.get("total_columns"),
        # 비준수 컬럼이 많은 데이터셋도 있으므로 상한을 둔다
        "non_compliant_columns": issues[:20],
    }


def get_quality_rule_recommendations(ctx: ToolContext, dataset_id: int) -> dict:
    """프로파일 기반 품질 규칙 추천 후보 — "이 테이블에 어떤 품질 규칙을 걸면 좋아?".

    백엔드가 최신 프로파일 통계(NULL 비율·고유율·최빈값·범위·행 수)를 분석해
    NOT_NULL·UNIQUE·ACCEPTED_VALUES·MIN_VALUE·ROW_COUNT 등 후보를 산출한다
    (규칙을 생성하지는 않는다 — 제안만). 각 후보의 `reason` 에 근거가 담겨 있어
    LLM 이 그대로 설명·우선순위화하기 좋다. 프로파일이 없으면 빈 목록이 오므로
    "프로파일링을 먼저 수행하라"고 안내한다.
    """
    recs = ctx.get(f"/quality/datasets/{dataset_id}/rules/recommendations") or []
    if not recs:
        logger.info("품질 규칙 추천 없음 (프로파일 부재 가능): dataset=%s", dataset_id)
        return {
            "recommendations": [],
            "note": "추천 후보가 없습니다 — 데이터셋 프로파일링을 먼저 수행하면 통계 기반 규칙을 추천할 수 있습니다.",
        }
    return {"recommendations": [
        {"rule_name": r.get("rule_name"), "check_type": r.get("check_type"),
         "column": r.get("column_name"), "severity": r.get("severity"),
         "reason": (r.get("reason") or "")[:150]}
        for r in recs[:20]
    ]}


# SQL 검증 — quality 배치의 SELECT-only 가드와 동일한 2차 방어선
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec|execute)\b",
    re.IGNORECASE,
)


def validate_sql(ctx: ToolContext, sql: str) -> dict:
    """생성한 SQL 의 안전성 자가 점검 — SELECT 단일 문장만 통과.

    어시스턴트가 SQL 을 사용자에게 제시하기 전에 스스로 호출하도록
    시스템 프롬프트에서 유도한다 (실행 기능은 없음 — 검증만).
    """
    stripped = re.sub(r"--[^\n]*", "", sql)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    stripped = stripped.strip().rstrip(";").strip()
    if not stripped:
        return {"valid": False, "reason": "SQL 이 비어 있습니다"}
    if ";" in stripped:
        return {"valid": False, "reason": "다중 문장(;)은 허용되지 않습니다"}
    head = stripped.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        return {"valid": False, "reason": "SELECT/WITH 조회 쿼리만 허용됩니다"}
    m = _FORBIDDEN_SQL.search(stripped)
    if m:
        return {"valid": False, "reason": f"허용되지 않는 키워드: {m.group(1)}"}
    return {"valid": True, "reason": "통과"}


# ---------------------------------------------------------------------------
# 도구 레지스트리 — OpenAI tools 스키마 + 실행 함수
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict] = {
    "search_datasets": {
        "fn": search_datasets,
        "schema": {
            "type": "function",
            "function": {
                "name": "search_datasets",
                "description": "데이터 카탈로그를 시맨틱 검색한다. 사용자가 어떤 데이터/테이블을 찾거나 언급하면 먼저 이 도구로 찾는다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "검색어 (한국어 가능)"},
                        "limit": {"type": "integer", "description": "결과 수 (기본 5)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "get_dataset_detail": {
        "fn": get_dataset_detail,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_dataset_detail",
                "description": "데이터셋의 스키마(컬럼·타입·PK·PII)·설명·태그·행 수를 조회한다. SQL 작성이나 테이블 설명에 필수.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer", "description": "search_datasets 결과의 id"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "get_erd": {
        "fn": get_erd,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_erd",
                "description": "테이블의 FK 관계(조인 경로)를 조회한다. 여러 테이블을 조인하는 SQL 을 작성할 때 반드시 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "get_quality": {
        "fn": get_quality,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_quality",
                "description": "데이터셋의 품질 점수·실패 규칙·위반 샘플을 조회한다. 품질이 왜 나쁜지 질문받으면 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "get_lineage": {
        "fn": get_lineage,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_lineage",
                "description": "데이터셋의 리니지(원천/영향 범위)와 업스트림 품질 경고를 조회한다. 데이터 출처나 변경 영향 질문에 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "get_glossary_term": {
        "fn": get_glossary_term,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_glossary_term",
                "description": "용어집(글로서리)에서 비즈니스 용어의 정의를 찾는다. 사용자가 용어의 뜻이나 사내 정의를 물으면 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"term_name": {"type": "string", "description": "찾을 용어 이름 (한국어 가능)"}},
                    "required": ["term_name"],
                },
            },
        },
    },
    "get_standard_compliance": {
        "fn": get_standard_compliance,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_standard_compliance",
                "description": "데이터셋의 표준 용어 준수율과 표준에 어긋난 컬럼을 조회한다. 표준 용어/명명 규칙 준수 여부를 물으면 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer", "description": "search_datasets 결과의 id"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "get_quality_rule_recommendations": {
        "fn": get_quality_rule_recommendations,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_quality_rule_recommendations",
                "description": "데이터셋에 걸면 좋을 품질 규칙 후보를 프로파일 통계 기반으로 추천한다. 어떤 품질 규칙/검증을 추가할지 물으면 사용.",
                "parameters": {
                    "type": "object",
                    "properties": {"dataset_id": {"type": "integer", "description": "search_datasets 결과의 id"}},
                    "required": ["dataset_id"],
                },
            },
        },
    },
    "validate_sql": {
        "fn": validate_sql,
        "schema": {
            "type": "function",
            "function": {
                "name": "validate_sql",
                "description": "작성한 SQL 이 안전한 조회(SELECT 단일 문장)인지 검증한다. 사용자에게 SQL 을 제시하기 전에 호출할 것.",
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            },
        },
    },
}


def tool_schemas() -> list[dict]:
    """LLM 요청에 넣을 OpenAI tools 스키마 목록."""
    return [t["schema"] for t in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """도구 실행 — 모든 예외를 결과(오류)로 변환해 루프가 끊기지 않게 한다."""
    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"알 수 없는 도구: {name}"}
    try:
        return tool["fn"](ctx, **args)
    except TypeError as e:
        return {"error": f"잘못된 인자: {e}"}
    except Exception as e:  # noqa: BLE001 — 도구 실패를 LLM 에게 알려 재시도/우회 유도
        logger.warning("도구 실행 실패: %s(%s) — %s", name, args, e)
        return {"error": f"도구 실행 실패: {e}"}
