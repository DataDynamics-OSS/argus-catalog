# SPDX-License-Identifier: Apache-2.0
"""외부 에이전트 소개 정보(에이전트 카드) 읽어 들이기.

사용자가 준 주소에서 소개 정보(``/.well-known/agent-card.json`` · ``/.well-known/agent.json``, 또는 ``.json`` 주소 그대로)를
읽어 정의 파일(``agent.yaml``) 내용으로 바꾼 **미리 보기**만 돌려준다. 저장은 사용자가 확인·보완한 뒤 ``apply`` 로 한다.

설계: design/ai-agent-bulk-registration-design.md §5.3

받는 형식
- A2A 1.0 에이전트 카드: ``name, description, version, supportedInterfaces[{url, protocolBinding, protocolVersion}],
  capabilities{streaming, extensions[]}, defaultInputModes, defaultOutputModes,
  skills[{id, name, description, tags, examples, inputModes, outputModes}](→ ``spec.skills``), securitySchemes{<이름>: {apiKeySecurityScheme |
  httpAuthSecurityScheme | oauth2SecurityScheme | openIdConnectSecurityScheme | mtlsSecurityScheme}}``
- A2A 0.3 이하 카드: 호출 주소가 최상위 ``url``·``preferredTransport``, 인증은 ``securitySchemes{type, scheme}`` 또는 ``authentication{schemes}``
- 이 카탈로그가 내보내는 카드(``GET /ai-agents/{name}/card``): A2A 1.0 형식이고, 식별 이름·소유자·부서·분류·인증 방식 등
  카탈로그 정보를 ``capabilities.extensions`` 의 카탈로그 확장(``CATALOG_CARD_EXTENSION_URI``) ``params`` 로 싣는다.
- 예전 이 카탈로그 카드: ``display_name, protocol, auth_method, provider{organization(=부서), contact(=소유자)},
  capabilities[], supported_languages[], skills[], streaming``

외부 카드의 ``provider.organization`` 은 카드를 제공한 조직이라 사내 부서로 옮기지 않는다(부서는 사용자가 채운다).

보안(서버가 사용자 대신 외부 주소를 호출하므로)
- 공용 외부 호출 정책(``app.core.outbound.check_url``)을 매 요청·리다이렉트마다 적용 — http/https 만, 루프백·링크로컬
  (클라우드 메타데이터)·카탈로그 내부 인프라 차단, ``api_outbound.*`` 설정(사설망 차단·허용/차단 목록) 그대로.
- 응답 크기 1MB, 요청당 10초, 전체 20초, 리다이렉트 5회 제한. JSON 이 아니면 거절.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.manifest import API_VERSION, AgentManifest, dump_yaml
from app.agents.models import AIAgent
from app.agents.schemas import CATALOG_CARD_EXTENSION_URI
from app.core.outbound import OutboundBlockedError, OutboundResolveError, check_url

logger = logging.getLogger(__name__)

WELL_KNOWN = ("/.well-known/agent-card.json", "/.well-known/agent.json")
MAX_BYTES = 1_000_000
REQUEST_TIMEOUT = 10.0
TOTAL_TIMEOUT = 20.0
MAX_REDIRECTS = 5
_REQUIRED = ("owner", "department", "category")


class CardImportError(ValueError):
    """소개 정보를 읽어 들일 수 없음. ``status`` 는 HTTP 상태(400 입력·차단 / 502 상대 서버 문제)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _transport() -> httpx.AsyncBaseTransport | None:
    """테스트에서 가짜 응답으로 바꿔 끼우는 자리."""
    return None


# ---------------------------------------------------------------------------
# 가져오기
# ---------------------------------------------------------------------------


