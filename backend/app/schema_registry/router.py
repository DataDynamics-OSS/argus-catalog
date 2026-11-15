# SPDX-License-Identifier: Apache-2.0
"""Confluent 호환 스키마 레지스트리 API (읽기 전용) — ``/api/v1/schema-registry``.

클라이언트(NiFi ``ConfluentSchemaRegistry``, Kafka Avro 직렬화기 등)에는 ``http://<카탈로그>/api/v1/schema-registry`` 를
레지스트리 주소로 준다. subject 는 데이터셋 URN(옛 URN·별칭도 받음), 스키마 형식은 AVRO 뿐이다.

- ``GET /subjects`` · ``/subjects/{subject}/versions`` · ``/subjects/{subject}/versions/{version|latest}[/schema]``
- ``POST /subjects/{subject}`` — 보낸 스키마가 이 subject 에 등록돼 있는지(id·버전) 확인
- ``GET /schemas/ids/{id}[/schema|/subjects|/versions]`` · ``/schemas/types`` · ``/config[/{subject}]``

스키마 등록·삭제·호환성 변경은 하지 않는다(405). 스키마는 카탈로그에서 관리한다(수집·화면 편집·변경 요청).
오류 본문은 Confluent 형식 ``{"error_code", "message"}``. 인증은 ``app.schema_registry.auth``.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenUser
from app.core.database import get_session
from app.schema_registry import service
from app.schema_registry.auth import registry_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schema-registry", tags=["schema-registry"])

MEDIA_TYPE = "application/vnd.schemaregistry.v1+json"
# 내용이 바뀌지 않는 응답(id·버전 번호 조회) — 인증이 필요한 응답이라 공유 캐시는 쓰지 않는다
IMMUTABLE = "private, max-age=86400, immutable"


def _ok(content, *, cached: bool | None = None, immutable: bool = False) -> JSONResponse:
    headers = {}
    if cached is not None:
        headers["X-Cache-Hit"] = "true" if cached else "false"
    headers["Cache-Control"] = IMMUTABLE if immutable else "no-cache"
    return JSONResponse(content=content, media_type=MEDIA_TYPE, headers=headers)


def _raw_schema(text: str, *, cached: bool, immutable: bool) -> Response:
    """``…/schema`` — 스키마 본문만(이미 JSON 문자열이므로 다시 직렬화하지 않음)."""
    return Response(content=text, media_type=MEDIA_TYPE, headers={
        "X-Cache-Hit": "true" if cached else "false",
        "Cache-Control": IMMUTABLE if immutable else "no-cache",
    })


def _error(status_code: int, error_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, media_type=MEDIA_TYPE,
                        content={"error_code": error_code, "message": message})


def _subject_not_found(subject: str) -> JSONResponse:
    return _error(404, 40401, f"Subject '{subject}' not found. (데이터셋이 없습니다)")


def _version_view(v: dict) -> dict:
    return {"subject": v["subject"], "version": v["version"], "id": v["id"], "schema": v["schema"]}


async def _subject(session: AsyncSession, subject: str):
    return await service.resolve_subject(session, subject)


async def _version(session: AsyncSession, dataset_id: int, urn: str, version: str):
    """버전 문자열(``latest``·``-1``·숫자) → (값, 캐시 적중, 바뀌지 않는 응답인지) 또는 오류 응답."""
    if version in ("latest", "-1"):
        result = await service.latest(session, dataset_id, urn)
        if result is None:
            return _error(404, 40402, "Version not found. (Avro 스키마를 만들 수 없습니다)")
        return result[0], result[1], False
    try:
        number = int(version)
    except ValueError:
        return _error(422, 42202, "The specified version is not a valid version id. (latest 또는 1 이상의 정수)")
    if number < 1:
        return _error(422, 42202, "The specified version is not a valid version id. (latest 또는 1 이상의 정수)")
    result = await service.by_version(session, dataset_id, urn, number)
    if result is None:
        # 아직 등록 전일 수 있다 — 지금 스키마를 등록해 보고 다시 찾는다
        await service.latest(session, dataset_id, urn)
        result = await service.by_version(session, dataset_id, urn, number)
    if result is None:
        return _error(404, 40402, f"Version {number} not found.")
    return result[0], result[1], True


# ---------------------------------------------------------------------------
# subject
# ---------------------------------------------------------------------------

@router.get("/subjects")
async def list_subjects(_user: TokenUser = Depends(registry_user),
                        session: AsyncSession = Depends(get_session)):
    """subject(데이터셋 URN) 목록."""
    return _ok(await service.subjects(session))


@router.get("/subjects/{subject}/versions")
async def list_versions(subject: str, _user: TokenUser = Depends(registry_user),
                        session: AsyncSession = Depends(get_session)):
    """등록된 버전 번호 — 지금 스키마가 아직 등록 전이면 먼저 등록한다."""
    resolved = await _subject(session, subject)
    if resolved is None:
        return _subject_not_found(subject)
    dataset_id, urn = resolved
    await service.latest(session, dataset_id, urn)
    return _ok(await service.versions(session, dataset_id))


@router.get("/subjects/{subject}/versions/{version}")
async def get_version(subject: str, version: str, _user: TokenUser = Depends(registry_user),
                      session: AsyncSession = Depends(get_session)):
    """버전 하나 — ``{subject, version, id, schema}``. ``latest`` 는 지금 데이터셋 스키마."""
    resolved = await _subject(session, subject)
    if resolved is None:
        return _subject_not_found(subject)
    found = await _version(session, *resolved, version)
    if isinstance(found, Response):
        return found
    value, cached, immutable = found
    return _ok(_version_view(value), cached=cached, immutable=immutable)


@router.get("/subjects/{subject}/versions/{version}/schema")
async def get_version_schema(subject: str, version: str, _user: TokenUser = Depends(registry_user),
                             session: AsyncSession = Depends(get_session)):
    """버전 하나의 스키마 본문만."""
    resolved = await _subject(session, subject)
    if resolved is None:
        return _subject_not_found(subject)
    found = await _version(session, *resolved, version)
    if isinstance(found, Response):
        return found
    value, cached, immutable = found
    return _raw_schema(value["schema"], cached=cached, immutable=immutable)


@router.post("/subjects/{subject}")
async def lookup_schema(subject: str, request: Request, _user: TokenUser = Depends(registry_user),
                        session: AsyncSession = Depends(get_session)):
    """보낸 스키마가 이 subject 에 등록돼 있으면 그 버전(등록은 하지 않음). NiFi 가 id 로 찾은 스키마의 버전을 알 때 쓴다."""
    resolved = await _subject(session, subject)
    if resolved is None:
        return _subject_not_found(subject)
    try:
        body = json.loads(await request.body() or b"{}")
        schema = body.get("schema") if isinstance(body, dict) else None
    except ValueError:
        schema = None
    if not isinstance(schema, str):
        return _error(422, 42201, "Invalid schema. (본문은 {\"schema\": \"<Avro JSON 문자열>\"})")
    dataset_id, urn = resolved
    try:
        found = await service.by_schema(session, dataset_id, urn, schema)
        if found is None:      # 지금 스키마가 아직 등록 전일 수 있다
            await service.latest(session, dataset_id, urn)
            found = await service.by_schema(session, dataset_id, urn, schema)
    except ValueError as e:
        return _error(422, 42201, f"Invalid schema. ({e})")
    if found is None:
        return _error(404, 40403, "Schema not found.")
    return _ok(_version_view(found), immutable=True)


@router.post("/subjects/{subject}/versions")
@router.delete("/subjects/{subject}")
@router.delete("/subjects/{subject}/versions/{version}")
async def write_not_supported(_user: TokenUser = Depends(registry_user)):
    """등록·삭제는 하지 않는다 — 스키마는 카탈로그에서 관리한다."""
    return _error(405, 405, "읽기 전용 레지스트리입니다. 스키마는 Argus Catalog 화면·수집·변경 요청으로 바꿉니다.")


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------

async def _by_id(session: AsyncSession, schema_id: int):
    result = await service.by_id(session, schema_id)
    if result is None:
        return None, _error(404, 40403, f"Schema {schema_id} not found.")
    return result, None


@router.get("/schemas/ids/{schema_id}")
async def get_schema_by_id(schema_id: int, _user: TokenUser = Depends(registry_user),
                           session: AsyncSession = Depends(get_session)):
    """스키마 id → ``{"schema": "<Avro JSON 문자열>"}``."""
    result, err = await _by_id(session, schema_id)
    if err:
        return err
    value, cached = result
    return _ok({"schema": value["schema"]}, cached=cached, immutable=True)


@router.get("/schemas/ids/{schema_id}/schema")
async def get_schema_text_by_id(schema_id: int, _user: TokenUser = Depends(registry_user),
                                session: AsyncSession = Depends(get_session)):
    """스키마 id → 스키마 본문만."""
    result, err = await _by_id(session, schema_id)
    if err:
        return err
    value, cached = result
    return _raw_schema(value["schema"], cached=cached, immutable=True)


@router.get("/schemas/ids/{schema_id}/subjects")
async def get_subjects_by_id(schema_id: int, _user: TokenUser = Depends(registry_user),
                             session: AsyncSession = Depends(get_session)):
    """스키마 id 를 쓰는 subject — 데이터셋마다 행이 따로라 항상 하나."""
    result, err = await _by_id(session, schema_id)
    if err:
        return err
    value, cached = result
    return _ok([value["subject"]], cached=cached, immutable=True)


@router.get("/schemas/ids/{schema_id}/versions")
async def get_versions_by_id(schema_id: int, _user: TokenUser = Depends(registry_user),
                             session: AsyncSession = Depends(get_session)):
    """스키마 id → ``[{subject, version}]``."""
    result, err = await _by_id(session, schema_id)
    if err:
        return err
    value, cached = result
    return _ok([{"subject": value["subject"], "version": value["version"]}], cached=cached, immutable=True)


@router.get("/schemas/types")
async def schema_types(_user: TokenUser = Depends(registry_user)):
    return _ok(["AVRO"], immutable=True)


@router.get("/config")
@router.get("/config/{subject}")
async def get_config(_user: TokenUser = Depends(registry_user)):
    """호환성 검사는 하지 않는다(등록을 받지 않으므로) — 클라이언트 호환용 응답."""
    return _ok({"compatibilityLevel": "NONE"})
