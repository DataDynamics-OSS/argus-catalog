# SPDX-License-Identifier: Apache-2.0
"""설정(Settings) 서비스.

``catalog_configuration`` 테이블에 저장된 카테고리별 설정 값을 읽고 쓰는
헬퍼와, 시작 시 기본값을 시드(``seed_configuration``)하고 DB 값을 in-memory
``settings`` 객체에 반영(``load_*_settings``)하는 함수들을 모은다.

DB 행은 ``(category, config_key, config_value, description)`` 4-튜플 구조.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.models import CatalogConfiguration

logger = logging.getLogger(__name__)

def _build_os_defaults() -> list[tuple[str, str, str]]:
    """최초 시드용 Object Storage 기본값을 config 파일 값에서 구성.

    (auth/cors 와 동일 패턴 — 컨테이너 배포 시 config 의 object_storage.endpoint 를
     서비스명(예: http://minio:9000)으로 주입할 수 있게 한다.)
    """
    from app.core.config import settings
    return [
        ("object_storage_endpoint", settings.os_endpoint, "S3-compatible endpoint URL"),
        ("object_storage_access_key", settings.os_access_key, "S3 access key"),
        ("object_storage_secret_key", settings.os_secret_key, "S3 secret key"),
        ("object_storage_region", settings.os_region, "S3 region"),
        ("object_storage_use_ssl", "true" if settings.os_use_ssl else "false", "Use SSL for S3 connection"),
        ("object_storage_bucket", settings.os_bucket, "S3 bucket for model artifacts"),
        ("object_storage_presigned_url_expiry", str(settings.os_presigned_url_expiry), "Presigned URL expiry in seconds"),
    ]

# 임베딩 기본 설정
_EMBEDDING_DEFAULTS: list[tuple[str, str, str]] = [
    ("embedding_enabled", "false", "Enable semantic search embedding"),
    ("embedding_auto_on_write", "false",
     "쓰기(생성/수정/sync)마다 자동 임베딩 — off 면 백필로만 생성(bulk sync 부하 회피)"),
    ("embedding_provider", "local", "Embedding provider: local, openai, ollama"),
    ("embedding_model", "all-MiniLM-L6-v2", "Embedding model identifier"),
    ("embedding_api_key", "", "API key for remote providers (OpenAI)"),
    ("embedding_api_url", "", "API URL override for remote providers"),
    ("embedding_dimension", "384", "Embedding vector dimension"),
]


# AI 메타데이터 생성용 LLM 기본 설정
_LLM_DEFAULTS: list[tuple[str, str, str]] = [
    ("llm_enabled", "false", "Enable AI-based metadata generation"),
    ("llm_provider", "openai", "LLM provider: openai, ollama, anthropic"),
    ("llm_model", "gpt-4o-mini", "LLM model identifier"),
    ("llm_api_key", "", "API key for LLM provider"),
    ("llm_api_url", "", "API URL override (for custom/local endpoints)"),
    ("llm_temperature", "0.3", "Generation temperature (0.0-1.0)"),
    ("llm_max_tokens", "1024", "Max tokens per generation"),
    ("llm_auto_generate_on_sync", "false", "Auto-generate descriptions after metadata sync"),
    ("llm_language", "ko", "Target language for generated descriptions (en, ko, etc.)"),
]


_MAIL_DEFAULTS: list[tuple[str, str, str]] = [
    ("mail_enabled", "false", "Enable email (SMTP) sending"),
    ("mail_smtp_host", "smtp.gmail.com", "SMTP server hostname"),
    ("mail_smtp_port", "587", "SMTP server port (587=STARTTLS, 465=SSL)"),
    ("mail_use_tls", "true", "Use STARTTLS (port 587)"),
    ("mail_use_ssl", "false", "Use implicit SSL (port 465)"),
    ("mail_timeout_seconds", "10", "SMTP connection timeout (seconds)"),
    ("mail_from_email", "", "Sender email address"),
    ("mail_from_name", "Argus Catalog", "Sender display name"),
    ("mail_smtp_user", "", "SMTP username (defaults to sender email if blank)"),
    ("mail_smtp_password", "", "SMTP password / app password"),
    ("mail_subject_prefix", "[Argus Catalog]", "Subject line prefix"),
    ("mail_default_recipients", "", "Default recipients (comma-separated)"),
]


_NOTIFY_DEFAULTS: list[tuple[str, str, str]] = [
    ("notify_enabled", "false", "Enable Slack/Mattermost notifications"),
    ("notify_provider", "slack", "Active provider: slack | mattermost"),
    ("notify_timeout_seconds", "10", "Webhook request timeout (seconds)"),
    ("notify_slack_webhook_url", "", "Slack incoming webhook URL"),
    ("notify_slack_channel", "", "Slack channel override (optional)"),
    ("notify_slack_username", "", "Slack display username override (optional)"),
    ("notify_slack_icon_emoji", ":bell:", "Slack icon emoji (optional)"),
    ("notify_mattermost_webhook_url", "", "Mattermost incoming webhook URL"),
    ("notify_mattermost_channel", "", "Mattermost channel override (optional)"),
    ("notify_mattermost_username", "", "Mattermost display username override (optional)"),
    ("notify_mattermost_icon_emoji", "", "Mattermost icon emoji (optional)"),
]

# 변경관리 기본값: 참조자 통지 전역 설정
_CHANGE_DEFAULTS: list[tuple[str, str, str]] = [
    ("change_notify_enabled", "true", "변경 요청 참조자 통지 사용 여부"),
    ("change_notify_channel", "email", "참조자 통지 채널: email | slack | mattermost"),
]


def _build_assistant_defaults() -> list[tuple[str, str, str]]:
    """최초 시드용 AI 어시스턴트 기본값 — config 파일의 ``assistant.agent.url`` 을 반영.

    기존에 properties 로 URL 을 설정해 둔 배포는 enabled=true 로 출고되어
    동작이 유지된다. URL 이 없으면 enabled=false (내장 단순 대화로 폴백).
    """
    from app.core.config import settings
    url = (settings.assistant_agent_url or "").strip()
    return [
        ("assistant_enabled", "true" if url else "false",
         "Enable agent-backed AI assistant (tool-use chat); off = builtin fallback chat"),
        ("assistant_agent_url", url,
         "Agent serve URL (e.g. http://localhost:8930). Empty = builtin fallback chat"),
    ]


def _build_auth_defaults() -> list[tuple[str, str, str]]:
    """최초 시드용 인증 기본값을 config 파일 값에서 구성."""
    from app.core.config import settings
    return [
        ("auth_type", settings.auth_type, "Authentication type"),
        ("auth_keycloak_server_url", settings.auth_keycloak_server_url, "Keycloak server URL"),
        ("auth_keycloak_realm", settings.auth_keycloak_realm, "Keycloak realm"),
        ("auth_keycloak_client_id", settings.auth_keycloak_client_id, "Keycloak client ID"),
        ("auth_keycloak_client_secret", settings.auth_keycloak_client_secret, "Keycloak client secret"),
        ("auth_keycloak_admin_role", settings.auth_keycloak_admin_role, "Admin role name"),
        ("auth_keycloak_superuser_role", settings.auth_keycloak_superuser_role, "Superuser role name"),
        ("auth_keycloak_user_role", settings.auth_keycloak_user_role, "User role name"),
    ]


def _build_cors_defaults() -> list[tuple[str, str, str]]:
    """최초 시드용 CORS 기본값을 config 파일 값에서 구성."""
    from app.core.config import settings
    origins = settings.cors_origins
    origins_str = ",".join(origins) if isinstance(origins, list) else str(origins)
    return [
        ("cors_origins", origins_str, "Allowed CORS origins (comma-separated)"),
    ]


async def seed_configuration(session: AsyncSession) -> None:
    """기본 설정 행이 없으면 카테고리별로 삽입한다(idempotent).

    설정 키가 secret 류면 INFO 로그에 값은 마스킹해서 남긴다.
    """
    all_defaults = [
        ("object_storage", _build_os_defaults()),
        ("embedding", _EMBEDDING_DEFAULTS),
        ("llm", _LLM_DEFAULTS),
        ("mail", _MAIL_DEFAULTS),
        ("notify", _NOTIFY_DEFAULTS),
        ("change", _CHANGE_DEFAULTS),
        ("assistant", _build_assistant_defaults()),
        ("auth", _build_auth_defaults()),
        ("cors", _build_cors_defaults()),
    ]
    for category, defaults in all_defaults:
        for key, value, desc in defaults:
            existing = await session.execute(
                select(CatalogConfiguration).where(CatalogConfiguration.config_key == key)
            )
            if not existing.scalars().first():
                session.add(CatalogConfiguration(
                    category=category,
                    config_key=key,
                    config_value=value,
                    description=desc,
                ))
                logger.info("설정 시드: %s = %s", key, value if "secret" not in key else "****")
    await session.commit()


async def get_config_by_category(session: AsyncSession, category: str) -> dict[str, str]:
    """주어진 카테고리에 속한 설정 항목을 dict 로 반환."""
    result = await session.execute(
        select(CatalogConfiguration).where(CatalogConfiguration.category == category)
    )
    return {row.config_key: row.config_value for row in result.scalars().all()}


async def update_config(session: AsyncSession, category: str, items: dict[str, str]) -> None:
    """주어진 카테고리의 설정 키를 upsert. 존재하면 값만 갱신, 없으면 새 행 추가."""
    for key, value in items.items():
        result = await session.execute(
            select(CatalogConfiguration).where(CatalogConfiguration.config_key == key)
        )
        row = result.scalars().first()
        if row:
            row.config_value = value
        else:
            session.add(CatalogConfiguration(
                category=category,
                config_key=key,
                config_value=value,
            ))
    await session.commit()
    logger.info("설정 갱신됨: category=%s, %d개 항목", category, len(items))


async def load_os_settings(session: AsyncSession) -> dict[str, str]:
    """오브젝트 스토리지 설정을 DB 에서 읽어 in-memory ``settings`` 에 반영."""
    from app.core.config import settings

    cfg = await get_config_by_category(session, "object_storage")

    settings.os_endpoint = cfg.get("object_storage_endpoint", "http://localhost:9000")
    settings.os_access_key = cfg.get("object_storage_access_key", "minioadmin")
    settings.os_secret_key = cfg.get("object_storage_secret_key", "minioadmin")
    settings.os_region = cfg.get("object_storage_region", "us-east-1")
    settings.os_use_ssl = cfg.get("object_storage_use_ssl", "false").lower() in ("true", "1", "yes")
    settings.os_bucket = cfg.get("object_storage_bucket", "model-artifacts")
    settings.os_presigned_url_expiry = int(cfg.get("object_storage_presigned_url_expiry", "3600"))

    logger.info("DB 에서 오브젝트 스토리지 설정 로드: endpoint=%s, bucket=%s",
                settings.os_endpoint, settings.os_bucket)
    return cfg


async def load_embedding_settings(session: AsyncSession) -> dict[str, str]:
    """임베딩 설정을 DB 에서 읽고, 활성화돼 있으면 provider 를 초기화한다."""
    cfg = await get_config_by_category(session, "embedding")
    enabled = cfg.get("embedding_enabled", "false").lower() in ("true", "1", "yes")

    # 쓰기 시 자동 임베딩 정책을 in-memory 에 반영(기본 off). 설정 변경/재로딩 시 갱신.
    from app.embedding.service import set_auto_on_write
    set_auto_on_write(
        cfg.get("embedding_auto_on_write", "false").lower() in ("true", "1", "yes")
    )

    if enabled:
        from app.embedding.registry import initialize_provider
        try:
            await initialize_provider(cfg)
        except Exception as e:
            logger.warning("임베딩 provider 초기화 실패: %s", e)
    else:
        logger.info("임베딩이 비활성화됨")

    return cfg


async def load_llm_settings(session: AsyncSession) -> dict[str, str]:
    """LLM 설정을 DB 에서 읽고, 활성화돼 있으면 provider 를 초기화한다."""
    cfg = await get_config_by_category(session, "llm")
    enabled = cfg.get("llm_enabled", "false").lower() in ("true", "1", "yes")

    if enabled:
        from app.ai.registry import initialize_provider
        try:
            await initialize_provider(cfg)
        except Exception as e:
            logger.warning("LLM provider 초기화 실패: %s", e)
    else:
        logger.info("LLM (AI 메타데이터 생성)이 비활성화됨")

    return cfg


async def load_auth_settings(session: AsyncSession) -> dict[str, str]:
    """인증 설정을 DB 에서 읽어 in-memory ``settings`` 에 반영하고 JWKS 캐시를 비운다."""
    from app.core.config import settings

    cfg = await get_config_by_category(session, "auth")
    if not cfg:
        logger.info("DB 에 인증 설정 없음, config 파일 기본값 사용")
        return {}

    settings.auth_type = cfg.get("auth_type", settings.auth_type)
    settings.auth_keycloak_server_url = cfg.get("auth_keycloak_server_url", settings.auth_keycloak_server_url)
    settings.auth_keycloak_realm = cfg.get("auth_keycloak_realm", settings.auth_keycloak_realm)
    settings.auth_keycloak_client_id = cfg.get("auth_keycloak_client_id", settings.auth_keycloak_client_id)
    settings.auth_keycloak_client_secret = cfg.get("auth_keycloak_client_secret", settings.auth_keycloak_client_secret)
    settings.auth_keycloak_admin_role = cfg.get("auth_keycloak_admin_role", settings.auth_keycloak_admin_role)
    settings.auth_keycloak_superuser_role = cfg.get("auth_keycloak_superuser_role", settings.auth_keycloak_superuser_role)
    settings.auth_keycloak_user_role = cfg.get("auth_keycloak_user_role", settings.auth_keycloak_user_role)

    # Keycloak 서버가 바뀐 경우에 대비해 JWKS 캐시를 비운다.
    # 다음 토큰 검증 시 새 서버에서 키를 다시 받아오게 된다.
    from app.core.auth import _jwks_cache
    _jwks_cache.clear()

    logger.info("DB 에서 인증 설정 로드: server_url=%s, realm=%s",
                settings.auth_keycloak_server_url, settings.auth_keycloak_realm)
    return cfg


async def load_cors_settings(session: AsyncSession) -> dict[str, str]:
    """CORS 설정을 DB 에서 읽어 in-memory ``settings`` 에 반영."""
    from app.core.config import settings

    cfg = await get_config_by_category(session, "cors")
    if not cfg:
        logger.info("DB 에 CORS 설정 없음, config 파일 기본값 사용")
        return {}

    origins_str = cfg.get("cors_origins", "*")
    settings.cors_origins = [o.strip() for o in origins_str.split(",") if o.strip()]

    logger.info("DB 에서 CORS 설정 로드: origins=%s", settings.cors_origins)
    return cfg
