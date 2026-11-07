# SPDX-License-Identifier: Apache-2.0
"""API Catalog 서비스 — 스펙 파싱·등록, CRUD, 상태 이력, Try-it 프록시.

주요 기능:
- 등록: 스펙(원문/URL) 기반(source=spec) 또는 수동(source=manual). 스펙 버전 업로드 시 Breaking 변경을 감지해 알림 생성.
- 수동 엔드포인트 CRUD, 리니지(provides/consumes/depends_on), 즐겨찾기, 상태 이력, 린트·버전 비교.
- 자격증명: 시크릿을 Fernet 으로 암호화해 저장하고, Try-it 호출 시 서버에서만 복호화해 주입한다.
- Try-it 프록시: SSRF 방어(``guarded_request``) 후 호출, 알려진 API 면 호출 로그(비밀 값 마스킹)를 남긴다.

라우터는 반환값 None 을 404 로, ``ValueError`` 계열을 400/409 로 변환한다(라우터 참고).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import String, case, cast, delete, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis import crypto
from app.apis import diff as specdiff
from app.apis import lint as speclint
from app.apis import openapi_parser as parser
from app.apis.models import (
    CatalogApi,
    CatalogApiAlert,
    CatalogApiCredential,
    CatalogApiEndpoint,
    CatalogApiFavorite,
    CatalogApiInvocation,
    CatalogApiLineage,
    CatalogApiSecurityScheme,
    CatalogApiServer,
    CatalogApiSpec,
    CatalogApiStatusHistory,
)
from app.apis.schemas import (
    ApiAlertResponse,
    ApiCreate,
    ApiDetailResponse,
    ApiInvocationResponse,
    ApiLintResponse,
    ApiOwnerInfo,
    ApiStats,
    ApiStatusHistoryResponse,
    ApiSummary,
    ApiUpdate,
    ApiUsageResponse,
    CredentialCreate,
    CredentialResponse,
    EndpointCreate,
    FavoriteCreate,
    FavoriteResponse,
    IncomingLineageResponse,
    InvokeRequest,
    InvokeResponse,
    LineageCreate,
    LineageResponse,
    SpecDiffResponse,
    SpecPreviewRequest,
    SpecPreviewResponse,
    SpecUpload,
)
from app.core.config import settings
from app.core.outbound import OutboundBlockedError, OutboundResolveError, guarded_request
from app.core.secrets import InsecureSecretError
from app.core.sql import LIKE_ESCAPE, like_contains
from app.usermgr.models import ArgusUser

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """식별자 규칙(영문·숫자·점·하이픈·밑줄) 밖의 문자열을 '-' 로 바꾸고 소문자화. 비면 'api'."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower()).strip("-.")
    return s or "api"


async def _get_by_name(session: AsyncSession, name: str) -> CatalogApi | None:
    return (await session.execute(select(CatalogApi).where(CatalogApi.name == name))).scalars().first()


async def is_name_available(session: AsyncSession, name: str) -> bool:
    return await _get_by_name(session, name.strip()) is None


async def _auto_name(session: AsyncSession, title: str | None) -> str:
    """스펙 제목으로 식별자 자동 생성 — 이미 있으면 -2, -3 … 을 붙여 비어 있는 이름을 고른다.
    (제목이 한글뿐이면 slug 가 'api' 가 되므로 번호로 구분된다)"""
    base = _slugify(title or "")[:240].strip("-.") or "api"
    name, n = base, 2
    while await _get_by_name(session, name):
        name = f"{base}-{n}"
        n += 1
    return name


async def preview_spec(session: AsyncSession, req: SpecPreviewRequest) -> SpecPreviewResponse:
    """등록 전 미리보기 — 스펙을 파싱해 요약과 사용될 식별자를 돌려준다(저장하지 않음)."""
    text, source_url = await _resolve_spec_text(req.spec_text, req.spec_url)
    parsed = parser.parse(text, source_url, for_import=True)
    info = parsed["parsed"]
    if req.name:
        name, auto, available = req.name, False, await is_name_available(session, req.name)
    else:
        name, auto, available = await _auto_name(session, parsed.get("title")), True, True
    return SpecPreviewResponse(
        format=parsed["format"],
        title=parsed.get("title"),
        description=parsed.get("description"),
        version=parsed.get("version") or "1.0.0",
        base_url=parsed.get("base_url"),
        endpoint_count=info.get("endpoint_count", 0),
        server_count=info.get("server_count", 0),
        security_count=info.get("security_count", 0),
        name=name,
        name_auto=auto,
        name_available=available,
    )


async def _resolve_spec_text(req_text: str | None, req_url: str | None) -> tuple[str, str | None]:
    """스펙 텍스트를 직접 또는 URL 에서 가져와 반환. (text, source_url)"""
    if req_text and req_text.strip():
        return req_text, None
    if req_url and req_url.strip():
        url = req_url.strip()
        # 로그에는 쿼리의 비밀 값(token·key 등)을 가린 주소만 남긴다
        safe_url = _mask_url_query(url)
        logger.debug("스펙 URL 조회 시작: url=%s", safe_url)
        # URL 조회 실패는 사용자 입력 문제이므로 500 이 아닌 SpecParseError(→ 400)로 원인을 구분해 알려준다.
        # SSRF 방어: guarded_request 가 대상·리다이렉트 hop 을 검사(app.core.outbound).
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                resp = await guarded_request(client, "GET", url)
                resp.raise_for_status()
        except OutboundBlockedError as e:
            logger.warning("스펙 URL 호출이 보안 정책으로 차단됨: url=%s reason=%s", safe_url, e)
            raise parser.SpecParseError(f"스펙 URL 호출이 보안 정책으로 차단되었습니다: {e}") from e
        except OutboundResolveError as e:
            logger.warning("스펙 URL 호스트 해석 실패: url=%s", safe_url)
            raise parser.SpecParseError(f"스펙 URL 의 호스트를 찾을 수 없습니다: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.warning("스펙 URL 오류 응답: url=%s status=%d", safe_url, e.response.status_code)
            raise parser.SpecParseError(f"스펙 URL을 가져올 수 없습니다 (HTTP {e.response.status_code}): {url}") from e
        except httpx.InvalidURL as e:
            logger.warning("스펙 URL 형식 오류: url=%s", safe_url)
            raise parser.SpecParseError(f"스펙 URL 형식이 올바르지 않습니다: {url}") from e
        except httpx.TimeoutException as e:
            logger.warning("스펙 URL 응답 시간 초과(30초): url=%s", safe_url)
            raise parser.SpecParseError(f"스펙 URL 응답 시간이 초과되었습니다(30초): {url}") from e
        except httpx.HTTPError as e:
            logger.warning("스펙 URL 접속 실패: url=%s error=%s", safe_url, type(e).__name__)
            raise parser.SpecParseError(f"스펙 URL에 접속할 수 없습니다 ({type(e).__name__}): {url}") from e
        logger.debug("스펙 URL 조회 완료: url=%s length=%d", safe_url, len(resp.text))
        return resp.text, url
    raise parser.SpecParseError("스펙 텍스트 또는 URL 중 하나는 필수입니다.")


async def _replace_children(session: AsyncSession, api: CatalogApi, parsed: dict) -> None:
    """엔드포인트/서버/보안 스킴을 파싱 결과로 교체."""
    for model in (CatalogApiEndpoint, CatalogApiServer, CatalogApiSecurityScheme):
        await session.execute(delete(model).where(model.api_id == api.id))
    for s in parsed["servers"]:
        session.add(CatalogApiServer(api_id=api.id, url=s["url"], description=s.get("description") or None))
    for sc in parsed["security_schemes"]:
        session.add(CatalogApiSecurityScheme(api_id=api.id, scheme_name=sc["scheme_name"], type=sc.get("type"), config=sc.get("config")))
    for ep in parsed["endpoints"]:
        session.add(CatalogApiEndpoint(
            api_id=api.id, method=ep["method"], path=ep["path"], operation_id=ep.get("operation_id"),
            summary=ep.get("summary"), description=ep.get("description"), tags=ep.get("tags"),
            parameters=ep.get("parameters"), request_body=ep.get("request_body"),
            responses=ep.get("responses"), security=ep.get("security"), sort_order=ep.get("sort_order", 0),
        ))


async def get_api_created_by(session: AsyncSession, name: str) -> str | None:
    """API 생성자 — 소유권 체크용."""
    return (await session.execute(
        select(CatalogApi.created_by).where(CatalogApi.name == name)
    )).scalar_one_or_none()


class ApiInputError(ValueError):
    """요청 값 형식 오류 — 라우터에서 HTTP 400 (이름 중복 등 409 와 구분)."""


_URL_MAX = 1000  # catalog_apis.contract_url·base_url 컬럼 길이


def _normalize_http_url(value: str | None, label: str) -> str | None:
    """사용자가 입력한 주소 — 앞뒤 공백 제거, 빈 값은 None. http(s) 절대 주소(호스트 포함)만 허용."""
    v = (value or "").strip()
    if not v:
        return None
    msg = f"{label} 은 http:// 또는 https:// 로 시작하는 주소여야 합니다."
    if len(v) > _URL_MAX:
        raise ApiInputError(f"{label} 이 너무 깁니다(최대 {_URL_MAX}자).")
    if any(c.isspace() or ord(c) < 0x20 for c in v):
        raise ApiInputError(msg)
    try:
        p = urlsplit(v)
    except ValueError:
        raise ApiInputError(msg) from None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        raise ApiInputError(msg)
    return v


def normalize_contract_url(value: str | None) -> str | None:
    """계약 문서 URL — 화면에서 링크로 렌더되므로 javascript:·data:·ftp: 등은 서버에서 막는다
    (UI 검사만으로는 API 직접 호출을 못 막음)."""
    return _normalize_http_url(value, "계약 문서 URL")


def normalize_base_url(value: str | None) -> str | None:
    """사용자가 지정한 Base URL — 호출 대상 주소이므로 http(s) 절대 주소만.
    (스펙의 servers 에서 온 값은 상대 경로일 수 있어 검사하지 않는다 — 그때는 Base URL 직접 지정을 안내)"""
    return _normalize_http_url(value, "Base URL")


async def create_api(session: AsyncSession, req: ApiCreate, created_by: str | None = None) -> ApiSummary:
    """API 등록. 스펙이 있으면 파싱해 스펙 버전·엔드포인트·서버·보안 스킴을 함께 저장한다.

    예외: 스펙 파싱/URL 조회 실패 ``SpecParseError``, 주소 형식 오류·수동 등록 이름 누락 ``ApiInputError``(→ 400),
    이름 중복 ``ValueError``(→ 409).
    """
    # 스펙 미입력 시 수동 등록(엔드포인트는 상세에서 직접 추가)
    if not (req.spec_text or req.spec_url):
        return await _create_manual_api(session, req, created_by)

    # 사용자가 준 Base URL 은 스펙을 가져오기 전에 검사(잘못된 값이면 외부 조회 없이 400)
    user_base_url = normalize_base_url(req.base_url)
    text, source_url = await _resolve_spec_text(req.spec_text, req.spec_url)
    parsed = parser.parse(text, source_url, for_import=True)

    # 식별자: 입력값이 있으면 그대로(중복이면 409), 없으면 스펙 제목으로 자동 생성(중복 회피)
    if req.name:
        name = req.name
        if await _get_by_name(session, name):
            logger.warning("API 생성 거부(이름 중복): name=%s", name)
            raise ValueError(f"이미 존재하는 API 이름입니다: {name}")
    else:
        name = await _auto_name(session, parsed.get("title"))
        logger.debug("API 식별자 자동 생성: title=%s name=%s", parsed.get("title"), name)

    api = CatalogApi(
        name=name,
        urn=f"{name}.api",
        display_name=req.display_name or parsed.get("title"),
        description=req.description or parsed.get("description"),
        version=parsed.get("version") or "1.0.0",
        status=req.status.value if hasattr(req.status, "value") else req.status,
        owner_email=req.owner_email,
        department=req.department,
        category=req.category,
        protocol="REST",
        spec_format=parsed["format"],
        # 스펙에 서버가 없거나 상대 경로뿐이면 등록 화면에서 Base URL 을 직접 줄 수 있다(수동 지정으로 표시)
        base_url=user_base_url or parsed.get("base_url"),
        base_url_overridden="true" if user_base_url else "false",
        tags=req.tags,
        created_by=created_by,
    )
    session.add(api)
    try:
        await session.flush()  # api.id
    except IntegrityError:
        # 동시에 같은 이름으로 등록된 경우 — 500 대신 "이미 존재"(409)
        await session.rollback()
        logger.warning("API 생성 충돌(동시 등록): name=%s", name)
        raise ValueError(f"이미 존재하는 API 이름입니다: {name}") from None

    session.add(CatalogApiSpec(
        api_id=api.id, version=api.version, format=parsed["format"], raw=parsed["raw"],
        parsed=parsed["parsed"], source_url=source_url, is_current="true", created_by=created_by,
    ))
    await _replace_children(session, api, parsed)
    await session.commit()
    await session.refresh(api)
    logger.info("API 생성: %s (id=%d, 엔드포인트 %d개)", name, api.id, parsed["parsed"]["endpoint_count"])

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)
    return await _summary(session, api)


