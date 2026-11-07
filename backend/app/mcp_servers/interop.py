# SPDX-License-Identifier: Apache-2.0
"""표준 형식 가져오기·내보내기 (다른 개발자가 만든 MCP 서버와의 호환).

가져오기
  - MCP Registry ``server.json`` (단건 또는 목록, Registry API 응답 ``{"servers": [...]}`` 포함)
  - 클라이언트 설정 ``mcp.json`` — ``{"mcpServers": {...}}`` (Claude Desktop/Code, Cursor) 또는
    ``{"servers": {...}}`` (VS Code). **환경변수 값·헤더 값은 버리고 이름만** 남긴다(비밀값 유입 방지).
    실행 인자(args)의 ``--token X``·``--api-key=X`` 같은 비밀류 값과 endpoint URL 의 쿼리스트링 값은
    ``***`` 로 가려서 저장한다.

내보내기
  - ``server.json`` — 표준 필드만 최상위에 두고, Argus 전용 정보는
    ``_meta["io.datadynamics.argus/catalog"]`` 아래에만 싣는다. 이 값을 다시 가져오면 복원된다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.mcp_servers.forge_mapping import FORGE_REPO, catalog_name, find_forge_server
from app.mcp_servers.origins import ARGUS_META_NAMESPACE, SERVER_FIELDS, argus_only

logger = logging.getLogger(__name__)

SERVER_JSON_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-09-29/server.schema.json"
_NAME_RE = re.compile(r"[^a-z0-9._-]+")  # 카탈로그 이름에 쓸 수 없는 문자 연속
# 값이 비밀일 가능성이 높은 인자 이름(``--token``, ``--api-key``, ``--client_secret``, ``--password`` …)
_SECRET_ARG_RE = re.compile(r"^-{1,2}[\w.-]*(token|key|secret|passw(or)?d|pwd|credential)[\w.-]*$", re.I)
_MASK = "***"

# server.json ``_meta`` 의 Argus 네임스페이스에서 가져오기 때 되살리는 필드(그 밖의 키는 무시)
_ARGUS_RESTORABLE = {
    "display_name", "status", "version", "owner_email", "department", "category", "tags",
    "provider_type", "vendor", "framework", "target_platform", "source_repo", "source_path",
    "package_checksum", "oci_artifact_ref", "supported_transports", "launch_config",
}


def slugify(value: str) -> str:
    """카탈로그 이름 규칙(소문자, 숫자, . _ -)으로 변환. Registry 이름 ``io.github.x/y`` → ``io.github.x.y``."""
    s = _NAME_RE.sub("-", (value or "").strip().lower().replace("/", ".")).strip("-.")
    return s or "mcp-server"


def _transport_of(kind: str | None) -> str:
    """표준 transport 표기 → 카탈로그 값. sse·stdio 외(``streamable-http``·``http``·미지정 등)는 모두 streamable_http."""
    k = (kind or "").lower()
    if k in ("sse",):
        return "sse"
    if k in ("stdio",):
        return "stdio"
    return "streamable_http"


# ---------------------------------------------------------------------------
# server.json
# ---------------------------------------------------------------------------


def parse_server_json(doc: Any) -> list[dict]:
    """server.json(단건/목록/Registry 응답) → 서버 정의 생성 payload 목록."""
    if isinstance(doc, dict) and isinstance(doc.get("servers"), list):
        items = [x.get("server", x) if isinstance(x, dict) else x for x in doc["servers"]]
    elif isinstance(doc, list):
        items = doc
    else:
        items = [doc]
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError("server.json 항목에 name 이 없습니다")
        out.append(_server_json_to_payload(item))
    logger.debug("server.json 파싱: servers=%d", len(out))
    return out


def _server_json_to_payload(item: dict) -> dict:
    """server.json 항목 하나 → 서버 정의 생성 payload(None 값 제외).

    ``_meta`` 의 Argus 네임스페이스가 있으면 복원 가능한 필드와 원래 카탈로그 이름을 덮어쓴다.
    """
    repo = item.get("repository") or {}
    packages = item.get("packages") or []
    remotes = item.get("remotes") or []
    transports = sorted(
        {_transport_of((p.get("transport") or {}).get("type")) for p in packages if isinstance(p, dict)}
        | {_transport_of(r.get("type")) for r in remotes if isinstance(r, dict)}
    )
    payload: dict[str, Any] = {
        "name": slugify(item["name"]),
        "registry_name": item["name"],
        "title": item.get("title"),
        "display_name": item.get("title"),
        "description": item.get("description"),
        "version": item.get("version") or "0.1.0",
        "website_url": item.get("websiteUrl"),
        "repository_url": repo.get("url") if isinstance(repo, dict) else None,
        "packages": packages or None,
        "remotes": remotes or None,
        "supported_transports": transports or None,
        "provider_type": "community",
        "framework": "generic",
    }
    meta = (item.get("_meta") or {}).get(ARGUS_META_NAMESPACE)
    if isinstance(meta, dict):
        for k, v in meta.items():
            if k in _ARGUS_RESTORABLE:
                payload[k] = v
        if isinstance(meta.get("name"), str):
            payload["name"] = meta["name"]
    forge = find_forge_server(payload["name"]) if payload.get("framework") == "argus-forge" else None
    if forge:
        payload.setdefault("target_platform", forge.target_platform)
    return {k: v for k, v in payload.items() if v is not None}


def export_server_json(server: dict, tools: list[dict] | None = None,
                       deployments: list[dict] | None = None) -> dict:
    """카탈로그 서버 정의 → server.json. Argus 전용 정보는 ``_meta`` 네임스페이스에만."""
    doc: dict[str, Any] = {
        "$schema": SERVER_JSON_SCHEMA,
        "name": server.get("registry_name") or server["name"],
        "description": server.get("description") or "",
        "version": server.get("version") or "0.1.0",
    }
    if server.get("title"):
        doc["title"] = server["title"]
    if server.get("website_url"):
        doc["websiteUrl"] = server["website_url"]
    if server.get("repository_url"):
        doc["repository"] = {"url": server["repository_url"], "source": _repo_source(server["repository_url"])}
    if server.get("packages"):
        doc["packages"] = server["packages"]
    remotes = list(server.get("remotes") or [])
    for d in deployments or []:
        if d.get("endpoint") and d.get("transport") in ("streamable_http", "sse") and d.get("status") == "active":
            remote = {"type": "streamable-http" if d["transport"] == "streamable_http" else "sse",
                      "url": d["endpoint"]}
            if remote not in remotes:
                remotes.append(remote)
    if remotes:
        doc["remotes"] = remotes

    argus = argus_only(SERVER_FIELDS, server)
    argus["name"] = server["name"]
    if tools:
        argus["tools"] = [
            {k: t[k] for k in ("name", "group", "risk", "risk_confirmed", "requires_approval", "resource_types")
             if t.get(k) not in (None, [], False)}
            for t in tools
        ]
    doc["_meta"] = {ARGUS_META_NAMESPACE: argus}
    return doc


def _repo_source(url: str) -> str:
    """저장소 URL 의 호스팅 종류(github/gitlab/bitbucket, 그 밖은 git)."""
    u = url.lower()
    for host in ("github", "gitlab", "bitbucket"):
        if host in u:
            return host
    return "git"


# ---------------------------------------------------------------------------
# mcp.json (클라이언트 설정)
# ---------------------------------------------------------------------------


def parse_mcp_json(doc: Any) -> list[dict]:
    """mcp.json → ``[{"server": {...}, "deployment": {...} | None}]``.

    - command 기반(stdio): 정의만 만들고 ``launch_config`` 에 command·args(비밀류 옵션 값은 ``***``)·env **키 이름**만 저장
    - url 기반(http/sse): 정의 + 배포(endpoint, 쿼리스트링 값은 ``***``) 생성. 헤더는 이름만 ``auth_config.header_names`` 로
    """
    if not isinstance(doc, dict):
        raise ValueError("mcp.json 최상위는 객체여야 합니다")
    servers = doc.get("mcpServers") or doc.get("servers") or doc.get("mcp", {}).get("servers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError("mcpServers(또는 servers) 항목이 없습니다")
    out = []
    for key, cfg in servers.items():
        if not isinstance(cfg, dict):
            logger.warning("mcp.json 항목 무시(객체가 아님): key=%s", key)
            continue
        name = slugify(key)
        forge = _detect_forge(cfg)
        server: dict[str, Any] = {
            "name": catalog_name(forge) if forge else name,
            "display_name": key,
            "provider_type": "internal" if forge else "community",
            "framework": "argus-forge" if forge else "generic",
        }
        if forge:
            server.update(
                target_platform=forge.target_platform,
                description=forge.description,
                source_repo=FORGE_REPO,
                source_path=forge.source_path,
                supported_transports=["stdio", "streamable_http"],
            )
        deployment = None
        url = cfg.get("url") or cfg.get("serverUrl")
        if url:
            transport = _transport_of(cfg.get("type") or cfg.get("transport"))
            server.setdefault("supported_transports", [transport])
            headers = cfg.get("headers") or {}
            auth = "bearer" if any(h.lower() == "authorization" for h in headers) else (
                "api_key" if headers else "none")
            deployment = {
                "name": f"{name}-{_host_slug(url)}",
                "display_name": key,
                "transport": transport,
                "endpoint": _mask_url_query(url),
                "auth_method": auth,
                "auth_config": {"header_names": sorted(headers)} if headers else None,
                "deployment_scope": "external" if not forge else "shared",
            }
        elif cfg.get("command"):
            server["launch_config"] = {
                "command": cfg.get("command"),
                "args": _mask_args(cfg.get("args") or []),
                "env_keys": sorted((cfg.get("env") or {}).keys()),
            }
            server.setdefault("supported_transports", ["stdio"])
        out.append({"server": server, "deployment": deployment})
    logger.debug("mcp.json 파싱: servers=%d with_deployment=%d",
                 len(out), sum(1 for o in out if o["deployment"]))
    return out


def _mask_args(args: list) -> list[str]:
    """실행 인자에서 비밀류 옵션의 값을 가린다 — ``--token X`` 는 X 를, ``--token=X`` 는 ``=`` 뒤를 ``***`` 로."""
    out: list[str] = []
    mask_next = False
    for a in (str(x) for x in args):
        if mask_next:
            out.append(_MASK)
            mask_next = False
            continue
        opt, eq, _val = a.partition("=")
        if _SECRET_ARG_RE.match(opt):
            if eq:
                out.append(f"{opt}={_MASK}")
            else:
                out.append(a)
                mask_next = True
            continue
        out.append(a)
    return out


def _mask_url_query(url: str) -> str:
    """URL 쿼리스트링의 값을 모두 ``***`` 로 가린다(키 이름은 유지). 쿼리가 없으면 그대로."""
    base, sep, query = url.partition("?")
    if not sep:
        return url
    frag = ""
    if "#" in query:
        query, _, frag = query.partition("#")
        frag = "#" + frag
    masked = "&".join(f"{p.partition('=')[0]}={_MASK}" if "=" in p else p for p in query.split("&") if p)
    return f"{base}?{masked}{frag}"


def _detect_forge(cfg: dict):
    """command·args 의 실행 파일 이름(경로 마지막 마디)이 Forge 패키지(``*-mcp``)면 그 Forge 서버 정의."""
    words = [str(cfg.get("command") or "")] + [str(a) for a in cfg.get("args") or []]
    for w in words:
        base = w.rsplit("/", 1)[-1]
        forge = find_forge_server(base)
        if forge and (base.endswith("-mcp") or base == forge.package_name):
            return forge
    return None


def _host_slug(url: str) -> str:
    """URL 호스트의 첫 마디를 slug 로 — 배포 이름 접미사용(호스트 없으면 ``remote``)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or "remote"
    return slugify(host.split(".")[0])
