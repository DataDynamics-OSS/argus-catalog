# SPDX-License-Identifier: Apache-2.0
"""필드 출처(origin) 레지스트리.

MCP Server 카탈로그는 다른 개발자가 만든 MCP 서버와도 호환되어야 하므로, 각 필드가
어디서 온 값인지 구분해 노출한다. 특히 **Argus Catalog 에서만 관리하는 정보**(소유자,
상태, 위험도, 정책 등)는 MCP 표준에 없는 값이므로 UI 에서 ``Argus`` 배지로 표시하고,
``server.json`` 내보내기 시 ``_meta["io.datadynamics.argus/catalog"]`` 아래에만 싣는다.

출처 종류:
  - ``mcp``           : MCP 서버가 프로토콜로 보고한 값 (initialize, tools/list, resources/list, prompts/list)
  - ``registry``      : MCP Registry ``server.json`` 표준 메타데이터
  - ``server_config`` : 서버 설정에서 수집한 값 (Argus Forge 설정 파일·manifest 등 — MCP 표준 밖)
  - ``argus``         : Argus Catalog 전용 관리 정보 (서버에는 존재하지 않음)
"""

from __future__ import annotations

# server.json ``_meta`` 안에서 Argus 전용 정보를 싣는 네임스페이스 키
ARGUS_META_NAMESPACE = "io.datadynamics.argus/catalog"

ORIGIN_LABELS = {
    "mcp": "MCP 서버 보고",
    "registry": "MCP Registry 표준",
    "server_config": "서버 설정 수집",
    "argus": "Argus 전용",
}

SERVER_FIELDS: dict[str, str] = {
    # Argus
    "name": "argus",
    "urn": "argus",
    "display_name": "argus",
    "status": "argus",
    "version": "argus",
    "owner_email": "argus",
    "department": "argus",
    "category": "argus",
    "tags": "argus",
    "provider_type": "argus",
    "vendor": "argus",
    "framework": "argus",
    "target_platform": "argus",
    "source_repo": "argus",
    "source_path": "argus",
    "package_checksum": "argus",
    "oci_artifact_ref": "argus",
    "supported_transports": "argus",
    "launch_config": "argus",
    "capability_hash": "argus",
    "last_synced_at": "argus",
    # Registry server.json
    "registry_name": "registry",
    "title": "registry",
    "description": "registry",
    "website_url": "registry",
    "repository_url": "registry",
    "license": "registry",
    "packages": "registry",
    "remotes": "registry",
    # MCP initialize
    "server_info": "mcp",
    "protocol_version": "mcp",
    "capabilities": "mcp",
    "instructions": "mcp",
}

DEPLOYMENT_FIELDS: dict[str, str] = {
    "name": "argus",
    "urn": "argus",
    "display_name": "argus",
    "description": "argus",
    "environment": "argus",
    "status": "argus",
    "owner_email": "argus",
    "department": "argus",
    "tags": "argus",
    "transport": "argus",
    "endpoint": "argus",
    "rest_api_base": "argus",
    "auth_method": "argus",
    "auth_config": "argus",
    "deployment_scope": "argus",
    "host": "argus",
    "datasource_id": "argus",
    "policy_mode": "argus",
    "policy_version": "argus",
    "collect_interval_minutes": "argus",
    "health_status": "argus",
    "last_health_check_at": "argus",
    "latency_ms": "argus",
    "observed_hash": "argus",
    "last_observed_at": "argus",
    "observed_source": "argus",
    # 서버 설정에서 수집
    "target_endpoint": "server_config",
    "service_principal": "server_config",
    "principal_auth": "server_config",
    "limits": "server_config",
    "security_warnings": "server_config",
    "instance_id": "server_config",
    # MCP initialize
    "deployed_version": "mcp",
    "server_info": "mcp",
    "protocol_version": "mcp",
}

TOOL_FIELDS: dict[str, str] = {
    "name": "mcp",
    "title": "mcp",
    "description": "mcp",
    "input_schema": "mcp",
    "output_schema": "mcp",
    "annotations": "mcp",
    "meta": "mcp",
    "group": "argus",
    "mutating": "argus",
    "risk": "argus",
    "risk_confirmed": "argus",
    "requires_approval": "argus",
    "resource_types": "argus",
    "notes": "argus",
    "source": "argus",
    "removed_at": "argus",
}

GROUP_FIELDS: dict[str, str] = {
    "key": "argus",
    "label": "argus",
    "description": "argus",
    "config_key": "server_config",
    "default_enabled": "server_config",
    "default_risk": "argus",
    "observed_enabled": "server_config",
    "approved_enabled": "argus",
}

RESOURCE_SCOPE_FIELDS: dict[str, str] = {
    "resource_type": "argus",
    "action": "argus",
    "effect": "argus",
    "patterns": "server_config",
    "config_key": "server_config",
    "layer": "argus",
}

# 행 단위 source 값 → 출처
ROW_SOURCE_ORIGINS = {
    "mcp": "mcp",
    "forge_rest": "mcp",
    "registry": "registry",
    "config": "server_config",
    "forge_seed": "argus",
    "manual": "argus",
    "manifest": "server_config",
    "adopted": "argus",
}


def field_origins() -> dict:
    """필드 출처 레지스트리 전체 — ``GET /mcp-servers/field-origins`` 응답(UI 배지용)."""
    return {
        "labels": ORIGIN_LABELS,
        "meta_namespace": ARGUS_META_NAMESPACE,
        "server": SERVER_FIELDS,
        "deployment": DEPLOYMENT_FIELDS,
        "tool": TOOL_FIELDS,
        "tool_group": GROUP_FIELDS,
        "resource_scope": RESOURCE_SCOPE_FIELDS,
        "row_source": ROW_SOURCE_ORIGINS,
    }


def argus_only(fields: dict[str, str], values: dict) -> dict:
    """``values`` 중 출처가 argus 인 키만 남긴다 (server.json ``_meta`` 내보내기용)."""
    return {k: v for k, v in values.items() if fields.get(k) == "argus" and v not in (None, [], {})}
