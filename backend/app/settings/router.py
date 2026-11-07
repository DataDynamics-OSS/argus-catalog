# SPDX-License-Identifier: Apache-2.0
"""Catalog Server 의 설정(Settings) API.

오브젝트 스토리지(MinIO/S3), 임베딩, LLM, 인증(Keycloak), CORS, 캐시 등의
구성 값을 읽고 갱신하는 엔드포인트를 제공한다. 모든 설정은 ``catalog_configuration``
테이블에 카테고리별로 저장되며, 갱신 시 ``app/settings/service.py`` 의
``load_*_settings`` 헬퍼를 호출해 in-memory ``settings`` 객체에 즉시 반영한다.

로깅 정책:
- 설정 저장은 INFO 로 변경된 핵심 키만 기록 (secret 은 제외)
- 외부 연동(테스트·초기화) 실패는 WARNING
"""

import logging

import aioboto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.database import async_session, get_session
from app.embedding.base import DEFAULT_EMBEDDING_MODEL
from app.settings.mail import EmailConfig, load_mail_config, send_mail
from app.settings.notify import NotifyConfig, load_notify_config, send_notify
from app.settings.service import (
    DATASET_CREATE_BY_USERS_KEY,
    config_sources,
    dataset_create_by_users_enabled,
    get_config_by_category,
    load_auth_settings,
    load_cors_settings,
    load_embedding_settings,
    load_llm_settings,
    load_os_settings,
    update_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

class ObjectStorageConfig(BaseModel):
    endpoint: str = ""
    # 외부용 주소(presigned URL 호스트). 비우면 endpoint 를 쓴다.
    public_endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    use_ssl: bool = False
    bucket: str = "model-artifacts"
    presigned_url_expiry: int = 3600


class ObjectStorageTestRequest(BaseModel):
    endpoint: str = Field(..., description="S3-compatible endpoint URL")
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    bucket: str = "model-artifacts"


class TestResponse(BaseModel):
    success: bool
    message: str


# 비밀값(키·비밀번호·webhook URL)은 조회 응답에서 마스킹한다. 클라이언트가 마스킹 값을
# 그대로 돌려보내면(다른 필드만 수정) 저장 시 기존 값을 유지하고, 연결 테스트에서는
# 저장된 원본으로 치환한다.
_SECRET_MASK = "••••••••"


def _mask(value: str | None) -> str:
    """값이 있으면 마스킹 문자열, 없으면 빈 문자열."""
    return _SECRET_MASK if value else ""


def _is_masked(value: str | None) -> bool:
    """클라이언트가 마스킹 문자열을 그대로 돌려보냈는지(=값을 바꾸지 않음)."""
    return value == _SECRET_MASK


async def _unmask(value: str, category: str, key: str) -> str:
    """마스킹 값이면 DB 에 저장된 원본을 돌려준다(연결 테스트용)."""
    if not _is_masked(value):
        return value
    logger.debug("마스킹 값 → 저장된 원본으로 치환: category=%s key=%s", category, key)
    async with async_session() as session:
        cfg = await get_config_by_category(session, category)
    return cfg.get(key, "")


def _norm_url(url: str | None) -> str:
    """URL 비교용 정규화 — 공백·끝 슬래시 제거, 소문자."""
    return (url or "").strip().rstrip("/").lower()


_KEY_REENTRY_MSG = ("제공자나 API URL 을 바꿀 때는 API 키를 다시 입력하세요 — "
                    "저장된 키를 다른 대상으로 보내지 않습니다.")


async def _masked_key_retargeted(api_key: str, category: str, prefix: str,
                                 provider: str, api_url: str) -> bool:
    """마스킹된(=저장된) API 키를 저장 때와 다른 제공자/URL 로 쓰려는지.

    True 면 거부해야 한다 — 그대로 두면 관리자 화면에서 URL 만 바꿔 저장된 LLM·임베딩 키를
    임의 서버로 보낼 수 있다(키 탈취). 새 키를 직접 입력한 경우는 해당 없음.
    """
    if not _is_masked(api_key):
        return False
    async with async_session() as session:
        cfg = await get_config_by_category(session, category)
    return (
        (cfg.get(f"{prefix}_provider", "") or "") != (provider or "")
        or _norm_url(cfg.get(f"{prefix}_api_url", "")) != _norm_url(api_url)
    )


async def _audit_initialize(actor, action: str, target: str, steps: list[dict]) -> None:
    """초기화(버킷 생성·Keycloak realm 구성)는 외부에 변경을 만들므로 단계 결과와 함께 감사 기록."""
    from app.audit.service import record
    failed = any(st.get("status") == "error" for st in steps)
    await record(
        None, actor, "settings", action, target=target,
        summary=f"{action.split('.', 1)[1]} {'실패' if failed else '완료'} ({len(steps)}단계)",
        detail={"steps": [{k: st.get(k) for k in ("step", "status")} for st in steps]},
    )


# ---------------------------------------------------------------------------
# 오브젝트 스토리지 설정 조회 / 갱신 (DB 기반)
# ---------------------------------------------------------------------------

@router.get("/object-storage", response_model=ObjectStorageConfig)
async def get_object_storage_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """DB 에서 현재 오브젝트 스토리지 설정을 읽어 반환한다(secret_key 는 마스킹)."""
    logger.debug("오브젝트 스토리지 설정 조회")
    cfg = await get_config_by_category(session, "object_storage")
    return ObjectStorageConfig(
        endpoint=cfg.get("object_storage_endpoint", ""),
        public_endpoint=cfg.get("object_storage_public_endpoint", ""),
        access_key=cfg.get("object_storage_access_key", ""),
        secret_key=_mask(cfg.get("object_storage_secret_key", "")),
        region=cfg.get("object_storage_region", "us-east-1"),
        use_ssl=cfg.get("object_storage_use_ssl", "false").lower() in ("true", "1", "yes"),
        bucket=cfg.get("object_storage_bucket", "model-artifacts"),
        presigned_url_expiry=int(cfg.get("object_storage_presigned_url_expiry", "3600")),
    )


@router.put("/object-storage")
async def update_object_storage_config(_guard: AdminUser,
    body: ObjectStorageConfig,
    session: AsyncSession = Depends(get_session),
):
    """오브젝트 스토리지 설정을 DB 에 저장하고 in-memory 설정·S3 세션을 재초기화한다."""
    items = {
        "object_storage_endpoint": body.endpoint,
        "object_storage_public_endpoint": body.public_endpoint.strip(),
        "object_storage_access_key": body.access_key,
        "object_storage_region": body.region,
        "object_storage_use_ssl": str(body.use_ssl).lower(),
        "object_storage_bucket": body.bucket,
        "object_storage_presigned_url_expiry": str(body.presigned_url_expiry),
    }
    if not _is_masked(body.secret_key):
        items["object_storage_secret_key"] = body.secret_key
    await update_config(session, "object_storage", items, actor=_guard)

    # 변경된 값을 in-memory settings 에 반영
    await load_os_settings(session)

    # 다음 S3 호출이 새 자격 증명을 쓰도록 캐시된 세션 폐기
    from app.core import s3
    s3._session = None

    logger.info("오브젝트 스토리지 설정 DB 저장: endpoint=%s, bucket=%s", body.endpoint, body.bucket)
    return {"status": "ok", "message": "Object Storage configuration saved"}


# ---------------------------------------------------------------------------
# 연결성 테스트
# ---------------------------------------------------------------------------

@router.post("/object-storage/test", response_model=TestResponse)
async def test_object_storage(_guard: AdminUser, body: ObjectStorageTestRequest):
    """지정된 버킷에 ``head_bucket`` 을 호출해 S3 연결성을 확인한다."""
    logger.info("오브젝트 스토리지 테스트: endpoint=%s, bucket=%s", body.endpoint, body.bucket)
    try:
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=body.endpoint,
            aws_access_key_id=body.access_key,
            aws_secret_access_key=await _unmask(
                body.secret_key, "object_storage", "object_storage_secret_key"
            ),
            region_name=body.region,
        ) as s3:
            await s3.head_bucket(Bucket=body.bucket)
            msg = f"연결 성공. 버킷 '{body.bucket}' 가 존재합니다."
            logger.info("오브젝트 스토리지 테스트 성공: %s", msg)
            return TestResponse(success=True, message=msg)
    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg or "NoSuchBucket" in error_msg or "Not Found" in error_msg:
            msg = f"버킷 '{body.bucket}' 가 존재하지 않습니다."
        elif "InvalidAccessKeyId" in error_msg or "SignatureDoesNotMatch" in error_msg:
            msg = "인증 실패: access key 또는 secret key 가 올바르지 않습니다."
        elif "ConnectTimeoutError" in error_msg or "EndpointConnectionError" in error_msg:
            msg = f"연결 실패: {body.endpoint} 에 도달할 수 없습니다."
        else:
            msg = f"오류: {error_msg}"
        logger.warning("오브젝트 스토리지 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)


class _StepsDoneError(Exception):
    """초기화 단계를 이미 기록하고 중단할 때."""


@router.post("/object-storage/initialize")
async def initialize_object_storage(_guard: AdminUser, body: ObjectStorageTestRequest):
    """버킷이 없으면 생성한다. 이미 있으면 건너뛴다(멱등)."""
    logger.info("오브젝트 스토리지 초기화: endpoint=%s, bucket=%s", body.endpoint, body.bucket)
    steps: list[dict] = []

    try:
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=body.endpoint,
            aws_access_key_id=body.access_key,
            aws_secret_access_key=await _unmask(
                body.secret_key, "object_storage", "object_storage_secret_key"
            ),
            region_name=body.region,
        ) as s3:
            # Step 1: 연결 확인 — 실제로 응답을 받은 뒤에만 ok (예전에는 클라이언트 생성만으로 ok 를 기록)
            bucket_missing = False
            try:
                await s3.head_bucket(Bucket=body.bucket)
            except ClientError as e:
                code = str(e.response.get("Error", {}).get("Code", ""))
                if code in ("404", "NoSuchBucket", "NotFound"):
                    bucket_missing = True
                else:
                    # 서버는 응답했지만 거부(403 등) — 연결은 됐고 버킷 단계가 실패
                    steps.append({"step": "S3 Connection", "status": "ok", "message": f"{body.endpoint} 응답함"})
                    steps.append({"step": f"Bucket '{body.bucket}'", "status": "error",
                                  "message": f"버킷을 확인할 수 없습니다 ({code}): {e}"})
                    raise _StepsDoneError() from None
            steps.append({"step": "S3 Connection", "status": "ok", "message": f"{body.endpoint} 에 연결됨"})

            # Step 2: 버킷이 없으면 생성
            if not bucket_missing:
                steps.append({"step": f"Bucket '{body.bucket}'", "status": "skip", "message": "이미 존재함"})
            else:
                try:
                    await s3.create_bucket(Bucket=body.bucket)
                    steps.append({"step": f"Bucket '{body.bucket}'", "status": "created", "message": "생성 완료"})
                except Exception as create_err:
                    logger.warning("오브젝트 스토리지 버킷 '%s' 생성 실패: %s", body.bucket, create_err)
                    steps.append({"step": f"Bucket '{body.bucket}'", "status": "error", "message": str(create_err)})

    except _StepsDoneError:
        pass
    except Exception as e:
        logger.warning("오브젝트 스토리지 초기화 연결 단계 실패: %s", e)
        steps.append({"step": "S3 Connection", "status": "error", "message": str(e)})

    has_error = any(s.get("status") == "error" for s in steps)
    if has_error:
        logger.warning("오브젝트 스토리지 초기화 오류로 종료 (%d개 단계)", len(steps))
    else:
        logger.info("오브젝트 스토리지 초기화 정상 종료 (%d개 단계)", len(steps))
    await _audit_initialize(_guard, "settings.object_storage_initialize", body.bucket, steps)
    return {"steps": steps}


