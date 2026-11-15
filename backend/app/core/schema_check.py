# SPDX-License-Identifier: Apache-2.0
"""DB 스키마가 서버 버전과 맞는지 확인한다 — 서버가 쓰는 표·컬럼이 DB 에 있는지.

스키마는 SQL DDL(``packaging/config/argus-catalog-*.sql``)이 전담하고 서버는 표를 만들지 않는다.
그래서 패키지만 올리고 DDL 을 다시 적용하지 않으면, 새 버전에서 생긴 표·컬럼이 없는 채로 서버가 뜨고
그 표를 쓰는 화면만 500 으로 실패한다(원인은 서버 로그의 SQL 오류에만 남는다).

- 기동할 때 ``warn_schema_drift`` 가 모델(``Base.metadata``)과 DB 를 비교해 없는 표·컬럼을 WARNING 으로 알린다.
  기동은 막지 않는다 — 없는 표와 무관한 기능은 그대로 쓸 수 있어야 한다.
- 요청 중에 "표·컬럼 없음" 오류가 나면 ``is_schema_mismatch`` 로 구분해 사용자에게 원인을 알린다(``app/main.py``).
- 운영자가 직접 확인: ``python -m app.core.schema_check`` (없는 것이 있으면 종료 코드 1).

DB 에만 있는 표·컬럼(이전 버전의 흔적, 고객이 추가한 것)은 문제 삼지 않는다. 컬럼 타입·인덱스도 보지 않는다.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field

from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# 표·컬럼이 없을 때의 오류 코드 — PostgreSQL SQLSTATE, MariaDB/MySQL 오류 번호
_PG_MISSING = {"42P01", "42703"}  # undefined_table, undefined_column
_MYSQL_MISSING = {1146, 1054}  # ER_NO_SUCH_TABLE, ER_BAD_FIELD_ERROR

MISMATCH_MESSAGE = (
    "DB 스키마가 서버 버전과 맞지 않습니다(표 또는 컬럼이 없음). "
    "관리자에게 새 버전 스키마 DDL 적용을 요청하세요."
)


@dataclass
class SchemaDrift:
    """모델에는 있는데 DB 에 없는 것."""

    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)  # 표 → 컬럼들

    def __bool__(self) -> bool:
        return bool(self.missing_tables or self.missing_columns)

    def describe(self) -> list[str]:
        """사람이 읽을 줄 단위 설명."""
        lines = []
        if self.missing_tables:
            lines.append(f"없는 표 {len(self.missing_tables)}개: {', '.join(self.missing_tables)}")
        if self.missing_columns:
            cols = [f"{t}.{c}" for t, cs in self.missing_columns.items() for c in cs]
            lines.append(f"없는 컬럼 {len(cols)}개: {', '.join(cols)}")
        return lines


def find_drift(conn: Connection, metadata: MetaData) -> SchemaDrift:
    """``metadata`` 의 표·컬럼 가운데 DB(접속 계정의 기본 스키마)에 없는 것을 찾는다. 이름은 대소문자를 가리지 않는다."""
    inspector = inspect(conn)
    with warnings.catch_warnings():
        # 이름만 본다 — SQLAlchemy 가 모르는 타입(pgvector 의 vector 등) 경고는 여기서 의미가 없다
        warnings.simplefilter("ignore", SAWarning)
        reflected = inspector.get_multi_columns()
    existing = {table.lower(): {c["name"].lower() for c in columns} for (_schema, table), columns in reflected.items()}
    drift = SchemaDrift()
    for table in sorted(metadata.tables.values(), key=lambda t: t.name):
        columns = existing.get(table.name.lower())
        if columns is None:
            drift.missing_tables.append(table.name)
            continue
        missing = [c.name for c in table.columns if c.name.lower() not in columns]
        if missing:
            drift.missing_columns[table.name] = missing
    return drift


async def check_schema(engine: AsyncEngine, metadata: MetaData) -> SchemaDrift:
    """비동기 엔진으로 ``find_drift`` 를 실행한다."""
    async with engine.connect() as conn:
        return await conn.run_sync(find_drift, metadata)


def ddl_file_name(db_type: str) -> str:
    """DB 종류에 맞는 DDL 파일 이름."""
    return "argus-catalog-postgresql.sql" if db_type.lower() == "postgresql" else "argus-catalog-mariadb.sql"


async def warn_schema_drift(engine: AsyncEngine, metadata: MetaData, db_type: str) -> SchemaDrift | None:
    """기동 때 부른다 — 없는 표·컬럼을 WARNING 으로 남긴다. 확인 자체가 실패해도 기동은 계속한다."""
    try:
        drift = await check_schema(engine, metadata)
    except Exception as e:  # noqa: BLE001 — 점검은 보조 기능, 기동을 막지 않는다
        logger.warning("DB 스키마 확인을 건너뜀: %r", e)
        return None
    if not drift:
        logger.info("DB 스키마 확인 완료 — 서버가 쓰는 표 %d개가 모두 있음", len(metadata.tables))
        return drift
    for line in drift.describe():
        logger.warning("DB 스키마가 서버 버전과 다름 — %s", line)
    logger.warning(
        "위 표·컬럼을 쓰는 기능은 오류가 납니다. 새 버전 스키마 DDL(%s)을 DB 에 적용한 뒤 서버를 다시 시작하세요 "
        "(DDL 은 이미 있는 표·컬럼을 건너뛰므로 다시 적용해도 됩니다).",
        ddl_file_name(db_type),
    )
    return drift


def is_schema_mismatch(exc: BaseException) -> bool:
    """SQLAlchemy ``DBAPIError`` 가 "표·컬럼 없음" 오류인지."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code in _PG_MISSING:
        return True
    args = getattr(orig, "args", ())
    return bool(args) and args[0] in _MYSQL_MISSING


async def _main() -> int:
    """서버 설정의 DB 를 점검해 결과를 출력한다. 설정은 서버 기본 위치(``ARGUS_CATALOG_SERVER_CONFIG_DIR``)에서 읽는다."""
    import importlib
    import pkgutil

    import app
    from app.core.config import settings
    from app.core.database import Base, engine

    # 모델 모듈을 모두 불러 Base.metadata 를 채운다 (서버는 라우터 import 로 채운다)
    for mod in pkgutil.walk_packages(app.__path__, "app."):
        if mod.name.rsplit(".", 1)[-1] == "models":
            importlib.import_module(mod.name)
    try:
        drift = await check_schema(engine, Base.metadata)
    finally:
        await engine.dispose()
    target = f"{settings.db_type}://{settings.db_host}:{settings.db_port}/{settings.db_name}"
    if not drift:
        print(f"[{target}] 서버가 쓰는 표 {len(Base.metadata.tables)}개가 모두 있습니다.")
        return 0
    print(f"[{target}] DB 스키마가 서버 버전과 다릅니다.")
    for line in drift.describe():
        print(f"  - {line}")
    print(f"새 버전 스키마 DDL({ddl_file_name(settings.db_type)})을 적용하세요.")
    return 1


if __name__ == "__main__":
    import asyncio
    import sys

    sys.exit(asyncio.run(_main()))
