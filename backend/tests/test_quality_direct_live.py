# SPDX-License-Identifier: Apache-2.0
"""원천 직접 진단 — 실제 원본 DB 에서 두 방식이 같은 값을 내는지 (TODO-097).

원천 직접 경로는 규칙을 원본 집계(``rule_direct.aggregate`` — 원본 DB 가 SQL 로 센다)와 행 읽기(``rule_direct.probe`` —
워커가 Python 으로 센다)로 나눈다. 같은 규칙을 두 방식으로 모두 계산해 값이 같은지 본다. 프로파일의 문자열 통계(고유값 수 ·
최소 · 최대)도 읽은 행으로 Python 이 센 값과 비교한다 — MySQL · MariaDB 의 정렬 규칙(대소문자 무시 · 끝 공백 무시)을
따르지 않고 Trino · 워커처럼 바이트로 세는지.

원본 DB 가 있어야 해서 평소에는 건너뛴다. 접속 정보를 JSON 목록으로 주면 돈다(읽기 전용 SELECT 만 낸다)::

    ARGUS_QUALITY_LIVE_SOURCES='[{"type": "mysql", "host": "127.0.0.1", "port": 3306, "user": "u",
        "password": "p", "database": "sakila", "schema": "sakila", "tables": ["address", "payment"]}]' \
    PYTHONPATH=$(pwd) python -m pytest -q tests/test_quality_direct_live.py

``schema`` 는 PostgreSQL 의 스키마(MySQL · MariaDB 는 데이터베이스 이름과 같다), ``tables`` 를 비우면 그 스키마의 모든 테이블.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.quality import profiling, rule_direct
from app.quality.rule_sql import RuleSpec, TableRef

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "argus-catalog-quality-worker" / "src"))
from quality_worker import direct  # noqa: E402

_SOURCES = json.loads(os.environ.get("ARGUS_QUALITY_LIVE_SOURCES") or "[]")
pytestmark = pytest.mark.skipif(not _SOURCES, reason="ARGUS_QUALITY_LIVE_SOURCES 가 없음 — 실제 원본 DB 가 필요")

_TIMEOUTS = {"connect_timeout": 10, "query_timeout": 600}


def _source(cfg: dict) -> dict:
    return {k: cfg.get(k) for k in ("type", "host", "port", "user", "password", "database")} | {"ssl": bool(cfg.get("ssl"))}


async def _q(src: dict, sql: str, limit: int = 100_000) -> list[list]:
    return (await direct.fetch_table(src, sql, limit=limit, **_TIMEOUTS))[1]


async def _tables(cfg: dict) -> dict[str, dict[str, str]]:
    """테이블 → {컬럼: 원본 타입}. 뷰는 뺀다."""
    src, kind = _source(cfg), cfg["type"]
    schema = cfg.get("schema") or cfg["database"]
    if kind == "postgresql":
        typ = ("CASE WHEN c.data_type IN ('character varying', 'character') "
               "THEN c.data_type || '(' || coalesce(c.character_maximum_length::text, '') || ')' ELSE c.data_type END")
    else:
        typ = "c.column_type"
    rows = await _q(src, (
        f"SELECT c.table_name, c.column_name, {typ} FROM information_schema.columns c "
        "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        f"AND t.table_type = 'BASE TABLE' WHERE c.table_schema = '{schema}' ORDER BY c.table_name, c.ordinal_position"))
    wanted = set(cfg.get("tables") or [])
    out: dict[str, dict[str, str]] = {}
    for table, column, column_type in rows:
        if not wanted or table in wanted:
            out.setdefault(table, {})[column] = column_type
    return out


async def _rules(cfg: dict, schema: str, table: str, types: dict[str, str]) -> list[RuleSpec]:
    """원본 집계로 셀 수 있는 규칙들 — 허용 값은 실제 값 두 개와 대소문자만 바꾼 값(바이트 비교면 다른 값)."""
    src, kind = _source(cfg), cfg["type"]
    rules = [RuleSpec(0, "ROW_COUNT", None, "1")]
    by_family: dict = {}

    def add(*args) -> None:
        rules.append(RuleSpec(len(rules), *args))

    for col, typ in types.items():
        family = rule_direct.type_family(typ)
        by_family.setdefault(family, []).append(col)
        add("NOT_NULL", col)
        add("UNIQUE", col)
        if family in ("integer", "numeric"):
            add("MIN_VALUE", col, "1")
            add("MAX_VALUE", col, "100")
        if family in ("text", "enum", "integer"):
            q = rule_direct.quote_ident(kind, col)
            values = await _q(src, f"SELECT {q} FROM {rule_direct.source_table(kind, schema, table)} WHERE {q} IS NOT NULL "
                                   f"GROUP BY {q} ORDER BY count(*) DESC LIMIT 2", limit=2)
            accepted = [str(v[0]) for v in values if str(v[0]).strip() and "," not in str(v[0])]
            if values and isinstance(values[0][0], str) and str(values[0][0]).swapcase() != str(values[0][0]):
                accepted.append(str(values[0][0]).swapcase())
            if accepted:
                add("ACCEPTED_VALUES", col, ",".join(accepted))
    for family, cols in by_family.items():
        if family and len(cols) >= 2:
            add("COLUMN_COMPARE", cols[0], f"<= {cols[1]}")
            add("COLUMN_COMPARE", cols[1], f"< {cols[0]}")
    return rules


def _norm(v):
    """두 방식의 값을 같은 모양으로 — 워커는 Decimal 을 문자열로 보내고, 판정은 float 로 읽는다."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (Decimal, int, float)):
        return round(float(v), 6)
    if isinstance(v, str):
        try:
            return round(float(v), 6)
        except ValueError:
            return v
    return v