# ---------------------------------------------------------------------------
# 임베딩 설정
# ---------------------------------------------------------------------------

class EmbeddingConfig(BaseModel):
    enabled: bool = False
    # 쓰기(생성/수정/sync)마다 자동 임베딩. off 면 백필로만 — bulk sync 부하 회피(기본 off).
    auto_on_write: bool = False
    provider: str = "local"
    model: str = DEFAULT_EMBEDDING_MODEL
    api_key: str = ""
    api_url: str = ""
    dimension: int = 384


@router.get("/embedding", response_model=EmbeddingConfig)
async def get_embedding_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 임베딩 설정 반환(api_key 는 마스킹)."""
    logger.debug("임베딩 설정 조회")
    cfg = await get_config_by_category(session, "embedding")
    return EmbeddingConfig(
        enabled=cfg.get("embedding_enabled", "false").lower() in ("true", "1", "yes"),
        auto_on_write=cfg.get("embedding_auto_on_write", "false").lower() in ("true", "1", "yes"),
        provider=cfg.get("embedding_provider", "local"),
        model=cfg.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
        api_key=_mask(cfg.get("embedding_api_key", "")),
        api_url=cfg.get("embedding_api_url", ""),
        dimension=int(cfg.get("embedding_dimension", "384")),
    )


@router.put("/embedding")
async def update_embedding_config(_guard: AdminUser,
    body: EmbeddingConfig,
    session: AsyncSession = Depends(get_session),
):
    """임베딩 설정을 저장하고 provider 를 재초기화한다."""
    from app.embedding.models import VECTOR_DIMENSION
    from app.embedding.registry import KNOWN_MODEL_DIMENSIONS
    # pgvector 컬럼은 vector(384) 고정 — 다른 차원 모델을 저장하면 백필이 모두 오류,
    # 시맨틱 검색이 500 이 되므로 저장 단계에서 거부한다. 알려진 모델은 모델 차원과 요청 차원이
    # 모두 384 여야 하고, 모르는 모델은 요청 차원만 본다.
    dim = KNOWN_MODEL_DIMENSIONS.get((body.provider, body.model), body.dimension)
    if dim == VECTOR_DIMENSION:
        dim = body.dimension
    if dim != VECTOR_DIMENSION:
        logger.warning("임베딩 설정 거부(차원 불일치): provider=%s model=%s dim=%s",
                       body.provider, body.model, dim)
        raise HTTPException(
            status_code=422,
            detail=(f"임베딩 차원 {dim} 은(는) 지원하지 않습니다 — "
                    f"현재 벡터 컬럼은 {VECTOR_DIMENSION}차원 고정입니다. {VECTOR_DIMENSION}차원 모델을 선택하세요."),
        )
    if await _masked_key_retargeted(body.api_key, "embedding", "embedding", body.provider, body.api_url):
        logger.warning("임베딩 설정 거부(저장된 키를 다른 대상으로 사용): provider=%s", body.provider)
        raise HTTPException(status_code=400, detail=_KEY_REENTRY_MSG)
    items = {
        "embedding_enabled": str(body.enabled).lower(),
        "embedding_auto_on_write": str(body.auto_on_write).lower(),
        "embedding_provider": body.provider,
        "embedding_model": body.model,
        "embedding_api_url": body.api_url,
        "embedding_dimension": str(body.dimension),
    }
    if not _is_masked(body.api_key):
        items["embedding_api_key"] = body.api_key
    await update_config(session, "embedding", items, actor=_guard)

    # 임베딩 provider 재초기화
    await load_embedding_settings(session)

    logger.info("임베딩 설정 저장됨: provider=%s, model=%s, enabled=%s",
                body.provider, body.model, body.enabled)
    return {"status": "ok", "message": "Embedding configuration saved"}


@router.post("/embedding/test", response_model=TestResponse)
async def test_embedding(_guard: AdminUser, body: EmbeddingConfig):
    """샘플 문장 임베딩을 시도해 provider 동작을 검증한다."""
    logger.info("임베딩 테스트: provider=%s, model=%s", body.provider, body.model)
    if await _masked_key_retargeted(body.api_key, "embedding", "embedding", body.provider, body.api_url):
        return TestResponse(success=False, message=f"실패: {_KEY_REENTRY_MSG}")
    try:
        config = {
            "embedding_provider": body.provider,
            "embedding_model": body.model,
            "embedding_api_key": await _unmask(body.api_key, "embedding", "embedding_api_key"),
            "embedding_api_url": body.api_url,
        }
        # 임시 인스턴스로만 테스트 — 전역 provider(저장·enabled 상태 기준)는 건드리지 않는다.
        from app.embedding.models import VECTOR_DIMENSION
        from app.embedding.registry import create_provider
        provider = create_provider(config)
        try:
            result = await provider.embed(["test embedding connection"])
        finally:
            await provider.close()
        dim = len(result[0])
        if dim != VECTOR_DIMENSION:
            msg = (f"실패: {body.provider}/{body.model} 가 {dim}차원 벡터를 반환했습니다 — "
                   f"벡터 컬럼은 {VECTOR_DIMENSION}차원 고정이라 저장할 수 없습니다")
            logger.warning("임베딩 테스트 차원 불일치: %s", msg)
            return TestResponse(success=False, message=msg)
        msg = f"성공: {body.provider}/{body.model} 가 {dim}차원 벡터를 반환했습니다"
        logger.info("임베딩 테스트 성공: %s", msg)
        return TestResponse(success=True, message=msg)
    except Exception as e:
        msg = f"실패: {e}"
        logger.warning("임베딩 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)


# ---------------------------------------------------------------------------
# Change management configuration (참조자 통지 전역 설정)
# ---------------------------------------------------------------------------

class ChangeMgmtConfig(BaseModel):
    notify_enabled: bool = True
    notify_channel: str = "email"  # email | slack | mattermost


@router.get("/change", response_model=ChangeMgmtConfig)
async def get_change_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 변경관리 통지 설정 반환."""
    logger.debug("변경관리 통지 설정 조회")
    cfg = await get_config_by_category(session, "change")
    return ChangeMgmtConfig(
        notify_enabled=cfg.get("change_notify_enabled", "true").lower() in ("true", "1", "yes"),
        notify_channel=cfg.get("change_notify_channel", "email"),
    )


