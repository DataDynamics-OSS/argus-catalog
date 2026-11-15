# SPDX-License-Identifier: Apache-2.0
"""API 키 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.usermgr.schemas import RoleId, RoleName


def _check_scopes(value: list[str] | None) -> list[str] | None:
    from app.apikeys.service import SCOPES
    if value is None:
        return None
    cleaned = sorted({s.strip() for s in value if s and s.strip()})
    if not cleaned:
        raise ValueError("범위를 하나 이상 고르세요.")
    unknown = [s for s in cleaned if s not in SCOPES]
    if unknown:
        raise ValueError(f"알 수 없는 범위: {', '.join(unknown)} (가능: {', '.join(SCOPES)})")
    return cleaned


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="키 이름(어느 프로그램이 쓰는지)")
    description: str | None = Field(None, max_length=500)
    role: RoleId = Field(RoleName.USER.value, description="키에 줄 역할 — 사용자와 같은 권한 기준(사용자 정의 역할 포함)")
    scopes: list[str] = Field(default_factory=lambda: ["catalog:read"], description="부를 수 있는 API 범위")
    expires_in_days: int | None = Field(
        None, ge=0, le=3650, description="만료까지 일수. 비우면 서버 기본값, 0 이면 무기한(서버가 허용할 때)")

    _scopes = field_validator("scopes")(_check_scopes)


class ApiKeyUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    role: RoleId | None = None
    scopes: list[str] | None = None
    enabled: bool | None = None

    _scopes = field_validator("scopes")(_check_scopes)


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    key_prefix: str
    role_id: str
    scopes: list[str]
    status: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    last_used_ip: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str):
            return [s for s in v.split(",") if s]
        return v

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    api_key: str = Field(..., description="평문 키 — 이 응답에서만 보여 주며 다시 볼 수 없다")


class ScopeInfo(BaseModel):
    code: str
    label: str
    description: str
