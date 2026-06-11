# SPDX-License-Identifier: Apache-2.0
"""애플리케이션 설정.

설정은 설정 디렉터리의 두 파일에서 로드된다:
1. config.properties - Java 스타일 key=value 변수 정의
2. config.yml - Spring Boot 스타일 ${variable:default} 을 사용하는 메인 YAML 설정
"""

import os
from pathlib import Path

from app import __version__
from app.core.config_loader import load_config

_CONFIG_DIR = Path(os.environ.get("ARGUS_CATALOG_SERVER_CONFIG_DIR", "/etc/argus-catalog-server"))
_yaml_path: Path = _CONFIG_DIR / "config.yml"
_properties_path: Path = _CONFIG_DIR / "config.properties"
_raw: dict = load_config(config_dir=_CONFIG_DIR)


def _get(section: str, key: str, default=None):
    return _raw.get(section, {}).get(key, default)


def _get_nested(section: str, subsection: str, key: str, default=None):
    return _raw.get(section, {}).get(subsection, {}).get(key, default)


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


class Settings:
    """config.yml + config.properties 에서 로드한 전역 애플리케이션 설정."""

    def __init__(self) -> None:
        self.app_name: str = _get("app", "name", "argus-catalog-server")
        # 버전은 코드(app.__version__)를 단일 소스로 사용한다. config.yml 로 재정의하지 않는다.
        self.app_version: str = __version__
        self.debug: bool = _to_bool(_get("app", "debug", False))

        self.host: str = _get("server", "host", "0.0.0.0")
        self.port: int = int(_get("server", "port", 4600))

        self.log_level: str = _get("logging", "level", "INFO")
        self.log_dir: Path = Path(_get("logging", "dir", "logs"))
        self.log_filename: str = _get("logging", "filename", "argus-catalog-server.log")
        self.log_rolling_type: str = _get_nested("logging", "rolling", "type", "daily")
        self.log_rolling_backup_count: int = int(
            _get_nested("logging", "rolling", "backup_count", 30)
        )

        self.data_dir: Path = Path(_get("data", "dir", "/var/lib/argus-catalog-server"))

        self.config_dir: Path = _CONFIG_DIR
        self.config_yaml_path: Path = _yaml_path
        self.config_properties_path: Path = _properties_path

        self.cors_origins: list[str] = _get("cors", "origins", ["*"])

        # 데이터베이스
        self.db_type: str = _get("database", "type", "postgresql")
        self.db_host: str = _get("database", "host", "localhost")
        self.db_port: int = int(_get("database", "port", 5432))
        self.db_name: str = _get("database", "name", "argus_catalog")
        self.db_username: str = _get("database", "username", "argus")
        self.db_password: str = _get("database", "password", "argus")
        self.db_pool_size: int = int(_get_nested("database", "pool", "size", 5))
        self.db_pool_max_overflow: int = int(_get_nested("database", "pool", "max_overflow", 10))
        self.db_pool_recycle: int = int(_get_nested("database", "pool", "recycle", 3600))
        self.db_echo: bool = _to_bool(_get("database", "echo", False))

        # SSO 인증용 추가 - Keycloak OIDC 설정
        self.auth_type: str = _get("auth", "type", "local")
        self.auth_keycloak_server_url: str = _get_nested("auth", "keycloak", "server_url", "http://localhost:8180")
        self.auth_keycloak_realm: str = _get_nested("auth", "keycloak", "realm", "argus")
        self.auth_keycloak_client_id: str = _get_nested("auth", "keycloak", "client_id", "argus-client")
        self.auth_keycloak_client_secret: str = _get_nested("auth", "keycloak", "client_secret", "argus-client-secret")
        self.auth_keycloak_admin_role: str = _get_nested("auth", "keycloak", "admin_role", "argus-admin")
        self.auth_keycloak_superuser_role: str = _get_nested("auth", "keycloak", "superuser_role", "argus-superuser")
        self.auth_keycloak_user_role: str = _get_nested("auth", "keycloak", "user_role", "argus-user")

        # Object Storage (MinIO / S3) — config 파일 기본값. 시작 시 seed 후 DB 값으로 덮어쓴다.
        # (auth/cors 와 동일하게 최초 seed 기본값을 config 파일에서 읽어 컨테이너 배포 시
        #  object_storage 엔드포인트를 서비스명으로 주입할 수 있게 한다.)
        self.os_endpoint: str = _get("object_storage", "endpoint", "http://localhost:9000")
        self.os_access_key: str = _get("object_storage", "access_key", "minioadmin")
        self.os_secret_key: str = _get("object_storage", "secret_key", "minioadmin")
        self.os_region: str = _get("object_storage", "region", "us-east-1")
        self.os_use_ssl: bool = _to_bool(_get("object_storage", "use_ssl", False))
        self.os_bucket: str = _get("object_storage", "bucket", "model-artifacts")
        self.os_presigned_url_expiry: int = int(
            _get("object_storage", "presigned_url_expiry", 3600)
        )

        # 메타데이터 동기화 서비스 (프로파일 분석 프록시)
        self.metadata_sync_base_url: str = _get(
            "metadata_sync", "base_url", "http://localhost:4610"
        )

        # 외부 API 캐시
        self.cache_max_size: int = int(_get_nested("external", "cache", "max_size", 1000))
        self.cache_ttl_seconds: int = int(_get_nested("external", "cache", "ttl_seconds", 300))
        self.cache_enabled: bool = _to_bool(_get_nested("external", "cache", "enabled", True))

        # Temporal (변경 관리 결재/통지 워크플로우 엔진)
        # enabled=false 면 lifespan 에서 연결을 건너뛰고, CR submit 은 503 으로 거부된다.
        # AI 어시스턴트 에이전트 (agent/ serve 모드) — 설정 시 채팅을 프록시
        self.assistant_agent_url: str = _get("assistant", "agent.url", "")

        self.temporal_enabled: bool = _to_bool(_get("temporal", "enabled", True))
        self.temporal_target: str = _get("temporal", "target", "localhost:7233")
        self.temporal_namespace: str = _get("temporal", "namespace", "default")
        self.temporal_task_queue: str = _get("temporal", "task_queue", "change-mgmt")


def init_settings(
    yaml_path: str | None = None,
    properties_path: str | None = None,
) -> None:
    """사용자 지정 설정 파일 경로로 설정을 재초기화한다."""
    global _raw, _yaml_path, _properties_path
    if yaml_path:
        _yaml_path = Path(yaml_path)
    if properties_path:
        _properties_path = Path(properties_path)
    _raw = load_config(
        config_dir=_CONFIG_DIR,
        yaml_path=yaml_path,
        properties_path=properties_path,
    )
    settings.__init__()


settings = Settings()
