# SPDX-License-Identifier: Apache-2.0
"""데이터베이스 연결 및 세션 관리.

SQLAlchemy 비동기 엔진을 통해 PostgreSQL 과 MariaDB/MySQL 을 지원한다.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _build_database_url() -> str:
    db_type = settings.db_type.lower()
    host = settings.db_host
    port = settings.db_port
    name = settings.db_name
    user = settings.db_username
    password = settings.db_password

    if db_type == "postgresql":
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"
    elif db_type in ("mariadb", "mysql"):
        return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"
    else:
        raise ValueError(f"Unsupported database type: {db_type}. Use 'postgresql' or 'mariadb'.")


engine = create_async_engine(
    _build_database_url(),
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    echo=settings.db_echo,
)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_database() -> None:
    db_url = _build_database_url()
    masked = db_url
    if settings.db_password:
        masked = db_url.replace(f":{settings.db_password}@", ":****@")
    logger.info("데이터베이스 연결: %s", masked)

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("데이터베이스 연결 확인 완료")


async def migrate_platform_to_datasource() -> None:
    """레거시 platform→datasource 마이그레이션 훅 (현재 no-op).

    스키마 생성·변경은 SQL DDL(``packaging/config/argus-catalog-*.sql``)이 전담한다.
    런타임에서 스키마를 건드리던 로직은 제거했으며, 향후 스키마 관련 후처리가
    필요해지면 이 자리에 추가한다. 기동 시퀀스(``app/main.py``)는 이 훅을 그대로 호출한다.
    """
    return


async def reconcile_schema() -> None:
    """스키마 보정 훅 (현재 no-op).

    ``create_all`` 이 메우지 못하던 컬럼/제약 보정을 런타임에 수행하던 함수였으나,
    스키마 일원화 정책에 따라 비웠다. 스키마는 SQL DDL 에서만 관리한다.
    향후 버전업으로 스키마 후처리가 필요하면 이 자리에 idempotent 하게 추가한다.
    """
    return


async def close_database() -> None:
    await engine.dispose()
    logger.info("데이터베이스 연결 풀 종료")
