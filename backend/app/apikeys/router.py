# SPDX-License-Identifier: Apache-2.0
"""API 키 관리 API — 관리자, 또는 settings.api-keys 를 위임받은 역할 (``/api/v1/api-keys``). 관리자 역할 키는 관리자만.

- ``GET    /api-keys``              목록(평문 없음, ``include_revoked`` 로 폐기한 키 포함)
- ``GET    /api-keys/scopes``       고를 수 있는 범위
- ``POST   /api-keys``              발급 — 응답에만 평문 키
- ``PUT    /api-keys/{id}``         이름·설명·역할·범위·사용(중지/재개)
- ``POST   /api-keys/{id}/rotate``  재발급 — 새 평문, 이전 평문은 바로 무효
- ``DELETE /api-keys/{id}``         폐기(행은 남김, 되돌릴 수 없음)

발급한 키는 ``X-API-Key: <키>`` 또는 ``Authorization: Bearer <키>`` 로 보낸다. Basic 만 되는 클라이언트(스키마 레지스트리)는
비밀번호 자리에 키를 넣는다. 키로는 이 API 와 계정 API(``/auth/*``)를 부를 수 없다. 변경은 설정 범주 감사 로그에 남긴다.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.apikeys import service
from app.apikeys.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiKeyUpdateRequest,
    ScopeInfo,
)
from app.audit.service import record as audit_record
from app.core.auth import PermissionDenied, TokenUser
from app.core.database import get_session
from app.permissions.router import require_feature_granted

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


# API 키 관리 — 관리자, 또는 위임 기능 settings.api-keys(명시 허용형)를 받은 역할(TODO-123)
ApiKeyManager = Annotated[TokenUser, Depends(require_feature_granted("settings.api-keys"))]
ADMIN_ROLE = "argus-admin"


def _forbid_admin_key(current, *roles: str | None) -> None:
    """위임받은 사람은 자기가 줄 수 없는 역할(관리자 · 슈퍼유저 · 다른 사용자 정의 역할)의 키를 발급 · 수정 · 재발급 · 폐기할
    수 없다 — 권한 상승을 막는다(TODO-123, DEF-163). 줄 수 있는 역할은 사용자 관리 위임과 같다."""
    from app.usermgr.router import assignable_roles
    allowed = assignable_roles(current)
    if allowed is None:
        return
    if ADMIN_ROLE in roles:
        logger.warning("관리자 역할 API 키 작업 거부(위임 사용자): user=%s", current.username)
        raise PermissionDenied("관리자 역할 API 키는 관리자만 다룰 수 있습니다.",
                               user=current, kind="DELEGATE", key=f"settings.api-keys — {ADMIN_ROLE} 키")
    beyond = [r for r in roles if r is not None and r not in allowed]
    if beyond:
        logger.warning("역할 %s API 키 작업 거부(위임 사용자): user=%s", beyond[0], current.username)
        raise PermissionDenied("API 키 관리를 위임받은 사람은 기본 사용자 역할과 자기 역할의 키만 다룰 수 있습니다.",
                               user=current, kind="DELEGATE", key=f"settings.api-keys — {beyond[0]} 키")


async def _check_role(session: AsyncSession, role: str | None) -> None:
    """키에 줄 수 있는 역할인지 — 기본 역할 또는 사용자 정의 역할(TODO-124). 아니면 422."""
    if role is None:
        return
    from app.permissions import roles as custom_roles
    if role in custom_roles.builtin_role_ids() or role in await custom_roles.load_custom_roles(session):
        return
    logger.warning("API 키 역할 거부(없는 역할): %s", role)
    raise HTTPException(status_code=422, detail=f"없는 역할입니다: {role}")


def _target(row) -> str:
    return f"{row.name} ({service.owner_identity(row.id)})"


async def _get(session: AsyncSession, key_id: int):
    row = await service.get_key(session, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API 키를 찾을 수 없습니다.")
    return row


def _not_revoked(row) -> None:
    if row.status == "revoked":
        raise HTTPException(status_code=409, detail="폐기한 API 키는 바꿀 수 없습니다. 새로 발급하세요.")


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(_admin: ApiKeyManager, include_revoked: bool = False,
                        session: AsyncSession = Depends(get_session)):
    return await service.list_keys(session, include_revoked)


@router.get("/scopes", response_model=list[ScopeInfo])
async def list_scopes(_admin: ApiKeyManager):
    return [ScopeInfo(code=c, label=v[0], description=v[1]) for c, v in service.SCOPES.items()]


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(req: ApiKeyCreateRequest, admin: ApiKeyManager,
                         session: AsyncSession = Depends(get_session)):
    """API 키 발급. 평문 키는 이 응답에서만 보여 준다."""
    _forbid_admin_key(admin, req.role)
    await _check_role(session, req.role)
    try:
        expires_at = service.resolve_expiry(req.expires_in_days)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    row, raw = await service.create_key(session, name=req.name, description=req.description, role=req.role,
                                        scopes=req.scopes, expires_at=expires_at, created_by=admin.username)
    await session.commit()
    await session.refresh(row)
    logger.info("API 키 발급: id=%s name=%s role=%s scopes=%s by=%s", row.id, row.name, row.role_id, row.scopes,
                admin.username)
    await audit_record(session, admin, "settings", "apikey.create", target=_target(row),
                       summary=f"API 키 발급: {row.name}",
                       detail={"role": row.role_id, "scopes": row.scopes, "expires_at": row.expires_at})
    await session.refresh(row)
    return ApiKeyCreatedResponse(api_key=raw, **ApiKeyResponse.model_validate(row).model_dump())


@router.put("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(key_id: int, req: ApiKeyUpdateRequest, admin: ApiKeyManager,
                         session: AsyncSession = Depends(get_session)):
    row = await _get(session, key_id)
    _forbid_admin_key(admin, row.role_id, req.role)
    await _check_role(session, req.role)
    _not_revoked(row)
    changes: dict = {}

    def _set(field: str, new):
        old = getattr(row, field)
        if new is not None and new != old:
            changes[field] = {"old": old, "new": new}
            setattr(row, field, new)

    _set("name", req.name)
    _set("description", req.description)
    _set("role_id", req.role)
    _set("scopes", ",".join(req.scopes) if req.scopes is not None else None)
    if req.enabled is not None:
        _set("status", "active" if req.enabled else "disabled")
    if not changes:
        return row
    await session.commit()
    service.invalidate()
    logger.info("API 키 수정: id=%s fields=%s by=%s", row.id, list(changes), admin.username)
    await audit_record(session, admin, "settings", "apikey.modify", target=_target(row),
                       summary=f"API 키 수정: {row.name}", detail=changes)
    await session.refresh(row)
    return row


@router.post("/{key_id}/rotate", response_model=ApiKeyCreatedResponse)
async def rotate_api_key(key_id: int, admin: ApiKeyManager, session: AsyncSession = Depends(get_session)):
    """재발급 — 새 평문 키를 이 응답에서만 보여 주고, 이전 평문 키는 바로 못 쓴다."""
    row = await _get(session, key_id)
    _forbid_admin_key(admin, row.role_id)
    _not_revoked(row)
    raw = await service.rotate_key(session, row)
    await session.commit()
    logger.info("API 키 재발급: id=%s name=%s by=%s", row.id, row.name, admin.username)
    await audit_record(session, admin, "settings", "apikey.rotate", target=_target(row),
                       summary=f"API 키 재발급: {row.name}")
    await session.refresh(row)
    return ApiKeyCreatedResponse(api_key=raw, **ApiKeyResponse.model_validate(row).model_dump())


@router.delete("/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(key_id: int, admin: ApiKeyManager, session: AsyncSession = Depends(get_session)):
    """폐기 — 바로 못 쓰게 하고 행은 이력으로 남긴다(되돌릴 수 없음)."""
    row = await _get(session, key_id)
    _forbid_admin_key(admin, row.role_id)
    if row.status == "revoked":
        return row
    await service.revoke_key(session, row, admin.username)
    await session.commit()
    logger.info("API 키 폐기: id=%s name=%s by=%s", row.id, row.name, admin.username)
    await audit_record(session, admin, "settings", "apikey.revoke", target=_target(row),
                       summary=f"API 키 폐기: {row.name}")
    await session.refresh(row)
    return row