def candidate_urls(url: str) -> list[str]:
    """시도할 주소 — ``.json`` 이면 그대로, 아니면 그 주소 아래와 사이트 최상위의 잘 알려진 경로."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.path.lower().endswith(".json"):
        return [url]
    base = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    out: list[str] = []
    for b in (base, root):
        for p in WELL_KNOWN:
            if b + p not in out:
                out.append(b + p)
    return out


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[str, Any] | None:
    """한 주소에서 JSON 을 읽는다. 404 면 None(다음 후보로).

    리다이렉트는 직접 따라가며 hop 마다 외부 호출 정책을 다시 검사한다(SSRF 우회 방지).
    크기 초과·오류 응답·JSON 아님은 502, 차단·호스트 해석 실패는 400 ``CardImportError``.
    """
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await check_url(url)
        except OutboundBlockedError as e:
            logger.warning("에이전트 카드 주소 차단(외부 호출 정책): %s", url)
            raise CardImportError(f"보안 정책으로 차단된 주소입니다: {e}") from e
        except OutboundResolveError as e:
            logger.warning("에이전트 카드 주소 호스트 해석 실패: %s", url)
            raise CardImportError(f"주소의 호스트를 찾을 수 없습니다: {e}") from e
        logger.debug("에이전트 카드 요청: %s", url)
        async with client.stream("GET", url, headers={"Accept": "application/json"}) as resp:
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                url = urljoin(str(resp.url), location)
                logger.debug("에이전트 카드 리다이렉트: → %s", url)
                continue
            if resp.status_code == 404:
                logger.debug("에이전트 카드 없음(404): %s", url)
                return None
            if resp.status_code >= 400:
                raise CardImportError(f"소개 정보 주소가 오류를 돌려주었습니다(HTTP {resp.status_code}): {url}", 502)
            size = resp.headers.get("content-length", "")
            if size.isdigit() and int(size) > MAX_BYTES:
                raise CardImportError(f"소개 정보가 너무 큽니다(최대 {MAX_BYTES // 1_000_000}MB): {url}", 502)
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise CardImportError(f"소개 정보가 너무 큽니다(최대 {MAX_BYTES // 1_000_000}MB): {url}", 502)
        try:
            return url, json.loads(bytes(buf))
        except ValueError as e:
            raise CardImportError(f"소개 정보가 JSON 형식이 아닙니다: {url}", 502) from e
    raise CardImportError(f"리다이렉트가 너무 많습니다(최대 {MAX_REDIRECTS}회).", 502)


async def fetch_card(url: str) -> tuple[str, dict]:
    """(실제로 읽은 주소, 소개 정보). 후보 주소를 차례로 시도한다."""
    tried = candidate_urls(url)

    async def _run() -> tuple[str, dict]:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False,
                                     transport=_transport()) as client:
            for cand in tried:
                got = await _get_json(client, cand)
                if got is None:
                    continue
                final, data = got
                if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
                    raise CardImportError(f"에이전트 소개 정보 형식이 아닙니다(name 없음): {final}", 502)
                return final, data
        raise CardImportError("소개 정보를 찾지 못했습니다. 시도한 주소: " + ", ".join(tried), 502)

    try:
        return await asyncio.wait_for(_run(), TOTAL_TIMEOUT)
    except (asyncio.TimeoutError, httpx.TimeoutException) as e:
        logger.warning("에이전트 카드 읽기 시간 초과: %s", url)
        raise CardImportError(f"소개 정보 주소의 응답 시간이 초과되었습니다: {url}", 502) from e
    except httpx.InvalidURL as e:
        logger.warning("에이전트 카드 주소 형식 오류: %s", url)
        raise CardImportError(f"주소 형식이 올바르지 않습니다: {url}") from e
    except httpx.HTTPError as e:
        logger.warning("에이전트 카드 주소 접속 실패: %s (%s)", url, type(e).__name__)
        raise CardImportError(f"소개 정보 주소에 접속할 수 없습니다({type(e).__name__}): {url}", 502) from e


# ---------------------------------------------------------------------------
# 정의 내용으로 바꾸기
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """카탈로그 이름 형식(소문자·숫자·점·하이픈·밑줄, 최대 255자)으로 바꾼다. 앞뒤 구분자는 제거."""
    s = re.sub(r"[^a-z0-9._-]+", "-", (text or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return s[:255].rstrip("-._")


def _str(v: Any, limit: int) -> str | None:
    """비어 있지 않은 문자열이면 공백 제거 후 ``limit`` 자로 자른 값, 아니면 None."""
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def _str_list(v: Any, limit: int = 50) -> list[str]:
    """목록에서 문자열만 골라 중복 없이 최대 ``limit`` 개(항목당 200자)."""
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        s = _str(x, 200)
        if s and s not in out:
            out.append(s)
    return out[:limit]


# A2A 1.0 SecurityScheme(oneOf 감싸기) 키 → 인증 방식 이름(0.3 ``type`` 값과 같은 표기)
_SCHEME_KINDS = {
    "apiKeySecurityScheme": "apikey",
    "oauth2SecurityScheme": "oauth2",
    "openIdConnectSecurityScheme": "openidconnect",
    "mtlsSecurityScheme": "mutualtls",
}


def _scheme_kind(sc: dict) -> str | None:
    """보안 스킴 하나의 인증 방식 — 1.0(``httpAuthSecurityScheme{scheme}`` 등 감싼 형식)과 0.3(``type``·``scheme``) 모두."""
    http = sc.get("httpAuthSecurityScheme")
    if isinstance(http, dict):
        return _str(http.get("scheme"), 50)
    for key, kind in _SCHEME_KINDS.items():
        if isinstance(sc.get(key), dict):
            return kind
    return _str(sc.get("scheme"), 50) if sc.get("type") == "http" else _str(sc.get("type"), 50)


def _auth_method(card: dict) -> str | None:
    """인증 방식 추출 — 우선순위: 예전 카탈로그 카드 ``auth_method`` → A2A ``securitySchemes`` 첫 항목
    (1.0 감싼 형식 또는 0.3 ``type``, http 면 scheme) → 구형 ``authentication.schemes`` 첫 값."""
    if _str(card.get("auth_method"), 50):
        return _str(card.get("auth_method"), 50)
    schemes = card.get("securitySchemes")
    if isinstance(schemes, dict):
        for sc in schemes.values():
            if isinstance(sc, dict):
                kind = _scheme_kind(sc)
                if kind:
                    return kind[:50].lower()
    auth = card.get("authentication")
    if isinstance(auth, dict):
        names = _str_list(auth.get("schemes"))
        if names:
            return names[0][:50].lower()
    return None


# 여러 호출 주소 중 고르는 순서 — A2A 의 기본 바인딩(JSON-RPC) 우선
_BINDING_ORDER = ("JSONRPC", "HTTP+JSON", "GRPC")


def _interface(card: dict) -> tuple[str | None, str | None]:
    """(호출 주소, 프로토콜 바인딩). 1.0 ``supportedInterfaces`` 를 바인딩 순서로 고르고, 없으면 0.3 최상위 ``url``·``preferredTransport``."""
    items = [i for i in card.get("supportedInterfaces") or [] if isinstance(i, dict) and _str(i.get("url"), 1000)] \
        if isinstance(card.get("supportedInterfaces"), list) else []
    if items:
        def rank(i: dict) -> int:
            b = (_str(i.get("protocolBinding"), 30) or "").upper()
            return _BINDING_ORDER.index(b) if b in _BINDING_ORDER else len(_BINDING_ORDER)
        best = min(items, key=rank)
        return _str(best.get("url"), 1000), _str(best.get("protocolBinding"), 30)
    return _str(card.get("url"), 1000), _str(card.get("preferredTransport"), 30)


def _catalog_params(card: dict) -> dict:
    """이 카탈로그가 내보낸 카드의 카탈로그 확장 ``params``(없으면 빈 dict)."""
    caps = card.get("capabilities")
    if not isinstance(caps, dict):
        return {}
    for ext in caps.get("extensions") or []:
        if isinstance(ext, dict) and ext.get("uri") == CATALOG_CARD_EXTENSION_URI and isinstance(ext.get("params"), dict):
            return ext["params"]
    return {}


_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _skills(raw: list[dict]) -> list[dict]:
    """카드 ``skills[]`` → 정의 파일 ``spec.skills[]`` (최대 50개). id 가 형식에 맞지 않으면 이름에서 새로 만든다."""
    from app.agents.service import _card_skill_id

    out: list[dict] = []
    used: set[str] = set()
    for sk in raw[:50]:
        name = _str(sk.get("name"), 200) or _str(sk.get("id"), 200)
        if not name:
            continue
        sid = _str(sk.get("id"), 100)
        if sid and _SKILL_ID.match(sid) and sid not in used:
            used.add(sid)
        else:
            sid = _card_skill_id(sid or name, used)
        doc = {"id": sid, "name": name, "description": _str(sk.get("description"), 10_000),
               "tags": _str_list(sk.get("tags"), 20) or None, "examples": _str_list(sk.get("examples"), 20) or None,
               "inputModes": _str_list(sk.get("inputModes"), 20) or None,
               "outputModes": _str_list(sk.get("outputModes"), 20) or None}
        out.append({k: v for k, v in doc.items() if v is not None})
    return out


def card_to_doc(card: dict, source_url: str, *, name: str | None = None, owner: str | None = None,
                department: str | None = None, category: str | None = None) -> tuple[dict, list[str]]:
    """소개 정보 → 정의 문서. (문서, 안내 문구). 소유자·부서·분류가 없으면 null 로 둔다(채워야 반영 가능)."""
    notes: list[str] = []
    ext = _catalog_params(card)
    # 예전 카탈로그 카드: provider.organization 이 부서, provider.contact 가 소유자였다
    legacy_catalog = any(k in card for k in ("display_name", "auth_method"))
    raw_name = card["name"].strip()
    agent_name = (slugify(name or "") or slugify(_str(ext.get("name"), 255) or "") or slugify(raw_name)
                  or slugify(urlsplit(source_url).hostname or "") or "agent")
    if not name and not ext.get("name") and agent_name != raw_name:
        notes.append(f"이름 '{raw_name}' 을(를) 카탈로그 이름 형식에 맞춰 '{agent_name}' 로 바꿨습니다.")

    provider = card.get("provider") if isinstance(card.get("provider"), dict) else {}
    contact = _str(provider.get("contact"), 200)
    caps = card.get("capabilities")
    skills = [s for s in card.get("skills") or [] if isinstance(s, dict)] if isinstance(card.get("skills"), list) else []

    # 예전 카탈로그 카드는 capabilities 가 기능 이름 목록이었다. 이 카탈로그 카드는 확장 params 에 싣는다
    capabilities = _str_list(ext.get("capabilities")) or (_str_list(caps) if isinstance(caps, list) else [])
    # 이 카탈로그가 스킬 없이 기능 목록에서 만든 skills 는 스킬로 다시 들이지 않는다(기능 목록으로 돌아감)
    skill_docs = [] if ext.get("skillSource") == "capabilities" else _skills(skills)
    tags: list[str] = _str_list(card.get("tags"), 20)
    for sk in skills:  # 스킬 태그를 에이전트 태그로도 모은다(검색용)
        for t in _str_list(sk.get("tags")):
            if t not in tags:
                tags.append(t)
    streaming = caps.get("streaming") if isinstance(caps, dict) else card.get("streaming")
    endpoint, binding = _interface(card)
    # A2A 카드 고유 키가 하나라도 있으면 protocol 을 a2a 로 추정
    is_a2a = any(k in card for k in ("supportedInterfaces", "protocolVersion", "preferredTransport", "securitySchemes",
                                     "defaultInputModes"))

    interface = {
        "endpoint": endpoint,
        "protocol": _str(ext.get("protocol"), 30) or _str(card.get("protocol"), 30) or ("a2a" if is_a2a else None),
        # 호출 방식(sync/async/streaming)은 카드에 없다 — 전송 바인딩(JSONRPC 등)과 다른 값이라 옮기지 않는다
        "invocationMethod": _str(ext.get("invocationMethod"), 30),
        "streaming": bool(streaming),
        "authMethod": _str(ext.get("authMethod"), 50) or _auth_method(card),
    }
    organization = _str(provider.get("organization"), 200)
    metadata = {
        "name": agent_name,
        "displayName": _str(ext.get("displayName"), 255) or _str(card.get("display_name"), 255) or raw_name[:255],
        "owner": (_str(owner, 200) or _str(ext.get("owner"), 200)
                  or (contact if contact and "@" in contact else None)),
        "department": (_str(department, 200) or _str(ext.get("department"), 200)
                       or (organization if legacy_catalog else None)),
        "category": _str(category, 100) or _str(ext.get("category"), 100),
    }
    if organization and not legacy_catalog and not ext and not department:
        notes.append(f"제공 조직 '{organization}' 은(는) 카드를 제공한 외부 조직이라 부서로 옮기지 않았습니다. 부서를 직접 정해 주세요.")
    if binding and not ext:
        notes.append(f"전송 방식 '{binding}' 은(는) 호출 방식(sync/async/streaming)과 달라 옮기지 않았습니다. 필요하면 호출 방식을 직접 정해 주세요.")
    if tags:
        metadata["tags"] = tags[:20]
    spec: dict[str, Any] = {"description": _str(card.get("description"), 10_000)}
    if _str(card.get("version"), 50):
        spec["version"] = _str(card.get("version"), 50)
    spec["interface"] = {k: v for k, v in interface.items() if v is not None and v is not False}
    if capabilities:
        spec["capabilities"] = capabilities[:50]
    if skill_docs:
        spec["skills"] = skill_docs
    modes_in = _str_list(card.get("defaultInputModes"), 20)
    modes_out = _str_list(card.get("defaultOutputModes"), 20)
    if modes_in:
        spec["interface"]["inputModes"] = modes_in
    if modes_out:
        spec["interface"]["outputModes"] = modes_out
    gov = {"riskTier": _str(ext.get("riskTier"), 20), "autonomyLevel": _str(ext.get("autonomyLevel"), 20)}
    gov = {k: v for k, v in gov.items() if v}
    if gov:
        spec["governance"] = gov
    langs = _str_list(ext.get("supportedLanguages")) or _str_list(card.get("supported_languages"))
    if langs:
        spec["supportedLanguages"] = langs
    spec["release"] = {"changelog": "소개 정보에서 가져옴", "source": source_url[:1000]}
    if skill_docs and not ext:
        notes.append(f"스킬 {len(skill_docs)}개를 옮겼습니다. 스킬은 외부에 알리는 기능이라 도구(tools)가 아닙니다 — "
                     "에이전트가 호출하는 도구와 위험도는 직접 정해 주세요.")

    doc = {"apiVersion": API_VERSION, "kind": "Agent", "metadata": metadata, "spec": spec}
    # 필수 항목 외의 형식 문제를 미리 알려 준다(필수 항목은 임시 값으로 채워 검사)
    probe = {**doc, "metadata": {**metadata, **{k: metadata[k] or "x@x" for k in _REQUIRED}}}
    try:
        AgentManifest.model_validate(probe)
    except ValidationError as e:
        for err in e.errors():
            notes.append(f"확인 필요 — {'.'.join(map(str, err['loc']))}: {err['msg']}")
    return doc, notes


async def import_card(
    session: AsyncSession, url: str, *, name: str | None = None, owner: str | None = None,
    department: str | None = None, category: str | None = None,
) -> dict:
    """소개 정보를 읽어 정의 내용 미리 보기를 만든다(저장하지 않음)."""
    source_url, card = await fetch_card(url)
    doc, notes = card_to_doc(card, source_url, name=name, owner=owner, department=department, category=category)
    agent_name = doc["metadata"]["name"]
    exists = (await session.execute(select(AIAgent.id).where(AIAgent.name == agent_name))).first() is not None
    if exists:
        notes.insert(0, f"'{agent_name}' 은(는) 이미 등록되어 있습니다. 반영하면 이 내용으로 갱신됩니다.")
    missing = [k for k in _REQUIRED if not doc["metadata"].get(k)]
    logger.info("소개 정보 읽어 들이기: %s → %s (이미 있음=%s, 비어 있는 필수 항목=%s)", source_url, agent_name, exists, missing)
    return {"source_url": source_url, "name": agent_name, "exists": exists, "missing": missing,
            "notes": notes, "document": doc, "yaml": dump_yaml(doc)}
