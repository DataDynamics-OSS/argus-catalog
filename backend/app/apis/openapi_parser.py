"""OpenAPI(Swagger) 2.0 / 3.x 스펙 파서.

업로드/URL 로 받은 스펙(JSON/YAML 문자열)을 dict 로 로드한 뒤, 카탈로그에 저장할
요약 정보(엔드포인트·서버·보안 스킴·info)를 추출한다. 외부 의존 없이 표준 dict
구조만 다룬다(2.0 ↔ 3.x 차이를 흡수).
"""

from __future__ import annotations

import json
from typing import Any

import yaml

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class SpecParseError(ValueError):
    pass


def load_spec(text: str) -> dict[str, Any]:
    """JSON 또는 YAML 문자열을 dict 로 로드."""
    text = (text or "").strip()
    if not text:
        raise SpecParseError("스펙 내용이 비어 있습니다.")
    # JSON 우선, 실패 시 YAML
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        raise SpecParseError(f"스펙을 JSON/YAML 로 파싱할 수 없습니다: {e}")
    if not isinstance(data, dict):
        raise SpecParseError("스펙 최상위가 객체가 아닙니다.")
    return data


def detect_format(spec: dict[str, Any]) -> str:
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


def parse(text: str, source_url: str | None = None) -> dict[str, Any]:
    """스펙 텍스트를 파싱해 카탈로그 저장용 구조를 반환.

    반환: { format, version, title, description, base_url, raw(json),
            servers[], security_schemes[], endpoints[], parsed(summary) }
    """
    spec = load_spec(text)
    fmt = detect_format(spec)
    if fmt == "unknown":
        raise SpecParseError("지원하지 않는 스펙입니다(openapi/swagger 필드 없음).")

    info = spec.get("info") or {}
    servers = _servers(spec, fmt)
    schemes = _security_schemes(spec, fmt)
    endpoints = _endpoints(spec)

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
