"""카탈로그 서버 - FastAPI 애플리케이션 진입점."""

import argparse
import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agents.router import router as agents_router
from app.ai.router import router as ai_router
from app.alert.router import router as alert_router
from app.apis.router import router as apis_router
from app.auth.router import router as auth_router  # SSO 인증용 추가
from app.catalog.impala_profile_router import router as impala_profile_router
from app.catalog.router import router as catalog_router
from app.catalog.taxonomy_router import router as taxonomy_router
from app.catalog.topology_router import router as topology_router
from app.change_mgmt.router import router as change_mgmt_router
from app.comments.router import router as comments_router
from app.core.config import settings
from app.core.database import (
    close_database,
    init_database,
    migrate_platform_to_datasource,
    reconcile_schema,
)
from app.core.logging import setup_logging
from app.core.password_gate import PasswordChangeGateMiddleware
from app.core.security import SecurityHeadersMiddleware
from app.external.router import router as external_router
from app.filesystemmgr.router import router as filesystem_router
from app.models.router import router as models_router
from app.models.store_router import router as model_store_router
from app.models.uc_compat import router as uc_compat_router
from app.oci_hub.router import router as oci_hub_router
from app.quality.router import router as quality_router
from app.search.router import router as search_router
from app.settings.router import router as settings_router
from app.standard.router import router as standard_router
from app.usermgr.router import router as usermgr_router

logger = logging.getLogger(__name__)
_start_time: float = 0.0

BANNER = r"""
_______                                  _________      _____       ______                  ________
___    |_____________ ____  _________    __  ____/_____ __  /______ ___  /____________ _    __  ___/______________   ______________
__  /| |_  ___/_  __ `/  / / /_  ___/    _  /    _  __ `/  __/  __ `/_  /_  __ \_  __ `/    _____ \_  _ \_  ___/_ | / /  _ \_  ___/
_  ___ |  /   _  /_/ // /_/ /_(__  )     / /___  / /_/ // /_ / /_/ /_  / / /_/ /  /_/ /     ____/ //  __/  /   __ |/ //  __/  /
/_/  |_/_/    _\__, / \__,_/ /____/      \____/  \__,_/ \__/ \__,_/ /_/  \____/_\__, /      /____/ \___//_/    _____/ \___//_/
              /____/                                                           /____/
"""