async def _create_manual_api(session: AsyncSession, req: ApiCreate, created_by: str | None = None) -> ApiSummary:
    """스펙 없이 수동 등록 — 메타데이터만(엔드포인트 0). source=manual."""
    name = (req.name or "").strip()
    if not name:
        # 입력 누락은 중복(409)이 아니라 입력 오류 → 라우터에서 400
        raise ApiInputError("수동 등록 시 API 이름은 필수입니다.")
    if await _get_by_name(session, name):
        logger.warning("수동 API 생성 거부(이름 중복): name=%s", name)
        raise ValueError(f"이미 존재하는 API 이름입니다: {name}")
    base_url = normalize_base_url(req.base_url)
    api = CatalogApi(
        name=name,
        urn=f"{name}.api",
        display_name=req.display_name or name,
        description=req.description,
        version=(req.version or "1.0.0").strip(),
        status=req.status.value if hasattr(req.status, "value") else req.status,
        owner_email=req.owner_email,
        department=req.department,
        category=req.category,
        protocol=(req.protocol or "REST").strip(),
        source="manual",
        spec_format=None,
        base_url=base_url,
        base_url_overridden="true" if base_url else "false",
        contract_text=(req.contract_text or "").strip() or None,
        contract_url=normalize_contract_url(req.contract_url),
        tags=req.tags,
        created_by=created_by,
    )
    session.add(api)
    try:
        await session.commit()
    except IntegrityError:
        # 동시에 같은 이름으로 등록된 경우 — 500 대신 "이미 존재"(409)
        await session.rollback()
        logger.warning("수동 API 생성 충돌(동시 등록): name=%s", name)
        raise ValueError(f"이미 존재하는 API 이름입니다: {name}") from None
    await session.refresh(api)
    logger.info("수동 API 생성: %s (id=%d)", name, api.id)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)
    return await _summary(session, api)


# ---------------------------------------------------------------------------
# 수동 엔드포인트 CRUD (source=manual 전용)
# ---------------------------------------------------------------------------

def _norm_method(api: CatalogApi, method: str) -> str:
    """REST 는 메서드를 대문자화, 그 외 프로토콜은 오퍼레이션 유형을 원형 그대로 보존."""
    m = (method or "").strip()
    return m.upper() if (api.protocol or "REST").upper() == "REST" else m


async def _assert_endpoint_unique(session: AsyncSession, api: CatalogApi, method: str, path: str, exclude_id: int | None = None) -> None:
    """같은 API 안에 같은 메서드(오퍼레이션)+경로 엔드포인트가 이미 있으면 거부."""
    q = select(CatalogApiEndpoint.id).where(
        CatalogApiEndpoint.api_id == api.id, CatalogApiEndpoint.method == method, CatalogApiEndpoint.path == path,
    )
    if exclude_id is not None:
        q = q.where(CatalogApiEndpoint.id != exclude_id)
    if (await session.execute(q.limit(1))).first() is not None:
        raise ValueError(f"이미 같은 엔드포인트가 있습니다: {method} {path}")


async def add_endpoint(session: AsyncSession, name: str, req: EndpointCreate, created_by: str | None = None):
    """수동(manual) API 에 엔드포인트를 맨 뒤 순서로 추가한다. API 없으면 None.

    스펙 기반 API 이거나 같은 메서드+경로가 이미 있으면 ``ValueError``(→ 400).
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    if api.source != "manual":
        raise ValueError("스펙 기반 API 는 엔드포인트를 수동 추가할 수 없습니다(스펙 업로드로 관리).")
    max_order = await session.scalar(
        select(func.coalesce(func.max(CatalogApiEndpoint.sort_order), -1)).where(CatalogApiEndpoint.api_id == api.id)
    )
    method, path = _norm_method(api, req.method), req.path.strip()
    await _assert_endpoint_unique(session, api, method, path)
    ep = CatalogApiEndpoint(
        api_id=api.id, method=method, path=path,
        operation_id=req.operation_id, summary=req.summary, description=req.description,
        tags=req.tags, parameters=req.parameters, request_body=req.request_body,
        responses=req.responses, extra=req.extra, sort_order=int(max_order) + 1,
    )
    session.add(ep)
    await session.commit()
    await session.refresh(ep)
    logger.info("API 엔드포인트 추가: api=%s endpoint=%s %s (id=%d)", name, method, path, ep.id)

    # 엔드포인트는 임베딩 소스 텍스트에 포함되므로 재임베딩
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return ep


async def update_endpoint(session: AsyncSession, name: str, ep_id: int, req: EndpointCreate):
    """수동 API 의 엔드포인트를 전체 교체 방식으로 수정한다. API·엔드포인트가 없으면 None."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    if api.source != "manual":
        raise ValueError("스펙 기반 API 는 엔드포인트를 수정할 수 없습니다.")
    ep = (await session.execute(
        select(CatalogApiEndpoint).where(CatalogApiEndpoint.id == ep_id, CatalogApiEndpoint.api_id == api.id)
    )).scalars().first()
    if not ep:
        return None
    method, path = _norm_method(api, req.method), req.path.strip()
    await _assert_endpoint_unique(session, api, method, path, exclude_id=ep.id)
    ep.method = method
    ep.path = path
    ep.operation_id = req.operation_id
    ep.summary = req.summary
    ep.description = req.description
    ep.tags = req.tags
    ep.parameters = req.parameters
    ep.request_body = req.request_body
    ep.responses = req.responses
    ep.extra = req.extra
    await session.commit()
    await session.refresh(ep)
    logger.info("API 엔드포인트 수정: api=%s endpoint=%s %s (id=%d)", name, method, path, ep.id)

    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return ep