@router.put("/change")
async def update_change_config(_guard: AdminUser,
    body: ChangeMgmtConfig,
    session: AsyncSession = Depends(get_session),
):
    """변경관리 통지 설정을 저장한다."""
    items = {
        "change_notify_enabled": str(body.notify_enabled).lower(),
        "change_notify_channel": body.notify_channel,
    }
    await update_config(session, "change", items, actor=_guard)
    logger.info("변경관리 설정 저장됨: enabled=%s, channel=%s",
                body.notify_enabled, body.notify_channel)
    return {"status": "ok", "message": "Change management configuration saved"}


# ---------------------------------------------------------------------------
# 카탈로그 설정 — 일반 사용자의 데이터셋 직접 등록 허용
# ---------------------------------------------------------------------------

class CatalogPolicyConfig(BaseModel):
    # 꺼짐(기본): 관리자와 동기화 계정만 데이터셋을 직접 등록. 켜면 일반 사용자도(기능 권한 datasets.create 적용)
    dataset_create_by_users: bool = False


@router.get("/catalog", response_model=CatalogPolicyConfig)
async def get_catalog_policy_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """카탈로그 정책 설정 반환."""
    return CatalogPolicyConfig(dataset_create_by_users=await dataset_create_by_users_enabled(session))


@router.put("/catalog")
async def update_catalog_policy_config(_guard: AdminUser,
    body: CatalogPolicyConfig,
    session: AsyncSession = Depends(get_session),
):
    """카탈로그 정책 설정을 저장한다."""
    await update_config(session, "catalog", {
        DATASET_CREATE_BY_USERS_KEY: str(body.dataset_create_by_users).lower(),
    }, actor=_guard)
    logger.info("카탈로그 설정 저장됨: dataset_create_by_users=%s", body.dataset_create_by_users)
    return {"status": "ok", "message": "Catalog configuration saved"}


# ---------------------------------------------------------------------------
# LLM (AI 메타데이터 생성) 설정
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    auto_generate_on_sync: bool = False
    language: str = "ko"


@router.get("/llm", response_model=LLMConfig)
async def get_llm_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """AI 메타데이터 생성에 사용할 LLM 설정 반환(api_key 는 마스킹)."""
    logger.debug("LLM 설정 조회")
    cfg = await get_config_by_category(session, "llm")
    return LLMConfig(
        enabled=cfg.get("llm_enabled", "false").lower() in ("true", "1", "yes"),
        provider=cfg.get("llm_provider", "openai"),
        model=cfg.get("llm_model", "gpt-4o-mini"),
        api_key=_mask(cfg.get("llm_api_key", "")),
        api_url=cfg.get("llm_api_url", ""),
        temperature=float(cfg.get("llm_temperature", "0.3")),
        max_tokens=int(cfg.get("llm_max_tokens", "1024")),
        auto_generate_on_sync=cfg.get("llm_auto_generate_on_sync", "false").lower()
        in ("true", "1", "yes"),
        language=cfg.get("llm_language", "ko"),
    )


@router.put("/llm")
async def update_llm_config(_guard: AdminUser,
    body: LLMConfig,
    session: AsyncSession = Depends(get_session),
):
    """LLM 설정을 저장하고 provider 를 재초기화한다."""
    if await _masked_key_retargeted(body.api_key, "llm", "llm", body.provider, body.api_url):
        logger.warning("LLM 설정 거부(저장된 키를 다른 대상으로 사용): provider=%s", body.provider)
        raise HTTPException(status_code=400, detail=_KEY_REENTRY_MSG)
    items = {
        "llm_enabled": str(body.enabled).lower(),
        "llm_provider": body.provider,
        "llm_model": body.model,
        "llm_api_url": body.api_url,
        "llm_temperature": str(body.temperature),
        "llm_max_tokens": str(body.max_tokens),
        "llm_auto_generate_on_sync": str(body.auto_generate_on_sync).lower(),
        "llm_language": body.language,
    }
    if not _is_masked(body.api_key):
        items["llm_api_key"] = body.api_key
    await update_config(session, "llm", items, actor=_guard)
    await load_llm_settings(session)

    logger.info("LLM 설정 저장됨: provider=%s, model=%s, enabled=%s",
                body.provider, body.model, body.enabled)
    return {"status": "ok", "message": "LLM configuration saved"}


