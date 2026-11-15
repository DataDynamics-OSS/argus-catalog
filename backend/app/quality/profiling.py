# SPDX-License-Identifier: Apache-2.0
"""프로파일 실행 — 품질 워커가 컬럼 통계를 수집하는 계획과 그 결과 읽기 (TODO-095 3단계).

프로파일도 정밀 진단처럼 품질 워커가 실행한다(``catalog_quality_runs.kind = 'PROFILE'``). 카탈로그는 쿼리를 만들고
워커가 돌려준 행으로 ``ColumnProfile`` 을 만든다. 경로는 진단과 같다 — Trino 연결이 있으면 Trino 집계 쿼리,
없으면 원천 DB 에 같은 모양의 집계 쿼리(표준 SQL — PostgreSQL·MySQL·MariaDB·Oracle·SQL Server 가 함께 읽는다)를 낸다.
DB 마다 다른 곳(앞 몇 행만 읽기, SQL Server 정수 평균, 문자열 비교 규칙)은 ``_Sql`` · ``_Text`` 가 맞춘다.

한 단계(``profile``)의 쿼리 하나가 모든 컬럼의 통계를 센다::

    SELECT count(*), count(c1), count(DISTINCT c1), min(c1), max(c1), avg(c1), count(c2), … FROM t

이 쿼리가 실패하면(통계를 낼 수 없는 타입의 컬럼 하나 때문에 등) 워커는 컬럼마다 따로 낸다(``fallback``) — 실패한
컬럼만 통계 없이 남는다. 고유값이 ``TOP_MAX_DISTINCT`` 개 이하인 컬럼(코드성)은 워커가 이어서 최빈값 쿼리(``top``)를
낸다 — 고유값 수는 앞 쿼리의 결과에서 읽으므로 카탈로그는 미리 알 수 없다.

통계 항목은 예전 카탈로그 서버의 원천 프로파일(간이 진단과 함께 없앰)과 같다. 원천 직접 경로의 문자열 컬럼은
Trino 처럼 대소문자 · 끝 공백을 구분해 센다(``_Text`` — MySQL 의 기본 정렬 규칙을 따르지 않는다).
"""

from __future__ import annotations

import re

from app.quality import rule_direct, rule_sql
from app.quality.rule_sql import TableRef
from app.quality.schemas import ColumnProfile

PROFILE_KEY = "profile"
# 최빈값을 구하는 컬럼 — 고유값이 이 수 이하(코드성 컬럼)
TOP_MAX_DISTINCT = 20
TOP_LIMIT = 5

_NUMERIC_TYPES = {"NUMBER", "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT", "LONG", "SHORT", "DECIMAL",
                  "NUMERIC", "FLOAT", "DOUBLE", "REAL", "INT2", "INT4", "INT8", "FLOAT4", "FLOAT8", "SERIAL", "BIGSERIAL",
                  "SMALLSERIAL", "MONEY"}
# Trino 가 돌려주는 시각 문자열 — "2006-02-15 05:03:42.000 UTC". 원천 직접(드라이버 값의 str)과 같은 모양으로 맞춘다
_TRINO_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(\.\d+)?(?: (UTC|[+-]\d{2}:\d{2}))?$")
# 집계를 내지 않는 복합 타입 — NULL 수만 센다
_COMPLEX_RE = re.compile(r"^(ARRAY|STRUCT|MAP|ROW|UNION|JSON|LIST|RECORD)\b", re.IGNORECASE)


def _base(field_type: str | None) -> str:
    return re.split(r"[\s(<]", (field_type or "").strip().upper(), maxsplit=1)[0]


def column_stats(field_type: str | None, native_type: str | None) -> tuple[str, ...]:
    """컬럼에서 셀 통계 — ``nonnull`` · ``distinct`` · ``min`` · ``max`` · ``avg`` 의 부분 집합(이 순서)."""
    if _COMPLEX_RE.match((field_type or "").strip()) or _COMPLEX_RE.match((native_type or "").strip()):
        return ("nonnull",)
    stats = ["nonnull", "distinct"]
    from app.quality.service import _is_binary_type

    if not (_is_binary_type(field_type) or _is_binary_type(native_type)):
        stats += ["min", "max"]
        # 평균은 원천 타입이 숫자일 때만 — MySQL YEAR 는 카탈로그 타입이 NUMBER 지만 Trino 는 date 로 읽는다
        if _base(native_type or field_type) in _NUMERIC_TYPES:
            stats.append("avg")
    return tuple(stats)