async def _never_stop(_rows: int) -> bool:
    return False


async def _compare_table(cfg: dict, table: str, types: dict[str, str]) -> list[str]:
    """한 테이블의 규칙을 두 방식으로 계산해 다른 것을 돌려준다."""
    src, kind = _source(cfg), cfg["type"]
    schema = cfg.get("schema") or cfg["database"]
    pairs = []
    for rule in await _rules(cfg, schema, table, types):
        agg = rule_direct.aggregate(rule, types, kind, (schema, table), 5)
        if agg is not None:
            pairs.append((rule, agg, rule_direct.probe(rule, types) or {"kind": "rows"}))
    exprs = [e for _, a, _ in pairs for e in a.exprs]
    row = await direct.fetch_row(src, rule_direct.aggregate_sql(kind, (schema, table), exprs), **_TIMEOUTS)

    probes = [{"key": f"r{r.id}", "label": r.check_type, **p} for r, _, p in pairs]
    columns = rule_direct.columns_of(probes)
    step = {"columns": columns, "char_columns": [c for c in columns if rule_direct.is_char(types.get(c))], "probes": probes}
    evaluator = direct.Evaluator(step)
    await direct.scan(src, rule_direct.select_sql(kind, schema, table, columns), evaluator, max_rows=5_000_000,
                      fetch_size=5000, on_batch=_never_stop, **_TIMEOUTS)
    by_key = {r["key"]: r for r in evaluator.results()}

    diffs, pos = [], 1
    for rule, agg, _ in pairs:
        got = [_norm(v) for v in row[pos:pos + len(agg.exprs)]]
        pos += len(agg.exprs)
        read = by_key.get(f"r{rule.id}") or {}
        if "error" in read:
            continue  # 행 읽기가 규칙 오류로 본 것(타입이 맞지 않는 비교 등) — 집계도 그런 규칙은 만들지 않는다
        want = [_norm(v) for v in read.get("row", [None])[1:]]
        if got != want or int(row[0]) != read["row"][0]:
            diffs.append(f"{table}.{rule.column_name} {rule.check_type} {rule.expected_value!r}: 집계 {got} / 행 읽기 {want}")
    return diffs


async def _compare_profile(cfg: dict, table: str, types: dict[str, str]) -> list[str]:
    """프로파일의 문자열 컬럼 통계(고유값 수 · 최소 · 최대)를 읽은 행으로 센 값과 비교한다."""
    src, kind = _source(cfg), cfg["type"]
    schema = cfg.get("schema") or cfg["database"]
    text_cols = [c for c, t in types.items() if rule_direct.type_family(t) in ("text", "enum")]
    if not text_cols:
        return []
    columns = [[c, "STRING", types[c]] for c in text_cols]
    step = profiling.profile_step(TableRef("x", schema, table), columns, direct=True, source_type=kind)
    results = {step["key"]: {"row": await direct.fetch_row(src, step["sql"], **_TIMEOUTS)}}
    _, profiles, _ = profiling.read_profile(columns, results)
    rows = await _q(src, rule_direct.select_sql(kind, schema, table, text_cols), limit=5_000_000)
    diffs = []
    for i, (cp, col) in enumerate(zip(profiles, text_cols)):
        values = [v.rstrip() if rule_direct.is_char(types[col]) else v for v in (r[i] for r in rows) if v is not None]
        want = (len(set(values)), min(values) if values else None, max(values) if values else None)
        got = (cp.unique_count, cp.min_value, cp.max_value)
        if got != want:
            diffs.append(f"{table}.{col} 프로파일: SQL {got} / 행 {want}")
    return diffs


@pytest.mark.parametrize("cfg", _SOURCES, ids=lambda c: f"{c.get('type')}:{c.get('database')}")
def test_aggregate_matches_row_read(cfg: dict) -> None:
    async def run() -> list[str]:
        diffs = []
        for table, types in (await _tables(cfg)).items():
            diffs += await _compare_table(cfg, table, types)
            diffs += await _compare_profile(cfg, table, types)
        return diffs

    diffs = asyncio.run(run())
    assert not diffs, "\n".join(diffs[:50])
