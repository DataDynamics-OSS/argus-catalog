# SPDX-License-Identifier: Apache-2.0
"""API Catalog 서비스 — 스펙 파싱·등록, CRUD, 상태 이력, Try-it 프록시."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, func, select
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
    InvokeRequest,
    InvokeResponse,
    LineageCreate,
    LineageResponse,
    SpecDiffResponse,
    SpecUpload,
)

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower()).strip("-.")
    return s or "api"


async def _get_by_name(session: AsyncSession, name: str) -> CatalogApi | None:
    return (await session.execute(select(CatalogApi).where(CatalogApi.name == name))).scalars().first()


async def _resolve_spec_text(req_text: str | None, req_url: str | None) -> tuple[str, str | None]:
    """스펙 텍스트를 직접 또는 URL 에서 가져와 반환. (text, source_url)"""
    if req_text and req_text.strip():
        return req_text, None
    if req_url and req_url.strip():
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(req_url.strip())
            resp.raise_for_status()
            return resp.text, req_url.strip()
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


async def create_api(session: AsyncSession, req: ApiCreate, created_by: str | None = None) -> ApiSummary:
    # 스펙 미입력 시 수동 등록(엔드포인트는 상세에서 직접 추가)
    if not (req.spec_text or req.spec_url):
        return await _create_manual_api(session, req, created_by)

    text, source_url = await _resolve_spec_text(req.spec_text, req.spec_url)
    parsed = parser.parse(text, source_url)

    name = (req.name or _slugify(parsed.get("title") or "")).strip()
    if await _get_by_name(session, name):
        raise ValueError(f"이미 존재하는 API 이름입니다: {name}")

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
        base_url=parsed.get("base_url"),
        tags=req.tags,
        created_by=created_by,
    )
    session.add(api)
    await session.flush()  # api.id

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
        raise ValueError("수동 등록 시 API 이름은 필수입니다.")
    if await _get_by_name(session, name):
        raise ValueError(f"이미 존재하는 API 이름입니다: {name}")
    base_url = (req.base_url or "").strip() or None
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
        contract_url=(req.contract_url or "").strip() or None,
        tags=req.tags,
        created_by=created_by,
    )
    session.add(api)
    await session.commit()
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


async def add_endpoint(session: AsyncSession, name: str, req: EndpointCreate, created_by: str | None = None):
    api = await _get_by_name(session, name)
    if not api:
        return None
    if api.source != "manual":
        raise ValueError("스펙 기반 API 는 엔드포인트를 수동 추가할 수 없습니다(스펙 업로드로 관리).")
    max_order = await session.scalar(
        select(func.coalesce(func.max(CatalogApiEndpoint.sort_order), -1)).where(CatalogApiEndpoint.api_id == api.id)
    )
    ep = CatalogApiEndpoint(
        api_id=api.id, method=_norm_method(api, req.method), path=req.path.strip(),
        operation_id=req.operation_id, summary=req.summary, description=req.description,
        tags=req.tags, parameters=req.parameters, request_body=req.request_body,
        responses=req.responses, extra=req.extra, sort_order=int(max_order) + 1,
    )
    session.add(ep)
    await session.commit()
    await session.refresh(ep)

    # 엔드포인트는 임베딩 소스 텍스트에 포함되므로 재임베딩
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return ep


async def update_endpoint(session: AsyncSession, name: str, ep_id: int, req: EndpointCreate):
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
    ep.method = _norm_method(api, req.method)
    ep.path = req.path.strip()
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

    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return ep


async def delete_endpoint(session: AsyncSession, name: str, ep_id: int) -> bool | None:
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

    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)

    return True


async def add_spec(session: AsyncSession, name: str, req: SpecUpload, created_by: str | None = None) -> ApiSummary | None:
    api = await _get_by_name(session, name)
    if not api:
        return None
    text, source_url = await _resolve_spec_text(req.spec_text, req.spec_url)
    parsed = parser.parse(text, source_url)

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
    return True


# ---------------------------------------------------------------------------
# 리니지 (provides / consumes / depends_on)
# ---------------------------------------------------------------------------

_LINEAGE_RELATIONS = {"provides", "consumes", "depends_on"}
_LINEAGE_TARGET_TYPES = {"api", "dataset", "model", "agent", "system"}


async def list_api_lineage(session: AsyncSession, name: str):
    """API 의 리니지 엣지 목록(최신순)."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    rows = (await session.execute(
        select(CatalogApiLineage).where(CatalogApiLineage.api_id == api.id).order_by(CatalogApiLineage.id.desc())
    )).scalars().all()
    return [LineageResponse.model_validate(r) for r in rows]


