# SPDX-License-Identifier: Apache-2.0
"""OpenAPI(Swagger) 2.0 / 3.x 스펙 파서.

업로드/URL 로 받은 스펙(JSON/YAML 문자열)을 dict 로 로드한 뒤, 카탈로그에 저장할
요약 정보(엔드포인트·서버·보안 스킴·info)를 추출한다. 외부 의존 없이 표준 dict
구조만 다룬다(2.0 ↔ 3.x 차이를 흡수).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class SpecParseError(ValueError):
    """스펙을 읽을 수 없거나 지원하지 않는 형식 — 라우터에서 HTTP 400 으로 변환된다."""


def load_spec(text: str) -> dict[str, Any]:
    """JSON 또는 YAML 문자열을 dict 로 로드.

    빈 문자열·YAML 파싱 실패·최상위가 객체가 아닌 JSON/YAML(``123``, ``null`` 등)은 ``SpecParseError``.
    """
    text = (text or "").strip()
    if not text:
        raise SpecParseError("스펙 내용이 비어 있습니다.")
    # JSON 우선, 실패 시 YAML
    try:
        data = json.loads(text)
    except Exception:
        try:
            data = yaml.safe_load(text)
        except Exception as e:
            raise SpecParseError(f"스펙을 JSON/YAML 로 파싱할 수 없습니다: {e}")
    if not isinstance(data, dict):
        raise SpecParseError("스펙 최상위가 객체가 아닙니다.")
    return data


def detect_format(spec: dict[str, Any]) -> str:
    """최상위 키로 스펙 형식을 판별: openapi3 / openapi(3 이외 버전 표기) / openapi2 / asyncapi / unknown."""
    if "openapi" in spec:
        v = str(spec.get("openapi", ""))
        return "openapi3" if v.startswith("3") else "openapi"
    if "swagger" in spec:
        return "openapi2"
    if "asyncapi" in spec:
        return "asyncapi"
    return "unknown"


def _servers(spec: dict[str, Any], fmt: str) -> list[dict[str, str]]:
    """대표 서버 목록(url/description)."""
    out: list[dict[str, str]] = []
    if fmt == "openapi2":
        # swagger 2.0: host + basePath + schemes
        host = spec.get("host")
        base = spec.get("basePath", "")
        schemes = spec.get("schemes") or (["https"] if host else [])
        for sc in schemes or []:
            if host:
                out.append({"url": f"{sc}://{host}{base}", "description": ""})
    else:
        for s in spec.get("servers") or []:
            if isinstance(s, dict) and s.get("url"):
                out.append({"url": str(s["url"]), "description": str(s.get("description") or "")})
    return out


def _security_schemes(spec: dict[str, Any], fmt: str) -> list[dict[str, Any]]:
    """보안 스킴 목록(name/type/config)."""
    if fmt == "openapi2":
        defs = spec.get("securityDefinitions") or {}
    else:
        defs = (spec.get("components") or {}).get("securitySchemes") or {}
    out: list[dict[str, Any]] = []
    for name, d in (defs or {}).items():
        if not isinstance(d, dict):
            continue
        out.append({"scheme_name": name, "type": str(d.get("type") or ""), "config": d})
    return out


def tag_defs(spec: dict[str, Any]) -> list[dict[str, str]]:
    """최상위 tags 정의(name + description) — 엔드포인트 카테고리(그룹) 설명."""
    out: list[dict[str, str]] = []
    for t in spec.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            out.append({"name": str(t["name"]), "description": str(t.get("description") or "")})
    return out


def _endpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """paths → 오퍼레이션 목록."""
    out: list[dict[str, Any]] = []
    order = 0
    paths = spec.get("paths") or {}
    for path, item in (paths.items() if isinstance(paths, dict) else []):
        if not isinstance(item, dict):
            continue
        # 경로 공통 파라미터
        common_params = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = list(common_params) + (op.get("parameters") or [])
            out.append({
                "method": method.upper(),
                "path": str(path),
                "operation_id": op.get("operationId"),
                "summary": op.get("summary"),
                "description": op.get("description"),
                "tags": op.get("tags") or [],
                "parameters": params,
                "request_body": op.get("requestBody"),  # OpenAPI3
                "responses": op.get("responses") or {},
                "security": op.get("security"),
                "sort_order": order,
            })
            order += 1
    return out


def parse(text: str, source_url: str | None = None, *, for_import: bool = False) -> dict[str, Any]:
    """스펙 텍스트를 파싱해 카탈로그 저장용 구조를 반환.

    for_import=True: 가져오기(미리보기·등록·새 버전) — 엔드포인트를 추출할 수 없는 형식(AsyncAPI)은 거부.
    저장된 스펙을 다시 해석(버전 비교 등)할 때는 False 로 두어 기존 데이터를 그대로 읽는다.

    반환: { format, version, title, description, base_url, raw(json),
            servers[], security_schemes[], endpoints[], tag_defs[], parsed(summary) }
    ``source_url`` 은 현재 파싱에 쓰이지 않는다(호출 측 호환용 인자).
    """
    spec = load_spec(text)
    fmt = detect_format(spec)
    logger.debug("스펙 형식 판별: format=%s for_import=%s", fmt, for_import)
    if fmt == "unknown":
        raise SpecParseError("지원하지 않는 스펙입니다(openapi/swagger 필드 없음).")
    if fmt == "asyncapi" and for_import:
        # AsyncAPI 는 채널/메시지 구조라 엔드포인트를 추출하지 않는다 — 가져오면 엔드포인트 0건으로
        # 조용히 등록되므로 거부하고, 수동 등록의 계약 문서로 안내한다.
        raise SpecParseError(
            "AsyncAPI 스펙은 가져오기(엔드포인트 자동 추출)를 지원하지 않습니다. "
            "수동 등록에서 프로토콜(Webhook 등)을 선택하고 AsyncAPI 문서를 계약 문서(원문/URL)로 등록하세요."
        )

    info = spec.get("info") or {}
    servers = _servers(spec, fmt)
    schemes = _security_schemes(spec, fmt)
    endpoints = _endpoints(spec)
    logger.debug("스펙 파싱 완료: format=%s endpoints=%d servers=%d security_schemes=%d",
                 fmt, len(endpoints), len(servers), len(schemes))

    return {
        "format": fmt,
        "version": str(info.get("version") or "1.0.0"),
        "title": info.get("title"),
        "description": info.get("description"),
        "base_url": servers[0]["url"] if servers else None,
        "raw": json.dumps(spec, ensure_ascii=False),
        "servers": servers,
        "security_schemes": schemes,
        "endpoints": endpoints,
        "tag_defs": tag_defs(spec),
        "parsed": {
            "info": info,
            "format": fmt,
            "endpoint_count": len(endpoints),
            "server_count": len(servers),
            "security_count": len(schemes),
            "components": list((spec.get("components") or {}).get("schemas", {}).keys()) if fmt != "openapi2" else list((spec.get("definitions") or {}).keys()),
        },
    }
