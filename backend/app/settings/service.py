# SPDX-License-Identifier: Apache-2.0
"""설정(Settings) 서비스.

``catalog_configuration`` 테이블에 저장된 카테고리별 설정 값을 읽고 쓰는
헬퍼와, 시작 시 기본값을 시드(``seed_configuration``)하고 DB 값을 in-memory
``settings`` 객체에 반영(``load_*_settings``)하는 함수들을 모은다.

DB 행은 ``(category, config_key, config_value, description)`` 4-튜플 구조.

비밀값(``SECRET_CONFIG_KEYS`` — API 키·비밀번호·client secret·webhook URL)은 ``enc:`` 접두사가 붙은
Fernet 암호문으로 저장한다(``ARGUS_SECRET_KEY`` 파생 키, ``app.apis.crypto`` — API 자격증명·데이터 소스
비밀값과 같은 키). 읽기(:func:`get_config_by_category`)에서 복호화하므로 호출 측은 평문을 받는다.
접두사 없는 값은 도입 전 평문으로 보고 그대로 쓰며, 기동 시 시드 단계에서 암호문으로 바꾼다.
**주의**: 암호문으로 바뀐 뒤에는 이 기능 이전 버전의 백엔드가 그 값을 쓰지 못한다.

:func:`update_config` 에 ``actor`` 를 주면 바뀐 키를 관리 감사 로그(``settings.<category>``)에 남긴다.
"""

import logging

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embedding.base import DEFAULT_EMBEDDING_MODEL
from app.settings.models import CatalogConfiguration

logger = logging.getLogger(__name__)

SECRET_CONFIG_KEYS = frozenset({
    "object_storage_secret_key",
    "embedding_api_key",
    "llm_api_key",
    "mail_smtp_password",
    "auth_keycloak_client_secret",
    "notify_slack_webhook_url",       # webhook URL 자체가 전송 권한(토큰 포함)
    "notify_mattermost_webhook_url",
})
_SEALED_PREFIX = "enc:"


def seal_value(key: str, value: str | None) -> str:
    """저장용 값 — 비밀 키면 암호화(이미 암호문이거나 빈 값이면 그대로)."""
    value = value or ""
    if key not in SECRET_CONFIG_KEYS or not value or value.startswith(_SEALED_PREFIX):
        return value
    from app.apis.crypto import encrypt
    return _SEALED_PREFIX + encrypt(value)


def unseal_value(key: str, value: str | None) -> str:
    """읽기용 값 — 암호문이면 복호화. 복호화 실패(키 변경 등)면 빈 값(로그 남김)."""
    value = value or ""
    if not value.startswith(_SEALED_PREFIX):
        return value
    from app.apis.crypto import decrypt
    try:
        return decrypt(value[len(_SEALED_PREFIX):])
    except InvalidToken:
        logger.error("설정 비밀값 복호화 실패: %s (ARGUS_SECRET_KEY 가 바뀌었으면 값을 다시 저장하세요)", key)
        return ""