async def add_api_lineage(session: AsyncSession, name: str, req: LineageCreate, created_by: str | None = None) -> LineageResponse | None:
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
        await session.rollback()
        raise ValueError("이미 등록된 리니지 관계입니다.")
    await session.refresh(edge)
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
    return True


async def _endpoint_count(session: AsyncSession, api_id: int) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(CatalogApiEndpoint).where(CatalogApiEndpoint.api_id == api_id)
    ) or 0)


async def _summary(session: AsyncSession, api: CatalogApi) -> ApiSummary:
    s = ApiSummary.model_validate(api)
    s.endpoint_count = await _endpoint_count(session, api.id)
    return s


async def list_apis(session: AsyncSession, *, search: str | None, status: str | None,
                    category: str | None, page: int, page_size: int) -> dict:
    q = select(CatalogApi)
    if search:
        like = f"%{search}%"
        q = q.where(
            CatalogApi.name.ilike(like) | CatalogApi.display_name.ilike(like)
            | CatalogApi.description.ilike(like)
        )
    if status:
        q = q.where(CatalogApi.status == status)
    if category:
        q = q.where(CatalogApi.category == category)
    total = int(await session.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = (await session.execute(
        q.order_by(CatalogApi.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = []
    for api in rows:
        items.append(await _summary(session, api))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_detail(session: AsyncSession, name: str) -> ApiDetailResponse | None:
    api = await _get_by_name(session, name)
    if not api:
        return None
    detail = ApiDetailResponse.model_validate(api)
    detail.endpoint_count = await _endpoint_count(session, api.id)
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
    api = await _get_by_name(session, name)
    if not api:
        return None
    data = req.model_dump(exclude_unset=True)
    prev_status = api.status
    if "status" in data and data["status"] is not None:
        data["status"] = data["status"].value if hasattr(data["status"], "value") else data["status"]
    # Base URL 수동 지정 우선: 값이 있으면 override 플래그 ON, 비우면 OFF(스펙 자동 추출로 복귀)
    if "base_url" in data:
        v = (data["base_url"] or "").strip() or None
        data["base_url"] = v
        api.base_url_overridden = "true" if v else "false"
    for k, v in data.items():
        setattr(api, k, v)
    if "status" in data and data["status"] is not None and data["status"] != prev_status:
        session.add(CatalogApiStatusHistory(api_id=api.id, from_status=prev_status, to_status=data["status"], changed_by=changed_by))
    await session.commit()
    await session.refresh(api)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("api", api.id)
    return await _summary(session, api)


async def delete_api(session: AsyncSession, name: str) -> bool:
    api = await _get_by_name(session, name)
    if not api:
        return False

    # 임베딩 행 정리 (다형 테이블이라 FK CASCADE 가 없음)
    from app.embedding.service import delete_entity_embedding
    await delete_entity_embedding(session, "api", api.id)

    await session.delete(api)
    await session.commit()
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
    total = int(await session.scalar(select(func.count()).select_from(CatalogApi)) or 0)
    published = int(await session.scalar(select(func.count()).select_from(CatalogApi).where(CatalogApi.status == "published")) or 0)
    by_status_rows = (await session.execute(select(CatalogApi.status, func.count()).group_by(CatalogApi.status))).all()
    by_proto_rows = (await session.execute(select(CatalogApi.protocol, func.count()).group_by(CatalogApi.protocol))).all()
    total_ep = int(await session.scalar(select(func.count()).select_from(CatalogApiEndpoint)) or 0)
    return ApiStats(
        total_apis=total,
        published_apis=published,
        by_status=[{"name": s or "unknown", "count": c} for s, c in by_status_rows],
        by_protocol=[{"name": p or "unknown", "count": c} for p, c in by_proto_rows],
        total_endpoints=total_ep,
    )


# ---------------------------------------------------------------------------
# 버전 디프 (Breaking change 감지)
# ---------------------------------------------------------------------------

async def lint_api(session: AsyncSession, name: str, spec_id: int | None = None) -> ApiLintResponse | None:
    """API 의 현재(또는 지정) 스펙을 린팅한다. 스펙이 없으면 None 대신 빈 결과."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    if spec_id is not None:
        spec = (await session.execute(
            select(CatalogApiSpec).where(CatalogApiSpec.id == spec_id, CatalogApiSpec.api_id == api.id)
        )).scalars().first()
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

async def add_credential(session: AsyncSession, name: str, req: CredentialCreate, created_by: str | None = None) -> CredentialResponse | None:
    api = await _get_by_name(session, name)
    if not api:
        return None
    cred = CatalogApiCredential(
        api_id=api.id, scheme_name=req.scheme_name, label=req.label, type=req.type,
        secret=crypto.encrypt(json.dumps(req.values or {})), created_by=created_by,
    )
    session.add(cred)
    await session.commit()
    await session.refresh(cred)
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
    return True


async def _build_auth(session: AsyncSession, api_id: int, credential_id: int) -> tuple[dict[str, str], dict[str, str]]:
    """자격증명 → (주입 헤더, 주입 쿼리). 시크릿은 서버에서만 복호화."""
    cred = (await session.execute(
        select(CatalogApiCredential).where(CatalogApiCredential.id == credential_id, CatalogApiCredential.api_id == api_id)
    )).scalars().first()
    if not cred:
        return {}, {}
    try:
        values = json.loads(crypto.decrypt(cred.secret) or "{}")
    except Exception as e:
        logger.warning("자격증명 복호화/파싱 실패(api_id=%s, cred_id=%s): %s", api_id, credential_id, e)
        return {}, {}
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
    return headers, query


async def invoke(session: AsyncSession, req: InvokeRequest, called_by: str | None = None) -> InvokeResponse:
    """Try-it 프록시 — 브라우저 CORS 우회 및 저장 자격증명 서버 주입.

    호출 대상이 알려진 API 로 식별되면 사용량 관측을 위해 호출 로그를 남긴다.
    """
    headers = dict(req.headers or {})
    url = req.url
    api = await _get_by_name(session, req.api_name) if req.api_name else None
    if api and req.credential_id:
        ah, aq = await _build_auth(session, api.id, req.credential_id)
        headers.update(ah)
        if aq:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(aq)}"
    start = time.monotonic()
    status, body, resp_headers, error = 0, "", {}, None
    try:
        async with httpx.AsyncClient(timeout=req.timeout, follow_redirects=True) as client:
            resp = await client.request(
                req.method.upper(), url,
                headers=headers,
                content=None if req.body is None else (req.body if isinstance(req.body, str) else json.dumps(req.body)),
            )
        status, body, resp_headers = resp.status_code, resp.text[:200000], dict(resp.headers)
    except Exception as e:
        error = str(e)
    latency = int((time.monotonic() - start) * 1000)

    # 사용량 로깅(알려진 API 한정) — 로깅 실패가 호출 응답을 막지 않도록 보호
    if api is not None:
        try:
            session.add(CatalogApiInvocation(
                api_id=api.id, method=req.method.upper(), url=req.url[:2000],
                status_code=status, ok="true" if 200 <= status < 400 else "false",
                latency_ms=latency, error=error, called_by=called_by,
                endpoint_method=(req.endpoint_method or req.method).upper(),
                endpoint_path=req.endpoint_path,
                request_input={
                    "path_params": req.path_params or {},
                    "query_params": req.query_params or {},
                    "headers": _mask_secret_headers(req.headers or {}),
                    "body": req.body if isinstance(req.body, str) else (json.dumps(req.body, ensure_ascii=False) if req.body is not None else ""),
                },
            ))
            await session.commit()
        except Exception as log_err:  # noqa: BLE001
            await session.rollback()
            logger.warning("호출 로그 기록 실패(api=%s): %s", api.name, log_err)

    return InvokeResponse(status=status, headers=resp_headers, body=body, latency_ms=latency, error=error)


_SECRET_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "cookie", "proxy-authorization", "x-auth-token"}


def _mask_secret_headers(headers: dict[str, str]) -> dict[str, str]:
    """민감 헤더 값을 마스킹해 이력에 저장(직접 입력한 토큰 등 보호)."""
    return {k: ("***" if k.lower() in _SECRET_HEADER_KEYS else v) for k, v in headers.items()}


# ---------------------------------------------------------------------------
# 사용량 관측 (호출 로깅 + 미터링)
# ---------------------------------------------------------------------------

def _percentile(values: list[int], pct: float) -> int:
    """정렬 없이 호출 가능 — 오름차순 정렬 후 선형보간 없이 최근접 인덱스로 계산."""
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return int(s[k])


async def get_api_usage(session: AsyncSession, name: str, days: int = 30) -> ApiUsageResponse | None:
    """API 의 호출 사용량 집계(최근 N일). 호출 수·성공률·지연·상태/엔드포인트/호출자별 분포·일별 추이."""
    api = await _get_by_name(session, name)
    if not api:
        return None
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await session.execute(
        select(CatalogApiInvocation)
        .where(CatalogApiInvocation.api_id == api.id, CatalogApiInvocation.created_at >= since)
        .order_by(CatalogApiInvocation.id.desc())
    )).scalars().all()

    total = len(rows)
    ok_count = sum(1 for r in rows if r.ok == "true")
    err_count = total - ok_count
    latencies = [int(r.latency_ms or 0) for r in rows]
    avg_latency = int(sum(latencies) / total) if total else 0

    by_status: dict[str, int] = {}
    by_endpoint: dict[str, dict[str, Any]] = {}
    by_caller: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for r in rows:
        sc = str(r.status_code)
        by_status[sc] = by_status.get(sc, 0) + 1
        key = f"{r.method} {r.url}"
        ep = by_endpoint.setdefault(key, {"endpoint": key, "count": 0, "_lat": 0})
        ep["count"] += 1
        ep["_lat"] += int(r.latency_ms or 0)
        caller = r.called_by or "(미상)"
        by_caller[caller] = by_caller.get(caller, 0) + 1
        day = r.created_at.date().isoformat() if r.created_at else "?"
        by_day[day] = by_day.get(day, 0) + 1

    top_endpoints = sorted(
        ({"endpoint": v["endpoint"], "count": v["count"], "avg_latency_ms": int(v["_lat"] / v["count"])} for v in by_endpoint.values()),
        key=lambda x: x["count"], reverse=True,
    )[:10]
    top_callers = sorted(
        ({"name": k, "count": v} for k, v in by_caller.items()), key=lambda x: x["count"], reverse=True,
    )[:10]
    daily = [{"date": d, "count": by_day[d]} for d in sorted(by_day.keys())]
    recent = [
        {
            "id": r.id, "method": r.method, "url": r.url, "status_code": r.status_code,
            "ok": r.ok == "true", "latency_ms": r.latency_ms, "error": r.error,
            "called_by": r.called_by, "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows[:20]
    ]

    return ApiUsageResponse(
        days=days, total_calls=total, success_calls=ok_count, error_calls=err_count,
        success_rate=round(ok_count / total * 100, 1) if total else 0.0,
        avg_latency_ms=avg_latency, p95_latency_ms=_percentile(latencies, 95),
        by_status=[{"status": k, "count": v} for k, v in sorted(by_status.items())],
        top_endpoints=top_endpoints, top_callers=top_callers, daily=daily, recent=recent,
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
    rows = (await session.execute(
        select(CatalogApiFavorite, CatalogApi)
        .join(CatalogApi, CatalogApi.id == CatalogApiFavorite.api_id)
        .where(CatalogApiFavorite.user_key == user)
        .order_by(CatalogApi.name, CatalogApiFavorite.path)
    )).all()
    out: list[FavoriteResponse] = []
    for fav, api in rows:
        ep = (await session.execute(
            select(CatalogApiEndpoint).where(
                CatalogApiEndpoint.api_id == api.id,
                CatalogApiEndpoint.method == fav.method,
                CatalogApiEndpoint.path == fav.path,
            )
        )).scalars().first()
        out.append(FavoriteResponse(
            id=fav.id, api_id=api.id, api_name=api.name, api_display_name=api.display_name,
            method=fav.method, path=fav.path, summary=ep.summary if ep else None, created_at=fav.created_at,
        ))
    return out
