# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 manifest 수집·반영 (설계 §6.2).

manifest 는 서버가 스스로 보고하는 "도구 ↔ 그룹 ↔ 노출 여부 ↔ 리소스 범위 ↔ limits ↔
대상 시스템" 정보다. MCP 표준(``tools/list``)은 **등록된 도구만** 알려주므로 꺼진 그룹의
도구를 알 수 없지만, manifest 는 ``exposed=false`` 인 도구까지 싣기 때문에 그룹 on/off 를
추론 없이 확정할 수 있다.

두 가지 형태:
  - **배포 manifest** (``GET {api}/manifest`` 또는 파일) — ``exposed``·``enabled``·``target`` 포함.
    정의(도구·그룹)와 배포 관측값(노출·그룹 상태·범위·limits·보안 경고)을 모두 갱신한다.
  - **정적 manifest** (릴리즈 산출물 ``mcp-manifest.json``) — 위 항목이 없다. 정의만 갱신한다.

형식 (필수는 ``tools[].name`` 뿐, 나머지는 있으면 반영)::

    {
      "server":  {"name": "hive-mcp", "version": "1.2.0", "instance_id": "...", "transport": "streamable-http"},
      "target":  {"platform": "hive", "endpoint": "thrift://hms:9083", "principal": "mcp", "auth": "kerberos"},
      "groups":  [{"key": "dml", "config_key": "allow.dml", "enabled": false, "mutating": true, "risk": "high"}],
      "tools":   [{"name": "execute", "group": "dml", "mutating": true, "exposed": false,
                   "description": "...", "inputSchema": {...}, "annotations": {...}}],
      "resource_scopes": [{"config_key": "...", "resource_type": "topic", "action": "read",
                           "effect": "deny", "patterns": ["__*"]}],
      "limits": {"limits.max_rows": 200},
      "security_warnings": ["TLS certificate verification is disabled"]
    }

비밀값은 manifest 에 싣지 않는 것이 계약이다. 그래도 들어온 경우를 대비해 ``target`` 에서는
platform/endpoint/principal/auth 네 키만 읽는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_servers import normalize
from app.mcp_servers.models import (
    McpDeployment,
    McpDeploymentGroupState,
    McpDeploymentToolState,
    McpResourceScope,
    McpServer,
    McpServerTool,
    McpServerToolGroup,
)

logger = logging.getLogger(__name__)

# 허용되는 범위 규칙 action/effect 값 — 그 밖의 action 은 any 로, effect 가 다르면 규칙을 버린다
_ACTIONS = {"read", "write", "delete", "any"}
_EFFECTS = {"allow", "deny", "protect", "mask"}
# manifest server.transport 표기 → 카탈로그 transport 값
_TRANSPORTS = {"streamable-http": "streamable_http", "streamable_http": "streamable_http", "http": "streamable_http",
               "sse": "sse", "stdio": "stdio"}


@dataclass
class ParsedManifest:
    """검증·정리된 manifest — 알 수 없는 키·형식이 틀린 항목은 이미 버려진 상태."""

    server: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)
    groups: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    scopes: list[dict] = field(default_factory=list)
    limits: dict = field(default_factory=dict)
    security_warnings: list[str] = field(default_factory=list)
    # exposed/enabled 정보나 target 이 하나라도 있으면 배포 manifest
    has_runtime_state: bool = False