def _expr(stat: str, col: str, direct: bool, text: "_Text | None" = None, source_type: str = "") -> str:
    if stat == "nonnull":
        return f"count({col})"
    if stat == "distinct":
        return f"count(DISTINCT {text.key if text else col})"
    if stat == "avg":
        # Trino 의 decimal 평균은 decimal — 화면에는 실수면 된다. SQL Server 는 정수 컬럼의 평균을 정수로 자른다
        if source_type == "mssql":
            return f"avg(CAST({col} AS float))"
        return f"avg({col})" if direct else f"avg(CAST({col} AS double))"
    if text:
        return text.extreme(stat)
    return f"{stat}({col})"


class _Text:
    """원천 직접 경로의 문자열 컬럼 — Trino 처럼 대소문자 · 끝 공백을 구분하고 코드 포인트 순서로 센다(TODO-097).

    MySQL · MariaDB 는 기본 정렬 규칙이 대소문자를 무시하므로 UTF-8 바이트(``rule_direct._text_key``)로 세고 꺼낼 때 문자열로
    되돌린다. PostgreSQL 은 같음이 바이트 비교라 최소 · 최대만 ``COLLATE "C"`` 로 한다.
    """

    def __init__(self, source_type: str, col: str, family: str):
        self.source_type = source_type
        self.mysql = source_type in ("mysql", "mariadb")
        self.col = col
        if self.mysql:
            self.key = rule_direct._text_key(source_type, col, family)
        elif source_type == "mssql":
            # SQL Server 의 기본 정렬 규칙은 대소문자 · 끝 공백을 무시한다 — 값의 바이트로 센다
            self.key = f"CAST({col} AS VARBINARY(MAX))"
        else:
            self.key = col  # PostgreSQL(결정적 정렬) · Oracle(NLS_COMP=BINARY 기본)은 같음이 바이트 비교다

    def extreme(self, stat: str) -> str:
        if self.mysql:
            return f"CONVERT({stat}({self.key}) USING utf8mb4)"
        if self.source_type == "mssql":
            return f"{stat}({self.col} COLLATE Latin1_General_BIN2)"  # 코드 포인트 순서
        if self.source_type == "oracle":
            return f"{stat}({self.col})"
        return f'{stat}({self.col} COLLATE "C")'

    @property
    def shown(self) -> str:
        """최빈값 쿼리에서 묶은 값을 꺼내는 식 — MySQL · SQL Server 는 묶음 안의 값이 모두 같은 바이트라 min 으로 꺼낸다
        (``ONLY_FULL_GROUP_BY`` 는 GROUP BY 식을 감싼 식도 받지 않는다)."""
        if self.mysql:
            return f"CONVERT(min({self.key}) USING utf8mb4)"
        if self.source_type == "mssql":
            return f"min({self.col})"
        return self.col


class _Sql:
    """경로별 식별자 인용과 테이블 이름."""

    def __init__(self, engine_direct: bool, source_type: str | None, table: TableRef):
        self.direct = engine_direct
        self.source_type = source_type or ""
        self.table = table

    def ident(self, name: str) -> str:
        return rule_direct.quote_ident(self.source_type, name) if self.direct else rule_sql.quote_ident(name)

    @property
    def from_(self) -> str:
        if not self.direct:
            return self.table.sql
        target = self.ident(self.table.table)
        return f"{self.ident(self.table.schema)}.{target}" if self.table.schema else target

    def top(self, columns: str, rest: str, limit: int) -> str:
        """앞 ``limit`` 행 — Trino · PostgreSQL · MySQL 은 LIMIT, Oracle 은 FETCH FIRST, SQL Server 는 TOP."""
        if not self.direct:
            return f"SELECT {columns} {rest} LIMIT {int(limit)}"
        return rule_direct.limited_select(self.source_type, columns, rest, limit)