async def delete_endpoint(session: AsyncSession, name: str, ep_id: int) -> bool | None:
    """수동 API 의 엔드포인트 삭제. API 없음 None, 엔드포인트 없음 False, 삭제 True."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    if api.source != "manual":
        raise ValueError("스펙 기반 API 는 엔드포인트를 삭제할 수 없습니다.")
    ep = (await session.execute(
        select(CatalogApiEndpoint).where(CatalogApiEndpoint.id == ep_id, CatalogApiEndpoint.api_id == api.id)
    )).scalars().first()
    if not ep:
        return False
    await session.delete(ep)
    await session.commit()
    logger.info("API 엔드포인트 삭제: api=%s endpoint_id=%d", name, ep_id)

    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return True


async def add_spec(session: AsyncSession, name: str, req: SpecUpload, created_by: str | None = None) -> ApiSummary | None:
    """기존 스펙 기반 API 에 새 스펙 버전을 올린다. API 없으면 None.

    새 스펙을 current 로 바꾸고 엔드포인트·서버·보안 스킴을 교체하며, 직전 current 대비 Breaking 변경이
    있으면 알림을 만든다. 수동 API·같은 버전 번호면 ``ValueError``, 파싱 실패면 ``SpecParseError``(모두 → 400).
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    if api.source == "manual":
        # 수동 API 의 엔드포인트는 직접 관리 — 스펙을 올리면 모두 교체되므로 막는다
        raise ValueError("수동 등록 API 에는 스펙 버전을 등록할 수 없습니다.")
    text, source_url = await _resolve_spec_text(req.spec_text, req.spec_url)
    parsed = parser.parse(text, source_url, for_import=True)

    # 같은 버전 번호는 (api_id, version) 유니크 — 무엇도 바꾸기 전에 명확한 메시지로 거부
    new_version = parsed.get("version") or api.version
    dup = (await session.execute(
        select(CatalogApiSpec.id).where(CatalogApiSpec.api_id == api.id, CatalogApiSpec.version == new_version).limit(1)
    )).first()
    if dup is not None:
        logger.warning("스펙 버전 등록 거부(버전 중복): api=%s version=%s", name, new_version)
        raise ValueError(f"이미 등록된 버전(v{new_version})입니다 — 스펙의 info.version 을 올려 주세요.")

    # 직전 current 스펙(있으면) — Breaking 비교 기준
    prev_spec = (await session.execute(
        select(CatalogApiSpec)
        .where(CatalogApiSpec.api_id == api.id, CatalogApiSpec.is_current == "true")
        .order_by(CatalogApiSpec.id.desc())
    )).scalars().first()

    # 기존 current 해제
    await session.execute(
        CatalogApiSpec.__table__.update().where(CatalogApiSpec.api_id == api.id).values(is_current="false")
    )
    api.version = parsed.get("version") or api.version
    api.spec_format = parsed["format"]
    # Base URL 은 사용자가 수동 지정(override)한 경우 스펙 값으로 덮어쓰지 않는다.
    if api.base_url_overridden != "true":
        api.base_url = parsed.get("base_url") or api.base_url
    new_spec = CatalogApiSpec(
        api_id=api.id, version=api.version, format=parsed["format"], raw=parsed["raw"],
        parsed=parsed["parsed"], source_url=source_url, is_current="true", created_by=created_by,
    )
    session.add(new_spec)
    await session.flush()  # new_spec.id 확보
    await _replace_children(session, api, parsed)

    # Breaking 변경 감지 → 알림 자동 생성
    if prev_spec is not None:
        _maybe_create_breaking_alert(session, api, prev_spec, new_spec, parsed, created_by)

    await session.commit()
    await session.refresh(api)
    logger.info("API 스펙 버전 등록: api=%s version=%s (엔드포인트 %d개, 요청자 %s)",
                name, api.version, parsed["parsed"]["endpoint_count"], created_by)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)
    return await _summary(session, api)


def _maybe_create_breaking_alert(
    session: AsyncSession,
    api: CatalogApi,
    prev_spec: CatalogApiSpec,
    new_spec: CatalogApiSpec,
    parsed: dict,
    created_by: str | None,
) -> None:
    """직전 current 스펙과 새 스펙을 비교해 Breaking 변경이 있으면 알림을 생성한다(미커밋)."""
    try:
        old_eps = parser.parse(prev_spec.raw or "")["endpoints"]
        new_eps = parsed["endpoints"]
    except Exception as e:  # 파싱 실패 시 알림 생략(스펙 등록 자체는 진행)
        logger.warning("Breaking 비교용 스펙 파싱 실패(api=%s): %s", api.name, e)
        return
    d = specdiff.diff_endpoints(old_eps, new_eps)
    if not d.get("breaking"):
        return
    parts = []
    if d.get("removed_count"):
        parts.append(f"엔드포인트 제거 {d['removed_count']}건")
    breaking_changes = sum(1 for c in d.get("changed", []) for i in c.get("items", []) if i.get("breaking"))
    if breaking_changes:
        parts.append(f"파라미터/바디 호환성 변경 {breaking_changes}건")
    detail_text = "; ".join(parts) or "Breaking 변경"
    summary = (
        f"{prev_spec.version or '?'} → {new_spec.version or '?'} 스펙에서 "
        f"Breaking 변경 {d['breaking_count']}건이 감지되었습니다 ({detail_text})."
    )
    session.add(CatalogApiAlert(
        api_id=api.id,
        from_spec_id=prev_spec.id,
        to_spec_id=new_spec.id,
        from_version=prev_spec.version,
        to_version=new_spec.version,
        severity="BREAKING",
        breaking_count=int(d["breaking_count"]),
        summary=summary[:500],
        detail=json.dumps({"removed": d.get("removed", []), "changed": d.get("changed", [])}, ensure_ascii=False),
        status="OPEN",
        created_by=created_by,
    ))
    logger.info("API Breaking 알림 생성: %s (%d건)", api.name, d["breaking_count"])


async def list_api_alerts(session: AsyncSession, name: str, status: str | None = None):
    """API 의 Breaking 알림 목록(최신순). status 로 OPEN/ACKNOWLEDGED 필터."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    stmt = select(CatalogApiAlert).where(CatalogApiAlert.api_id == api.id)
    if status:
        stmt = stmt.where(CatalogApiAlert.status == status)
    rows = (await session.execute(stmt.order_by(CatalogApiAlert.id.desc()))).scalars().all()
    return [ApiAlertResponse.model_validate(r) for r in rows]


async def acknowledge_api_alert(session: AsyncSession, name: str, alert_id: int, user: str | None = None) -> bool:
    """Breaking 알림 확인 처리(OPEN → ACKNOWLEDGED)."""
    api = await _get_by_name(session, name)
    if not api:
        return False
    alert = (await session.execute(
        select(CatalogApiAlert).where(CatalogApiAlert.id == alert_id, CatalogApiAlert.api_id == api.id)
    )).scalars().first()
    if not alert:
        return False
    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_by = user
    alert.acknowledged_at = func.now()
    await session.commit()
    logger.info("API Breaking 알림 확인: api=%s alert_id=%d by=%s", name, alert_id, user)
    return True


# ---------------------------------------------------------------------------
# 리니지 (provides / consumes / depends_on)
# ---------------------------------------------------------------------------

_LINEAGE_RELATIONS = {"provides", "consumes", "depends_on"}
_LINEAGE_TARGET_TYPES = {"api", "dataset", "model", "agent", "system"}


async def list_api_lineage(session: AsyncSession, name: str):
    """API 의 리니지 엣지 목록(최신순). 데이터셋 대상은 URN·이름으로 상세 화면 id 를 찾아 붙인다."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    rows = (await session.execute(
        select(CatalogApiLineage).where(CatalogApiLineage.api_id == api.id).order_by(CatalogApiLineage.id.desc())
    )).scalars().all()
    out = [LineageResponse.model_validate(r) for r in rows]
    ds_refs = {e.target_ref for e in out if e.target_type == "dataset"}
    if ds_refs:
        from app.catalog.models import Dataset
        ds_rows = (await session.execute(
            select(Dataset.id, Dataset.urn, Dataset.name).where(or_(Dataset.urn.in_(ds_refs), Dataset.name.in_(ds_refs)))
        )).all()
        by_urn = {u: i for i, u, _ in ds_rows}
        by_name: dict[str, int | None] = {}
        for i, _, n in ds_rows:
            # 같은 이름이 여러 데이터 소스에 있으면 어느 것인지 모르므로 연결하지 않는다
            by_name[n] = None if n in by_name else i
        for e in out:
            if e.target_type == "dataset":
                e.target_id = by_urn.get(e.target_ref) or by_name.get(e.target_ref)
    return out


_LINEAGE_TARGET_LIMIT = 10


