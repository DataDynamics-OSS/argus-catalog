# SPDX-License-Identifier: Apache-2.0
"""원천 직접 진단 — 실제 DB 네 가지(PostgreSQL · MySQL · Oracle · SQL Server)에서 같은 데이터의 판정이 같은지 (TODO-095).

같은 시드 데이터(주문 300행 — NULL · 중복 · 날짜 빈틈 · 한글 · 대소문자 · 앞뒤 공백 · 깨진 글자 · 더미 값)를 DB 마다
넣고, 카탈로그가 만든 실행 계획(``execution.compile_snapshot`` · ``plan_steps`` — 원천 방언의 집계 · 조인 · 읽기)을
**워커 코드**(``quality_worker.runner.Runner`` 의 직접 단계 · 프로파일 단계 · 위반 샘플, ``quality_worker.direct`` 의 실제
드라이버 — asyncpg · aiomysql · python-oracledb thin · pymssql)로 실행한 뒤 카탈로그의 판정(``execution._judge``)을 비교한다.

  - ``test_rules_match_python_expectation`` — 규칙마다 진단 건수 · 위반 건수 · 실제값을 시드 데이터에서 Python 으로 따로 센
    기대값과 비교한다(DB 마다).
  - ``test_rules_match_across_dialects`` — 있는 DB 가 둘 이상이면 규칙마다 판정(통과 · 실제값 · 건수)이 모두 같은지.
  - ``test_profile_matches`` — 프로파일(행 수 · NULL · 고유값 · 최소 · 최대 · 평균 · 최빈값)이 기대값 · 다른 DB 와 같은지.
  - ``test_violation_samples`` — 위반 샘플(읽는 동안 모은 것 · 이어서 낸 샘플 쿼리)이 위반 행인지.

실제 DB 가 있어야 해서 평소에는 건너뛴다. 접속 정보(URL)를 하나 이상 주면 그 DB 만 돈다 — 테이블 ``dqv_orders`` ·
``dqv_customer`` 를 **지우고 다시 만든다**(검증용 빈 DB · 계정만 쓰세요)::

    ARGUS_TEST_PG_DSN=postgresql://dqv:DqvPass123%21@127.0.0.1:46743/dqv
    ARGUS_TEST_MYSQL_DSN=mysql://dqv:DqvPass123%21@127.0.0.1:46746/dqv
    ARGUS_TEST_ORACLE_DSN=oracle://dqv:DqvPass123@127.0.0.1:46721/FREEPDB1      # 경로 = Service Name
    ARGUS_TEST_MSSQL_DSN=mssql://sa:DqvPass123%21@127.0.0.1:46733/dqv          # 경로 = 데이터베이스(없으면 만든다)

비밀번호의 특수 문자는 URL 인코딩한다(``!`` → ``%21``).

임시 컨테이너를 띄우고 돌리는 방법은 ``backend/scripts/quality-direct-dialects.sh`` (python-oracledb · pymssql 이 필요하다 —
``pip install oracledb pymssql``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import random
import re
import sys
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from app.quality import execution, profiling, rule_direct, rule_sql, runs

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker.config import Settings  # noqa: E402
from quality_worker.runner import Runner, _JobState  # noqa: E402

_ENV = {"postgresql": "ARGUS_TEST_PG_DSN", "mysql": "ARGUS_TEST_MYSQL_DSN",
        "oracle": "ARGUS_TEST_ORACLE_DSN", "mssql": "ARGUS_TEST_MSSQL_DSN"}
DIALECTS = [d for d, env in _ENV.items() if os.environ.get(env)]
pytestmark = pytest.mark.skipif(
    not DIALECTS, reason="ARGUS_TEST_{PG,MYSQL,ORACLE,MSSQL}_DSN 이 없음 — 실제 원본 DB 가 필요(scripts/quality-direct-dialects.sh)")

# ---------------------------------------------------------------------------
# 시드 데이터 — 모든 DB 에 같은 값
# ---------------------------------------------------------------------------

N_ROWS = 300
N_CUSTOMERS = 60
STATUSES = ["PAID", "PAID", "PAID", "SHIP", "CANCEL", "REFUND"]
# 대소문자만 다른 값 · 앞뒤 공백 · 깨진 한글(자모) · 더미 값 · 제어 문자(탭)
NAMES = ["홍길동", "김철수", "이영희", "박민수", "최지우", "Hong", "hong", " 이영희", "최지우 ", "ㄱㄴㄷ", "N/A", "A\tB", "정우성"]
BASE_DAY = dt.date(2024, 1, 1)
# 날짜 빈틈 — 하루 · 사흘 · 한 주 넘게(주 단위 빈틈도 생기게)
GAP_DAYS = {10, 11, 12, 40, 41, 42, 43, 44, 45, 46, 47, 48, 100}
DAY_OFFSETS = [d for d in range(0, 151) if d not in GAP_DAYS]

COLUMNS = ["id", "cust_id", "status", "name", "code", "reg_ymd", "amt", "vat", "total", "qty",
           "order_date", "ship_date", "paid_at"]

# 컬럼 타입 — 카탈로그가 수집하는 native_type 의 모양(Oracle · SQL Server 는 metadata-sync 의 표기)
TYPES = {
    "postgresql": {"id": "integer", "cust_id": "integer", "status": "varchar(10)", "name": "varchar(50)",
                   "code": "char(5)", "reg_ymd": "varchar(10)", "amt": "numeric(12,2)", "vat": "numeric(12,2)",
                   "total": "numeric(12,2)", "qty": "integer", "order_date": "date", "ship_date": "date",
                   "paid_at": "timestamp"},
    "mysql": {"id": "int", "cust_id": "int", "status": "varchar(10)", "name": "varchar(50)", "code": "char(5)",
              "reg_ymd": "varchar(10)", "amt": "decimal(12,2)", "vat": "decimal(12,2)", "total": "decimal(12,2)",
              "qty": "int", "order_date": "date", "ship_date": "date", "paid_at": "datetime"},
    "oracle": {"id": "NUMBER(10)", "cust_id": "NUMBER(10)", "status": "VARCHAR2(10)", "name": "NVARCHAR2(50)",
               "code": "CHAR(5)", "reg_ymd": "VARCHAR2(10)", "amt": "NUMBER(12,2)", "vat": "NUMBER(12,2)",
               "total": "NUMBER(12,2)", "qty": "NUMBER(10)", "order_date": "DATE", "ship_date": "DATE",
               "paid_at": "TIMESTAMP(6)"},
    "mssql": {"id": "INT", "cust_id": "INT", "status": "VARCHAR(10)", "name": "NVARCHAR(50)", "code": "CHAR(5)",
              "reg_ymd": "VARCHAR(10)", "amt": "DECIMAL(12,2)", "vat": "DECIMAL(12,2)", "total": "DECIMAL(12,2)",
              "qty": "INT", "order_date": "DATE", "ship_date": "DATE", "paid_at": "DATETIME2"},
}
_CUSTOMER_TYPES = {"postgresql": ("integer", "varchar(10)"), "mysql": ("int", "varchar(10)"),
                   "oracle": ("NUMBER(10)", "VARCHAR2(10)"), "mssql": ("INT", "VARCHAR(10)")}


def seed_rows() -> list[dict]:
    rng = random.Random(20261115)
    rows = []
    for i in range(1, N_ROWS + 1):
        day = BASE_DAY + dt.timedelta(days=rng.choice(DAY_OFFSETS))
        status = None if i % 29 == 0 else "paid" if i % 41 == 0 else "XX" if i % 53 == 0 else rng.choice(STATUSES)
        amt = Decimal(rng.randint(100_000, 5_000_000)) / 100
        vat = None if i % 27 == 0 else (amt * Decimal("0.1")).quantize(Decimal("0.01"))
        total = amt + (vat or 0) + (1 if i % 13 == 0 else 0)
        code = (None if i % 47 == 0 else "B12" if i % 17 == 0 else "a1234" if i % 43 == 0
                else f"{rng.choice('ABC')}{rng.randint(0, 9999):04d}")
        reg = (None if i % 59 == 0 else "20240231" if i % 61 == 0 else day.isoformat() if i % 19 == 0
               else day.strftime("%Y%m%d"))
        paid = None
        if status in ("PAID", "paid") and i % 7 != 0:
            paid = dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        rows.append({
            "id": i,
            "cust_id": None if i % 23 == 0 else 900 + i % 3 if i % 37 == 0 else rng.randint(1, N_CUSTOMERS),
            "status": status,
            "name": None if i % 31 == 0 else rng.choice(NAMES),
            "code": code,
            "reg_ymd": reg,
            "amt": amt, "vat": vat, "total": total,
            "qty": None if i % 33 == 0 else -999 if i % 21 == 0 else rng.randint(1, 20),
            "order_date": day,
            "ship_date": None if i % 25 == 0 else day + dt.timedelta(days=rng.randint(-1, 5)),
            "paid_at": paid,
        })
    return rows


ROWS = seed_rows()
CUSTOMERS = [(c, "VIP" if c % 10 == 0 else "NORMAL") for c in range(1, N_CUSTOMERS + 1)]

# ---------------------------------------------------------------------------
# 규칙 — (id, 유형, 컬럼, 기대값, 임계값)
# ---------------------------------------------------------------------------

PLUGIN = '''
import pandas as pd


def paid_ratio(df, params):
    """상태가 있는 행 가운데 PAID 비율과 금액 합계 — 컬럼 이름은 DB 마다 대소문자가 다르다(Oracle 은 대문자)."""
    cols = {c.lower(): c for c in df.columns}
    status = df[cols["status"]]
    n = int(status.notna().sum())
    paid = int((status == "PAID").sum())
    amt = sum(float(v) for v in df[cols["amt"]] if pd.notna(v))
    ratio = paid / n if n else 0.0
    return ratio >= float(params.get("min", 0.5)), f"{ratio:.4f}", f"PAID {paid}/{n}, 금액 합계 {amt:.2f}, 행 {len(df)}"
'''

RULES = [
    (1, "ROW_COUNT", None, "250", 100.0),
    (2, "NOT_NULL", "cust_id", None, 100.0),
    (3, "NOT_NULL", "name", None, 90.0),
    (4, "UNIQUE", "id", None, 100.0),
    (5, "UNIQUE", "cust_id", None, 100.0),
    (6, "UNIQUE", "name", None, 100.0),
    (7, "UNIQUE", "code", None, 100.0),
    (8, "MIN_VALUE", "qty", "0", 100.0),
    (9, "MAX_VALUE", "qty", "15", 100.0),
    (10, "MIN_VALUE", "amt", "1000", 100.0),
    (11, "MAX_VALUE", "amt", "40000", 100.0),
    (12, "ACCEPTED_VALUES", "status", '["PAID","SHIP","CANCEL","REFUND"]', 100.0),
    (13, "ACCEPTED_VALUES", "qty", '["1","2","3","4","5"]', 100.0),
    (14, "REGEX", "code", "^[A-Z][0-9]{4}$", 100.0),
    (15, "DATE_FORMAT", "reg_ymd", "YYYYMMDD", 100.0),
    (16, "DATE_FORMAT", "order_date", "YYYY-MM-DD", 100.0),
    (17, "COLUMN_COMPARE", "ship_date", ">= order_date", 100.0),
    (18, "COLUMN_COMPARE", "total", ">= amt", 100.0),
    (19, "BROKEN_TEXT", "name", None, 100.0),
    (20, "WHITESPACE", "name", None, 100.0),
    (21, "LOGICAL", None, "IF status = 'PAID' THEN paid_at IS NOT NULL", 100.0),
    (22, "LOGICAL", None, "IF order_date >= DATE '2024-03-01' THEN qty > 0", 100.0),
    (23, "FORMULA", "total", "amt + vat", 100.0),
    (24, "FORMULA", "total", "amt + vat ~ 0.5", 100.0),
    (25, "DUMMY_VALUE", "qty", '["-999"]', 100.0),
    (26, "DUMMY_VALUE", "name", '["N/A","UNKNOWN"]', 100.0),
    (27, "FRESHNESS", "paid_at", "24", 100.0),
    (28, "CONTINUITY", "order_date", "DAY", 100.0),
    (29, "CONTINUITY", "order_date", "WEEK", 100.0),
    (30, "CONTINUITY", "order_date", "MONTH", 100.0),
    (31, "CONTINUITY", "reg_ymd", "DAY YYYYMMDD", 100.0),
    (32, "CONTINUITY", "paid_at", "DAY", 100.0),
    (33, "REFERENTIAL", "cust_id", None, 100.0),
    (34, "COMPOSITE_UNIQUE", None, '["cust_id","order_date"]', 100.0),
    (35, "COMPOSITE_UNIQUE", None, '["status","name"]', 100.0),
    (36, "DIVERSITY", "status", "60", 100.0),
    (37, "DIVERSITY", "name", "30", 100.0),
    (38, "CUSTOM_PYTHON", None, '{"module": "dqv_checks", "fn": "paid_ratio", "params": {"min": 0.5}}', 100.0),
    (39, "ACCEPTED_VALUES", "code", '["A0001"]', 100.0),
    (40, "MIN_VALUE", "reg_ymd", "20240101", 100.0),
    (41, "UNIQUE", "paid_at", None, 100.0),
    (42, "NOT_NULL", "paid_at", None, 50.0),
]


# ---------------------------------------------------------------------------
# DB 마다 — 접속 · 시드
# ---------------------------------------------------------------------------

def _dsn(dialect: str) -> dict:
    u = urlparse(os.environ[_ENV[dialect]])
    return {"type": dialect, "host": u.hostname, "port": u.port, "user": unquote(u.username or ""),
            "password": unquote(u.password or ""), "database": unquote(u.path.lstrip("/")), "ssl": False}


def _names(dialect: str) -> dict:
    """DB 안의 이름 — 스키마, 테이블, 컬럼(Oracle 은 따옴표 없이 만든 이름이 대문자)."""
    up = dialect == "oracle"

    def n(x: str) -> str:
        return x.upper() if up else x

    schema = {"postgresql": "public", "mysql": _dsn(dialect)["database"], "oracle": _dsn(dialect)["user"].upper(),
              "mssql": "dbo"}[dialect]
    return {"schema": schema, "orders": n("dqv_orders"), "customer": n("dqv_customer"), "col": n}


def _source(dialect: str) -> dict:
    """워커에 줄 원천 접속 정보 — ``execution._direct_source`` 로 만든다(카탈로그와 같은 경로)."""
    d = _dsn(dialect)
    config = {"host": d["host"], "port": d["port"], "username": d["user"], "password": d["password"]}
    if dialect == "oracle":
        config["service_name"] = d["database"]
    else:
        config["database"] = d["database"]
    return execution._direct_source(dialect, config, d["database"] if dialect == "mssql" else None)


def _ddl(dialect: str) -> tuple[list[str], str, str]:
    names = _names(dialect)
    col, types = names["col"], TYPES[dialect]
    ident = (lambda x: x) if dialect == "oracle" else (lambda x: rule_direct.quote_ident(dialect, x))
    orders = f"{ident(names['orders'])}"
    customer = f"{ident(names['customer'])}"
    cols = ", ".join(f"{ident(col(c))} {types[c]}" for c in COLUMNS)
    ctype = _CUSTOMER_TYPES[dialect]
    create = [f"CREATE TABLE {orders} ({cols})",
              f"CREATE TABLE {customer} ({ident(col('cust_id'))} {ctype[0]}, {ident(col('grade'))} {ctype[1]})"]
    return create, orders, customer


def _row_values(row: dict, _dialect: str) -> list:
    return [row[c] for c in COLUMNS]


def _seed_postgresql(src: dict) -> None:
    import asyncpg

    async def go() -> None:
        conn = await asyncpg.connect(host=src["host"], port=src["port"], user=src["user"], password=src["password"],
                                     database=src["database"])
        try:
            create, orders, customer = _ddl("postgresql")
            await conn.execute(f"DROP TABLE IF EXISTS {orders}, {customer}")
            for sql in create:
                await conn.execute(sql)
            marks = ", ".join(f"${i + 1}" for i in range(len(COLUMNS)))
            await conn.executemany(f"INSERT INTO {orders} VALUES ({marks})", [_row_values(r, "postgresql") for r in ROWS])
            await conn.executemany(f"INSERT INTO {customer} VALUES ($1, $2)", CUSTOMERS)
        finally:
            await conn.close()

    asyncio.run(go())


def _seed_mysql(src: dict) -> None:
    import aiomysql

    async def go() -> None:
        conn = await aiomysql.connect(host=src["host"], port=src["port"], user=src["user"], password=src["password"],
                                      db=src["database"], charset="utf8mb4", autocommit=True)
        try:
            create, orders, customer = _ddl("mysql")
            async with conn.cursor() as cur:
                await cur.execute(f"DROP TABLE IF EXISTS {orders}, {customer}")
                for sql in create:
                    await cur.execute(sql + " DEFAULT CHARSET=utf8mb4")
                marks = ", ".join("%s" for _ in COLUMNS)
                await cur.executemany(f"INSERT INTO {orders} VALUES ({marks})", [_row_values(r, "mysql") for r in ROWS])
                await cur.executemany(f"INSERT INTO {customer} VALUES (%s, %s)", CUSTOMERS)
        finally:
            conn.close()

    asyncio.run(go())


def _seed_oracle(src: dict) -> None:
    import oracledb

    conn = oracledb.connect(user=src["user"], password=src["password"], host=src["host"], port=src["port"],
                            service_name=src["service_name"])
    try:
        cur = conn.cursor()
        create, orders, customer = _ddl("oracle")
        for table in (orders, customer):
            cur.execute(f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {table} PURGE'; "
                        "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;")
        for sql in create:
            cur.execute(sql)
        # NVARCHAR2 컬럼(name)은 국가 문자 집합으로 보낸다
        cur.setinputsizes(None, None, None, oracledb.DB_TYPE_NVARCHAR)
        marks = ", ".join(f":{i + 1}" for i in range(len(COLUMNS)))
        cur.executemany(f"INSERT INTO {orders} VALUES ({marks})", [_row_values(r, "oracle") for r in ROWS])
        cur.executemany(f"INSERT INTO {customer} VALUES (:1, :2)", CUSTOMERS)
        conn.commit()
        # 방금 만든 테이블은 몇 초 동안 읽기 전용 트랜잭션에서 ORA-01466(테이블 정의가 바뀜)으로 읽히지 않는다 — 워커가
        # 쓰는 읽기 전용 트랜잭션으로 읽힐 때까지 기다린다
        for _ in range(60):
            try:
                cur.execute("SET TRANSACTION READ ONLY")
                for table in (orders, customer):
                    cur.execute(f"SELECT count(*) FROM {table}").fetchall()
                conn.rollback()
                break
            except oracledb.DatabaseError as e:
                conn.rollback()
                if "ORA-01466" not in str(e):
                    raise
                time.sleep(0.5)
    finally:
        conn.close()


def _seed_mssql(src: dict) -> None:
    import pymssql

    db = src["database"]
    master = pymssql.connect(server=src["host"], port=str(src["port"]), user=src["user"], password=src["password"],
                             database="master", autocommit=True, tds_version="7.4")
    try:
        cur = master.cursor()
        cur.execute(f"IF DB_ID(N'{db}') IS NULL CREATE DATABASE [{db}]")
    finally:
        master.close()
    conn = pymssql.connect(server=src["host"], port=str(src["port"]), user=src["user"], password=src["password"],
                           database=db, autocommit=True, tds_version="7.4")
    try:
        cur = conn.cursor()
        create, orders, customer = _ddl("mssql")
        cur.execute(f"DROP TABLE IF EXISTS {orders}, {customer}")
        for sql in create:
            cur.execute(sql)
        marks = ", ".join("%s" for _ in COLUMNS)
        cur.executemany(f"INSERT INTO {orders} VALUES ({marks})", [tuple(_row_values(r, "mssql")) for r in ROWS])
        cur.executemany(f"INSERT INTO {customer} VALUES (%s, %s)", CUSTOMERS)
    finally:
        conn.close()


_SEEDERS = {"postgresql": _seed_postgresql, "mysql": _seed_mysql, "oracle": _seed_oracle, "mssql": _seed_mssql}

# ---------------------------------------------------------------------------
# 실행 — 카탈로그 계획 → 워커 → 카탈로그 판정
# ---------------------------------------------------------------------------


class _Catalog:
    """워커가 부르는 카탈로그 대신 — 진행 보고는 받기만, 위반 샘플은 남긴다."""

    def __init__(self):
        self.sent: list[dict] = []

    async def heartbeat(self, *_a, **_k) -> dict:
        return {"active": True, "cancel": False}

    async def samples(self, _run_id, results) -> None:
        self.sent = list(results)


def _snapshot(dialect: str) -> dict:
    names = _names(dialect)
    col, types = names["col"], TYPES[dialect]
    items = []
    for rid, check, column, expected, threshold in RULES:
        reference = None
        if check == "REFERENTIAL":
            reference = {"table": ["dqv", names["schema"], names["customer"]], "column": col("cust_id")}
        if check in ("COMPOSITE_UNIQUE",):
            expected = json.dumps([col(c) for c in json.loads(expected)])
        items.append({"id": rid, "check_type": check, "column_name": col(column) if column else None,
                      "expected_value": expected, "threshold": threshold,
                      "reference": reference})
    return {"engine": runs.DIRECT_ENGINE, "table": ["dqv", names["schema"], names["orders"]],
            "types": {col(c): t for c, t in types.items()}, "rules": items, "source_type": dialect,
            "source_timezone": "Asia/Seoul"}


def run_dialect(dialect: str, checks_dir: str) -> dict:
    """DB 하나 — 시드, 진단 실행, 프로파일. 반환: {"outcomes", "results", "samples", "profile", "plan"}."""
    src = _source(dialect)
    _SEEDERS[dialect]({**_dsn(dialect), **src})
    snapshot = _snapshot(dialect)
    plan = execution.compile_snapshot(snapshot)
    steps = execution.plan_steps(plan)
    catalog = _Catalog()
    runner = Runner(Settings(direct_checks_dir=checks_dir, direct_fetch_size=37), catalog)  # type: ignore[arg-type]
    job = {"run_id": 1, "engine": runs.DIRECT_ENGINE, "direct": src, "steps": steps}

    async def go():
        state = _JobState(runner, 1, len(steps))
        results: list[dict] = []
        for step in steps:
            results.extend(await runner._run_direct_step(job, step, state))
        by_key = {r["key"]: r for r in results}
        outcomes, failed, rule_errors, _ = execution._judge(plan, by_key)
        stored = {rid for rid in outcomes if not (by_key.get(execution.rule_key(rid)) or {}).get("samples")}
        await runner._run_samples(job, execution._sample_steps(plan, outcomes, stored))

        names = _names(dialect)
        pcols = [[names["col"](c), "", TYPES[dialect][c]] for c in COLUMNS]
        pstep = profiling.profile_step(rule_sql.TableRef("dqv", names["schema"], names["orders"]), pcols,
                                       direct=True, source_type=dialect)
        presults = await runner._run_profile_step(job, pstep, state)
        pby = {r["key"]: r for r in presults}
        rows, profiles, missing = profiling.read_profile(pcols, pby, source_type=dialect)
        return {"outcomes": outcomes, "failed": failed, "rule_errors": rule_errors, "results": by_key,
                "samples": {s["key"]: s for s in catalog.sent}, "profile": (rows, profiles, missing, pby),
                "plan": plan, "steps": steps}

    return asyncio.run(go())


# ---------------------------------------------------------------------------
# 기대값 — 시드 데이터에서 Python 으로 따로 센다(Trino 의 뜻: NULL 은 세지 않음, 문자열은 대소문자 · 끝 공백 구분)
# ---------------------------------------------------------------------------

def _rtrim_char(v):
    return v.rstrip() if isinstance(v, str) else v


def _vals(col: str) -> list:
    return [_rtrim_char(r[col]) if col == "code" else r[col] for r in ROWS]


def _nn(col: str) -> list:
    return [v for v in _vals(col) if v is not None]


def _periods(values, unit: str) -> tuple[int, int, int]:
    """(고유 기간 수, 처음 ~ 끝 기간 수, 빠진 기간 수)."""
    keys = set()
    for v in values:
        d = v.date() if isinstance(v, dt.datetime) else v
        if unit == "DAY":
            keys.add(d)
        elif unit == "WEEK":
            keys.add(d - dt.timedelta(days=d.weekday()))
        elif unit == "MONTH":
            keys.add((d.year, d.month))
    first, last = min(keys), max(keys)
    if unit == "DAY":
        span = (last - first).days + 1
    elif unit == "WEEK":
        span = (last - first).days // 7 + 1
    else:
        span = (last[0] - first[0]) * 12 + last[1] - first[1] + 1
    return len(keys), span, span - len(keys)


def expectations() -> dict[int, dict]:
    """규칙 → 기대 판정(``checked`` · ``errors`` · ``actual`` 가운데 아는 것)."""
    total = len(ROWS)
    exp: dict[int, dict] = {}
    exp[1] = {"actual": str(total), "passed": True}
    exp[2] = {"errors": sum(1 for v in _vals("cust_id") if v is None), "checked": total}
    exp[3] = {"errors": sum(1 for v in _vals("name") if v is None), "checked": total}
    for rid, col in ((4, "id"), (5, "cust_id"), (6, "name"), (7, "code"), (41, "paid_at")):
        exp[rid] = {"errors": total - len(set(_nn(col))), "checked": total}
    qty = _nn("qty")
    exp[8] = {"actual": str(float(min(qty))), "errors": sum(1 for v in qty if v < 0), "checked": len(qty)}
    exp[9] = {"actual": str(float(max(qty))), "errors": sum(1 for v in qty if v > 15), "checked": len(qty)}
    amt = _nn("amt")
    exp[10] = {"actual": str(float(min(amt))), "errors": sum(1 for v in amt if v < 1000), "checked": len(amt)}
    exp[11] = {"actual": str(float(max(amt))), "errors": sum(1 for v in amt if v > 40000), "checked": len(amt)}
    st = _nn("status")
    exp[12] = {"checked": len(st), "errors": sum(1 for v in st if v not in ("PAID", "SHIP", "CANCEL", "REFUND"))}
    exp[13] = {"checked": len(qty), "errors": sum(1 for v in qty if str(v) not in ("1", "2", "3", "4", "5"))}
    code = _nn("code")
    exp[14] = {"checked": len(code), "errors": sum(1 for v in code if not re.search(r"^[A-Z][0-9]{4}$", v))}

    def bad_ymd(v: str) -> bool:
        if not re.fullmatch(r"[0-9]{8}", v):
            return True
        try:
            dt.datetime.strptime(v, "%Y%m%d")
        except ValueError:
            return True
        return False
    reg = _nn("reg_ymd")
    exp[15] = {"checked": len(reg), "errors": sum(1 for v in reg if bad_ymd(v))}
    exp[16] = {"checked": total, "errors": 0}
    pairs = [(r["ship_date"], r["order_date"]) for r in ROWS if r["ship_date"] is not None]
    exp[17] = {"checked": len(pairs), "errors": sum(1 for s, o in pairs if not s >= o)}
    exp[18] = {"checked": total, "errors": sum(1 for r in ROWS if not r["total"] >= r["amt"])}
    names = _nn("name")
    exp[19] = {"checked": len(names), "errors": sum(1 for v in names if re.search(rule_sql.BROKEN_TEXT_PATTERN, v))}
    exp[20] = {"checked": len(names), "errors": sum(1 for v in names if re.search(rule_sql.WHITESPACE_PATTERN, v))}
    when = [r for r in ROWS if r["status"] == "PAID"]
    exp[21] = {"checked": len(when), "errors": sum(1 for r in when if r["paid_at"] is None)}
    when = [r for r in ROWS if r["order_date"] >= dt.date(2024, 3, 1)]
    exp[22] = {"checked": len(when), "errors": sum(1 for r in when if r["qty"] is not None and not r["qty"] > 0)}
    both = [r for r in ROWS if r["vat"] is not None]
    exp[23] = {"checked": len(both), "errors": sum(1 for r in both if r["total"] != r["amt"] + r["vat"])}
    exp[24] = {"checked": len(both), "errors": sum(1 for r in both if abs(r["total"] - r["amt"] - r["vat"]) > Decimal("0.5"))}
    exp[25] = {"checked": len(qty), "errors": sum(1 for v in qty if v == -999)}
    exp[26] = {"checked": len(names), "errors": sum(1 for v in names if v in ("N/A", "UNKNOWN"))}
    exp[27] = {"passed": False, "checked": len(_nn("paid_at"))}
    for rid, col, unit in ((28, "order_date", "DAY"), (29, "order_date", "WEEK"), (30, "order_date", "MONTH"),
                           (32, "paid_at", "DAY")):
        _, span, missing = _periods(_nn(col), unit)
        exp[rid] = {"checked": span, "errors": missing}
    # 문자열 날짜는 형식과 모양이 같은 값(8자리 숫자)을 묶는다 — 달력에 없는 20240231 도 한 기간으로 센다
    keys = {v for v in reg if re.fullmatch(r"[0-9]{8}", v)}
    days = [dt.datetime.strptime(v, "%Y%m%d").date() for v in keys if not bad_ymd(v)]
    span = (max(days) - min(days)).days + 1
    exp[31] = {"checked": span, "errors": span - len(keys)}
    child = [v for v in _vals("cust_id") if v is not None]
    exp[33] = {"checked": len(child), "errors": sum(1 for v in child if not 1 <= v <= N_CUSTOMERS)}
    for rid, cols in ((34, ("cust_id", "order_date")), (35, ("status", "name"))):
        groups: dict = {}
        for r in ROWS:
            key = tuple(r[c] for c in cols)
            if None not in key:
                groups[key] = groups.get(key, 0) + 1
        exp[rid] = {"checked": sum(groups.values()), "errors": sum(n for n in groups.values() if n > 1)}
    for rid, col in ((36, "status"), (37, "name")):
        counts: dict = {}
        for v in _nn(col):
            counts[v] = counts.get(v, 0) + 1
        exp[rid] = {"checked": sum(counts.values()), "distinct": len(counts), "top": max(counts.values())}
    n = len(st)
    paid = sum(1 for v in st if v == "PAID")
    exp[38] = {"actual": f"{paid / n:.4f}", "passed": paid / n >= 0.5}
    exp[39] = {"checked": len(code), "errors": sum(1 for v in code if v != "A0001")}
    exp[40] = {"checked": len(reg)}
    exp[42] = {"errors": sum(1 for v in _vals("paid_at") if v is None), "checked": total}
    return exp


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

_RESULTS: dict[str, dict] = {}


@pytest.fixture(scope="module")
def checks_dir(tmp_path_factory) -> str:
    d = tmp_path_factory.mktemp("dqv_checks")
    (d / "dqv_checks.py").write_text(PLUGIN, encoding="utf-8")
    return str(d)


def _run(dialect: str, checks_dir: str) -> dict:
    if dialect not in _RESULTS:
        _RESULTS[dialect] = run_dialect(dialect, checks_dir)
    return _RESULTS[dialect]


def _summary(o) -> tuple:
    return (o.passed, o.actual_value, o.checked_count, o.error_count)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_rules_match_python_expectation(dialect: str, checks_dir: str) -> None:
    got = _run(dialect, checks_dir)
    assert not got["failed"], got["failed"]
    assert not got["rule_errors"], got["rule_errors"]
    outcomes = got["outcomes"]
    diffs = []
    for rid, *_ in RULES:
        if rid not in outcomes:
            diffs.append(f"규칙 {rid}: 판정 없음")
    for rid, want in expectations().items():
        o = outcomes.get(rid)
        if o is None:
            continue
        if "checked" in want and o.checked_count != want["checked"]:
            diffs.append(f"규칙 {rid}: 진단 건수 {o.checked_count} ≠ 기대 {want['checked']} ({o.detail})")
        if "errors" in want and o.error_count != want["errors"]:
            diffs.append(f"규칙 {rid}: 위반 건수 {o.error_count} ≠ 기대 {want['errors']} ({o.detail})")
        if "actual" in want and o.actual_value != want["actual"]:
            diffs.append(f"규칙 {rid}: 실제값 {o.actual_value!r} ≠ 기대 {want['actual']!r} ({o.detail})")
        if "passed" in want and o.passed != want["passed"]:
            diffs.append(f"규칙 {rid}: 통과 {o.passed} ≠ 기대 {want['passed']} ({o.detail})")
        if "distinct" in want and f"고유값 {want['distinct']}" not in o.detail.replace(",", ""):
            diffs.append(f"규칙 {rid}: 고유값 {want['distinct']} 이 설명에 없음 ({o.detail})")
    assert not diffs, "\n".join(diffs)


def test_rules_match_across_dialects(checks_dir: str) -> None:
    if len(DIALECTS) < 2:
        pytest.skip("비교할 DB 가 둘 이상이어야 합니다")
    base = DIALECTS[0]
    want = _run(base, checks_dir)["outcomes"]
    diffs = []
    for dialect in DIALECTS[1:]:
        got = _run(dialect, checks_dir)["outcomes"]
        for rid, *_ in RULES:
            a, b = want.get(rid), got.get(rid)
            if a is None or b is None or _summary(a) != _summary(b):
                diffs.append(f"규칙 {rid}: {base} {a and _summary(a)} / {dialect} {b and _summary(b)}")
    assert not diffs, "\n".join(diffs)


def _profile_norm(v):
    """프로파일 값의 DB 별 표기 차이 — 숫자(1000.00 / 1000), 날짜(Oracle DATE 는 시각까지)."""
    if v is None:
        return None
    text = str(v)
    text = re.sub(r" 00:00:00$", "", text)
    try:
        return round(float(text), 4)
    except ValueError:
        return text


def _expected_profile() -> dict[str, tuple]:
    out = {}
    for c in COLUMNS:
        values = [v for v in _vals(c) if v is not None]
        mean = round(float(sum(values) / len(values)), 4) if c in ("id", "cust_id", "amt", "vat", "total", "qty") else None
        out[c] = (len(ROWS) - len(values), len(set(values)), _profile_norm(min(values)), _profile_norm(max(values)), mean)
    return out


@pytest.mark.parametrize("dialect", DIALECTS)
def test_profile_matches(dialect: str, checks_dir: str) -> None:
    rows, profiles, missing, _ = _run(dialect, checks_dir)["profile"]
    assert rows == N_ROWS
    assert not missing
    want = _expected_profile()
    diffs = []
    for cp in profiles:
        name = cp.column_name.lower()
        nulls, distinct, lo, hi, mean = want[name]
        got = (cp.null_count, cp.unique_count, _profile_norm(cp.min_value), _profile_norm(cp.max_value),
               None if cp.mean_value is None else round(cp.mean_value, 4))
        if mean is None:
            got = got[:4] + (None,)
        if got[4] is not None and mean is not None and abs(got[4] - mean) < 0.001:
            got = got[:4] + (mean,)
        if got != (nulls, distinct, lo, hi, mean):
            diffs.append(f"{name}: {got} ≠ 기대 {(nulls, distinct, lo, hi, mean)}")
        if 0 < distinct <= profiling.TOP_MAX_DISTINCT:
            counts: dict = {}
            for v in _vals(name):
                if v is not None:
                    counts[v] = counts.get(v, 0) + 1
            top = sorted(counts.values(), reverse=True)[:profiling.TOP_LIMIT]
            got_top = [t["count"] for t in cp.top_values or []]
            if got_top != top[:len(got_top)] or not got_top:
                diffs.append(f"{name}: 최빈값 건수 {got_top} ≠ 기대 {top}")
            for t in cp.top_values or []:
                if counts.get(t["value"]) != t["count"]:
                    diffs.append(f"{name}: 최빈값 {t['value']!r} {t['count']}건 ≠ 기대 {counts.get(t['value'])}")
    assert not diffs, "\n".join(diffs)


def _violates(rid: int, row: dict) -> bool:
    """샘플 행(``{컬럼: 문자열}``)이 그 규칙의 위반인지 — 시드 행을 id 로 찾아 본다."""
    seed = ROWS[int(float(row["id"])) - 1]
    if rid in (2,):
        return seed["cust_id"] is None
    if rid == 3:
        return seed["name"] is None
    if rid == 5:
        return sum(1 for r in ROWS if r["cust_id"] == seed["cust_id"]) > 1
    if rid == 6:
        return sum(1 for r in ROWS if r["name"] == seed["name"]) > 1
    if rid == 7:
        return sum(1 for r in ROWS if r["code"] == seed["code"]) > 1
    if rid == 8:
        return seed["qty"] is not None and seed["qty"] < 0
    if rid == 9:
        return seed["qty"] is not None and seed["qty"] > 15
    if rid == 11:
        return seed["amt"] > 40000
    if rid == 12:
        return seed["status"] not in (None, "PAID", "SHIP", "CANCEL", "REFUND")
    if rid == 14:
        return seed["code"] is not None and not re.search(r"^[A-Z][0-9]{4}$", seed["code"])
    if rid == 17:
        return seed["ship_date"] is not None and seed["ship_date"] < seed["order_date"]
    if rid == 21:
        return seed["status"] == "PAID" and seed["paid_at"] is None
    if rid in (23, 24):
        return seed["vat"] is not None and seed["total"] != seed["amt"] + seed["vat"]
    if rid == 25:
        return seed["qty"] == -999
    if rid == 26:
        return seed["name"] in ("N/A", "UNKNOWN")
    if rid == 33:
        return seed["cust_id"] is not None and not 1 <= seed["cust_id"] <= N_CUSTOMERS
    if rid == 34:
        return sum(1 for r in ROWS if (r["cust_id"], r["order_date"]) == (seed["cust_id"], seed["order_date"])) > 1
    return True


@pytest.mark.parametrize("dialect", DIALECTS)
def test_violation_samples(dialect: str, checks_dir: str) -> None:
    got = _run(dialect, checks_dir)
    outcomes, results, queried = got["outcomes"], got["results"], got["samples"]
    checked = 0
    diffs = []
    for rid, o in outcomes.items():
        if o.passed or not o.error_count:
            continue
        key = execution.rule_key(rid)
        own = results.get(key) or {}
        if own.get("samples"):
            rows = execution.sample_rows(own.get("sample_columns"), own.get("samples")) or []
        elif key in queried:
            if "error" in queried[key]:
                diffs.append(f"규칙 {rid}: 샘플 쿼리 실패 {queried[key]['error']}")
                continue
            rows = execution.sample_rows(queried[key]["columns"], queried[key]["rows"]) or []
        else:
            continue
        rows = [{k.lower(): v for k, v in r.items()} for r in rows]
        if len(rows) != min(rule_sql.SAMPLE_ROWS, o.error_count) and rid not in (34, 35, 5, 6, 7, 41):  # 고유값 · 키 — 위반 건수에 NULL 이 섞이거나 행이 아닌 값 수
            diffs.append(f"규칙 {rid}: 샘플 {len(rows)}행 (위반 {o.error_count}건)")
        for r in rows:
            if "id" in r and not _violates(rid, r):
                diffs.append(f"규칙 {rid}: 위반이 아닌 샘플 행 {r}")
        checked += 1
    assert checked >= 10, f"샘플을 확인한 규칙이 너무 적음: {checked}"
    assert not diffs, "\n".join(diffs)
