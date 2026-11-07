# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 정보 정규화.

- 표준 MCP 도구(annotation)를 카탈로그 도구 그룹으로 분류 (다른 개발자가 만든 서버 호환)
- 도구·리소스·프롬프트 목록 해시와 diff
- Argus Forge 서버 설정(YAML)을 관측 상태(그룹 on/off, 리소스 범위, limits, 실행 계정)로 변환

비밀값은 다루지 않는다. 설정에서 명시한 경로의 값만 읽으며, 비밀번호·토큰 등은
Forge 규칙상 YAML 이 아니라 ``*_env`` 가 가리키는 환경변수에 있다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.mcp_servers.forge_mapping import ForgeServer

logger = logging.getLogger(__name__)

# 위험도 서열 — 값 검증과 "더 높은 위험" 비교에 쓴다
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# 표준 MCP 서버용 자동 그룹 (annotation 기반)
GENERIC_GROUPS: tuple[dict, ...] = (
    {"key": "read", "label": "조회", "default_risk": "low", "mutating": False, "sort_order": 0,
     "description": "readOnlyHint=true 인 도구"},
    {"key": "write", "label": "변경", "default_risk": "high", "mutating": True, "sort_order": 10,
     "description": "readOnlyHint=false, destructiveHint=false 인 도구"},
    {"key": "destructive", "label": "파괴적 변경", "default_risk": "critical", "mutating": True,
     "sort_order": 20, "description": "destructiveHint=true 인 도구"},
    {"key": "unclassified", "label": "미분류", "default_risk": "high", "mutating": True,
     "sort_order": 30,
     "description": "annotation 이 없는 도구. MCP 기본값상 변경·파괴 가능으로 간주되므로 검토 필요"},
)


def classify_annotations(annotations: dict | None, mutating: bool | None = None) -> str:
    """MCP ToolAnnotations → 자동 그룹 key.

    MCP 스펙 기본값: readOnlyHint=false, destructiveHint=true. 따라서 annotation 이 없는
    도구는 변경·파괴 가능으로 보고 ``unclassified`` 로 둔다. Forge REST 의 ``mutating``
    플래그는 False 면 annotation 과 무관하게 ``read`` 로 확정하고, True 면 destructiveHint 가
    없을 때 ``unclassified`` 대신 ``write`` 로 둔다(readOnlyHint=true·destructiveHint 는 그대로 존중).
    """
    ann = annotations or {}
    read_only = ann.get("readOnlyHint", ann.get("read_only_hint"))
    destructive = ann.get("destructiveHint", ann.get("destructive_hint"))
    if mutating is False or read_only is True:
        return "read"
    if destructive is False:
        return "write"
    if destructive is True:
        return "destructive"
    if mutating is True:
        return "write"
    return "unclassified"


def is_mutating(annotations: dict | None) -> bool:
    """annotation 기준 변경 도구 여부 — ``read`` 로 분류되지 않으면 True."""
    return classify_annotations(annotations) != "read"


def stable_hash(value: Any) -> str:
    """키 정렬 JSON 의 sha256 hex — dict 키 순서와 무관한 결정적 해시."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capability_fingerprint(tools: list[dict], resources: list[dict], prompts: list[dict]) -> str:
    """수집된 도구(이름·설명·입력 스키마·annotations)·리소스 uri·프롬프트 이름 전체의 해시."""
    return stable_hash(
        {
            "tools": sorted(
                (
                    {
                        "name": t.get("name"),
                        "description": t.get("description"),
                        "inputSchema": t.get("inputSchema"),
                        "annotations": t.get("annotations"),
                    }
                    for t in tools
                ),
                key=lambda t: t["name"] or "",
            ),
            "resources": sorted(r.get("uri") or r.get("uriTemplate") or "" for r in resources),
            "prompts": sorted(p.get("name") or "" for p in prompts),
        }
    )


def diff_named(old: dict[str, dict], new: dict[str, dict], keys: tuple[str, ...]) -> dict:
    """이름 → 속성 dict 두 개의 차이 (added / removed / changed)."""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(
        n for n in set(old) & set(new) if any(old[n].get(k) != new[n].get(k) for k in keys)
    )
    return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# Forge 설정 파싱
# ---------------------------------------------------------------------------


def get_path(data: Any, path: str) -> Any:
    """``a.b.c`` / ``a.b[].c`` 경로 값. 없으면 None.

    ``[]`` 마디는 목록의 각 항목에 나머지 경로를 적용해 None·빈 문자열을 뺀 목록을 돌려준다.
    """
    cur: Any = data
    parts = path.split(".")
    for i, part in enumerate(parts):
        if part.endswith("[]"):
            seq = cur.get(part[:-2]) if isinstance(cur, dict) else None
            if not isinstance(seq, list):
                return None
            rest = ".".join(parts[i + 1 :])
            values = [get_path(item, rest) if rest else item for item in seq]
            return [v for v in values if v not in (None, "")]
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(data: dict, paths: tuple[str, ...]) -> str | None:
    """후보 경로 중 처음으로 값이 있는 것을 문자열로(목록은 쉼표로 이어 붙임). 없으면 None."""
    for p in paths:
        v = get_path(data, p)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v)
    return None


def _collect_limits(data: Any, prefix: str = "", out: dict | None = None) -> dict:
    """설정 트리의 모든 ``limits`` 매핑에서 스칼라 값(bool 제외)을 ``경로.키`` 로 평탄화해 모은다.

    logging·paths·process 하위는 limits 가 아니므로 내려가지 않는다.
    """
    out = {} if out is None else out
    if isinstance(data, dict):
        for k, v in data.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if k == "limits" and isinstance(v, dict):
                for lk, lv in v.items():
                    if isinstance(lv, (int, float, str)) and not isinstance(lv, bool):
                        out[f"{path}.{lk}"] = lv
            elif isinstance(v, dict) and k not in ("logging", "paths", "process"):
                _collect_limits(v, path, out)
    return out


def _security_warnings(data: Any, enabled_mutating: list[str], prefix: str = "") -> list[str]:
    """설정에서 읽을 수 있는 보안 경고 (Forge 서버별 warnings() 의 공통 부분)."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if k in ("require_verify", "verify_tls") and v is False:
                found.append(f"TLS 인증서 검증이 꺼져 있습니다 ({p}: false)")
            elif k.startswith("allow_cleartext") and v is True:
                found.append(f"평문 인증 전송을 허용합니다 ({p}: true)")
            elif k == "allow_superuser_ugi" and v is True:
                found.append(f"슈퍼유저 UGI 사칭을 허용합니다 ({p}: true)")
            elif isinstance(v, dict):
                walk(v, p)

    walk(data, prefix)
    mcp = data.get("mcp") if isinstance(data, dict) else None
    if isinstance(mcp, dict) and mcp.get("host") in ("0.0.0.0", "::") and enabled_mutating:
        found.append("모든 인터페이스(0.0.0.0)에 바인딩된 서버에서 변경 도구가 켜져 있습니다")
    return found