async def search_lineage_targets(session: AsyncSession, target_type: str, q: str) -> list[dict[str, Any]]:
    """리니지 대상 자동완성 — 유형별 이름 검색. ref 는 대상의 안정적 식별자(API·모델·에이전트=name, 데이터셋=URN)."""
    kw = like_contains(q.strip())
    t = (target_type or "").strip().lower()
    if t == "api":
        rows = (await session.execute(
            select(CatalogApi.name, CatalogApi.display_name)
            .where(or_(CatalogApi.name.ilike(kw, escape=LIKE_ESCAPE), CatalogApi.display_name.ilike(kw, escape=LIKE_ESCAPE)))
            .order_by(CatalogApi.name).limit(_LINEAGE_TARGET_LIMIT)
        )).all()
        return [{"ref": n, "label": d or n} for n, d in rows]
    if t == "dataset":
        from app.catalog.models import Dataset
        rows = (await session.execute(
            select(Dataset.id, Dataset.urn, Dataset.name, Dataset.display_name)
            .where(or_(Dataset.name.ilike(kw, escape=LIKE_ESCAPE), Dataset.display_name.ilike(kw, escape=LIKE_ESCAPE), Dataset.urn.ilike(kw, escape=LIKE_ESCAPE)))
            .order_by(Dataset.name).limit(_LINEAGE_TARGET_LIMIT)
        )).all()
        return [{"ref": u, "label": d or n, "id": i} for i, u, n, d in rows]
    if t == "model":
        from app.models.models import RegisteredModel
        rows = (await session.execute(
            select(RegisteredModel.name)
            .where(RegisteredModel.status != "deleted", RegisteredModel.name.ilike(kw, escape=LIKE_ESCAPE))
            .order_by(RegisteredModel.name).limit(_LINEAGE_TARGET_LIMIT)
        )).all()
        return [{"ref": n, "label": n} for (n,) in rows]
    if t == "agent":
        from app.agents.models import AIAgent
        rows = (await session.execute(
            select(AIAgent.name, AIAgent.display_name)
            .where(or_(AIAgent.name.ilike(kw, escape=LIKE_ESCAPE), AIAgent.display_name.ilike(kw, escape=LIKE_ESCAPE)))
            .order_by(AIAgent.name).limit(_LINEAGE_TARGET_LIMIT)
        )).all()
        return [{"ref": n, "label": d or n} for n, d in rows]
    return []  # system 등 — 자유 입력


async def list_api_incoming_lineage(session: AsyncSession, name: str):
    """다른 API 가 이 API 를 대상(target_type=api)으로 등록한 엣지 — 이름 또는 URN 으로 참조."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    refs = [r for r in {api.name, api.urn} if r]
    rows = (await session.execute(
        select(CatalogApiLineage, CatalogApi.name, CatalogApi.display_name)
        .join(CatalogApi, CatalogApi.id == CatalogApiLineage.api_id)
        .where(
            CatalogApiLineage.target_type == "api",
            CatalogApiLineage.target_ref.in_(refs),
            CatalogApiLineage.api_id != api.id,
        )
        .order_by(CatalogApiLineage.id.desc())
    )).all()
    return [
        IncomingLineageResponse(
            **LineageResponse.model_validate(edge).model_dump(),
            source_api_name=src_name, source_display_name=src_display,
        )
        for edge, src_name, src_display in rows
    ]


async def add_api_lineage(session: AsyncSession, name: str, req: LineageCreate, created_by: str | None = None) -> LineageResponse | None:
    """리니지 엣지 추가. API 없으면 None. 관계·대상 유형이 허용 목록 밖이거나 같은 엣지가 있으면 ``ValueError``."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    relation = (req.relation or "").strip().lower()
    target_type = (req.target_type or "").strip().lower()
    if relation not in _LINEAGE_RELATIONS:
        raise ValueError(f"관계 유형이 올바르지 않습니다: {req.relation}")
    if target_type not in _LINEAGE_TARGET_TYPES:
        raise ValueError(f"대상 유형이 올바르지 않습니다: {req.target_type}")
    edge = CatalogApiLineage(
        api_id=api.id, relation=relation, target_type=target_type,
        target_ref=req.target_ref.strip(),
        target_label=(req.target_label or "").strip() or None,
        note=(req.note or "").strip() or None,
        created_by=created_by,
    )
    session.add(edge)
    try:
        await session.commit()
    except IntegrityError:
        # uq_api_lineage_edge (api_id, relation, target_type, target_ref) 위반
        await session.rollback()
        logger.warning("API 리니지 추가 거부(중복): api=%s %s -> %s:%s", name, relation, target_type, req.target_ref)
        raise ValueError("이미 등록된 리니지 관계입니다.")
    await session.refresh(edge)
    logger.info("API 리니지 추가: api=%s %s -> %s:%s (id=%d)", name, relation, target_type, edge.target_ref, edge.id)
    return LineageResponse.model_validate(edge)


async def delete_api_lineage(session: AsyncSession, name: str, edge_id: int) -> bool:
    api = await _get_by_name(session, name)
    if not api:
        return False
    edge = (await session.execute(
        select(CatalogApiLineage).where(CatalogApiLineage.id == edge_id, CatalogApiLineage.api_id == api.id)
    )).scalars().first()
    if not edge:
        return False
    await session.delete(edge)
    await session.commit()
    logger.info("API 리니지 삭제: api=%s edge_id=%d", name, edge_id)
    return True


async def _endpoint_count(session: AsyncSession, api_id: int) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(CatalogApiEndpoint).where(CatalogApiEndpoint.api_id == api_id)
    ) or 0)


async def _owner_map(session: AsyncSession, emails: list[str | None]) -> dict[str, ApiOwnerInfo]:
    """소유자 이메일 목록 → 등록 사용자 정보(소문자 이메일 키). 한 번의 IN 쿼리로 일괄 조회."""
    keys = {e.strip().lower() for e in emails if e and e.strip()}
    if not keys:
        return {}
    users = (await session.execute(
        select(ArgusUser).where(func.lower(ArgusUser.email).in_(keys))
    )).scalars().all()
    return {u.email.lower(): ApiOwnerInfo.model_validate(u) for u in users}


def _owner_of(owners: dict[str, ApiOwnerInfo], email: str | None) -> ApiOwnerInfo | None:
    return owners.get(email.strip().lower()) if email else None


async def _endpoint_counts(session: AsyncSession, api_ids: list[int]) -> dict[int, int]:
    """API id 목록 → 엔드포인트 수. 목록 한 페이지를 GROUP BY 쿼리 한 번으로 센다."""
    if not api_ids:
        return {}
    rows = (await session.execute(
        select(CatalogApiEndpoint.api_id, func.count())
        .where(CatalogApiEndpoint.api_id.in_(api_ids))
        .group_by(CatalogApiEndpoint.api_id)
    )).all()
    return {api_id: int(c) for api_id, c in rows}


async def _summary(session: AsyncSession, api: CatalogApi,
                   owners: dict[str, ApiOwnerInfo] | None = None,
                   endpoint_counts: dict[int, int] | None = None) -> ApiSummary:
    s = ApiSummary.model_validate(api)
    s.endpoint_count = (
        endpoint_counts.get(api.id, 0) if endpoint_counts is not None
        else await _endpoint_count(session, api.id)
    )
    if owners is None:
        owners = await _owner_map(session, [api.owner_email])
    s.owner = _owner_of(owners, api.owner_email)
    return s


def _like(text: str) -> str:
    """부분 일치 패턴 — 사용자가 입력한 % _ \\ 는 와일드카드가 아니라 글자로 취급."""
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


# 목록 정렬 — 키 → ORDER BY (동률이면 id 로 고정해 페이지 간 중복·누락 방지)
_LIST_SORTS = {
    "updated": (CatalogApi.updated_at.desc(), CatalogApi.id.desc()),
    "created": (CatalogApi.created_at.desc(), CatalogApi.id.desc()),
    "name": (func.lower(func.coalesce(CatalogApi.display_name, CatalogApi.name)).asc(), CatalogApi.id.asc()),
}
LIST_SORT_KEYS = tuple(_LIST_SORTS)


def _list_order(sort: str, dialect: str) -> tuple:
    """정렬 키 → ORDER BY. PostgreSQL 이름순은 바이트 순(C) 비교.

    glibc 로케일(en_US.utf8 등) 정렬은 한글·영문이 섞이면 비교 대상에 따라 순서가 뒤바뀐다
    (예: '주문 이벤트' 가 '결제 …' 보다 앞). C 비교는 영문(소문자화) → 한글 가나다(유니코드 음절 순)로 일관적이다.
    MariaDB 는 utf8mb4 유니코드 정렬이 한글을 올바르게 다루므로 그대로 둔다.
    """
    if sort == "name" and dialect == "postgresql":
        key = func.lower(func.coalesce(CatalogApi.display_name, CatalogApi.name)).collate("C")
        return (key.asc(), CatalogApi.id.asc())
    return _LIST_SORTS.get(sort, _LIST_SORTS["updated"])