def _build_os_defaults(settings=None) -> list[tuple[str, str, str]]:
    """최초 시드용 Object Storage 기본값을 config 파일 값에서 구성.

    (auth/cors 와 동일 패턴 — 컨테이너 배포 시 config 의 object_storage.endpoint 를
     서비스명(예: http://minio:9000)으로 주입할 수 있게 한다.)
    """
    if settings is None:
        from app.core.config import settings
    return [
        ("object_storage_endpoint", settings.os_endpoint, "S3-compatible endpoint URL"),
        ("object_storage_public_endpoint", settings.os_public_endpoint,
         "Endpoint URL used in presigned URLs for browsers/SDK (empty = endpoint)"),
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
    ("embedding_model", DEFAULT_EMBEDDING_MODEL, "Embedding model identifier"),
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

# 데이터 품질 기본값: 원천 데이터 시간대 — 시간대 없는 시각 값(MySQL DATETIME 등)을 FRESHNESS 에서 해석할 때 사용.
# 품질 배치(quality/*.py)의 --source-timezone 기본값과 같아야 두 엔진 판정이 일치한다.
_QUALITY_DEFAULTS: list[tuple[str, str, str]] = [
    ("quality_source_timezone", "Asia/Seoul",
     "원천 데이터 시간대 (IANA 이름 또는 +09:00) — 시간대 없는 시각 값 해석(FRESHNESS)"),
]

# 카탈로그 기본값: 일반 사용자(비관리자)의 데이터셋 직접 등록 — 기본 꺼짐(관리자·동기화 계정만 등록)
DATASET_CREATE_BY_USERS_KEY = "catalog_dataset_create_by_users"
_CATALOG_DEFAULTS: list[tuple[str, str, str]] = [
    (DATASET_CREATE_BY_USERS_KEY, "false",
     "일반 사용자(비관리자)의 데이터셋 직접 등록 허용 여부 — 켜도 기능 권한 datasets.create 는 계속 적용"),
]


def _build_assistant_defaults(settings=None) -> list[tuple[str, str, str]]:
    """최초 시드용 AI 어시스턴트 기본값 — config 파일의 ``assistant.service.url``(이전 이름 ``assistant.agent.url``)을 반영.

    기존에 properties 로 URL 을 설정해 둔 배포는 enabled=true 로 출고되어
    동작이 유지된다. URL 이 없으면 enabled=false (내장 단순 대화로 폴백).
    """
    if settings is None:
        from app.core.config import settings
    url = (settings.assistant_agent_url or "").strip()
    return [
        ("assistant_enabled", "true" if url else "false",
         "Enable the AI service (argus-catalog-ai-service) assistant with tool-use chat; off = builtin fallback chat"),
        ("assistant_agent_url", url,
         "AI service assistant URL (e.g. http://localhost:8930). Empty = builtin fallback chat"),
    ]


def _build_auth_defaults(settings=None) -> list[tuple[str, str, str]]:
    """최초 시드용 인증 기본값을 config 파일 값에서 구성."""
    if settings is None:
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


def _build_cors_defaults(settings=None) -> list[tuple[str, str, str]]:
    """최초 시드용 CORS 기본값을 config 파일 값에서 구성."""
    if settings is None:
        from app.core.config import settings
    origins = settings.cors_origins
    origins_str = ",".join(origins) if isinstance(origins, list) else str(origins)
    return [
        ("cors_origins", origins_str, "Allowed CORS origins (comma-separated)"),
    ]


def _all_defaults(settings=None) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """카테고리별 시드 기본값. ``settings`` 를 주면 그 객체의 값(설정 파일 값)으로 구성."""
    return [
        ("object_storage", _build_os_defaults(settings)),
        ("embedding", _EMBEDDING_DEFAULTS),
        ("llm", _LLM_DEFAULTS),
        ("mail", _MAIL_DEFAULTS),
        ("notify", _NOTIFY_DEFAULTS),
        ("change", _CHANGE_DEFAULTS),
        ("quality", _QUALITY_DEFAULTS),
        ("catalog", _CATALOG_DEFAULTS),
        ("assistant", _build_assistant_defaults(settings)),
        ("auth", _build_auth_defaults(settings)),
        ("cors", _build_cors_defaults(settings)),
    ]


async def seed_configuration(session: AsyncSession) -> None:
    """기본 설정 행이 없으면 카테고리별로 삽입한다(idempotent).

    이미 있는 행은 값은 그대로 두고, 설명만 코드 기준으로 맞춘다(설명은 화면에서 바꿀 수 없다 —
    업그레이드로 바뀐 설명이 반영되게). 설정 키가 secret 류면 INFO 로그에 값은 마스킹해서 남긴다.
    """
    for category, defaults in _all_defaults():
        for key, value, desc in defaults:
            existing = await session.execute(
                select(CatalogConfiguration).where(CatalogConfiguration.config_key == key)
            )
            row = existing.scalars().first()
            if row is not None:
                if desc and row.description != desc:
                    row.description = desc
                continue
            session.add(CatalogConfiguration(
                category=category,
                config_key=key,
                config_value=seal_value(key, value),
                description=desc,
            ))
            logger.info("설정 시드: %s = %s", key, "****" if key in SECRET_CONFIG_KEYS else value)
    await session.commit()
    await seal_legacy_secrets(session)
    await migrate_default_embedding_model(session)


# 이전 기본 임베딩 모델(영어 전용). 한 번도 켜지 않은 설치는 새 기본값(다국어)으로 옮긴다.
_LEGACY_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


async def migrate_default_embedding_model(session: AsyncSession) -> bool:
    """임베딩이 꺼져 있고 모델이 이전 기본값 그대로면 새 기본 모델로 바꾼다(idempotent).

    사용자가 임베딩을 켜서 쓰고 있으면(벡터가 이전 모델로 만들어져 있으므로) 건드리지 않는다.
    둘 다 384차원이라 pgvector 컬럼과 호환된다. 바꿨으면 True.
    """
    rows = (await session.execute(
        select(CatalogConfiguration).where(
            CatalogConfiguration.config_key.in_(("embedding_enabled", "embedding_model")))
    )).scalars().all()
    by_key = {r.config_key: r for r in rows}
    model_row = by_key.get("embedding_model")
    enabled_row = by_key.get("embedding_enabled")
    enabled = (enabled_row.config_value or "").strip().lower() in ("true", "1", "yes") if enabled_row else False
    if model_row is None or enabled or model_row.config_value != _LEGACY_DEFAULT_EMBEDDING_MODEL:
        return False
    model_row.config_value = DEFAULT_EMBEDDING_MODEL
    await session.commit()
    logger.info("임베딩 기본 모델 변경: %s → %s (임베딩 미사용 설치)",
                _LEGACY_DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_MODEL)
    return True


# ---------------------------------------------------------------------------
# 값 출처 — 설정 화면의 "값 출처" 패널
# ---------------------------------------------------------------------------

def _file_path(key: str) -> tuple[str, ...] | None:
    """설정 파일(config.yml/properties)에서 이 키를 정하는 위치. 설정 파일로 정할 수 없는 키면 None."""
    if key == "auth_type":
        return ("auth", "type")
    if key.startswith("auth_keycloak_"):
        return ("auth", "keycloak", key[len("auth_keycloak_"):])
    if key == "cors_origins":
        return ("cors", "origins")
    if key.startswith("object_storage_"):
        return ("object_storage", key[len("object_storage_"):])
    if key in ("assistant_agent_url", "assistant_enabled"):  # enabled 는 URL 유무로 정해진다
        # assistant.service.url, 없으면 이전 이름 assistant.agent.url
        if str(cfgmod_raw().get("assistant", {}).get("service.url") or "").strip():
            return ("assistant", "service.url")
        return ("assistant", "agent.url")
    return None


def cfgmod_raw() -> dict:
    """설정 파일에서 읽은 원본 dict(``app.core.config._raw``) — init_settings 재로딩 후 값도 보도록 매번 조회."""
    from app.core import config as cfgmod
    return cfgmod._raw


def _in_file(raw: dict, path: tuple[str, ...]) -> bool:
    """설정 파일 dict 에 path 의 값이 있고 null 이 아닌지."""
    cur = raw
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return cur is not None


def _norm(v: str | None) -> str:
    """비교용 정규화 — None 은 빈 문자열, 앞뒤 공백 제거."""
    return (v or "").strip()


async def config_sources(session: AsyncSession) -> dict[str, dict[str, dict]]:
    """카테고리 → 키 → 값 출처.

    설정 파일은 최초 기동 때 DB 에 없는 키만 채우고, 그 뒤에는 DB 값이 항상 우선이다. 그래서 출처는
    지금 DB 값을 "지금 설정 파일·코드 기본값으로 시드한다면 들어갈 값"과 비교해 정한다.

    - ``default``: 코드 기본값 그대로(설정 파일이 이 키를 정하지 않음)
    - ``config``: 설정 파일 값 그대로
    - ``db``: 화면(API)에서 바꾼 값 — 시드 값과 다름

    설정 파일이 정하는 키인데 DB 값과 다르면 ``differs_from_file`` (설정 파일을 고쳐도 반영되지 않는다는
    뜻), 비밀값이 아니면 ``file_value`` 도 준다. 비밀값은 값 자체를 내보내지 않는다.
    ``ARGUS_AUTH_FORCE_LOCAL`` 로 인증 방식이 강제되면 ``auth_type`` 에 ``override`` 를 단다.
    """
    import os

    from app.core import config as cfgmod

    fresh = cfgmod.Settings()  # DB 반영 전 — 설정 파일 + 코드 기본값
    seed = {key: (cat, val) for cat, items in _all_defaults(fresh) for key, val, _ in items}
    rows = (await session.execute(select(CatalogConfiguration))).scalars().all()

    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        key = row.config_key
        value = unseal_value(key, row.config_value)
        cat, seed_value = seed.get(key, (row.category, None))
        path = _file_path(key)
        in_file = bool(path) and _in_file(cfgmod._raw, path)
        if seed_value is None:
            source = "db"  # 시드 대상이 아닌 키 — 화면/API 로만 생김
        elif _norm(value) == _norm(seed_value):
            source = "config" if in_file else "default"
        else:
            source = "db"
        info: dict = {"source": source, "description": row.description or "", "secret": key in SECRET_CONFIG_KEYS}
        if in_file and _norm(value) != _norm(seed_value):
            info["differs_from_file"] = True
            if key not in SECRET_CONFIG_KEYS:
                info["file_value"] = seed_value
        out.setdefault(row.category, {})[key] = info

    force_local = os.environ.get("ARGUS_AUTH_FORCE_LOCAL", "").strip().lower() in ("1", "true", "yes", "on")
    if force_local and "auth_type" in out.get("auth", {}):
        out["auth"]["auth_type"]["override"] = "환경변수 ARGUS_AUTH_FORCE_LOCAL=true 로 로컬 인증이 강제됨"
    return out


async def get_config_by_category(session: AsyncSession, category: str) -> dict[str, str]:
    """주어진 카테고리에 속한 설정 항목을 dict 로 반환(비밀값은 복호화된 평문 — 로그·응답에 주의)."""
    logger.debug("설정 카테고리 조회: category=%s", category)
    result = await session.execute(
        select(CatalogConfiguration).where(CatalogConfiguration.category == category)
    )
    return {row.config_key: unseal_value(row.config_key, row.config_value) for row in result.scalars().all()}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


async def dataset_create_by_users_enabled(session: AsyncSession) -> bool:
    """일반 사용자의 데이터셋 직접 등록 허용 여부(설정 ``catalog_dataset_create_by_users``). 행이 없으면 False."""
    value = (await session.execute(
        select(CatalogConfiguration.config_value).where(
            CatalogConfiguration.config_key == DATASET_CREATE_BY_USERS_KEY)
    )).scalars().first()
    return _truthy(value)


async def update_config(
    session: AsyncSession, category: str, items: dict[str, str], *, actor=None,
) -> None:
    """주어진 카테고리의 설정 키를 upsert. 존재하면 값만 갱신, 없으면 새 행 추가.

    비밀 키는 암호화해 저장한다. ``actor`` 가 있으면 바뀐 키를 감사 로그에 남긴다
    (비밀값은 값 없이 "(변경됨)").
    """
    changes: dict[str, dict] = {}
    for key, value in items.items():
        value = "" if value is None else str(value)
        result = await session.execute(
            select(CatalogConfiguration).where(CatalogConfiguration.config_key == key)
        )
        row = result.scalars().first()
        old = unseal_value(key, row.config_value) if row else None
        if old != value:
            if key in SECRET_CONFIG_KEYS:
                from app.audit.service import secret_change
                if (diff := secret_change(old, value)) is not None:
                    changes[key] = diff
            else:
                changes[key] = {"old": old, "new": value}
        sealed = seal_value(key, value)
        if row:
            if old != value:
                row.config_value = sealed
        else:
            session.add(CatalogConfiguration(
                category=category,
                config_key=key,
                config_value=sealed,
            ))
    await session.commit()
    logger.info("설정 갱신됨: category=%s, %d개 항목(변경 %d)", category, len(items), len(changes))
    if actor is not None and changes:
        from app.audit.service import record
        await record(
            session, actor, "settings", f"settings.{category}", target=category,
            summary=f"{category} 설정 변경: {', '.join(changes)}", detail={"changes": changes},
        )


async def seal_legacy_secrets(session: AsyncSession) -> int:
    """평문으로 남은 비밀 설정값을 암호문으로 바꾼다(기동 시, 멱등). 바꾼 건수."""
    rows = (await session.execute(
        select(CatalogConfiguration).where(CatalogConfiguration.config_key.in_(SECRET_CONFIG_KEYS))
    )).scalars().all()
    logger.debug("평문 비밀 설정값 점검: rows=%d", len(rows))
    n = 0
    for row in rows:
        sealed = seal_value(row.config_key, row.config_value)
        if sealed != (row.config_value or ""):
            row.config_value = sealed
            n += 1
    if n:
        await session.commit()
        logger.info("평문 비밀 설정값 %d건을 암호화해 저장", n)
    return n


async def load_os_settings(session: AsyncSession) -> dict[str, str]:
    """오브젝트 스토리지 설정을 DB 에서 읽어 in-memory ``settings`` 에 반영."""
    from app.core.config import settings

    cfg = await get_config_by_category(session, "object_storage")

    settings.os_endpoint = cfg.get("object_storage_endpoint", "http://localhost:9000")
    settings.os_public_endpoint = (cfg.get("object_storage_public_endpoint", "") or "").strip()
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
            logger.info("임베딩 활성화: provider=%s model=%s",
                        cfg.get("embedding_provider"), cfg.get("embedding_model"))
    else:
        # 이미 로드된 provider 도 해제한다 — 남겨 두면 비활성화 후에도 시맨틱/하이브리드 검색이
        # 계속 동작한다(semantic 은 503, hybrid 는 키워드 폴백이 기대 동작).
        from app.embedding.registry import shutdown_provider
        await shutdown_provider()
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
        # 이미 로드된 provider 도 해제한다 — 남겨 두면 비활성화 후에도 AI 메타데이터 생성이 계속 동작한다
        from app.ai.registry import shutdown_provider
        await shutdown_provider()
        logger.info("LLM (AI 메타데이터 생성)이 비활성화됨")

    return cfg


def _apply_force_local(settings) -> None:
    """비상 복구 — ``ARGUS_AUTH_FORCE_LOCAL=true`` 면 DB 설정과 무관하게 로컬 인증 모드로 동작한다.

    인증 설정을 잘못 저장해 관리자가 로그인할 수 없게 됐을 때, 환경변수를 켜고 재기동해 로컬 관리자
    계정으로 들어와 설정을 고친 뒤 환경변수를 끈다.
    """
    import os
    if os.environ.get("ARGUS_AUTH_FORCE_LOCAL", "").strip().lower() in ("1", "true", "yes", "on"):
        if settings.auth_type != "local":
            logger.warning("ARGUS_AUTH_FORCE_LOCAL=true — 인증 설정(%s)을 무시하고 로컬 인증 모드로 동작합니다",
                           settings.auth_type)
        settings.auth_type = "local"
        settings.auth_keycloak_admin_role = "argus-admin"
        settings.auth_keycloak_superuser_role = "argus-superuser"
        settings.auth_keycloak_user_role = "argus-user"


async def load_auth_settings(session: AsyncSession) -> dict[str, str]:
    """인증 설정을 DB 에서 읽어 in-memory ``settings`` 에 반영하고 JWKS 캐시를 비운다."""
    from app.core.config import settings

    cfg = await get_config_by_category(session, "auth")
    if not cfg:
        logger.info("DB 에 인증 설정 없음, config 파일 기본값 사용")
        _apply_force_local(settings)
        return {}

    settings.auth_type = cfg.get("auth_type", settings.auth_type)
    settings.auth_keycloak_server_url = cfg.get("auth_keycloak_server_url", settings.auth_keycloak_server_url)
    settings.auth_keycloak_realm = cfg.get("auth_keycloak_realm", settings.auth_keycloak_realm)
    settings.auth_keycloak_client_id = cfg.get("auth_keycloak_client_id", settings.auth_keycloak_client_id)
    settings.auth_keycloak_client_secret = cfg.get("auth_keycloak_client_secret", settings.auth_keycloak_client_secret)
    settings.auth_keycloak_admin_role = cfg.get("auth_keycloak_admin_role", settings.auth_keycloak_admin_role)
    settings.auth_keycloak_superuser_role = cfg.get("auth_keycloak_superuser_role", settings.auth_keycloak_superuser_role)
    settings.auth_keycloak_user_role = cfg.get("auth_keycloak_user_role", settings.auth_keycloak_user_role)

    _apply_force_local(settings)

    # Keycloak 서버가 바뀐 경우에 대비해 JWKS 캐시를 비운다.
    # 다음 토큰 검증 시 새 서버에서 키를 다시 받아오게 된다.
    from app.core.auth import _jwks_cache
    _jwks_cache.clear()

    logger.info("DB 에서 인증 설정 로드: auth_type=%s, server_url=%s, realm=%s",
                settings.auth_type, settings.auth_keycloak_server_url, settings.auth_keycloak_realm)
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