@router.post("/llm/test", response_model=TestResponse)
async def test_llm(_guard: AdminUser, body: LLMConfig):
    """샘플 프롬프트로 LLM provider 의 응답을 확인한다.

    전역 LLM 제공자는 건드리지 않고 ``create_provider`` 로 만든 임시 인스턴스로 시험한 뒤 닫는다
    (저장하지 않은 설정이 전역 제공자로 남거나 사용 중인 제공자가 닫히지 않도록).
    """
    logger.info("LLM 테스트: provider=%s, model=%s", body.provider, body.model)
    if await _masked_key_retargeted(body.api_key, "llm", "llm", body.provider, body.api_url):
        return TestResponse(success=False, message=f"실패: {_KEY_REENTRY_MSG}")
    provider = None
    try:
        config = {
            "llm_provider": body.provider,
            "llm_model": body.model,
            "llm_api_key": await _unmask(body.api_key, "llm", "llm_api_key"),
            "llm_api_url": body.api_url,
        }
        from app.ai.registry import create_provider
        provider = create_provider(config)
        result = await provider.generate(
            "Respond with exactly: OK",
            temperature=0.0,
            max_tokens=10,
        )
        text = result["text"].strip()
        msg = f"성공: {body.provider}/{body.model} 응답: {text[:50]}"
        logger.info("LLM 테스트 성공: %s", msg)
        return TestResponse(success=True, message=msg)
    except Exception as e:
        msg = f"실패: {e}"
        logger.warning("LLM 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)
    finally:
        if provider is not None:
            try:
                await provider.close()
            except Exception as e:  # 닫기 실패는 테스트 결과에 영향 없음
                logger.debug("LLM 테스트용 임시 제공자 닫기 실패: %s", e)


# ---------------------------------------------------------------------------
# AI 어시스턴트 (agent/ serve 프록시) 설정
# ---------------------------------------------------------------------------

class AssistantConfig(BaseModel):
    enabled: bool = False
    agent_url: str = ""


@router.get("/assistant", response_model=AssistantConfig)
async def get_assistant_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """AI 어시스턴트(에이전트 프록시) 설정 반환."""
    logger.debug("AI 어시스턴트 설정 조회")
    cfg = await get_config_by_category(session, "assistant")
    return AssistantConfig(
        enabled=cfg.get("assistant_enabled", "false").lower() in ("true", "1", "yes"),
        agent_url=cfg.get("assistant_agent_url", ""),
    )


@router.put("/assistant")
async def update_assistant_config(_guard: AdminUser,
    body: AssistantConfig,
    session: AsyncSession = Depends(get_session),
):
    """AI 어시스턴트 설정을 저장한다 (다음 채팅 요청부터 즉시 반영)."""
    items = {
        "assistant_enabled": str(body.enabled).lower(),
        "assistant_agent_url": body.agent_url.strip(),
    }
    await update_config(session, "assistant", items, actor=_guard)
    logger.info("AI 어시스턴트 설정 저장됨: enabled=%s, url=%s",
                body.enabled, body.agent_url.strip() or "(없음)")
    return {"status": "ok", "message": "Assistant configuration saved"}


@router.post("/assistant/test", response_model=TestResponse)
async def test_assistant(_guard: AdminUser, body: AssistantConfig):
    """에이전트 서버의 ``/health`` 를 호출해 연결을 확인한다."""
    url = body.agent_url.strip()
    if not url:
        logger.warning("AI 어시스턴트 테스트: URL 비어 있음")
        return TestResponse(success=False, message="에이전트 URL 이 비어 있습니다.")
    logger.info("AI 어시스턴트 연결 테스트: url=%s", url)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            resp = await client.get(url.rstrip("/") + "/health")
        if resp.status_code == 200:
            msg = f"성공: 에이전트 연결됨 ({url})"
            logger.info("AI 어시스턴트 테스트 성공: %s", msg)
            return TestResponse(success=True, message=msg)
        msg = f"실패: HTTP {resp.status_code} ({url})"
        logger.warning("AI 어시스턴트 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)
    except Exception as e:
        msg = f"실패: 연결할 수 없습니다 — {e}"
        logger.warning("AI 어시스턴트 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)


# ---------------------------------------------------------------------------
# 인증(Keycloak) 설정
# ---------------------------------------------------------------------------



class AuthConfig(BaseModel):
    type: str = "keycloak"
    server_url: str = ""
    realm: str = ""
    client_id: str = ""
    client_secret: str = ""
    admin_role: str = ""
    superuser_role: str = ""
    user_role: str = ""
    # 저장하면 현재 로그인이 끊기는 변경(인증 방식 전환·Keycloak 서버/realm/client 변경)을
    # 알고 진행한다는 확인. 없으면 409 {code: "confirm_required"} 로 무엇이 바뀌는지 알려 준다.
    confirm: bool = False


AUTH_TYPES = ("local", "keycloak")
# 로컬 모드의 역할 식별자(argus_roles.role_id) — 로컬 토큰의 roles claim 이 이 값이라 바꿀 수 없다
_LOCAL_ROLE_IDS = ("argus-admin", "argus-superuser", "argus-user")


async def _discovery_ok(server_url: str, realm: str) -> str | None:
    """Keycloak OpenID discovery 확인 — 실패 사유(성공이면 None)."""
    import httpx
    url = f"{server_url.rstrip('/')}/realms/{realm}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            resp.json()
        return None
    except Exception as e:  # noqa: BLE001 — 사유를 그대로 안내
        return f"{url} 에 연결할 수 없습니다: {e}"


async def _active_local_admins(session: AsyncSession) -> int:
    """로컬 비밀번호로 로그인할 수 있는 활성 관리자 수(Keycloak 연동으로 만들어진 계정 제외)."""
    from sqlalchemy import func, select

    from app.usermgr.models import ArgusRole, ArgusUser
    return (await session.execute(
        select(func.count()).select_from(ArgusUser)
        .join(ArgusRole, ArgusUser.role_id == ArgusRole.id)
        .where(ArgusRole.role_id == "argus-admin", ArgusUser.status == "active",
               ArgusUser.password_hash != "(external/keycloak)")
    )).scalar() or 0


async def _check_auth_change(body: AuthConfig, current, session: AsyncSession) -> None:
    """관리자가 스스로를 잠그는 인증 설정 저장을 막는다.

    - 형식 오류(알 수 없는 방식·빈 역할) → 400
    - 저장 즉시 현재 계정이 관리자가 아니게 되는 변경(역할 이름) → 400 (확인으로도 진행 불가)
    - 현재 로그인이 끊기는 변경(방식 전환·Keycloak 연결 정보) → 새 설정으로 로그인할 수 있는지
      확인(Keycloak 도달·로컬 관리자 존재)한 뒤 ``confirm`` 이 없으면 409
    """
    from app.core.config import settings

    if body.type not in AUTH_TYPES:
        logger.warning("인증 설정 거부(알 수 없는 방식): type=%s", body.type)
        raise HTTPException(status_code=400, detail=f"인증 방식은 {', '.join(AUTH_TYPES)} 중 하나여야 합니다.")
    roles = (body.admin_role.strip(), body.superuser_role.strip(), body.user_role.strip())
    if not all(roles):
        logger.warning("인증 설정 거부(빈 역할 이름)")
        raise HTTPException(status_code=400, detail="Admin·Superuser·User 역할 이름을 모두 입력하세요.")
    if body.type == "local" and roles != _LOCAL_ROLE_IDS:
        logger.warning("인증 설정 거부(로컬 모드 역할 이름 변경): roles=%s", roles)
        raise HTTPException(
            status_code=400,
            detail="로컬 인증 모드에서는 역할 이름을 바꿀 수 없습니다(argus-admin / argus-superuser / argus-user).",
        )

    type_changed = body.type != settings.auth_type
    if not type_changed and roles[0] != settings.auth_keycloak_admin_role \
            and roles[0] not in (current.roles + current.realm_roles):
        logger.warning("인증 설정 거부(저장 시 관리자 권한 상실): user=%s admin_role=%s",
                       current.username, roles[0])
        raise HTTPException(
            status_code=400,
            detail=f"현재 계정에 '{roles[0]}' 역할이 없어 저장하면 관리자 권한을 잃습니다. "
                   "먼저 Keycloak 에서 이 역할을 부여한 뒤 저장하세요.",
        )

    conn_changed = body.type == "keycloak" and (
        _norm_url(body.server_url) != _norm_url(settings.auth_keycloak_server_url)
        or body.realm != settings.auth_keycloak_realm
        or body.client_id != settings.auth_keycloak_client_id
    )
    logger.debug("인증 설정 변경 검사: type_changed=%s conn_changed=%s confirm=%s",
                 type_changed, conn_changed, body.confirm)
    if not (type_changed or conn_changed):
        return

    if body.type == "keycloak":
        if not body.server_url.strip() or not body.realm.strip() or not body.client_id.strip():
            raise HTTPException(status_code=400, detail="Keycloak 서버 URL·Realm·Client ID 를 입력하세요.")
        if problem := await _discovery_ok(body.server_url, body.realm):
            logger.warning("인증 설정 거부(새 Keycloak 도달 불가): %s", problem)
            raise HTTPException(status_code=400, detail=f"새 Keycloak 설정으로 로그인할 수 없습니다 — {problem}")
        consequence = ("저장하면 지금 로그인한 세션이 끊기고, 새 Keycloak 계정으로 다시 로그인해야 합니다. "
                       f"Keycloak 에서 '{roles[0]}' 역할을 가진 계정이 있어야 관리 화면을 쓸 수 있습니다.")
    else:
        if await _active_local_admins(session) == 0:
            logger.warning("인증 설정 거부(로컬 활성 관리자 없음)")
            raise HTTPException(
                status_code=400,
                detail="로컬 비밀번호로 로그인할 수 있는 활성 관리자가 없어 로컬 모드로 바꿀 수 없습니다.",
            )
        consequence = "저장하면 지금 로그인한 세션이 끊기고, 로컬 관리자 계정으로 다시 로그인해야 합니다."

    if not body.confirm:
        logger.info("인증 설정 저장 보류 — 사용자 확인 필요(409): type=%s", body.type)
        raise HTTPException(status_code=409, detail={"code": "confirm_required", "message": consequence})


@router.get("/sources")
async def get_config_sources(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """설정 값 출처 — 카테고리 → 키 → {source: default|config|db, differs_from_file?, file_value?, override?}.

    설정 파일은 최초 기동 때 비어 있는 키만 DB 에 채우고 이후에는 DB(이 화면) 값이 우선이라, 설정 파일을
    고쳐도 반영되지 않는 키를 화면에서 알려 주기 위한 것. 비밀값은 값을 내보내지 않는다.
    """
    logger.debug("설정 값 출처 조회")
    return await config_sources(session)


@router.get("/auth", response_model=AuthConfig)
async def get_auth_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 인증 설정 반환. ``client_secret`` 은 마스킹된 문자열로 응답."""
    logger.debug("인증 설정 조회")
    cfg = await get_config_by_category(session, "auth")
    return AuthConfig(
        type=cfg.get("auth_type", "keycloak"),
        server_url=cfg.get("auth_keycloak_server_url", ""),
        realm=cfg.get("auth_keycloak_realm", ""),
        client_id=cfg.get("auth_keycloak_client_id", ""),
        client_secret=_SECRET_MASK if cfg.get("auth_keycloak_client_secret") else "",
        admin_role=cfg.get("auth_keycloak_admin_role", ""),
        superuser_role=cfg.get("auth_keycloak_superuser_role", ""),
        user_role=cfg.get("auth_keycloak_user_role", ""),
    )


@router.get("/auth/secret")
async def get_auth_secret(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """원본 ``client_secret`` 반환 (관리자 전용 — 설정 화면의 '보기' 토글)."""
    logger.info("인증 client_secret 조회 요청됨")
    cfg = await get_config_by_category(session, "auth")
    return {"client_secret": cfg.get("auth_keycloak_client_secret", "")}


@router.put("/auth")
async def update_auth_config(admin: AdminUser,
    body: AuthConfig,
    session: AsyncSession = Depends(get_session),
):
    """인증 설정을 저장하고 in-memory 설정에 반영한다.

    관리자가 스스로를 잠그는 변경은 ``_check_auth_change`` 가 막거나 확인(``confirm``)을 요구한다.
    그래도 잠겼다면 환경변수 ``ARGUS_AUTH_FORCE_LOCAL=true`` 로 재기동해 로컬 모드로 복구한다.

    ``client_secret`` 은 클라이언트에서 마스킹 값(``••••••••``)을 그대로 돌려보낸
    경우 변경하지 않는다(실수로 비밀번호를 덮어쓰는 사고 방지).
    """
    await _check_auth_change(body, admin, session)
    items = {
        "auth_type": body.type,
        "auth_keycloak_server_url": body.server_url,
        "auth_keycloak_realm": body.realm,
        "auth_keycloak_client_id": body.client_id,
        "auth_keycloak_admin_role": body.admin_role.strip(),
        "auth_keycloak_superuser_role": body.superuser_role.strip(),
        "auth_keycloak_user_role": body.user_role.strip(),
    }
    # 사용자가 마스킹 값이 아닌 실제 값을 보낸 경우에만 secret 을 갱신
    if body.client_secret and body.client_secret != _SECRET_MASK:
        items["auth_keycloak_client_secret"] = body.client_secret

    await update_config(session, "auth", items, actor=admin)
    await load_auth_settings(session)

    logger.info(
        "인증 설정 저장됨: type=%s server_url=%s realm=%s",
        body.type, body.server_url, body.realm,
    )
    return {"status": "ok", "message": "Authentication configuration saved"}


def _client_check_result(status_code: int, err: dict, client_id: str) -> tuple[bool, str]:
    """client_credentials 토큰 요청 결과 → (client 인증 통과 여부, 한국어 안내).

    Keycloak 은 client 인증(secret 확인)을 grant 처리보다 먼저 한다.
    - 200 → secret 확인(서비스 계정이 켜진 client)
    - 401 또는 ``invalid_client`` → client 인증 실패. 최근 Keycloak 은 secret 이 틀리면
      ``unauthorized_client``·401, client 가 없으면 ``invalid_client``·401 을 돌려준다.
    - 그 밖의 400(``unauthorized_client`` "Client not enabled to retrieve service account" 등)
      → secret 은 통과했고 client_credentials grant 만 허용되지 않은 것(로그인은 password grant 라 무관)
    """
    if status_code == 200:
        return True, "client secret 확인됨"
    code = (err.get("error") or "").lower()
    if status_code == 401 or code == "invalid_client":
        return False, (f"client secret 이 올바르지 않습니다(또는 Client ID '{client_id}' 가 realm 에 없습니다). "
                       "이 값으로 저장하면 로그인할 수 없습니다.")
    if code in ("unauthorized_client", "unsupported_grant_type", "invalid_grant", "invalid_scope"):
        return True, "client secret 확인됨"
    return False, f"client 인증을 확인하지 못했습니다(HTTP {status_code}, {code or '응답 없음'})."


@router.post("/auth/test", response_model=TestResponse)
async def test_auth_connection(_guard: AdminUser, body: AuthConfig):
    """Keycloak 연결 테스트 — realm discovery 로 도달성을 확인한 뒤, 입력한 Client ID·secret 으로
    토큰 엔드포인트에 client_credentials 요청을 보내 client 인증(secret)이 맞는지 확인한다.

    secret 이 마스킹 값(``••••••••``)이면 저장된 secret 으로 확인한다. 설정은 저장하지 않는다.
    """
    import httpx
    base = f"{body.server_url.rstrip('/')}/realms/{body.realm}"
    url = f"{base}/.well-known/openid-configuration"
    logger.info("인증 테스트: %s client=%s", url, body.client_id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            issuer = resp.json().get("issuer", "")
    except httpx.HTTPStatusError as e:
        msg = (f"실패: {url} 이(가) HTTP {e.response.status_code} 를 돌려주었습니다 — "
               f"서버 URL·Realm('{body.realm}')을 확인하세요.")
        logger.warning("인증 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)
    except Exception as e:  # noqa: BLE001 — 사유를 그대로 안내
        msg = f"실패: {url} 에 연결할 수 없습니다 — {e}"
        logger.warning("인증 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)

    if not body.client_id.strip():
        logger.warning("인증 테스트: Client ID 비어 있음")
        return TestResponse(success=False, message="실패: Client ID 를 입력하세요.")
    secret = await _unmask(body.client_secret, "auth", "auth_keycloak_client_secret")
    # 토큰 엔드포인트는 discovery 의 token_endpoint(발급자 호스트 기준이라 이 서버에서 안 풀릴 수 있음)
    # 대신 로그인과 같은 방식으로 입력한 서버 URL 기준으로 만든다.
    token_url = f"{base}/protocol/openid-connect/token"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": body.client_id,
                "client_secret": secret,
            })
        try:
            err = {} if resp.status_code == 200 else resp.json()
        except ValueError:
            err = {}
    except Exception as e:  # noqa: BLE001
        msg = f"실패: 토큰 엔드포인트 {token_url} 에 연결할 수 없습니다 — {e}"
        logger.warning("인증 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)

    ok, detail = _client_check_result(resp.status_code, err if isinstance(err, dict) else {}, body.client_id)
    if not ok:
        logger.warning("인증 테스트 실패: client=%s status=%d error=%s", body.client_id, resp.status_code,
                       err.get("error") if isinstance(err, dict) else None)
        return TestResponse(success=False, message=f"실패: {detail}")
    msg = f"성공: {issuer} 에 연결됨, {detail}"
    logger.info("인증 테스트 성공: %s", msg)
    return TestResponse(success=True, message=msg)


# ---------------------------------------------------------------------------
# Keycloak 초기화 (realm·client·role 자동 설정)
# ---------------------------------------------------------------------------

class KeycloakInitRequest(BaseModel):
    server_url: str = Field(min_length=1)
    # Keycloak master 관리자 계정 — 기본값을 두지 않는다(admin/admin 으로 조용히 시도하지 않도록)
    admin_username: str = Field(min_length=1)
    admin_password: str = Field(min_length=1)
    realm: str = Field("argus", min_length=1)
    client_id: str = Field("argus-client", min_length=1)
    # 비우거나 마스킹 값이면 저장된 secret, 저장된 것도 없으면 새로 생성(고정 기본값을 쓰지 않는다)
    client_secret: str = ""
    roles: list[str] = ["argus-admin", "argus-superuser", "argus-user"]


class InitStep(BaseModel):
    step: str
    status: str  # "ok", "skip", "created", "error"
    message: str


# 초기화가 만드는 client 설정 — 카탈로그는 백엔드가 password/refresh grant 로만 Keycloak 을 부른다.
# 브라우저 리다이렉트 로그인(standard flow)·서비스 계정을 쓰지 않으므로 끄고, redirect URI·web origin 도
# 두지 않는다(예전의 "*" 는 임의 사이트로 토큰을 돌려보낼 수 있는 설정).
KEYCLOAK_CLIENT_SETTINGS = {
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": False,
    "directAccessGrantsEnabled": True,
    "serviceAccountsEnabled": False,
    "authorizationServicesEnabled": False,
    "standardFlowEnabled": False,
    "implicitFlowEnabled": False,
    "redirectUris": [],
    "webOrigins": [],
}


async def _init_secret(body: KeycloakInitRequest) -> tuple[str, dict[str, str], bool]:
    """초기화에 쓸 client secret, 저장된 인증 설정, 새로 생성했는지."""
    import secrets as _secrets
    async with async_session() as session:
        stored = await get_config_by_category(session, "auth")
    given = (body.client_secret or "").strip()
    if given and given != _SECRET_MASK:
        return given, stored, False
    if stored.get("auth_keycloak_client_secret"):
        return stored["auth_keycloak_client_secret"], stored, False
    return _secrets.token_urlsafe(32), stored, True


def _same_target(body: KeycloakInitRequest, stored: dict[str, str]) -> bool:
    """초기화 대상(서버·realm·client)이 저장된 인증 설정과 같은지."""
    return (
        _norm_url(stored.get("auth_keycloak_server_url", "")) == _norm_url(body.server_url)
        and stored.get("auth_keycloak_realm", "") == body.realm
        and stored.get("auth_keycloak_client_id", "") == body.client_id
    )


@router.post("/auth/initialize")
async def initialize_keycloak(_guard: AdminUser, body: KeycloakInitRequest):
    """Keycloak 부트스트랩: realm·client·client_secret·realm role 을 자동 생성.

    각 단계가 끝나면 ``steps`` 배열에 결과를 누적해 클라이언트가 진행 상황을 표시할
    수 있게 한다. 단계는 모두 GET-then-POST 패턴이라 멱등하다 — 이미 존재하는
    리소스는 ``skip`` 으로 건너뛴다.

    client secret 은 입력값 → 저장된 값 → 새로 생성 순으로 정한다. Keycloak 의 secret 을 바꾼 뒤
    저장된 설정과 어긋나지 않도록, 저장된 대상(서버·realm·client)과 같거나 저장된 secret 이 없으면
    그 secret 을 설정에도 저장한다(그 밖에는 화면에서 저장하라고 안내).
    """
    logger.info("Keycloak 초기화: server=%s realm=%s client=%s", body.server_url, body.realm, body.client_id)
    import httpx
    steps: list[dict] = []
    client_secret, stored, generated = await _init_secret(body)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: master realm 의 admin-cli 클라이언트로 관리자 토큰 발급
        try:
            token_resp = await client.post(
                f"{body.server_url}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": body.admin_username,
                    "password": body.admin_password,
                },
            )
            token_resp.raise_for_status()
            admin_token = token_resp.json()["access_token"]
            steps.append({"step": "Admin Login", "status": "ok", "message": "관리자로 인증됨"})
        except Exception as e:
            logger.warning("Keycloak 초기화: 관리자 로그인 실패: %s", e)
            steps.append({"step": "Admin Login", "status": "error", "message": f"실패: {e}"})
            await _audit_initialize(_guard, "settings.keycloak_initialize", f"{body.realm}/{body.client_id}", steps)
            return {"steps": steps}

        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        # Step 2: realm 존재 여부 확인 후 없으면 생성
        try:
            realm_resp = await client.get(f"{body.server_url}/admin/realms/{body.realm}", headers=headers)
            if realm_resp.status_code == 200:
                steps.append({"step": f"Realm '{body.realm}'", "status": "skip", "message": "이미 존재함"})
            else:
                create_resp = await client.post(
                    f"{body.server_url}/admin/realms",
                    headers=headers,
                    json={"realm": body.realm, "enabled": True},
                )
                create_resp.raise_for_status()
                steps.append({"step": f"Realm '{body.realm}'", "status": "created", "message": "생성 완료"})
        except Exception as e:
            logger.warning("Keycloak 초기화: realm 단계 실패: %s", e)
            steps.append({"step": f"Realm '{body.realm}'", "status": "error", "message": str(e)})
            await _audit_initialize(_guard, "settings.keycloak_initialize", f"{body.realm}/{body.client_id}", steps)
            return {"steps": steps}

        # Step 3: client 존재 여부 확인 후 없으면 생성
        client_uuid = None
        try:
            clients_resp = await client.get(
                f"{body.server_url}/admin/realms/{body.realm}/clients",
                headers=headers,
                params={"clientId": body.client_id},
            )
            clients_resp.raise_for_status()
            existing_clients = clients_resp.json()

            if existing_clients:
                client_uuid = existing_clients[0]["id"]
                loose = [k for k, v in KEYCLOAK_CLIENT_SETTINGS.items() if existing_clients[0].get(k, v) != v]
                if loose:
                    # 예전 초기화(redirect "*", standard flow·서비스 계정 켜짐)로 만든 client 를 좁힌다
                    upd = await client.put(
                        f"{body.server_url}/admin/realms/{body.realm}/clients/{client_uuid}",
                        headers=headers,
                        json={**existing_clients[0], **KEYCLOAK_CLIENT_SETTINGS},
                    )
                    upd.raise_for_status()
                    steps.append({"step": f"Client '{body.client_id}'", "status": "created",
                                  "message": f"이미 존재함 — 설정 강화({', '.join(loose)})"})
                else:
                    steps.append({"step": f"Client '{body.client_id}'", "status": "skip", "message": "이미 존재함"})
            else:
                create_client_resp = await client.post(
                    f"{body.server_url}/admin/realms/{body.realm}/clients",
                    headers=headers,
                    json={"clientId": body.client_id, "secret": client_secret, **KEYCLOAK_CLIENT_SETTINGS},
                )
                create_client_resp.raise_for_status()
                # 생성된 client 의 UUID 는 Location 헤더 마지막 세그먼트에 들어 있음
                loc = create_client_resp.headers.get("Location", "")
                client_uuid = loc.rsplit("/", 1)[-1] if loc else None
                steps.append({"step": f"Client '{body.client_id}'", "status": "created", "message": "생성 완료"})
        except Exception as e:
            logger.warning("Keycloak 초기화: client 단계 실패: %s", e)
            steps.append({"step": f"Client '{body.client_id}'", "status": "error", "message": str(e)})
            await _audit_initialize(_guard, "settings.keycloak_initialize", f"{body.realm}/{body.client_id}", steps)
            return {"steps": steps}

        # Step 4: Keycloak 의 client secret 이 정한 값(입력값 → 저장값 → 새로 생성)과 같으면 skip, 다르면 갱신
        secret_synced = False
        if client_uuid:
            try:
                secret_resp = await client.get(
                    f"{body.server_url}/admin/realms/{body.realm}/clients/{client_uuid}/client-secret",
                    headers=headers,
                )
                if secret_resp.status_code == 200:
                    current_secret = secret_resp.json().get("value", "")
                    if current_secret == client_secret:
                        secret_synced = True
                        steps.append({"step": "Client Secret", "status": "skip", "message": "이미 일치함"})
                    else:
                        # 원하는 secret 값으로 client 자체를 업데이트
                        update_resp = await client.put(
                            f"{body.server_url}/admin/realms/{body.realm}/clients/{client_uuid}",
                            headers=headers,
                            json={"secret": client_secret},
                        )
                        update_resp.raise_for_status()
                        secret_synced = True
                        steps.append({"step": "Client Secret", "status": "created",
                                      "message": "새로 생성함" if generated else "일치하도록 갱신됨"})
                else:
                    steps.append({"step": "Client Secret", "status": "skip", "message": "확인할 수 없음"})
            except Exception as e:
                logger.warning("Keycloak 초기화: secret 단계 실패: %s", e)
                steps.append({"step": "Client Secret", "status": "error", "message": str(e)})

        # Step 5: 요청된 realm role 들을 하나씩 확인/생성
        for role_name in body.roles:
            try:
                role_resp = await client.get(
                    f"{body.server_url}/admin/realms/{body.realm}/roles/{role_name}",
                    headers=headers,
                )
                if role_resp.status_code == 200:
                    steps.append({"step": f"Role '{role_name}'", "status": "skip", "message": "이미 존재함"})
                else:
                    create_role_resp = await client.post(
                        f"{body.server_url}/admin/realms/{body.realm}/roles",
                        headers=headers,
                        json={"name": role_name},
                    )
                    create_role_resp.raise_for_status()
                    steps.append({"step": f"Role '{role_name}'", "status": "created", "message": "생성 완료"})
            except Exception as e:
                logger.warning("Keycloak 초기화: role '%s' 단계 실패: %s", role_name, e)
                steps.append({"step": f"Role '{role_name}'", "status": "error", "message": str(e)})

    # Keycloak 에 넣은 secret 을 설정에도 — 저장된 값과 어긋나면 Keycloak 로그인이 401 이 된다
    if secret_synced and client_secret != stored.get("auth_keycloak_client_secret", ""):
        if generated or not stored.get("auth_keycloak_client_secret") or _same_target(body, stored):
            async with async_session() as session:
                await update_config(session, "auth", {"auth_keycloak_client_secret": client_secret}, actor=_guard)
                await load_auth_settings(session)
            steps.append({"step": "설정 저장", "status": "created", "message": "client secret 을 인증 설정에 저장함"})
            logger.info("Keycloak 초기화: client secret 을 인증 설정에 저장 (generated=%s)", generated)
        else:
            steps.append({"step": "설정 저장", "status": "skip",
                          "message": "저장된 Keycloak 연결과 대상이 달라 secret 을 저장하지 않음 — 이 대상으로 바꾸려면 화면에서 저장하세요"})

    error_count = sum(1 for s in steps if s.get("status") == "error")
    if error_count:
        logger.warning("Keycloak 초기화 %d개 오류로 종료 (총 %d개 단계)", error_count, len(steps))
    else:
        logger.info("Keycloak 초기화 정상 종료 (%d개 단계)", len(steps))
    await _audit_initialize(_guard, "settings.keycloak_initialize", f"{body.realm}/{body.client_id}", steps)
    return {"steps": steps}


# ---------------------------------------------------------------------------
# CORS 설정
# ---------------------------------------------------------------------------

class CorsConfig(BaseModel):
    origins: str = "*"


@router.get("/cors", response_model=CorsConfig)
async def get_cors_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 CORS 허용 출처 설정 반환."""
    logger.debug("CORS 설정 조회")
    cfg = await get_config_by_category(session, "cors")
    return CorsConfig(origins=cfg.get("cors_origins", "*"))


@router.put("/cors")
async def update_cors_config(_guard: AdminUser,
    body: CorsConfig,
    session: AsyncSession = Depends(get_session),
):
    """CORS 설정 저장. 다음 요청부터 적용된다(미들웨어가 in-memory 값을 참조)."""
    await update_config(session, "cors", {"cors_origins": body.origins}, actor=_guard)
    await load_cors_settings(session)

    logger.info("CORS 설정 저장됨: origins=%s", body.origins)
    return {"status": "ok", "message": "CORS configuration saved"}


# ---------------------------------------------------------------------------
# 이메일 (SMTP)
# ---------------------------------------------------------------------------

class MailTestRequest(BaseModel):
    to: str = ""              # 비우면 저장된 default_recipients / 발신자 본인
    subject: str = "Argus Catalog 메일 테스트"
    body: str = "Argus Catalog 에서 보낸 테스트 메일입니다."


@router.get("/mail", response_model=EmailConfig)
async def get_mail_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 메일(SMTP) 설정 반환. ``smtp_password`` 는 마스킹된 문자열."""
    logger.debug("메일 설정 조회")
    cfg = await load_mail_config(session)
    return cfg.model_copy(update={"smtp_password": _SECRET_MASK if cfg.smtp_password else ""})


@router.put("/mail")
async def update_mail_config(_guard: AdminUser,
    body: EmailConfig,
    session: AsyncSession = Depends(get_session),
):
    """메일 설정 저장. ``smtp_password`` 가 마스킹 값이면 기존 값을 유지, 빈 값이면 지운다(무인증 SMTP)."""
    items = {
        "mail_enabled": str(body.enabled).lower(),
        "mail_smtp_host": body.smtp_host,
        "mail_smtp_port": str(body.smtp_port),
        "mail_use_tls": str(body.use_tls).lower(),
        "mail_use_ssl": str(body.use_ssl).lower(),
        "mail_timeout_seconds": str(body.timeout_seconds),
        "mail_from_email": body.from_email,
        "mail_from_name": body.from_name,
        "mail_smtp_user": body.smtp_user,
        "mail_subject_prefix": body.subject_prefix,
        "mail_default_recipients": body.default_recipients,
    }
    # 마스킹 값(화면에서 건드리지 않음)이면 유지. 빈 값은 비밀번호 삭제 — 인증 없이 발송하는 사내 릴레이용
    if body.smtp_password != _SECRET_MASK:
        items["mail_smtp_password"] = body.smtp_password or ""

    await update_config(session, "mail", items, actor=_guard)
    logger.info(
        "메일 설정 저장됨: host=%s port=%s from=%s enabled=%s",
        body.smtp_host, body.smtp_port, body.from_email, body.enabled,
    )
    return {"status": "ok", "message": "Mail configuration saved"}


@router.post("/mail/test", response_model=TestResponse)
async def test_mail(_guard: AdminUser,
    body: MailTestRequest,
    session: AsyncSession = Depends(get_session),
):
    """저장된 메일 설정으로 테스트 메일 발송. (먼저 설정을 저장해야 함)"""
    cfg = await load_mail_config(session)
    to = [r.strip() for r in body.to.split(",") if r.strip()] or None
    if to is None and not cfg.default_recipients and cfg.from_email:
        to = [cfg.from_email]  # 수신자 미지정 시 발신자 본인에게
    logger.info("테스트 메일 발송 요청: by=%s recipients=%d", _guard.username, len(to) if to else 0)
    try:
        await send_mail(cfg, body.subject, body.body, to=to, raise_errors=True)
        return TestResponse(success=True, message="테스트 메일을 발송했습니다.")
    except Exception as exc:
        logger.warning("테스트 메일 발송 실패: %s", exc)
        return TestResponse(success=False, message=f"발송 실패: {exc}")


# ---------------------------------------------------------------------------
# 알림 (Slack / Mattermost)
# ---------------------------------------------------------------------------

class NotifyTestRequest(BaseModel):
    text: str = "Argus Catalog 알림 테스트입니다."


@router.get("/notify", response_model=NotifyConfig)
async def get_notify_config(_guard: AdminUser, session: AsyncSession = Depends(get_session)):
    """현재 Slack/Mattermost 알림 설정 반환. webhook URL 은 그 자체가 자격증명이므로 마스킹."""
    logger.debug("알림 설정 조회")
    cfg = await load_notify_config(session)
    return cfg.model_copy(update={
        "slack": cfg.slack.model_copy(update={"webhook_url": _mask(cfg.slack.webhook_url)}),
        "mattermost": cfg.mattermost.model_copy(
            update={"webhook_url": _mask(cfg.mattermost.webhook_url)}
        ),
    })


@router.put("/notify")
async def update_notify_config(_guard: AdminUser,
    body: NotifyConfig,
    session: AsyncSession = Depends(get_session),
):
    """알림 설정 저장. webhook URL 이 마스킹 값이면 기존 값을 유지한다."""
    items = {
        "notify_enabled": str(body.enabled).lower(),
        "notify_provider": body.provider,
        "notify_timeout_seconds": str(body.timeout_seconds),
        "notify_slack_channel": body.slack.channel,
        "notify_slack_username": body.slack.username,
        "notify_slack_icon_emoji": body.slack.icon_emoji,
        "notify_mattermost_channel": body.mattermost.channel,
        "notify_mattermost_username": body.mattermost.username,
        "notify_mattermost_icon_emoji": body.mattermost.icon_emoji,
    }
    # webhook URL 은 마스킹 값이 돌아오면 기존 값 유지
    if not _is_masked(body.slack.webhook_url):
        items["notify_slack_webhook_url"] = body.slack.webhook_url
    if not _is_masked(body.mattermost.webhook_url):
        items["notify_mattermost_webhook_url"] = body.mattermost.webhook_url
    await update_config(session, "notify", items, actor=_guard)
    logger.info("알림 설정 저장됨: provider=%s enabled=%s", body.provider, body.enabled)
    return {"status": "ok", "message": "Notify configuration saved"}


@router.post("/notify/test", response_model=TestResponse)
async def test_notify(_guard: AdminUser,
    body: NotifyTestRequest,
    session: AsyncSession = Depends(get_session),
):
    """저장된 알림 설정으로 활성 provider 에 테스트 메시지 발송. (먼저 저장 필요)"""
    cfg = await load_notify_config(session)
    logger.info("테스트 알림 발송 요청: by=%s provider=%s", _guard.username, cfg.provider)
    try:
        await send_notify(cfg, body.text, raise_errors=True)
        return TestResponse(success=True, message=f"{cfg.provider} 로 테스트 메시지를 발송했습니다.")
    except Exception as exc:
        logger.warning("테스트 알림 발송 실패: %s", exc)
        return TestResponse(success=False, message=f"발송 실패: {exc}")