async def list_apis(session: AsyncSession, *, search: str | None, status: str | None,
                    category: str | None, page: int, page_size: int,
                    protocol: str | None = None, certification: str | None = None,
                    owner: str | None = None, has_open_alert: bool = False,
                    sort: str = "updated") -> dict:
    """API 목록(검색·필터·정렬·페이지). 항목마다 엔드포인트 수와 소유자 사용자 정보를 붙인다."""
    logger.debug("API 목록 조회: search=%s status=%s category=%s protocol=%s owner=%s open_alert=%s sort=%s page=%d",
                 search, status, category, protocol, owner, has_open_alert, sort, page)
    q = select(CatalogApi)
    if search and search.strip():
        like = _like(search.strip())
        # 소유자 이름·username 으로도 찾을 수 있도록 일치하는 사용자 이메일을 함께 조건에 넣는다.
        owner_emails = select(func.lower(ArgusUser.email)).where(or_(
            ArgusUser.username.ilike(like, escape="\\"),
            func.concat(func.coalesce(ArgusUser.last_name, ""), func.coalesce(ArgusUser.first_name, "")).ilike(like, escape="\\"),
        ))
        q = q.where(or_(
            CatalogApi.name.ilike(like, escape="\\"),
            CatalogApi.display_name.ilike(like, escape="\\"),
            CatalogApi.description.ilike(like, escape="\\"),
            CatalogApi.category.ilike(like, escape="\\"),
            CatalogApi.owner_email.ilike(like, escape="\\"),
            cast(CatalogApi.tags, String).ilike(like, escape="\\"),
            # tags(JSON)는 기본 직렬화(ensure_ascii)로 한글이 \\uXXXX 로 저장될 수 있어 이스케이프 형태로도 비교
            cast(CatalogApi.tags, String).ilike(_like(json.dumps(search.strip())[1:-1]), escape="\\"),
            func.lower(CatalogApi.owner_email).in_(owner_emails),
        ))
    if status:
        q = q.where(CatalogApi.status == status)
    if category:
        q = q.where(CatalogApi.category == category)
    if protocol:
        q = q.where(func.lower(CatalogApi.protocol) == protocol.lower())
    if certification:
        q = q.where(CatalogApi.certification == certification)
    if owner == "unowned":
        q = q.where(or_(CatalogApi.owner_email.is_(None), func.trim(CatalogApi.owner_email) == ""))
    if has_open_alert:
        q = q.where(CatalogApi.id.in_(
            select(CatalogApiAlert.api_id).where(CatalogApiAlert.status == "OPEN")
        ))
    total = int(await session.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = (await session.execute(
        q.order_by(*_list_order(sort, session.bind.dialect.name if session.bind is not None else ""))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    owners = await _owner_map(session, [a.owner_email for a in rows])
    counts = await _endpoint_counts(session, [a.id for a in rows])
    items = []
    for api in rows:
        items.append(await _summary(session, api, owners, counts))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_detail(session: AsyncSession, name: str) -> ApiDetailResponse | None:
    """API 상세(서버·보안 스킴·엔드포인트·스펙 이력·현재 스펙 원문·태그 정의). 없으면 None."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    detail = ApiDetailResponse.model_validate(api)
    detail.endpoint_count = await _endpoint_count(session, api.id)
    detail.owner = _owner_of(await _owner_map(session, [api.owner_email]), api.owner_email)
    servers = (await session.execute(select(CatalogApiServer).where(CatalogApiServer.api_id == api.id).order_by(CatalogApiServer.id))).scalars().all()
    schemes = (await session.execute(select(CatalogApiSecurityScheme).where(CatalogApiSecurityScheme.api_id == api.id).order_by(CatalogApiSecurityScheme.id))).scalars().all()
    endpoints = (await session.execute(select(CatalogApiEndpoint).where(CatalogApiEndpoint.api_id == api.id).order_by(CatalogApiEndpoint.sort_order, CatalogApiEndpoint.id))).scalars().all()
    specs = (await session.execute(select(CatalogApiSpec).where(CatalogApiSpec.api_id == api.id).order_by(CatalogApiSpec.id.desc()))).scalars().all()
    current_spec = next((sp for sp in specs if sp.is_current == "true"), specs[0] if specs else None)
    detail.servers = [s for s in servers]  # type: ignore
    detail.security_schemes = [s for s in schemes]  # type: ignore
    detail.endpoints = [e for e in endpoints]  # type: ignore
    detail.specs = [sp for sp in specs]  # type: ignore
    detail.raw_spec = current_spec.raw if current_spec else None
    # 엔드포인트 카테고리(최상위 tags) 정의 — 현재 스펙 raw 에서 추출
    if current_spec and current_spec.raw:
        try:
            detail.tag_defs = parser.tag_defs(parser.load_spec(current_spec.raw))
        except Exception as e:  # noqa: BLE001
            logger.warning("태그 정의 추출 실패(api=%s): %s", name, e)
            detail.tag_defs = []
    return detail


async def update_api(session: AsyncSession, name: str, req: ApiUpdate, changed_by: str | None = None) -> ApiSummary | None:
    """API 메타데이터 부분 갱신(보낸 필드만). 상태가 바뀌면 이력(``status_note`` 포함)을 남긴다.

    API 없으면 None, 주소 형식 오류는 ``ApiInputError``(→ 400).
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    data = req.model_dump(exclude_unset=True)
    if "contract_url" in data:
        data["contract_url"] = normalize_contract_url(data["contract_url"])
    status_note = (data.pop("status_note", None) or "").strip() or None
    prev_status = api.status
    if "status" in data and data["status"] is not None:
        data["status"] = data["status"].value if hasattr(data["status"], "value") else data["status"]
    # Base URL 수동 지정 우선: 값이 있으면 override 플래그 ON, 비우면 OFF + 스펙의 첫 서버 주소로 복귀
    # (스펙 파싱 시 base_url = servers[0].url 과 동일 규칙. 수동 API 처럼 서버가 없으면 비움)
    if "base_url" in data:
        v = normalize_base_url(data["base_url"])
        if v is None:
            v = (await session.execute(
                select(CatalogApiServer.url).where(CatalogApiServer.api_id == api.id)
                .order_by(CatalogApiServer.id).limit(1)
            )).scalar_one_or_none()
            api.base_url_overridden = "false"
        else:
            api.base_url_overridden = "true"
        data["base_url"] = v
    for k, v in data.items():
        setattr(api, k, v)
    if "status" in data and data["status"] is not None and data["status"] != prev_status:
        session.add(CatalogApiStatusHistory(
            api_id=api.id, from_status=prev_status, to_status=data["status"], note=status_note, changed_by=changed_by,
        ))
        logger.info("API 상태 변경: api=%s %s -> %s (by=%s)", name, prev_status, data["status"], changed_by)
    await session.commit()
    await session.refresh(api)
    logger.info("API 수정: api=%s fields=%s (by=%s)", name, ",".join(sorted(data)), changed_by)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)
    return await _summary(session, api)


async def delete_api(session: AsyncSession, name: str) -> bool:
    """API 하드 삭제(하위 테이블은 FK CASCADE). 없으면 False."""
    api = await _get_by_name(session, name)
    if not api:
        return False

    # 임베딩 행 정리 (다형 테이블이라 FK CASCADE 가 없음)
    from app.embedding.service import delete_entity_embedding
    await delete_entity_embedding(session, "api", api.id)

    await session.delete(api)
    await session.commit()
    logger.info("API 삭제: api=%s", name)
    return True


async def list_status_history(session: AsyncSession, name: str):
    api = await _get_by_name(session, name)
    if not api:
        return None
    rows = (await session.execute(
        select(CatalogApiStatusHistory).where(CatalogApiStatusHistory.api_id == api.id)
        .order_by(CatalogApiStatusHistory.changed_at.desc(), CatalogApiStatusHistory.id.desc())
    )).scalars().all()
    return [ApiStatusHistoryResponse.model_validate(r) for r in rows]


async def get_stats(session: AsyncSession) -> ApiStats:
    """목록 상단 지표·필터 옵션용 통계(상태/프로토콜/카테고리 분포, 인증·미지정 소유자·OPEN 알림 API 수)."""
    total = int(await session.scalar(select(func.count()).select_from(CatalogApi)) or 0)
    published = int(await session.scalar(select(func.count()).select_from(CatalogApi).where(CatalogApi.status == "published")) or 0)
    by_status_rows = (await session.execute(select(CatalogApi.status, func.count()).group_by(CatalogApi.status))).all()
    by_proto_rows = (await session.execute(select(CatalogApi.protocol, func.count()).group_by(CatalogApi.protocol))).all()
    total_ep = int(await session.scalar(select(func.count()).select_from(CatalogApiEndpoint)) or 0)
    certified = int(await session.scalar(
        select(func.count()).select_from(CatalogApi).where(CatalogApi.certification == "CERTIFIED")
    ) or 0)
    unowned = int(await session.scalar(
        select(func.count()).select_from(CatalogApi)
        .where((CatalogApi.owner_email.is_(None)) | (func.trim(CatalogApi.owner_email) == ""))
    ) or 0)
    by_category_rows = (await session.execute(
        select(CatalogApi.category, func.count())
        .where(CatalogApi.category.is_not(None), func.trim(CatalogApi.category) != "")
        .group_by(CatalogApi.category).order_by(CatalogApi.category)
    )).all()
    open_alert_apis = int(await session.scalar(
        select(func.count(func.distinct(CatalogApiAlert.api_id))).where(CatalogApiAlert.status == "OPEN")
    ) or 0)
    return ApiStats(
        total_apis=total,
        published_apis=published,
        by_status=[{"name": s or "unknown", "count": c} for s, c in by_status_rows],
        by_protocol=[{"name": p or "unknown", "count": c} for p, c in by_proto_rows],
        by_category=[{"name": cat, "count": c} for cat, c in by_category_rows],
        total_endpoints=total_ep,
        certified_apis=certified,
        unowned_apis=unowned,
        open_alert_apis=open_alert_apis,
    )


# ---------------------------------------------------------------------------
# 버전 디프 (Breaking change 감지)
# ---------------------------------------------------------------------------

class SpecVersionNotFoundError(LookupError):
    """지정한 스펙 버전(spec_id)이 이 API 에 없음 → 404."""


async def lint_api(session: AsyncSession, name: str, spec_id: int | None = None) -> ApiLintResponse | None:
    """API 의 현재(또는 지정) 스펙을 린팅한다. 스펙이 없으면 None 대신 빈 결과.

    spec_id 를 지정했는데 이 API 의 스펙이 아니면 SpecVersionNotFoundError — "스펙 없음"(점수 0)과 구분.
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    if spec_id is not None:
        spec = (await session.execute(
            select(CatalogApiSpec).where(CatalogApiSpec.id == spec_id, CatalogApiSpec.api_id == api.id)
        )).scalars().first()
        if spec is None:
            raise SpecVersionNotFoundError(f"스펙 버전을 찾을 수 없습니다: {spec_id}")
    else:
        spec = (await session.execute(
            select(CatalogApiSpec)
            .where(CatalogApiSpec.api_id == api.id, CatalogApiSpec.is_current == "true")
            .order_by(CatalogApiSpec.id.desc())
        )).scalars().first()
    if spec is None or not spec.raw:
        return ApiLintResponse(spec_id=None, version=None, score=0, error_count=0, warning_count=0, info_count=0, findings=[])
    result = speclint.lint_spec(spec.raw)
    return ApiLintResponse(spec_id=spec.id, version=spec.version, **result)


async def diff_specs(session: AsyncSession, name: str, from_id: int | None, to_id: int | None) -> SpecDiffResponse | None:
    """두 스펙 버전의 엔드포인트 비교. 지정이 없으면 직전 → 최신(id 내림차순 [1] → [0]).

    API 없으면 None. 비교 불가(버전 부족·지정 버전 없음·파싱 실패)는 ``message`` 만 담은 응답.
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    specs = (await session.execute(
        select(CatalogApiSpec).where(CatalogApiSpec.api_id == api.id).order_by(CatalogApiSpec.id.desc())
    )).scalars().all()
    if len(specs) < 2 and not (from_id and to_id):
        return SpecDiffResponse(message="비교할 이전 스펙 버전이 없습니다.")

    by_id = {sp.id: sp for sp in specs}
    to_spec = by_id.get(to_id) if to_id else specs[0]
    from_spec = by_id.get(from_id) if from_id else specs[1]
    if not to_spec or not from_spec:
        return SpecDiffResponse(message="지정한 스펙 버전을 찾을 수 없습니다.")

    try:
        old_eps = parser.parse(from_spec.raw or "")["endpoints"]
        new_eps = parser.parse(to_spec.raw or "")["endpoints"]
    except Exception as e:
        logger.warning("스펙 비교용 파싱 실패: api=%s from=%s to=%s error=%s", name, from_spec.id, to_spec.id, e)
        return SpecDiffResponse(message=f"스펙 파싱 실패: {e}")

    d = specdiff.diff_endpoints(old_eps, new_eps)
    return SpecDiffResponse(
        from_spec_id=from_spec.id, to_spec_id=to_spec.id,
        from_version=from_spec.version, to_version=to_spec.version,
        **d,
    )


# ---------------------------------------------------------------------------
# 자격증명 (암호화 보관 + 주입)
# ---------------------------------------------------------------------------

def _validate_credential_values(req: CredentialCreate) -> None:
    """유형별 필수 시크릿이 비어 있으면 거부 — 빈 값으로 저장되면 호출 시 빈 인증 헤더가 주입된다."""
    vals = req.values or {}

    def has(*keys: str) -> bool:
        return any(str(vals.get(k) or "").strip() for k in keys)

    ctype = (req.type or "").lower()
    # 시크릿 값은 로그에 남기지 않는다 — 유형·누락 여부만
    logger.debug("자격증명 값 검사: type=%s keys=%s", ctype, sorted(vals))
    if ctype not in ("apikey", "bearer", "basic", "oauth2"):
        raise ValueError(f"자격증명 유형이 올바르지 않습니다: {req.type}")
    if ctype == "basic":
        if not has("username") or not has("password"):
            raise ValueError("Basic 인증은 사용자명과 비밀번호를 모두 입력해야 합니다.")
    elif ctype == "apikey":
        if not has("value", "key"):
            raise ValueError("API 키 값을 입력하세요.")
    elif not has("token", "access_token", "value"):
        raise ValueError("토큰을 입력하세요.")


async def add_credential(session: AsyncSession, name: str, req: CredentialCreate, created_by: str | None = None) -> CredentialResponse | None:
    """자격증명 저장 — ``values`` 를 JSON 직렬화 후 Fernet 암호화해 보관. 응답에는 시크릿을 넣지 않는다."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    try:
        _validate_credential_values(req)
    except ValueError as e:
        logger.warning("자격증명 저장 거부(값 검증 실패): api=%s type=%s reason=%s", name, req.type, e)
        raise
    cred = CatalogApiCredential(
        api_id=api.id, scheme_name=req.scheme_name, label=req.label, type=req.type,
        secret=crypto.encrypt(json.dumps(req.values or {})), created_by=created_by,
    )
    session.add(cred)
    await session.commit()
    await session.refresh(cred)
    logger.info("API 자격증명 저장: api=%s cred_id=%d label=%s type=%s (by=%s)",
                name, cred.id, cred.label, cred.type, created_by)
    return CredentialResponse.model_validate(cred)


async def list_credentials(session: AsyncSession, name: str):
    api = await _get_by_name(session, name)
    if not api:
        return None
    rows = (await session.execute(
        select(CatalogApiCredential).where(CatalogApiCredential.api_id == api.id).order_by(CatalogApiCredential.id.desc())
    )).scalars().all()
    return [CredentialResponse.model_validate(r) for r in rows]


async def delete_credential(session: AsyncSession, name: str, cred_id: int) -> bool:
    api = await _get_by_name(session, name)
    if not api:
        return False
    cred = (await session.execute(
        select(CatalogApiCredential).where(CatalogApiCredential.id == cred_id, CatalogApiCredential.api_id == api.id)
    )).scalars().first()
    if not cred:
        return False
    await session.delete(cred)
    await session.commit()
    logger.info("API 자격증명 삭제: api=%s cred_id=%d", name, cred_id)
    return True


async def _build_auth(session: AsyncSession, api_id: int, credential_id: int) -> tuple[dict[str, str], dict[str, str]]:
    """자격증명 → (주입 헤더, 주입 쿼리). 시크릿은 서버에서만 복호화.

    - apiKey: 연결된 보안 스킴의 ``in``/``name`` 을 따른다(없으면 헤더 ``X-API-Key``). in=cookie 면 Cookie 헤더.
    - bearer/oauth2: ``Authorization: Bearer <token>``. basic: ``Authorization: Basic base64(user:pw)``.
    이 API 소속 자격증명이 아니면 빈 값(인증 없이 호출). 복호화 실패는 ``CredentialDecryptError``.
    """
    cred = (await session.execute(
        select(CatalogApiCredential).where(CatalogApiCredential.id == credential_id, CatalogApiCredential.api_id == api_id)
    )).scalars().first()
    if not cred:
        logger.warning("자격증명을 찾을 수 없어 인증 없이 호출: api_id=%s cred_id=%s", api_id, credential_id)
        return {}, {}
    try:
        values = json.loads(crypto.decrypt(cred.secret) or "{}")
    except InsecureSecretError:
        raise
    except Exception as e:
        # 인증 없이 호출을 보내면 대상 API 의 401 만 보여 원인을 알 수 없다 — 호출하지 않고 이유를 알린다
        logger.warning("자격증명 복호화/파싱 실패(api_id=%s, cred_id=%s): %s", api_id, credential_id, e)
        raise CredentialDecryptError(
            f"저장된 자격증명 '{cred.label}' 을(를) 복호화하지 못했습니다 — 암호화 키(ARGUS_SECRET_KEY)가 바뀌었을 수 있습니다. "
            "인증 탭에서 자격증명을 다시 저장한 뒤 호출하세요."
        ) from e
    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    ctype = (cred.type or "").lower()

    scheme = None
    if cred.scheme_name:
        scheme = (await session.execute(
            select(CatalogApiSecurityScheme).where(
                CatalogApiSecurityScheme.api_id == api_id, CatalogApiSecurityScheme.scheme_name == cred.scheme_name
            )
        )).scalars().first()

    if ctype == "apikey":
        loc, pname = "header", "X-API-Key"
        if scheme and isinstance(scheme.config, dict):
            loc = scheme.config.get("in") or loc
            pname = scheme.config.get("name") or pname
        val = str(values.get("value") or values.get("key") or "")
        if loc == "query":
            query[pname] = val
        elif loc == "cookie":
            headers["Cookie"] = f"{pname}={val}"
        else:
            headers[pname] = val
    elif ctype in ("bearer", "oauth2"):
        tok = str(values.get("token") or values.get("access_token") or values.get("value") or "")
        headers["Authorization"] = f"Bearer {tok}"
    elif ctype == "basic":
        user = str(values.get("username") or "")
        pw = str(values.get("password") or "")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    # 주입 위치(헤더·쿼리 이름)만 남기고 값은 남기지 않는다
    logger.debug("자격증명 주입 준비: api_id=%s cred_id=%s type=%s headers=%s query=%s",
                 api_id, credential_id, ctype, sorted(headers), sorted(query))
    return headers, query


class CredentialDecryptError(ValueError):
    """저장된 자격증명을 복호화하지 못함(ARGUS_SECRET_KEY 변경 등) — 인증 없이 호출하지 않는다."""


class CredentialTargetError(ValueError):
    """자격증명을 API 의 서버가 아닌 주소로 보내려 할 때."""


def _origin(url: str) -> str | None:
    """scheme://host:port (기본 포트 채움). http(s) 절대 주소가 아니면 None."""
    try:
        p = urlsplit((url or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            return None
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return None
    return f"{p.scheme}://{p.hostname.lower()}:{port}"


def _origin_matches(server_url: str, target_origin: str) -> bool:
    """서버 주소(OpenAPI 서버 변수 {env} 포함 가능)와 호출 대상 출처가 같은지."""
    allowed = _origin(server_url)
    if not allowed:
        return False
    if "{" not in allowed:
        return allowed == target_origin
    # 서버 변수 자리는 호스트 일부(점·콜론·슬래시 제외)로 본다
    # 예: "https://{env}.api.example.com:443" → ^https://[^/:.]+\.api\.example\.com:443$ 로 전체 일치 검사
    parts = re.split(r"\{[^}]*\}", allowed)
    return re.fullmatch("[^/:.]+".join(re.escape(x) for x in parts), target_origin) is not None


async def _credential_target_allowed(session: AsyncSession, api: CatalogApi, url: str) -> bool:
    """저장 자격증명은 그 API 의 서버(servers)·Base URL 과 같은 출처로만 보낸다(시크릿 유출 방지)."""
    target = _origin(url)
    if not target:
        return False
    servers = (await session.execute(
        select(CatalogApiServer.url).where(CatalogApiServer.api_id == api.id)
    )).scalars().all()
    candidates = [u for u in [*servers, api.base_url] if u]
    allowed = any(_origin_matches(u, target) for u in candidates)
    logger.debug("자격증명 대상 출처 검사: api=%s target=%s candidates=%d allowed=%s",
                 api.name, target, len(candidates), allowed)
    return allowed


async def invoke(session: AsyncSession, req: InvokeRequest, called_by: str | None = None) -> InvokeResponse:
    """Try-it 프록시 — 브라우저 CORS 우회 및 저장 자격증명 서버 주입.

    호출 대상이 알려진 API 로 식별되면 사용량 관측을 위해 호출 로그를 남긴다.
    예외: SSRF 차단 ``OutboundBlockedError``, 자격증명 대상 불일치 ``CredentialTargetError``,
    복호화 실패 ``CredentialDecryptError`` (라우터에서 모두 400). 그 밖의 네트워크 오류는 응답의 ``error`` 로.
    """
    headers = dict(req.headers or {})
    url = req.url
    api = await _get_by_name(session, req.api_name) if req.api_name else None
    logger.debug("Try-it 호출: method=%s url=%s api=%s credential=%s by=%s",
                 req.method.upper(), _mask_url_query(req.url), req.api_name, bool(req.credential_id), called_by)
    if api and req.credential_id:
        if not await _credential_target_allowed(session, api, url):
            logger.warning("자격증명 주입 거부(API 서버가 아닌 주소): api=%s url=%s by=%s",
                           api.name, _mask_url_query(url), called_by)
            raise CredentialTargetError(
                "저장된 자격증명은 이 API 의 서버 주소(서버 목록·Base URL)로만 보낼 수 있습니다. "
                "다른 주소로 호출하려면 자격증명을 '사용 안 함'으로 바꾸세요."
            )
        ah, aq = await _build_auth(session, api.id, req.credential_id)
        headers.update(ah)
        if aq:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(aq)}"
    # SSRF 방어 — guarded_request 가 첫 요청과 리다이렉트 hop 마다 검사한다. 차단되면
    # OutboundBlockedError 를 그대로 올려 라우터가 400 으로 응답한다(호출 로그 미기록).
    # 호스트 해석 실패 등 일반 오류는 기존처럼 응답의 error 로 돌려준다.
    start = time.monotonic()
    status, body, resp_headers, error = 0, "", {}, None
    try:
        async with httpx.AsyncClient(timeout=req.timeout, follow_redirects=False) as client:
            resp = await guarded_request(
                client, req.method.upper(), url,
                headers=headers,
                content=None if req.body is None else (req.body if isinstance(req.body, str) else json.dumps(req.body)),
            )
        status, body, resp_headers = resp.status_code, resp.text[:200000], dict(resp.headers)
    except OutboundBlockedError:
        logger.warning("Try-it 호출이 보안 정책으로 차단됨: url=%s by=%s", _mask_url_query(req.url), called_by)
        raise
    except Exception as e:
        error = str(e)
        logger.warning("Try-it 호출 실패: url=%s error=%s", _mask_url_query(req.url), type(e).__name__)
    latency = int((time.monotonic() - start) * 1000)
    logger.debug("Try-it 호출 완료: status=%d latency_ms=%d", status, latency)

    # 사용량 로깅(알려진 API 한정) — 로깅 실패가 호출 응답을 막지 않도록 보호
    if api is not None:
        try:
            session.add(CatalogApiInvocation(
                api_id=api.id, method=req.method.upper(), url=_mask_url_query(req.url)[:2000],
                status_code=status, ok="true" if 200 <= status < 400 else "false",
                latency_ms=latency, error=error, called_by=called_by,
                endpoint_method=(req.endpoint_method or req.method).upper(),
                endpoint_path=req.endpoint_path,
                request_input={
                    "path_params": req.path_params or {},
                    "query_params": _mask_secret_params(req.query_params or {}),
                    "headers": _mask_secret_headers(req.headers or {}),
                    "body": req.body if isinstance(req.body, str) else (json.dumps(req.body, ensure_ascii=False) if req.body is not None else ""),
                },
            ))
            await session.commit()
        except Exception as log_err:  # noqa: BLE001
            await session.rollback()
            logger.warning("호출 로그 기록 실패(api=%s): %s", api.name, log_err)
        await _maybe_purge_invocations(session)

    return InvokeResponse(status=status, headers=resp_headers, body=body, latency_ms=latency, error=error)


# 호출 기록에 남기지 않을 쿼리 파라미터 — 이름에 아래 조각이 들어가면 값을 가린다(대소문자 무시).
_SECRET_QUERY_HINTS = ("token", "secret", "password", "passwd", "apikey", "api_key", "api-key", "signature", "credential")
_SECRET_QUERY_KEYS = {"key", "sig", "auth", "authorization", "pwd", "code_verifier"}
_MASK = "***"


def _is_secret_param(name: str) -> bool:
    n = (name or "").lower()
    return n in _SECRET_QUERY_KEYS or any(h in n for h in _SECRET_QUERY_HINTS)


def _mask_secret_params(params: dict[str, str]) -> dict[str, str]:
    return {k: (_MASK if _is_secret_param(k) and v else v) for k, v in params.items()}


def _mask_url_query(url: str) -> str:
    """호출 URL 의 쿼리 중 비밀로 보이는 값을 *** 로 바꾼다(직접 입력한 api_key 등이 기록·화면에 남지 않게)."""
    try:
        p = urlsplit(url)
        if not p.query:
            return url
        pairs = parse_qsl(p.query, keep_blank_values=True)
        if not any(_is_secret_param(k) for k, _ in pairs):
            return url
        masked = [(k, _MASK if _is_secret_param(k) and v else v) for k, v in pairs]
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(masked, safe="*"), p.fragment))
    except ValueError:
        return url


# 보관 기간 지난 호출 기록 정리 — 호출 기록 시 최대 6시간에 한 번(프로세스 단위)
_PURGE_INTERVAL_SEC = 6 * 3600
_last_purge_at: float | None = None


async def _maybe_purge_invocations(session: AsyncSession) -> None:
    """보관 기간(``api_invocation_retention_days``, 0 이하면 끔)이 지난 호출 기록을 지운다.

    프로세스당 최대 ``_PURGE_INTERVAL_SEC`` 마다 한 번만 실행하며, 실패해도 예외를 올리지 않는다.
    """
    global _last_purge_at
    days = settings.api_invocation_retention_days
    now = time.monotonic()
    if days <= 0 or (_last_purge_at is not None and now - _last_purge_at < _PURGE_INTERVAL_SEC):
        return
    _last_purge_at = now
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        res = await session.execute(delete(CatalogApiInvocation).where(CatalogApiInvocation.created_at < cutoff))
        await session.commit()
        if res.rowcount:
            logger.info("보관 기간(%d일) 지난 API 호출 기록 %d건 삭제", days, res.rowcount)
    except Exception as e:  # noqa: BLE001 — 정리 실패가 호출 응답을 막지 않도록
        await session.rollback()
        logger.warning("API 호출 기록 정리 실패: %s", e)


_SECRET_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "cookie", "proxy-authorization", "x-auth-token"}