def parse_manifest(content: Any) -> ParsedManifest:
    """manifest JSON(객체 또는 문자열) → ``ParsedManifest``. 형식 오류는 ``ValueError``."""
    if isinstance(content, (str, bytes)):
        try:
            content = json.loads(content)
        except ValueError as exc:
            raise ValueError(f"manifest JSON 파싱 실패: {exc}") from exc
    if not isinstance(content, dict):
        raise ValueError("manifest 는 JSON 객체여야 합니다")
    raw_tools = content.get("tools")
    if not isinstance(raw_tools, list):
        raise ValueError("manifest 에 tools 배열이 없습니다")

    out = ParsedManifest()
    srv = content.get("server") if isinstance(content.get("server"), dict) else {}
    out.server = {k: srv.get(k) for k in ("name", "version", "instance_id", "transport") if srv.get(k)}
    tgt = content.get("target") if isinstance(content.get("target"), dict) else {}
    out.target = {k: str(tgt[k]) for k in ("platform", "endpoint", "principal", "auth") if tgt.get(k)}

    for g in content.get("groups") or []:
        if not isinstance(g, dict) or not g.get("key"):
            logger.debug("manifest 그룹 항목 무시(key 없음)")
            continue
        item = {"key": str(g["key"])[:100]}
        for k in ("config_key", "label", "description"):
            if g.get(k):
                item[k] = str(g[k])
        if isinstance(g.get("enabled"), bool):
            item["enabled"] = g["enabled"]
            out.has_runtime_state = True
        if isinstance(g.get("mutating"), bool):
            item["mutating"] = g["mutating"]
        if g.get("risk") in normalize.RISK_ORDER:
            item["risk"] = g["risk"]
        out.groups.append(item)

    seen: set[str] = set()
    for t in raw_tools:
        if not isinstance(t, dict) or not t.get("name") or t["name"] in seen:
            continue
        seen.add(t["name"])
        item = {
            "name": str(t["name"])[:200],
            "title": t.get("title"),
            "description": t.get("description"),
            "inputSchema": t.get("inputSchema") or t.get("input_schema"),
            "outputSchema": t.get("outputSchema") or t.get("output_schema"),
            "annotations": t.get("annotations"),
            "group": t.get("group"),
            "resource_types": t.get("resource_types") if isinstance(t.get("resource_types"), list) else None,
        }
        if isinstance(t.get("mutating"), bool):
            item["mutating"] = t["mutating"]
        if isinstance(t.get("exposed"), bool):
            item["exposed"] = t["exposed"]
            out.has_runtime_state = True
        out.tools.append(item)

    for sc in content.get("resource_scopes") or []:
        if not isinstance(sc, dict):
            continue
        patterns = sc.get("patterns")
        if isinstance(patterns, str):
            patterns = [patterns]
        if not sc.get("resource_type") or sc.get("effect") not in _EFFECTS or not patterns:
            logger.debug("manifest 범위 규칙 무시(유형·effect·patterns 부족): config_key=%s", sc.get("config_key"))
            continue
        out.scopes.append({
            "resource_type": str(sc["resource_type"])[:50],
            "action": sc.get("action") if sc.get("action") in _ACTIONS else "any",
            "effect": sc["effect"],
            "patterns": [str(p) for p in patterns][:500],
            "config_key": sc.get("config_key"),
            "description": sc.get("description"),
        })
    if isinstance(content.get("limits"), dict):
        out.limits = {str(k): v for k, v in content["limits"].items() if isinstance(v, (int, float, str, bool))}
    out.security_warnings = [str(w) for w in content.get("security_warnings") or [] if w][:100]
    out.has_runtime_state = out.has_runtime_state or bool(out.target)
    logger.debug("manifest 파싱: server=%s tools=%d groups=%d scopes=%d runtime=%s",
                 out.server.get("name"), len(out.tools), len(out.groups), len(out.scopes), out.has_runtime_state)
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def apply_manifest(session: AsyncSession, server: McpServer, dep: McpDeployment | None,
                         m: ParsedManifest) -> dict:
    """manifest 를 정의(도구·그룹)와 (있으면) 배포 관측값에 반영하고 diff 를 돌려준다. commit 은 호출자.

    - 정의: 없는 그룹·도구는 만들고, manifest 에 빠진 도구(수동 등록 제외)는 ``removed_at`` 표시.
    - 배포(``dep`` 가 있을 때): 도구 노출·그룹 on/off 관측값, 관측 범위(config/manifest 출처 교체),
      limits·보안 경고·대상 시스템·버전을 갱신한다.
    반환 diff: ``tools``(added/changed/removed), ``new_groups``, 배포면 ``groups``·``scopes``·``limits`` 등.
    """
    from app.mcp_servers import service as svc

    now = _now()
    groups = await svc._ensure_groups(session, server)
    group_changes_def: list[str] = []
    for g in m.groups:
        grp = groups.get(g["key"])
        if grp is None:
            mutating = g.get("mutating")
            if mutating is None:
                mutating = any(t.get("mutating") for t in m.tools if t.get("group") == g["key"])
            grp = McpServerToolGroup(
                mcp_server_id=server.id, key=g["key"], label=g.get("label") or g["key"],
                description=g.get("description"), config_key=g.get("config_key"),
                default_enabled=not mutating, default_risk=g.get("risk") or ("high" if mutating else "low"),
                mutating=bool(mutating), sort_order=100 + len(groups),
            )
            session.add(grp)
            groups[g["key"]] = grp
            group_changes_def.append(g["key"])
        else:
            if g.get("config_key"):
                grp.config_key = g["config_key"]
            if isinstance(g.get("mutating"), bool):
                grp.mutating = g["mutating"]
    await session.flush()
    groups_by_id = {g.id: g for g in groups.values()}

    existing = {
        t.name: t for t in (
            await session.execute(select(McpServerTool).where(McpServerTool.mcp_server_id == server.id))
        ).scalars().all()
    }
    old_view = {n: {"description": t.description, "input_schema": t.input_schema, "annotations": t.annotations}
                for n, t in existing.items() if not t.removed_at and t.source != "forge_seed"}
    new_view: dict[str, dict] = {}
    names = set()
    for raw in m.tools:
        name = raw["name"]
        names.add(name)
        new_view[name] = {"description": raw.get("description"), "input_schema": raw.get("inputSchema"),
                          "annotations": raw.get("annotations")}
        key = raw.get("group")
        if key and key not in groups:
            # 선언되지 않은 그룹 — 도구 mutating 으로 만든다
            mutating = bool(raw.get("mutating"))
            grp = McpServerToolGroup(mcp_server_id=server.id, key=str(key)[:100], label=str(key),
                                     default_enabled=not mutating, default_risk="high" if mutating else "low",
                                     mutating=mutating, sort_order=100 + len(groups))
            session.add(grp)
            await session.flush()
            groups[grp.key] = grp
            groups_by_id[grp.id] = grp
        if not key:
            key = normalize.classify_annotations(raw.get("annotations"), raw.get("mutating"))
        group = groups.get(key) or groups.get("unclassified")
        tool = existing.get(name)
        if tool is None:
            tool = McpServerTool(mcp_server_id=server.id, name=name, source="manifest",
                                 group_id=group.id if group else None,
                                 mutating=raw.get("mutating", group.mutating if group else True),
                                 risk=group.default_risk if group else "high")
            session.add(tool)
            existing[name] = tool
        else:
            if tool.source in ("forge_seed", "manual", "forge_rest", "mcp"):
                tool.source = "manifest"
            tool.removed_at = None
            # manifest 의 그룹 선언은 서버 코드 기준이므로 우선한다
            if raw.get("group") and group:
                if tool.group_id != group.id and not tool.risk_confirmed:
                    tool.risk = group.default_risk
                tool.group_id = group.id
            if "mutating" in raw:
                tool.mutating = raw["mutating"]
            elif group:
                tool.mutating = group.mutating
        tool.title = raw.get("title") or tool.title
        tool.description = raw.get("description") if raw.get("description") is not None else tool.description
        tool.input_schema = raw.get("inputSchema") or tool.input_schema
        tool.output_schema = raw.get("outputSchema") or tool.output_schema
        tool.annotations = raw.get("annotations") or tool.annotations
        if raw.get("resource_types"):
            tool.resource_types = [str(r) for r in raw["resource_types"]]
    # manifest 는 전체 목록이므로 빠진 도구(수동 등록 제외)는 정의에서 사라진 것으로 표시
    removed = []
    for name, tool in existing.items():
        if name not in names and tool.source != "manual" and not tool.removed_at:
            tool.removed_at = now
            removed.append(name)
    await session.flush()

    tools_diff = normalize.diff_named(old_view, new_view, ("description", "input_schema", "annotations"))
    tools_diff["removed"] = sorted(set(tools_diff["removed"]) | set(removed))
    diff: dict[str, Any] = {"tools": tools_diff, "new_groups": group_changes_def}
    server.last_synced_at = now
    if m.server.get("version"):
        server.server_info = {**(server.server_info or {}), "version": m.server["version"],
                              **({"name": m.server["name"]} if m.server.get("name") else {})}

    if dep is None:
        logger.info("manifest 정의 반영: server=%s tools=%d removed=%d new_groups=%d",
                    server.name, len(names), len(removed), len(group_changes_def))
        return diff

    # --- 배포 관측값 ---
    await svc._init_group_states(session, server, dep)
    gstates = {
        gs.group_id: gs for gs in (
            await session.execute(select(McpDeploymentGroupState).where(McpDeploymentGroupState.deployment_id == dep.id))
        ).scalars().all()
    }
    tstates = {
        s.tool_id: s for s in (
            await session.execute(select(McpDeploymentToolState).where(McpDeploymentToolState.deployment_id == dep.id))
        ).scalars().all()
    }
    exposed_by_group: dict[int, bool] = {}
    exposure_known = any("exposed" in t for t in m.tools)
    for raw in m.tools:
        tool = existing[raw["name"]]
        st = tstates.get(tool.id)
        if st is None:
            st = McpDeploymentToolState(deployment_id=dep.id, tool_id=tool.id)
            session.add(st)
            tstates[tool.id] = st
        # manifest 전체에 exposed 가 없으면 실린 도구 = 등록된 도구로 본다(일부만 있으면 빠진 도구는 관측값 유지)
        exposed = raw.get("exposed", True if not exposure_known else None)
        if exposed is not None:
            st.observed_exposed = exposed
            st.observed_at = now
            if tool.group_id:
                exposed_by_group[tool.group_id] = exposed_by_group.get(tool.group_id, False) or exposed
    for name, tool in existing.items():
        if name not in names and tool.id in tstates:
            tstates[tool.id].observed_exposed = False
            tstates[tool.id].observed_at = now

    group_changes: dict[str, list] = {}
    declared = {g["key"]: g.get("enabled") for g in m.groups if "enabled" in g}
    for gid, gs in gstates.items():
        g = groups_by_id.get(gid)
        if g is None:
            continue
        if g.key in declared:
            new, basis = declared[g.key], "manifest"
        elif gid in exposed_by_group:
            new, basis = exposed_by_group[gid], "manifest"
        else:
            continue
        if gs.observed_enabled != new:
            group_changes[g.key] = [gs.observed_enabled, new]
        gs.observed_enabled = new
        gs.observed_basis = basis
    diff["groups"] = group_changes

    if m.scopes or m.has_runtime_state:
        old = (
            await session.execute(
                select(McpResourceScope).where(McpResourceScope.deployment_id == dep.id,
                                               McpResourceScope.layer == "observed",
                                               McpResourceScope.agent_link_id.is_(None),
                                               McpResourceScope.source.in_(("config", "manifest")))
            )
        ).scalars().all()
        for s in old:
            await session.delete(s)
        for sc in m.scopes:
            session.add(McpResourceScope(deployment_id=dep.id, layer="observed", source="manifest",
                                         match="fnmatch", **sc))
        diff["scopes"] = len(m.scopes)
    if m.limits or m.has_runtime_state:
        dep.limits = m.limits or None
        diff["limits"] = len(m.limits)
    if m.has_runtime_state:
        dep.security_warnings = m.security_warnings or None
        diff["security_warnings"] = m.security_warnings
    if m.target:
        dep.target_endpoint = m.target.get("endpoint") or dep.target_endpoint
        dep.service_principal = m.target.get("principal") or dep.service_principal
        dep.principal_auth = m.target.get("auth") or dep.principal_auth
    if m.server.get("version"):
        dep.deployed_version = str(m.server["version"])[:100]
    if m.server.get("instance_id"):
        dep.instance_id = str(m.server["instance_id"])[:200]
    if m.server.get("transport") in _TRANSPORTS and not dep.endpoint:
        dep.transport = _TRANSPORTS[m.server["transport"]]
    dep.observed_source = "manifest"
    dep.last_observed_at = now
    dep.observed_hash = normalize.stable_hash(
        sorted((t["name"], t.get("exposed")) for t in m.tools)
    )
    await session.flush()
    logger.info("manifest 배포 반영: server=%s deployment=%s tools=%d removed=%d group_changes=%d scopes=%d",
                server.name, dep.name, len(names), len(removed), len(group_changes), len(m.scopes))
    return diff