def profile_step(table: TableRef, columns: list[list], *, direct: bool, source_type: str | None) -> dict:
    """워커가 실행할 프로파일 단계. ``columns`` 는 ``[이름, field_type, native_type]`` 목록(스키마 순서)."""
    q = _Sql(direct, source_type, table)
    exprs = ["count(*)"]
    fallback, top = [], []
    for name, field_type, native_type in columns:
        stats = column_stats(field_type, native_type)
        col = q.ident(name)
        family = rule_direct.type_family(native_type or field_type) if direct else None
        text = _Text(q.source_type, col, family) if family in ("text", "enum") else None
        start = len(exprs)
        own = [_expr(s, col, direct, text, q.source_type if direct else "") for s in stats]
        exprs += own
        fallback.append({"key": f"{PROFILE_KEY}:col:{name}", "label": f"프로파일 {name}",
                         "sql": f"SELECT count(*), {', '.join(own)} FROM {q.from_}"})
        if "distinct" in stats and "min" in stats:
            # 고유값 수는 이 컬럼 식의 두 번째(nonnull 다음) — 묶은 쿼리에서는 start+1, 컬럼별 쿼리에서는 2
            top.append({
                "key": f"{PROFILE_KEY}:top:{name}", "label": f"최빈값 {name}",
                "sql": q.top(f"{text.shown if text else col}, count(*)",
                             f"FROM {q.from_} WHERE {col} IS NOT NULL GROUP BY {text.key if text else col} "
                             "ORDER BY count(*) DESC", TOP_LIMIT),
                "at": start + 1, "fallback_key": f"{PROFILE_KEY}:col:{name}", "fallback_at": 2,
            })
    return {
        "key": PROFILE_KEY, "kind": "profile",
        "label": f"프로파일 {table.display} (컬럼 {len(columns)}개)",
        "sql": f"SELECT {', '.join(exprs)} FROM {q.from_}",
        # 컬럼이 하나뿐이면 같은 쿼리를 다시 낼 뿐이라 두지 않는다
        "fallback": fallback if len(columns) > 1 else [],
        "top": top, "top_max": TOP_MAX_DISTINCT,
    }


def _int(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return int(float(v))


def text_value(v) -> str | None:
    """최소 · 최대 · 최빈값을 문자열로. Trino 시각은 원천 직접 경로(``str(datetime)``)와 같은 모양으로 — 0 뿐인 소수 초를
    떼고, 시간대는 ``+00:00`` 꼴로(``datetime.fromisoformat`` 이 읽는다 — 최신성 규칙이 최대값을 시각으로 읽는다)."""
    if v is None:
        return None
    text = str(v)
    m = _TRINO_TIME_RE.match(text)
    if m:
        base, frac, zone = m.groups()
        if frac and frac.strip(".0"):
            base += frac.rstrip("0")
        if zone:
            base += "+00:00" if zone == "UTC" else zone
        return base
    return text


def _percent(n: int, total: int) -> float:
    return round(n / total * 100, 2) if total > 0 else 0.0


def read_profile(columns: list[list], results: dict[str, dict]) -> tuple[int | None, list[ColumnProfile], list[str]]:
    """워커 결과 → (행 수, 컬럼 프로파일, 통계를 내지 못한 컬럼). 행 수를 하나도 얻지 못했으면 행 수 None."""
    main = (results.get(PROFILE_KEY) or {}).get("row")
    row_count: int | None = _int(main[0]) if main else None
    profiles: list[ColumnProfile] = []
    missing: list[str] = []
    pos = 1
    for name, field_type, native_type in columns:
        stats = column_stats(field_type, native_type)
        values: list | None = None
        if main:
            values = list(main[pos:pos + len(stats)])
        else:
            own = (results.get(f"{PROFILE_KEY}:col:{name}") or {}).get("row")
            if own:
                if row_count is None:
                    row_count = _int(own[0])
                values = list(own[1:1 + len(stats)])
        pos += len(stats)
        cp = ColumnProfile(column_name=name, column_type=field_type or native_type or "")
        if values is None or len(values) < len(stats):
            missing.append(name)
            profiles.append(cp)
            continue
        got = dict(zip(stats, values))
        total = row_count or 0
        cp.total_count = total
        cp.null_count = max(0, total - _int(got.get("nonnull")))
        cp.null_percent = _percent(cp.null_count, total)
        if "distinct" in got:
            cp.unique_count = _int(got["distinct"])
            cp.unique_percent = _percent(cp.unique_count, total)
        if "min" in got:
            cp.min_value, cp.max_value = text_value(got["min"]), text_value(got["max"])
        if got.get("avg") is not None:
            try:
                cp.mean_value = round(float(got["avg"]), 4)
            except (TypeError, ValueError):
                pass
        top = (results.get(f"{PROFILE_KEY}:top:{name}") or {}).get("rows")
        if top and 0 < cp.unique_count <= TOP_MAX_DISTINCT:
            cp.top_values = [{"value": text_value(r[0]), "count": _int(r[1])} for r in top if r and len(r) >= 2]
        profiles.append(cp)
    if row_count is not None:
        for cp in profiles:
            cp.total_count = row_count
    return row_count, profiles, missing


def step_queries(step: dict) -> list[dict]:
    """화면용 — 프로파일 단계가 낼 수 있는 쿼리(묶은 쿼리, 최빈값 쿼리). 컬럼별 재실행은 묶은 쿼리가 실패했을 때만."""
    return [{"key": step["key"], "label": step["label"], "sql": step["sql"]},
            *({"key": t["key"], "label": t["label"], "sql": t["sql"]} for t in step["top"])]
