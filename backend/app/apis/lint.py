"""OpenAPI 스펙 품질 린팅 — Spectral(oas 룰셋) 스타일의 경량 구현.

스펙 dict 를 규칙별로 검사해 위치(location)·심각도(severity)·메시지를 가진
finding 목록을 만든다. 심각도: error / warn / info.

OAS 3.x 와 Swagger 2.0 을 함께 지원한다(경로/오퍼레이션 구조가 동일).
"""

from __future__ import annotations

from typing import Any

from app.apis import openapi_parser as parser

_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

# 심각도별 점수 가중치(품질 점수 산정용)
_WEIGHT = {"error": 5, "warn": 2, "info": 1}


def _is_oas3(spec: dict[str, Any]) -> bool:
    return "openapi" in spec


def lint_spec(raw: str) -> dict[str, Any]:
    """원본 스펙 문자열을 린팅해 finding 목록과 요약을 돌려준다."""
    findings: list[dict[str, Any]] = []

    def add(rule: str, severity: str, message: str, location: str = "") -> None:
        findings.append({"rule": rule, "severity": severity, "message": message, "location": location})

    try:
        spec = parser.load_spec(raw or "")
    except Exception as e:  # noqa: BLE001
        add("spec-parse", "error", f"스펙을 파싱할 수 없습니다: {e}")
        return _summarize(findings)

    oas3 = _is_oas3(spec)

    # --- info 블록 ---------------------------------------------------------
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    if not info:
        add("info-required", "error", "info 객체가 없습니다.", "info")
    else:
        if not info.get("title"):
            add("info-title", "error", "info.title 이 없습니다.", "info.title")
        if not info.get("version"):
            add("info-version", "warn", "info.version 이 없습니다.", "info.version")
        if not info.get("description"):
            add("info-description", "warn", "info.description 권장: API 설명을 추가하세요.", "info.description")
        if not info.get("contact"):
            add("info-contact", "info", "info.contact 권장: 문의 담당자/이메일을 명시하세요.", "info.contact")
        if not info.get("license"):
            add("info-license", "info", "info.license 권장: 라이선스를 명시하세요.", "info.license")

    # --- 서버/호스트 -------------------------------------------------------
    if oas3:
        servers = spec.get("servers")
        if not servers:
            add("oas3-api-servers", "warn", "servers 가 정의되지 않았습니다. 최소 1개 이상 권장합니다.", "servers")
        else:
            for i, s in enumerate(servers):
                url = s.get("url") if isinstance(s, dict) else None
                if isinstance(url, str) and url.endswith("/"):
                    add("server-trailing-slash", "warn", f"server.url 끝의 '/' 를 제거하세요: {url}", f"servers[{i}].url")
    else:
        if not spec.get("host"):
            add("oas2-host", "warn", "host 가 정의되지 않았습니다.", "host")

    # --- 전역 태그 ---------------------------------------------------------
    global_tags = spec.get("tags") or []
    declared_tags = {t.get("name") for t in global_tags if isinstance(t, dict)}
    for i, t in enumerate(global_tags):
        if isinstance(t, dict) and not t.get("description"):
            add("tag-description", "info", f"태그 '{t.get('name')}' 에 description 이 없습니다.", f"tags[{i}]")

    # --- 경로/오퍼레이션 ---------------------------------------------------
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    if not paths:
        add("paths-required", "error", "paths 가 비어 있습니다. 엔드포인트를 1개 이상 정의하세요.", "paths")

    seen_op_ids: dict[str, str] = {}
    used_tags: set[str] = set()

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        # 경로 템플릿 파라미터 추출: {id} 형태
        template_params = {seg[1:-1] for seg in _split_braces(path)}
        for method, op in item.items():
            if method.lower() not in _METHODS or not isinstance(op, dict):
                continue
            loc = f"paths.{path}.{method.lower()}"

            # operationId
            op_id = op.get("operationId")
            if not op_id:
                add("operation-operationId", "warn", "operationId 가 없습니다.", loc)
            elif op_id in seen_op_ids:
                add("operation-operationId-unique", "error",
                    f"operationId '{op_id}' 가 중복됩니다(이전: {seen_op_ids[op_id]}).", loc)
            else:
                seen_op_ids[op_id] = loc

            # 설명/요약
            if not op.get("summary") and not op.get("description"):
                add("operation-description", "warn", "summary 또는 description 이 없습니다.", loc)

            # 태그
            op_tags = op.get("tags") or []
            if not op_tags:
                add("operation-tags", "info", "오퍼레이션에 tags 가 없습니다.", loc)
            else:
                used_tags.update(t for t in op_tags if isinstance(t, str))
                for t in op_tags:
                    if isinstance(t, str) and declared_tags and t not in declared_tags:
                        add("operation-tag-defined", "info",
                            f"태그 '{t}' 가 전역 tags 에 정의되어 있지 않습니다.", loc)

            # 응답
            responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
            if not responses:
                add("operation-responses", "error", "responses 가 정의되지 않았습니다.", f"{loc}.responses")
            else:
                codes = {str(c) for c in responses.keys()}
                if not any(c.startswith("2") or c == "default" for c in codes):
                    add("operation-success-response", "warn",
                        "2xx 성공 응답이 정의되지 않았습니다.", f"{loc}.responses")

            # 파라미터: 경로 템플릿 선언 + 설명 권장
            declared_path_params = set()
            for j, p in enumerate(op.get("parameters") or []):
                if not isinstance(p, dict):
                    continue
                p_in, p_name = p.get("in"), p.get("name")
                if p_in == "path":
                    declared_path_params.add(p_name)
                    if not p.get("required"):
                        add("path-params-required", "error",
                            f"경로 파라미터 '{p_name}' 는 required: true 여야 합니다.", f"{loc}.parameters[{j}]")
                if not p.get("description"):
                    add("parameter-description", "info",
                        f"파라미터 '{p_name}' 에 description 이 없습니다.", f"{loc}.parameters[{j}]")
            # 경로에 {x} 가 있는데 선언 안 됨
            missing = template_params - declared_path_params
            for mp in sorted(missing):
                add("path-params-declared", "error",
                    f"경로 템플릿 '{{{mp}}}' 에 대응하는 path 파라미터 정의가 없습니다.", loc)

    # 선언만 되고 사용되지 않은 전역 태그
    for t in sorted(declared_tags - used_tags):
        if t:
            add("tag-unused", "info", f"전역 태그 '{t}' 가 어떤 오퍼레이션에서도 사용되지 않습니다.", "tags")

    # --- 보안 --------------------------------------------------------------
    schemes = parser._security_schemes(spec, parser.detect_format(spec))
    global_security = spec.get("security")
    if global_security and not schemes:
        add("security-defined", "error",
            "security 가 참조되지만 securitySchemes 정의가 없습니다.",
            "components.securitySchemes" if oas3 else "securityDefinitions")
    if not global_security and not schemes:
        add("security-recommended", "info", "인증 스킴이 정의되어 있지 않습니다(공개 API 가 아니라면 권장).")

    return _summarize(findings)


def _split_braces(path: str) -> list[str]:
    """경로에서 '{param}' 토큰만 추출한다."""
    out: list[str] = []
    buf, depth = "", 0
    for ch in path:
        if ch == "{":
            depth, buf = 1, "{"
        elif ch == "}" and depth:
            out.append(buf + "}")
            depth, buf = 0, ""
        elif depth:
            buf += ch
    return out


def _summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warn")
    infos = sum(1 for f in findings if f["severity"] == "info")
    penalty = sum(_WEIGHT.get(f["severity"], 0) for f in findings)
    score = max(0, 100 - penalty)
    # 심각도 우선 정렬(error → warn → info)
    order = {"error": 0, "warn": 1, "info": 2}
    findings_sorted = sorted(findings, key=lambda f: (order.get(f["severity"], 9), f["rule"]))
    return {
        "score": score,
        "error_count": errors,
        "warning_count": warnings,
        "info_count": infos,
        "findings": findings_sorted,
    }
