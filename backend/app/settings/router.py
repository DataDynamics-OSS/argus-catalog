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
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.database import get_session
from app.settings.mail import EmailConfig, load_mail_config, send_mail
from app.settings.notify import NotifyConfig, load_notify_config, send_notify
from app.settings.service import (
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


# ---------------------------------------------------------------------------
# 오브젝트 스토리지 설정 조회 / 갱신 (DB 기반)
# ---------------------------------------------------------------------------

@router.get("/object-storage", response_model=ObjectStorageConfig)
async def get_object_storage_config(session: AsyncSession = Depends(get_session)):
    """DB 에서 현재 오브젝트 스토리지 설정을 읽어 반환한다."""
    cfg = await get_config_by_category(session, "object_storage")
    return ObjectStorageConfig(
        endpoint=cfg.get("object_storage_endpoint", ""),
        access_key=cfg.get("object_storage_access_key", ""),
        secret_key=cfg.get("object_storage_secret_key", ""),
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
        "object_storage_access_key": body.access_key,
        "object_storage_secret_key": body.secret_key,
        "object_storage_region": body.region,
        "object_storage_use_ssl": str(body.use_ssl).lower(),
        "object_storage_bucket": body.bucket,
        "object_storage_presigned_url_expiry": str(body.presigned_url_expiry),
    }
    await update_config(session, "object_storage", items)

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
            aws_secret_access_key=body.secret_key,
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
            aws_secret_access_key=body.secret_key,
            region_name=body.region,
        ) as s3:
            # Step 1: 연결 확인
            steps.append({"step": "S3 Connection", "status": "ok", "message": f"{body.endpoint} 에 연결됨"})

            # Step 2: 버킷 존재 확인 및 없으면 생성
            try:
                await s3.head_bucket(Bucket=body.bucket)
                steps.append({"step": f"Bucket '{body.bucket}'", "status": "skip", "message": "이미 존재함"})
            except Exception:
                # 버킷이 없으면 새로 생성
                try:
                    await s3.create_bucket(Bucket=body.bucket)
                    steps.append({"step": f"Bucket '{body.bucket}'", "status": "created", "message": "생성 완료"})
                except Exception as create_err:
                    logger.warning("오브젝트 스토리지 버킷 '%s' 생성 실패: %s", body.bucket, create_err)
                    steps.append({"step": f"Bucket '{body.bucket}'", "status": "error", "message": str(create_err)})

    except Exception as e:
        logger.warning("오브젝트 스토리지 초기화 연결 단계 실패: %s", e)
        steps.append({"step": "S3 Connection", "status": "error", "message": str(e)})

    has_error = any(s.get("status") == "error" for s in steps)
    if has_error:
        logger.warning("오브젝트 스토리지 초기화 오류로 종료 (%d개 단계)", len(steps))
    else:
        logger.info("오브젝트 스토리지 초기화 정상 종료 (%d개 단계)", len(steps))
    return {"steps": steps}


# ---------------------------------------------------------------------------
# 임베딩 설정
# ---------------------------------------------------------------------------

class EmbeddingConfig(BaseModel):
    enabled: bool = False
    # 쓰기(생성/수정/sync)마다 자동 임베딩. off 면 백필로만 — bulk sync 부하 회피(기본 off).
    auto_on_write: bool = False
    provider: str = "local"
    model: str = "all-MiniLM-L6-v2"
    api_key: str = ""
    api_url: str = ""
    dimension: int = 384


@router.get("/embedding", response_model=EmbeddingConfig)
async def get_embedding_config(session: AsyncSession = Depends(get_session)):
    """현재 임베딩 설정 반환."""
    cfg = await get_config_by_category(session, "embedding")
    return EmbeddingConfig(
        enabled=cfg.get("embedding_enabled", "false").lower() in ("true", "1", "yes"),
        auto_on_write=cfg.get("embedding_auto_on_write", "false").lower() in ("true", "1", "yes"),
        provider=cfg.get("embedding_provider", "local"),
        model=cfg.get("embedding_model", "all-MiniLM-L6-v2"),
        api_key=cfg.get("embedding_api_key", ""),
        api_url=cfg.get("embedding_api_url", ""),
        dimension=int(cfg.get("embedding_dimension", "384")),
    )


