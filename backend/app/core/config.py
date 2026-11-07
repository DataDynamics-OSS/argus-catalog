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
    """``section.key`` 값을 읽는다(없으면 default). YAML 의 점 포함 키(예: service.url)도 그대로 쓴다."""
    return _raw.get(section, {}).get(key, default)


def _get_nested(section: str, subsection: str, key: str, default=None):
    """``section.subsection.key`` 중첩 값을 읽는다(없으면 default)."""
    return _raw.get(section, {}).get(subsection, {}).get(key, default)


def _assistant_service_url() -> str:
    """AI 어시스턴트 서비스 URL — assistant.service.url 우선, 없으면 예전 키 assistant.agent.url(경고)."""
    url = str(_get("assistant", "service.url", "") or "").strip()
    if url:
        return url
    legacy = str(_get("assistant", "agent.url", "") or "").strip()
    if legacy:
        import logging
        logging.getLogger(__name__).warning(
            "설정 키 assistant.agent.url 은 곧 없어집니다 — assistant.service.url 로 바꾸세요")
    return legacy


def _to_bool(value) -> bool:
    """설정 값을 bool 로 — 문자열은 true/1/yes(대소문자 무시)만 True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _csv_list(value) -> list[str]:
    """쉼표 구분 문자열(또는 list)을 공백 제거된 문자열 리스트로 변환한다."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


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
        # 외부용 주소 — 사용자 브라우저·SDK·CLI 에 주는 presigned URL 의 호스트. 비우면 endpoint 를 쓴다.
        # (서버는 http://minio:9000 처럼 내부 이름으로 붙고, 밖에서는 다른 주소로 닿는 배포용)
        self.os_public_endpoint: str = _get("object_storage", "public_endpoint", "")
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
        # metadata-sync API 토큰 — 확장에 server.api_token 을 설정했으면 같은 값을 넣는다(프록시 호출용)
        self.metadata_sync_api_token: str = _get("metadata_sync", "api_token", "") or ""

        # 사용자 지정 URL 로의 서버측 호출(API 스펙 URL·Try-it) SSRF 정책 — app.core.outbound
        # 루프백·링크로컬(메타데이터)·카탈로그 인프라는 항상 차단. 사설망은 기본 허용(사내 API).
        self.api_outbound_block_private: bool = _to_bool(_get("api_outbound", "block_private", False))
        # 우선 허용(호스트명/IP/CIDR, 쉼표) — 다른 차단 규칙보다 우선
        self.api_outbound_allowed_hosts: list[str] = _csv_list(_get("api_outbound", "allowed_hosts", ""))
        # 추가 차단(호스트명/IP/CIDR, 쉼표)
        self.api_outbound_denied_hosts: list[str] = _csv_list(_get("api_outbound", "denied_hosts", ""))
        # API Try-it 호출 기록(관측 탭) 보관 기간(일). 0 이하면 지우지 않음.
        self.api_invocation_retention_days: int = int(_get("api_observability", "retention_days", 180))

        # 외부 API 캐시
        self.cache_max_size: int = int(_get_nested("external", "cache", "max_size", 1000))
        self.cache_ttl_seconds: int = int(_get_nested("external", "cache", "ttl_seconds", 300))
        self.cache_enabled: bool = _to_bool(_get_nested("external", "cache", "enabled", True))

        # AI 어시스턴트 (argus-catalog-ai-service 의 assistant 서버) — 설정 시 채팅을 프록시.
        # 키는 assistant.service.url. 이전 이름 assistant.agent.url 은 한 버전 동안 읽는다.
        self.assistant_agent_url: str = _assistant_service_url()

        # Temporal (변경 관리 결재/통지 워크플로우 엔진)
        # enabled=false 면 lifespan 에서 연결을 건너뛰고, CR submit 은 503 으로 거부된다.
        self.temporal_enabled: bool = _to_bool(_get("temporal", "enabled", True))
        self.temporal_target: str = _get("temporal", "target", "localhost:7233")
        self.temporal_namespace: str = _get("temporal", "namespace", "default")
        self.temporal_task_queue: str = _get("temporal", "task_queue", "change-mgmt")

        # 카탈로그 페더레이션
        # export 서비스 토큰 — peer 가 이 인스턴스의 /federation/export/* 를 호출할 때
        # 검증할 Bearer 토큰. 환경변수(ARGUS_FEDERATION_TOKEN) 우선, 없으면 config,
        # 둘 다 없으면 빈 값(인증 비강제 — 개발용, /external 과 동일 정책).
        self.federation_export_token: str = (
            os.environ.get("ARGUS_FEDERATION_TOKEN")
            or _get("federation", "export_token", "")
        )
        # export 토큰 미설정(인증 비강제) 상태에서 실데이터 샘플까지 노출할지. 기본 false —
        # 토큰 없이 운영하면 메타데이터만 노출되고 sample capability 는 자동으로 빠진다.
        self.federation_export_allow_anonymous_sample: bool = _to_bool(
            _get("federation", "export_allow_anonymous_sample", False)
        )
        # HARVEST 스케줄러 — HARVEST/HYBRID peer 의 메타데이터를 주기적으로 pull.
        self.federation_harvest_enabled: bool = _to_bool(
            _get("federation", "harvest_enabled", True)
        )
        self.federation_harvest_tick_seconds: int = int(
            _get("federation", "harvest_tick_seconds", 300)
        )
        # HARVEST 시 미러 데이터셋의 샘플 데이터도 받아 로컬에 저장할지(소비자가 sample 선택 +
        # peer 가 노출 시). 저장 경로는 로컬 데이터셋과 분리(federation/samples).
        self.federation_harvest_samples: bool = _to_bool(
            _get("federation", "harvest_samples", True)
        )
        # 주기적 전체 동기화 간격(초) — 증분은 삭제·비노출 전환을 감지하지 못하므로, 마지막
        # 성공한 전체 동기화 후 이 간격이 지나면 자동으로 전체 동기화(prune)로 승격한다. 0=비활성.
        self.federation_full_sync_interval_seconds: int = int(
            _get("federation", "full_sync_interval_seconds", 86400)
        )
        # 증분 watermark 겹침(초) — peer 시계 오차·실행 중 수정 누락을 줄이기 위해 기준 시각을
        # 이만큼 앞당겨 조회한다(재수신은 upsert 라 무해).
        self.federation_watermark_overlap_seconds: int = int(
            _get("federation", "watermark_overlap_seconds", 300)
        )
        # RUNNING 실행이 이 시간(초)보다 오래되면 비정상 종료로 보고 FAILED 처리한다
        # (프로세스 재시작 등으로 남은 고아 RUNNING 행이 이후 실행을 막지 않도록).
        self.federation_stale_run_seconds: int = int(
            _get("federation", "stale_run_seconds", 7200)
        )
        # 미러 샘플 저장 시 받아올 최대 행 수.
        self.federation_sample_limit: int = int(
            _get("federation", "sample_limit", 100)
        )
        # Export visibility 거버넌스 — 이 인스턴스가 peer 에게 노출하는 데이터셋 범위를 제한.
        # 검색/목록/드릴다운 모두에 적용된다. (이 인스턴스가 노출자일 때의 정책)
        self.federation_export_exclude_pii: bool = _to_bool(
            _get("federation", "export_exclude_pii", False)
        )
        # 노출 제외할 민감도 등급(쉼표) — 예: "RESTRICTED,CONFIDENTIAL"
        self.federation_export_exclude_sensitivity: list[str] = _csv_list(
            _get("federation", "export_exclude_sensitivity", "")
        )
        # 노출 허용 데이터소스 이름(쉼표) — 비어 있으면 전체 허용
        self.federation_export_datasource_allowlist: list[str] = _csv_list(
            _get("federation", "export_datasource_allowlist", "")
        )
        # 이 인스턴스의 외부 공개 base URL(peer 가 호출하는 주소). 설정하면 같은 URL 을
        # peer 로 등록/수정하는 자기 자신 등록을 거부한다. 비어 있으면(기본) 검사 생략.
        self.federation_public_base_url: str = (
            _get("federation", "public_base_url", "") or ""
        ).strip().rstrip("/")
        # Circuit breaker — LIVE peer 호출(검색 fan-out·드릴다운) 내결함성.
        # 연속 실패가 threshold 에 도달하면 cooldown 동안 회로를 열어 빠르게 실패한다.
        self.federation_breaker_threshold: int = int(
            _get("federation", "breaker_threshold", 3)
        )
        self.federation_breaker_cooldown_seconds: int = int(
            _get("federation", "breaker_cooldown_seconds", 60)
        )

        # 플러그인 — 고객사별 커스텀 화면/API 확장. importlib entry_points 그룹
        # "argus.plugins" 에 등록된 패키지 중, 이 목록에 이름이 포함된 것만 로드한다
        # (명시 허용). 빈 목록(기본)이면 설치돼 있어도 아무 것도 로드하지 않는다.
        # "*" 를 주면 발견된 플러그인을 모두 로드한다(개발용). 코어 동작에는 영향이 없다.
        self.enabled_plugins: list[str] = _csv_list(_get("plugins", "enabled", []))


def init_settings(
    yaml_path: str | None = None,
    properties_path: str | None = None,
) -> None:
    """사용자 지정 설정 파일 경로로 설정을 재초기화한다.

    전역 ``settings`` 객체를 같은 인스턴스로 다시 초기화하므로, 이미 import 해 둔 참조에도 반영된다.
    (단, 모듈 로드 시점에 값을 복사해 둔 곳 — 예: DB 엔진 — 에는 반영되지 않는다.)
    """
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