def _mask_secret_headers(headers: dict[str, str]) -> dict[str, str]:
    """민감 헤더 값을 마스킹해 이력에 저장(직접 입력한 토큰 등 보호)."""
    return {k: ("***" if k.lower() in _SECRET_HEADER_KEYS else v) for k, v in headers.items()}


# ---------------------------------------------------------------------------
# 사용량 관측 (호출 로깅 + 미터링)
# ---------------------------------------------------------------------------

def _percentile(values: list[int], pct: float) -> int:
    """백분위수(최근접 인덱스, 선형보간 없음). 입력은 정렬돼 있지 않아도 된다(내부에서 오름차순 정렬). 빈 목록은 0."""
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return int(s[k])


def _local_day(session: AsyncSession, col, tz_offset_min: int):
    """호출 시각을 사용자 시간대(UTC 기준 분 오프셋)의 날짜로 — 일별 집계용."""
    off = int(tz_offset_min)
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        # AT TIME ZONE INTERVAL '+540 minutes' → 그 시간대의 현지 시각(정수만 넣으므로 SQL 주입 없음)
        return func.date(func.timezone(literal_column(f"INTERVAL '{off} minutes'"), col))
    if dialect == "sqlite":
        return func.date(col, f"{off:+d} minutes")
    # MariaDB/MySQL — TIMESTAMP 는 세션 시간대로 읽히므로 그 날짜를 그대로 쓴다(보통 서버 현지 시간)
    return func.date(col)


