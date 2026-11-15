# SPDX-License-Identifier: Apache-2.0
"""DB 스키마 점검(``app.core.schema_check``) — 없는 표·컬럼 찾기, 기동 경고, 요청 오류 안내."""

import logging

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.schema_check import (
    MISMATCH_MESSAGE,
    SchemaDrift,
    check_schema,
    is_schema_mismatch,
    warn_schema_drift,
)


def _model() -> MetaData:
    md = MetaData()
    Table("t_keep", md, Column("id", Integer, primary_key=True), Column("name", String(50)))
    Table("t_new", md, Column("id", Integer, primary_key=True))
    return md


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


async def test_no_drift_when_db_matches(engine):
    md = _model()
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)
    drift = await check_schema(engine, md)
    assert not drift
    assert drift.describe() == []


async def test_finds_missing_table_and_column(engine):
    async with engine.begin() as conn:
        # t_new 없음, t_keep 에 name 없음, 모델에 없는 표·컬럼은 있어도 무시
        await conn.execute(text("CREATE TABLE T_KEEP (ID INTEGER PRIMARY KEY, legacy TEXT)"))
        await conn.execute(text("CREATE TABLE customer_extra (id INTEGER)"))
    drift = await check_schema(engine, _model())
    assert drift.missing_tables == ["t_new"]
    assert drift.missing_columns == {"t_keep": ["name"]}
    assert drift.describe() == ["없는 표 1개: t_new", "없는 컬럼 1개: t_keep.name"]


async def test_warn_logs_drift_and_ddl_file(engine, caplog):
    with caplog.at_level(logging.INFO, logger="app.core.schema_check"):
        drift = await warn_schema_drift(engine, _model(), "postgresql")
    assert drift and drift.missing_tables == ["t_keep", "t_new"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("t_keep, t_new" in m for m in warnings)
    assert any("argus-catalog-postgresql.sql" in m for m in warnings)


async def test_warn_quiet_when_ok_and_mariadb_file_name(engine, caplog):
    md = _model()
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)
    with caplog.at_level(logging.INFO, logger="app.core.schema_check"):
        assert not await warn_schema_drift(engine, md, "mariadb")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_warn_never_raises():
    """DB 에 닿지 못해도 기동을 막지 않는다."""
    eng = create_async_engine("sqlite+aiosqlite:////nonexistent-dir/x.db")
    try:
        assert await warn_schema_drift(eng, _model(), "postgresql") is None
    finally:
        await eng.dispose()


class _DbError(Exception):
    def __init__(self, *args, sqlstate=None):
        super().__init__(*args)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("orig", "expected"),
    [
        (_DbError("relation does not exist", sqlstate="42P01"), True),
        (_DbError("column does not exist", sqlstate="42703"), True),
        (_DbError("syntax error", sqlstate="42601"), False),
        (_DbError(1146, "Table 'x.y' doesn't exist"), True),
        (_DbError(1054, "Unknown column"), True),
        (_DbError(2003, "Can't connect"), False),
        (_DbError(), False),
    ],
)
def test_is_schema_mismatch(orig, expected):
    assert is_schema_mismatch(ProgrammingError("SELECT 1", {}, orig)) is expected


def test_is_schema_mismatch_without_orig():
    assert is_schema_mismatch(RuntimeError("x")) is False
    assert not SchemaDrift()


async def test_handler_explains_missing_table():
    """표가 없어 난 오류는 원인을 담은 500, 그 밖의 DB 오류는 그대로 올린다."""
    from starlette.requests import Request

    from app.main import schema_mismatch_handler

    request = Request({"type": "http", "method": "GET", "path": "/api/v1/api-keys", "headers": []})
    resp = await schema_mismatch_handler(request, ProgrammingError("SELECT", {}, _DbError("x", sqlstate="42P01")))
    assert resp.status_code == 500
    assert MISMATCH_MESSAGE.encode() in resp.body
    other = OperationalError("SELECT", {}, _DbError(2003, "Can't connect"))
    with pytest.raises(OperationalError):
        await schema_mismatch_handler(request, other)