def _print_banner() -> None:
    logger.info(BANNER)
    # 로고 아래 버전(단일 소스 app.__version__) — 아래 설정 라인들과 정렬된 라벨 형식.
    logger.info("버전              : %s", settings.app_version)
    logger.info("설정 YAML         : %s", settings.config_yaml_path)
    logger.info("설정 Properties   : %s", settings.config_properties_path)
    logger.info("데이터 디렉터리   : %s", settings.data_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    setup_logging()
    _print_banner()
    logger.info("카탈로그 서버 %s 기동 중", __version__)
    await init_database()

    import app.agents.models  # noqa: F401
    import app.ai.models  # noqa: F401
    import app.catalog.models  # noqa: F401
    import app.change_mgmt.models  # noqa: F401
    import app.comments.models  # noqa: F401
    import app.embedding.models  # noqa: F401
    import app.models.models  # noqa: F401
    import app.oci_hub.models  # noqa: F401
    import app.permissions.models  # noqa: F401
    import app.settings.models  # noqa: F401
    import app.usermgr.models  # noqa: F401

    # 스키마 생성·변경은 SQL DDL(packaging/config/argus-catalog-*.sql)이 전담한다.
    # 아래 두 훅은 향후 스키마 후처리를 위해 비워둔 자리(현재 no-op)다.
    await migrate_platform_to_datasource()
    await reconcile_schema()
    logger.info("스키마는 SQL DDL 로 관리됨 (런타임 스키마 훅은 no-op)")

    # 기본 데이터 시드(seed)
    from app.catalog.service import seed_datasource_metadata
    from app.core.database import async_session
    from app.settings.service import (
        load_auth_settings,
        load_cors_settings,
        load_embedding_settings,
        load_llm_settings,
        load_os_settings,
        seed_configuration,
    )
    from app.usermgr.service import seed_admin_user, seed_roles

    async with async_session() as session:
        await seed_datasource_metadata(session)
        await seed_roles(session)
        await seed_configuration(session)
        await load_os_settings(session)
        await load_embedding_settings(session)
        await load_llm_settings(session)
        await load_auth_settings(session)
        await load_cors_settings(session)
        await seed_admin_user(session)

    # S3 버킷 존재 보장
    try:
        from app.core.s3 import ensure_bucket
        await ensure_bucket()
        logger.info("S3 model-artifacts 버킷 확인 완료")
    except Exception as e:
        logger.warning("S3 버킷 확인 건너뜀 (MinIO 미가용 가능): %s", e)

    # 변경관리 워크플로우용 Temporal 클라이언트 연결 (선택 — 비활성/미가용이면 건너뜀)
    if settings.temporal_enabled:
        try:
            from app.change_mgmt import temporal_client
            await temporal_client.connect()
            logger.info("변경관리용 Temporal 클라이언트 연결됨")
        except Exception as e:
            logger.warning("Temporal 연결 건너뜀 (서버 미가용 가능): %s", e)
    else:
        logger.info("Temporal 비활성화됨 (temporal.enabled=false) — 변경관리 submit 사용 불가")

    # 권한 기본값 — 관리 메뉴(users/settings/permissions)는 admin 전용으로 출고
    from app.core.database import async_session as _ps
    from app.permissions.router import seed_default_permissions
    async with _ps() as _s:
        await seed_default_permissions(_s)

    # 품질 검증 주기 스케줄러 (argus.quality_schedule property 기반)
    from app.quality.scheduler import quality_scheduler_loop
    quality_task = asyncio.create_task(quality_scheduler_loop())

    yield
    quality_task.cancel()
    from app.ai.registry import shutdown_provider as shutdown_llm
    from app.embedding.registry import shutdown_provider as shutdown_embedding
    await shutdown_embedding()
    await shutdown_llm()
    await close_database()
    logger.info("카탈로그 서버 종료 중")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
# 강제 비밀번호 변경 게이트(로컬 모드) — 화이트리스트 외 API 를 차단
app.add_middleware(PasswordChangeGateMiddleware)


class DynamicCORSMiddleware:
    """요청 시점에 settings 에서 허용 origin 을 읽는 CORS 미들웨어.

    settings.cors_origins 가 변경된 경우에만 내부 CORSMiddleware 를 재생성한다.
    """

    def __init__(self, app):
        self.app = app
        self._inner = None
        self._origins_snapshot = None

    def _get_inner(self):
        current = tuple(settings.cors_origins)
        if current != self._origins_snapshot:
            self._inner = CORSMiddleware(
                app=self.app,
                allow_origins=list(current),
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            self._origins_snapshot = current
        return self._inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self._get_inner()(scope, receive, send)


app.add_middleware(DynamicCORSMiddleware)

from app.permissions.router import router as permissions_router

app.include_router(permissions_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(apis_router, prefix="/api/v1")
app.include_router(topology_router, prefix="/api/v1")
app.include_router(taxonomy_router, prefix="/api/v1")
app.include_router(comments_router, prefix="/api/v1")
app.include_router(filesystem_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
app.include_router(model_store_router, prefix="/api/v1")
app.include_router(oci_hub_router, prefix="/api/v1")
app.include_router(uc_compat_router)  # /api/2.0/mlflow/unity-catalog (추가 prefix 없음)
app.include_router(search_router, prefix="/api/v1")
app.include_router(quality_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(standard_router, prefix="/api/v1")
app.include_router(usermgr_router, prefix="/api/v1")
app.include_router(ai_router, prefix="/api/v1")
app.include_router(alert_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")  # SSO 인증용 추가
app.include_router(external_router, prefix="/api/v1")
app.include_router(impala_profile_router, prefix="/api/v1")
app.include_router(change_mgmt_router, prefix="/api/v1")


@app.get("/health")
async def health():
    """헬스 체크 엔드포인트."""
    uptime_seconds = int(time.monotonic() - _start_time)
    return {
        "status": "ok",
        "service": "argus-catalog-server",
        "uptime": uptime_seconds,
        "version": __version__,
    }


def run() -> None:
    parser = argparse.ArgumentParser(
        prog="argus-catalog-server",
        description="Catalog Server - Data catalog management server for Argus platform.",
    )
    parser.add_argument("--config-yaml", metavar="PATH", help="Path to YAML config file")
    parser.add_argument("--config-properties", metavar="PATH", help="Path to properties file")
    args = parser.parse_args()

    if args.config_yaml or args.config_properties:
        from app.core.config import init_settings
        init_settings(
            yaml_path=args.config_yaml,
            properties_path=args.config_properties,
        )
    else:
        yaml_exists = settings.config_yaml_path.is_file()
        props_exists = settings.config_properties_path.is_file()
        if not yaml_exists and not props_exists:
            parser.print_help()
            print()
            print(
                f"Error: No configuration files found at default location:\n"
                f"  - {settings.config_yaml_path}\n"
                f"  - {settings.config_properties_path}\n"
            )
            sys.exit(1)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