async def get_api_usage(
    session: AsyncSession, name: str, days: int = 30, caller: str | None = None,
    endpoint_method: str | None = None, endpoint_path: str | None = None, tz_offset_min: int = 0,
) -> ApiUsageResponse | None:
    """API 의 호출 사용량 집계(최근 N일). 호출 수·성공률·지연·상태/엔드포인트/호출자별 분포·일별 추이.

    집계는 DB 에서 한다(기간이 길어도 전체 호출 행을 메모리로 읽지 않음).
    """
    api = await _get_by_name(session, name)
    if not api:
        return None
    inv = CatalogApiInvocation
    since = datetime.now(timezone.utc) - timedelta(days=days)
    cond_list = [inv.api_id == api.id, inv.created_at >= since]
    if caller:
        cond_list.append(inv.called_by == caller)
    if endpoint_method and endpoint_path:
        cond_list.append(func.coalesce(inv.endpoint_method, inv.method) == endpoint_method.upper())
        cond_list.append(inv.endpoint_path == endpoint_path)
    cond = tuple(cond_list)

    total, ok_count, avg_lat = (await session.execute(
        select(func.count(inv.id), func.coalesce(func.sum(case((inv.ok == "true", 1), else_=0)), 0), func.avg(inv.latency_ms))
        .where(*cond)
    )).one()
    total, ok_count = int(total or 0), int(ok_count or 0)
    if total == 0:
        return ApiUsageResponse(days=days, total_calls=0, success_calls=0, error_calls=0, success_rate=0.0,
                                avg_latency_ms=0, p95_latency_ms=0)

    # p95 — 지연 오름차순 k 번째 값(최근접 인덱스, 기존 _percentile 과 같은 규칙)
    k = max(0, min(total - 1, int(round(0.95 * (total - 1)))))
    p95 = (await session.execute(
        select(inv.latency_ms).where(*cond).order_by(inv.latency_ms).offset(k).limit(1)
    )).scalar_one_or_none() or 0

    by_status = (await session.execute(
        select(inv.status_code, func.count(inv.id)).where(*cond).group_by(inv.status_code).order_by(inv.status_code)
    )).all()

    # 엔드포인트 — 템플릿(메서드+경로)이 있으면 그것으로, 없으면 호출 URL 로 묶는다
    ep_m = func.coalesce(inv.endpoint_method, inv.method)
    ep_p = func.coalesce(inv.endpoint_path, inv.url)
    cnt = func.count(inv.id)
    top_eps = (await session.execute(
        select(ep_m, ep_p, cnt, func.avg(inv.latency_ms)).where(*cond).group_by(ep_m, ep_p).order_by(cnt.desc()).limit(10)
    )).all()

    top_callers = (await session.execute(
        select(inv.called_by, cnt).where(*cond).group_by(inv.called_by).order_by(cnt.desc()).limit(10)
    )).all()

    # 일별 — 사용자 시간대 기준 날짜. 바인드 값이 있는 식을 GROUP BY 에 반복하지 않도록 별칭으로 묶는다.
    day = _local_day(session, inv.created_at, tz_offset_min).label("bucket_day")
    err = func.coalesce(func.sum(case((inv.ok == "true", 0), else_=1)), 0)
    daily = (await session.execute(
        select(day, cnt, err).where(*cond).group_by(literal_column("bucket_day")).order_by(literal_column("bucket_day"))
    )).all()

    recent_rows = (await session.execute(
        select(inv).where(*cond).order_by(inv.id.desc()).limit(20)
    )).scalars().all()

    def _day(v: Any) -> str:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    return ApiUsageResponse(
        days=days, total_calls=total, success_calls=ok_count, error_calls=total - ok_count,
        success_rate=round(ok_count / total * 100, 1),
        avg_latency_ms=int(avg_lat or 0), p95_latency_ms=int(p95),
        by_status=[{"status": str(sc), "count": int(c)} for sc, c in by_status],
        top_endpoints=[{"endpoint": f"{m} {path}", "count": int(c), "avg_latency_ms": int(a or 0)} for m, path, c, a in top_eps],
        top_callers=[{"name": who or "(미상)", "count": int(c)} for who, c in top_callers],
        daily=[{"date": _day(d), "count": int(c), "errors": int(e)} for d, c, e in daily],
        recent=[
            {
                "id": r.id, "method": r.method, "url": r.url, "status_code": r.status_code,
                "ok": r.ok == "true", "latency_ms": r.latency_ms, "error": r.error,
                "called_by": r.called_by, "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent_rows
        ],
    )


async def list_endpoint_invocations(
    session: AsyncSession, name: str, method: str, path: str, user: str | None = None, mine: bool = True, limit: int = 20,
) -> list[ApiInvocationResponse] | None:
    """특정 엔드포인트(method+path)의 최근 호출 이력(입력값 포함). mine=True 면 본인 호출만."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    stmt = select(CatalogApiInvocation).where(
        CatalogApiInvocation.api_id == api.id,
        CatalogApiInvocation.endpoint_method == method.upper(),
        CatalogApiInvocation.endpoint_path == path,
    )
    if mine and user:
        stmt = stmt.where(CatalogApiInvocation.called_by == user)
    rows = (await session.execute(stmt.order_by(CatalogApiInvocation.id.desc()).limit(limit))).scalars().all()
    return [
        ApiInvocationResponse(
            id=r.id, endpoint_method=r.endpoint_method, endpoint_path=r.endpoint_path,
            method=r.method, url=r.url, status_code=r.status_code, ok=r.ok == "true",
            latency_ms=r.latency_ms, error=r.error, called_by=r.called_by,
            created_at=r.created_at, request_input=r.request_input,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 즐겨찾기 (사용자별 엔드포인트)
# ---------------------------------------------------------------------------

async def list_api_favorites(session: AsyncSession, name: str, user: str) -> list[dict] | None:
    """특정 API 의 (사용자) 즐겨찾기 — 별 표시용 {method, path} 목록."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    rows = (await session.execute(
        select(CatalogApiFavorite).where(CatalogApiFavorite.api_id == api.id, CatalogApiFavorite.user_key == user)
    )).scalars().all()
    return [{"method": r.method, "path": r.path} for r in rows]