@dataclass
class ObservedConfig:
    """Forge 설정 파일에서 읽은 관측 상태(그룹 on/off·범위·limits·대상 시스템·보안 경고·listen 정보)."""

    groups: dict[str, bool] = field(default_factory=dict)
    scopes: list[dict] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)
    target_endpoint: str | None = None
    service_principal: str | None = None
    principal_auth: str | None = None
    security_warnings: list[str] = field(default_factory=list)
    transport: str | None = None
    listen_endpoint: str | None = None
    rest_api_path: str | None = None
    unknown_groups: list[str] = field(default_factory=list)


def parse_forge_config(text: str, server: ForgeServer) -> ObservedConfig:
    """Forge 서버 ``config.yaml`` → 관측 상태. 형식이 잘못되면 ValueError.

    그룹 설정 키가 없으면 그룹의 ``default_enabled`` 를 쓰고, ``read`` 그룹은 항상 켜진 것으로 본다.
    """
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("Forge 설정 YAML 파싱 실패: server=%s error=%s", server.name, exc)
        raise ValueError(f"YAML 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("설정 파일 최상위는 매핑이어야 합니다")

    obs = ObservedConfig()
    for g in server.groups:
        value = get_path(data, g.config_key)
        obs.groups[g.key] = bool(value) if value is not None else g.default_enabled
    obs.groups["read"] = True

    for sc in server.scopes:
        patterns = get_path(data, sc.config_key)
        if patterns is None:
            continue
        if isinstance(patterns, str):
            patterns = [patterns]
        obs.scopes.append(
            {
                "resource_type": sc.resource_type,
                "action": sc.action,
                "effect": sc.effect,
                "patterns": [str(p) for p in patterns],
                "config_key": sc.config_key,
                "description": sc.description or None,
            }
        )

    obs.limits = _collect_limits(data)
    obs.target_endpoint = _first(data, server.target_keys)
    obs.service_principal = _first(data, server.principal_keys)
    obs.principal_auth = _first(data, server.auth_keys)
    enabled_mutating = [g.key for g in server.groups if g.mutating and obs.groups.get(g.key)]
    obs.security_warnings = _security_warnings(data, enabled_mutating)
    if obs.principal_auth and obs.principal_auth.lower() in ("none", "plaintext") and enabled_mutating:
        obs.security_warnings.append(
            f"인증 없는 대상 연결({obs.principal_auth})에서 변경 도구가 켜져 있습니다: {', '.join(enabled_mutating)}"
        )

    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    transport = mcp.get("transport")
    if transport:
        obs.transport = "streamable_http" if transport == "streamable-http" else transport
    host, port, path = mcp.get("host"), mcp.get("port"), mcp.get("path") or "/mcp"
    if host and port:
        obs.listen_endpoint = f"http://{host}:{port}{path}"
    api = data.get("api") if isinstance(data.get("api"), dict) else {}
    if api.get("enabled", True):
        obs.rest_api_path = api.get("path") or "/api/v1"
    logger.debug("Forge 설정 파싱: server=%s groups_on=%d scopes=%d limits=%d warnings=%d",
                 server.name, sum(1 for v in obs.groups.values() if v), len(obs.scopes), len(obs.limits),
                 len(obs.security_warnings))
    return obs