@router.put("/embedding")
async def update_embedding_config(_guard: AdminUser,
    body: EmbeddingConfig,
    session: AsyncSession = Depends(get_session),
):
    """임베딩 설정을 저장하고 provider 를 재초기화한다."""
    items = {
        "embedding_enabled": str(body.enabled).lower(),
        "embedding_auto_on_write": str(body.auto_on_write).lower(),
        "embedding_provider": body.provider,
        "embedding_model": body.model,
        "embedding_api_key": body.api_key,
        "embedding_api_url": body.api_url,
        "embedding_dimension": str(body.dimension),
    }
    await update_config(session, "embedding", items)

    # 임베딩 provider 재초기화
    await load_embedding_settings(session)

    logger.info("임베딩 설정 저장됨: provider=%s, model=%s, enabled=%s",
                body.provider, body.model, body.enabled)
    return {"status": "ok", "message": "Embedding configuration saved"}


@router.post("/embedding/test", response_model=TestResponse)
async def test_embedding(_guard: AdminUser, body: EmbeddingConfig):
    """샘플 문장 임베딩을 시도해 provider 동작을 검증한다."""
    logger.info("임베딩 테스트: provider=%s, model=%s", body.provider, body.model)
    try:
        config = {
            "embedding_provider": body.provider,
            "embedding_model": body.model,
            "embedding_api_key": body.api_key,
            "embedding_api_url": body.api_url,
        }
        from app.embedding.registry import initialize_provider
        provider = await initialize_provider(config)
        result = await provider.embed(["test embedding connection"])
        dim = len(result[0])
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
async def get_change_config(session: AsyncSession = Depends(get_session)):
    """현재 변경관리 통지 설정 반환."""
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
    await update_config(session, "change", items)
    logger.info("변경관리 설정 저장됨: enabled=%s, channel=%s",
                body.notify_enabled, body.notify_channel)
    return {"status": "ok", "message": "Change management configuration saved"}


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
async def get_llm_config(session: AsyncSession = Depends(get_session)):
    """AI 메타데이터 생성에 사용할 LLM 설정 반환."""
    cfg = await get_config_by_category(session, "llm")
    return LLMConfig(
        enabled=cfg.get("llm_enabled", "false").lower() in ("true", "1", "yes"),
        provider=cfg.get("llm_provider", "openai"),
        model=cfg.get("llm_model", "gpt-4o-mini"),
        api_key=cfg.get("llm_api_key", ""),
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
    items = {
        "llm_enabled": str(body.enabled).lower(),
        "llm_provider": body.provider,
        "llm_model": body.model,
        "llm_api_key": body.api_key,
        "llm_api_url": body.api_url,
        "llm_temperature": str(body.temperature),
        "llm_max_tokens": str(body.max_tokens),
        "llm_auto_generate_on_sync": str(body.auto_generate_on_sync).lower(),
        "llm_language": body.language,
    }
    await update_config(session, "llm", items)
    await load_llm_settings(session)

    logger.info("LLM 설정 저장됨: provider=%s, model=%s, enabled=%s",
                body.provider, body.model, body.enabled)
    return {"status": "ok", "message": "LLM configuration saved"}


@router.post("/llm/test", response_model=TestResponse)
async def test_llm(_guard: AdminUser, body: LLMConfig):
    """샘플 프롬프트로 LLM provider 의 응답을 확인한다."""
    logger.info("LLM 테스트: provider=%s, model=%s", body.provider, body.model)
    try:
        config = {
            "llm_provider": body.provider,
            "llm_model": body.model,
            "llm_api_key": body.api_key,
            "llm_api_url": body.api_url,
        }
        from app.ai.registry import initialize_provider
        provider = await initialize_provider(config)
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


# ---------------------------------------------------------------------------
# AI 어시스턴트 (agent/ serve 프록시) 설정
# ---------------------------------------------------------------------------

class AssistantConfig(BaseModel):
    enabled: bool = False
    agent_url: str = ""


@router.get("/assistant", response_model=AssistantConfig)
async def get_assistant_config(session: AsyncSession = Depends(get_session)):
    """AI 어시스턴트(에이전트 프록시) 설정 반환."""
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
    await update_config(session, "assistant", items)
    logger.info("AI 어시스턴트 설정 저장됨: enabled=%s, url=%s",
                body.enabled, body.agent_url.strip() or "(없음)")
    return {"status": "ok", "message": "Assistant configuration saved"}


@router.post("/assistant/test", response_model=TestResponse)
async def test_assistant(_guard: AdminUser, body: AssistantConfig):
    """에이전트 서버의 ``/health`` 를 호출해 연결을 확인한다."""
    url = body.agent_url.strip()
    if not url:
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

_SECRET_MASK = "••••••••"


class AuthConfig(BaseModel):
    type: str = "keycloak"
    server_url: str = ""
    realm: str = ""
    client_id: str = ""
    client_secret: str = ""
    admin_role: str = ""
    superuser_role: str = ""
    user_role: str = ""


@router.get("/auth", response_model=AuthConfig)
async def get_auth_config(session: AsyncSession = Depends(get_session)):
    """현재 인증 설정 반환. ``client_secret`` 은 마스킹된 문자열로 응답."""
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
async def get_auth_secret(session: AsyncSession = Depends(get_session)):
    """원본 ``client_secret`` 반환. 관리자 토글로 보호되는 엔드포인트."""
    logger.info("인증 client_secret 조회 요청됨")
    cfg = await get_config_by_category(session, "auth")
    return {"client_secret": cfg.get("auth_keycloak_client_secret", "")}


@router.put("/auth")
async def update_auth_config(_guard: AdminUser,
    body: AuthConfig,
    session: AsyncSession = Depends(get_session),
):
    """인증 설정을 저장하고 in-memory 설정에 반영한다.

    ``client_secret`` 은 클라이언트에서 마스킹 값(``••••••••``)을 그대로 돌려보낸
    경우 변경하지 않는다(실수로 비밀번호를 덮어쓰는 사고 방지).
    """
    items = {
        "auth_type": body.type,
        "auth_keycloak_server_url": body.server_url,
        "auth_keycloak_realm": body.realm,
        "auth_keycloak_client_id": body.client_id,
        "auth_keycloak_admin_role": body.admin_role,
        "auth_keycloak_superuser_role": body.superuser_role,
        "auth_keycloak_user_role": body.user_role,
    }
    # 사용자가 마스킹 값이 아닌 실제 값을 보낸 경우에만 secret 을 갱신
    if body.client_secret and body.client_secret != _SECRET_MASK:
        items["auth_keycloak_client_secret"] = body.client_secret

    await update_config(session, "auth", items)
    await load_auth_settings(session)

    logger.info(
        "인증 설정 저장됨: type=%s server_url=%s realm=%s",
        body.type, body.server_url, body.realm,
    )
    return {"status": "ok", "message": "Authentication configuration saved"}


@router.post("/auth/test", response_model=TestResponse)
async def test_auth_connection(_guard: AdminUser, body: AuthConfig):
    """OpenID Configuration discovery 엔드포인트를 호출해 Keycloak 도달성을 확인한다."""
    import httpx
    url = f"{body.server_url}/realms/{body.realm}/.well-known/openid-configuration"
    logger.info("인증 테스트: %s", url)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            issuer = data.get("issuer", "")
            msg = f"성공: {issuer} 에 연결됨"
            logger.info("인증 테스트 성공: %s", msg)
            return TestResponse(success=True, message=msg)
    except Exception as e:
        msg = f"{url} 에 연결 실패: {e}"
        logger.warning("인증 테스트 실패: %s", msg)
        return TestResponse(success=False, message=msg)


# ---------------------------------------------------------------------------
# Keycloak 초기화 (realm·client·role 자동 설정)
# ---------------------------------------------------------------------------

class KeycloakInitRequest(BaseModel):
    server_url: str
    admin_username: str = "admin"
    admin_password: str = "admin"
    realm: str = "argus"
    client_id: str = "argus-client"
    client_secret: str = "argus-client-secret"
    roles: list[str] = ["argus-admin", "argus-superuser", "argus-user"]


class InitStep(BaseModel):
    step: str
    status: str  # "skip", "created", "error"
    message: str


@router.post("/auth/initialize")
async def initialize_keycloak(_guard: AdminUser, body: KeycloakInitRequest):
    """Keycloak 부트스트랩: realm·client·client_secret·realm role 을 자동 생성.

    각 단계가 끝나면 ``steps`` 배열에 결과를 누적해 클라이언트가 진행 상황을 표시할
    수 있게 한다. 단계는 모두 GET-then-POST 패턴이라 멱등하다 — 이미 존재하는
    리소스는 ``skip`` 으로 건너뛴다.
    """
    logger.info("Keycloak 초기화: server=%s realm=%s client=%s", body.server_url, body.realm, body.client_id)
    import httpx
    steps: list[dict] = []

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
                steps.append({"step": f"Client '{body.client_id}'", "status": "skip", "message": "이미 존재함"})
            else:
                create_client_resp = await client.post(
                    f"{body.server_url}/admin/realms/{body.realm}/clients",
                    headers=headers,
                    json={
                        "clientId": body.client_id,
                        "enabled": True,
                        "protocol": "openid-connect",
                        "publicClient": False,
                        "secret": body.client_secret,
                        "directAccessGrantsEnabled": True,
                        "serviceAccountsEnabled": True,
                        "authorizationServicesEnabled": False,
                        "standardFlowEnabled": True,
                        "redirectUris": ["*"],
                        "webOrigins": ["*"],
                    },
                )
                create_client_resp.raise_for_status()
                # 생성된 client 의 UUID 는 Location 헤더 마지막 세그먼트에 들어 있음
                loc = create_client_resp.headers.get("Location", "")
                client_uuid = loc.rsplit("/", 1)[-1] if loc else None
                steps.append({"step": f"Client '{body.client_id}'", "status": "created", "message": "생성 완료"})
        except Exception as e:
            logger.warning("Keycloak 초기화: client 단계 실패: %s", e)
            steps.append({"step": f"Client '{body.client_id}'", "status": "error", "message": str(e)})
            return {"steps": steps}

        # Step 4: client secret 이 입력값과 같으면 skip, 다르면 갱신
        if client_uuid:
            try:
                secret_resp = await client.get(
                    f"{body.server_url}/admin/realms/{body.realm}/clients/{client_uuid}/client-secret",
                    headers=headers,
                )
                if secret_resp.status_code == 200:
                    current_secret = secret_resp.json().get("value", "")
                    if current_secret == body.client_secret:
                        steps.append({"step": "Client Secret", "status": "skip", "message": "이미 일치함"})
                    else:
                        # 원하는 secret 값으로 client 자체를 업데이트
                        update_resp = await client.put(
                            f"{body.server_url}/admin/realms/{body.realm}/clients/{client_uuid}",
                            headers=headers,
                            json={"secret": body.client_secret},
                        )
                        update_resp.raise_for_status()
                        steps.append({"step": "Client Secret", "status": "created", "message": "일치하도록 갱신됨"})
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

    error_count = sum(1 for s in steps if s.get("status") == "error")
    if error_count:
        logger.warning("Keycloak 초기화 %d개 오류로 종료 (총 %d개 단계)", error_count, len(steps))
    else:
        logger.info("Keycloak 초기화 정상 종료 (%d개 단계)", len(steps))
    return {"steps": steps}


# ---------------------------------------------------------------------------
# CORS 설정
# ---------------------------------------------------------------------------

class CorsConfig(BaseModel):
    origins: str = "*"


@router.get("/cors", response_model=CorsConfig)
async def get_cors_config(session: AsyncSession = Depends(get_session)):
    """현재 CORS 허용 출처 설정 반환."""
    cfg = await get_config_by_category(session, "cors")
    return CorsConfig(origins=cfg.get("cors_origins", "*"))


@router.put("/cors")
async def update_cors_config(_guard: AdminUser,
    body: CorsConfig,
    session: AsyncSession = Depends(get_session),
):
    """CORS 설정 저장. 다음 요청부터 적용된다(미들웨어가 in-memory 값을 참조)."""
    await update_config(session, "cors", {"cors_origins": body.origins})
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
async def get_mail_config(session: AsyncSession = Depends(get_session)):
    """현재 메일(SMTP) 설정 반환. ``smtp_password`` 는 마스킹된 문자열."""
    cfg = await load_mail_config(session)
    return cfg.model_copy(update={"smtp_password": _SECRET_MASK if cfg.smtp_password else ""})


@router.put("/mail")
async def update_mail_config(_guard: AdminUser,
    body: EmailConfig,
    session: AsyncSession = Depends(get_session),
):
    """메일 설정 저장. ``smtp_password`` 가 마스킹 값이면 기존 값을 유지."""
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
    # 마스킹 값이 아닌 실제 비밀번호가 전달된 경우에만 갱신(덮어쓰기 사고 방지).
    if body.smtp_password and body.smtp_password != _SECRET_MASK:
        items["mail_smtp_password"] = body.smtp_password

    await update_config(session, "mail", items)
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
async def get_notify_config(session: AsyncSession = Depends(get_session)):
    """현재 Slack/Mattermost 알림 설정 반환. (webhook URL 은 orbit 과 동일하게 평문)"""
    return await load_notify_config(session)


@router.put("/notify")
async def update_notify_config(_guard: AdminUser,
    body: NotifyConfig,
    session: AsyncSession = Depends(get_session),
):
    """알림 설정 저장."""
    items = {
        "notify_enabled": str(body.enabled).lower(),
        "notify_provider": body.provider,
        "notify_timeout_seconds": str(body.timeout_seconds),
        "notify_slack_webhook_url": body.slack.webhook_url,
        "notify_slack_channel": body.slack.channel,
        "notify_slack_username": body.slack.username,
        "notify_slack_icon_emoji": body.slack.icon_emoji,
        "notify_mattermost_webhook_url": body.mattermost.webhook_url,
        "notify_mattermost_channel": body.mattermost.channel,
        "notify_mattermost_username": body.mattermost.username,
        "notify_mattermost_icon_emoji": body.mattermost.icon_emoji,
    }
    await update_config(session, "notify", items)
    logger.info("알림 설정 저장됨: provider=%s enabled=%s", body.provider, body.enabled)
    return {"status": "ok", "message": "Notify configuration saved"}


@router.post("/notify/test", response_model=TestResponse)
async def test_notify(_guard: AdminUser,
    body: NotifyTestRequest,
    session: AsyncSession = Depends(get_session),
):
    """저장된 알림 설정으로 활성 provider 에 테스트 메시지 발송. (먼저 저장 필요)"""
    cfg = await load_notify_config(session)
    try:
        await send_notify(cfg, body.text, raise_errors=True)
        return TestResponse(success=True, message=f"{cfg.provider} 로 테스트 메시지를 발송했습니다.")
    except Exception as exc:
        logger.warning("테스트 알림 발송 실패: %s", exc)
        return TestResponse(success=False, message=f"발송 실패: {exc}")