async def add_api_favorite(session: AsyncSession, name: str, req: FavoriteCreate, user: str) -> bool | None:
    api = await _get_by_name(session, name)
    if not api:
        return None
    session.add(CatalogApiFavorite(user_key=user, api_id=api.id, method=req.method.upper(), path=req.path))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # 이미 즐겨찾기 — 멱등 처리
    return True


async def remove_api_favorite(session: AsyncSession, name: str, method: str, path: str, user: str) -> bool | None:
    api = await _get_by_name(session, name)
    if not api:
        return None
    await session.execute(
        delete(CatalogApiFavorite).where(
            CatalogApiFavorite.api_id == api.id, CatalogApiFavorite.user_key == user,
            CatalogApiFavorite.method == method.upper(), CatalogApiFavorite.path == path,
        )
    )
    await session.commit()
    return True


async def list_all_favorites(session: AsyncSession, user: str) -> list[FavoriteResponse]:
    """사용자의 전체 즐겨찾기 — API 정보·엔드포인트 요약과 함께(즐겨찾기 화면용)."""
    # 엔드포인트는 한 번의 외부 조인으로(즐겨찾기마다 따로 조회하지 않음). 없으면 endpoint_missing.
    rows = (await session.execute(
        select(CatalogApiFavorite, CatalogApi, CatalogApiEndpoint.id, CatalogApiEndpoint.summary)
        .join(CatalogApi, CatalogApi.id == CatalogApiFavorite.api_id)
        .outerjoin(CatalogApiEndpoint, (CatalogApiEndpoint.api_id == CatalogApiFavorite.api_id)
                   & (CatalogApiEndpoint.method == CatalogApiFavorite.method)
                   & (CatalogApiEndpoint.path == CatalogApiFavorite.path))
        .where(CatalogApiFavorite.user_key == user)
        .order_by(CatalogApi.name, CatalogApiFavorite.path)
    )).all()
    out: list[FavoriteResponse] = []
    seen: set[int] = set()
    for fav, api, ep_id, ep_summary in rows:
        if fav.id in seen:  # 같은 메서드·경로 엔드포인트가 중복된 예외적 경우
            continue
        seen.add(fav.id)
        out.append(FavoriteResponse(
            id=fav.id, api_id=api.id, api_name=api.name, api_display_name=api.display_name,
            api_status=api.status, api_protocol=api.protocol,
            method=fav.method, path=fav.path, summary=ep_summary, endpoint_missing=ep_id is None,
            created_at=fav.created_at,
        ))
    return out
