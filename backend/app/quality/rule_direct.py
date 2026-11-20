# SPDX-License-Identifier: Apache-2.0
"""품질 규칙 → 직접 진단 명세 (순수 함수 — DB·네트워크를 쓰지 않는다).

Trino 연결이 없는 데이터셋은 품질 워커가 원천 DB 를 직접 읽어 Python 으로 평가한다(TODO-095). 이 모듈은 규칙을
워커가 평가할 **명세(probe)** 로 바꾼다. 명세의 계산 결과는 ``rule_sql`` 이 만든 집계 식과 같은 모양(같은 개수·
같은 뜻의 값)이라, 판정은 Trino 경로와 같은 ``Compiled.judge`` 가 한다 — 규칙의 기준은 카탈로그 한곳에 남는다.

명세 종류(``kind``)와 워커가 돌려줄 값:

  - ``rows``         ROW_COUNT                    → ``[]``
  - ``nulls``        NOT_NULL                     → ``[NULL 수]``
  - ``distinct``     UNIQUE                       → ``[고유값 수]``
  - ``bound``        MIN_VALUE·MAX_VALUE          → ``[최소·최대, 비-NULL 수, 벗어난 수]``
  - ``text``         ACCEPTED_VALUES·REGEX·DATE_FORMAT·BROKEN_TEXT·WHITESPACE·DUMMY_VALUE → ``[비-NULL 수, 위반 수]``
  - ``compare``      COLUMN_COMPARE               → ``[둘 다 비-NULL 수, 위반 수]``
  - ``logical``      LOGICAL                      → ``[전제가 참인 수, 결론이 거짓인 수]``
  - ``formula``      FORMULA                      → ``[둘 다 비-NULL 수, 다른 수]``

명세 밖의 직접 진단:

  - REFERENTIAL  참조 데이터셋이 **같은 원본 데이터 소스**에 있을 때 — 원본에 조인 쿼리를 하나 낸다(``referential_sql``).
  - CUSTOM_PYTHON  품질 워커의 플러그인 함수 ``fn(df, params)`` — 워커가 읽은 행으로 pandas DataFrame 을 만들어 부른다.

  - DIVERSITY · COMPOSITE_UNIQUE  값(키) 묶음별 행 수를 원천에서 다시 집계하는 쿼리를 하나 낸다(``group_counts_sql`` —
    다양성 ``diversity_sql``, 여러 컬럼 키 ``composite_unique_sql``). 결과 한 행은 Trino 경로와 같은 모양이다.

FUNCTION(Trino 에 등록한 함수)·CUSTOM_SQL(Trino 에서 실행)은 직접 진단하지 않는다. CONTINUITY(데이터 연속성)는 명세가 없고
언제나 원천 집계로 센다(``aggregate`` — 날짜 · 시각 컬럼이나 형식을 적은 문자열 날짜만, 주 단위는 날짜 · 시각 컬럼만).

원천 유형: PostgreSQL · MySQL · MariaDB · Oracle · SQL Server(``DIRECT_SOURCE_TYPES``). Oracle · SQL Server 는 DB 마다 뜻이
같은 집계(행 수 · NULL 수 · 최신성 · 데이터 연속성)만 원천에서 세고, 나머지는 행을 읽어 워커가 센다 — 문자열 같음 · 정렬 규칙(SQL Server 의
기본 정렬은 대소문자 · 끝 공백을 무시한다)을 Trino 와 맞춰 둔 것은 PostgreSQL · MySQL 계열뿐이다.
"""

from __future__ import annotations

import json
import re as _re
from collections.abc import Callable
from dataclasses import dataclass

from app.quality import rule_sql
from app.quality.rule_sql import (
    _CHAR_RE,
    _TEMPORAL_PREFIXES,
    ExpressionError,
    RuleSpec,
    _lookup,
    _tokens,
)

# 명세(probe)로 계산하는 유형 — 최신성(FRESHNESS) · 데이터 연속성(CONTINUITY)은 늘 원천 집계로 센다
# 다양성 · 여러 컬럼 키는 묶음별 집계 쿼리를 따로 낸다(``diversity_sql`` · ``composite_unique_sql``)
_GROUPED_TYPES = ("DIVERSITY", "COMPOSITE_UNIQUE")
PROBE_TYPES = tuple(t for t in rule_sql.TRINO_CHECK_TYPES
                    if t not in ("FUNCTION", "REFERENTIAL", "CONTINUITY", *_GROUPED_TYPES))
# 직접 진단으로 평가할 수 있는 유형
DIRECT_CHECK_TYPES = (*PROBE_TYPES, "FRESHNESS", "REFERENTIAL", "CUSTOM_PYTHON", "CONTINUITY", *_GROUPED_TYPES)
# 어느 한 경로라도 평가하는 유형 — 품질 탭이 만드는 모든 유형
ENGINE_CHECK_TYPES = tuple(dict.fromkeys((*rule_sql.TRINO_PATH_TYPES, *DIRECT_CHECK_TYPES)))

_IDENT_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 원천 DB 를 직접 읽을 수 있는 데이터 소스 유형 — 워커의 드라이버(asyncpg·aiomysql·oracledb·pymssql)가 정한다
DIRECT_SOURCE_TYPES = ("postgresql", "mysql", "mariadb", "oracle", "mssql")
# 집계 SQL 의 뜻(문자열 같음 · 정렬)을 맞춰 둔 유형 — 그 밖(Oracle · SQL Server)은 DB 마다 뜻이 같은 집계만 원천에서 센다
_FULL_AGGREGATE_TYPES = ("postgresql", "mysql", "mariadb")
# DB 와 상관없이 같은 뜻인 집계 — 행 수 · NULL 수 · 최신성(최댓값)
_PORTABLE_AGGREGATES = ("ROW_COUNT", "NOT_NULL", "FRESHNESS", "CONTINUITY")

# date_parse 형식 → strptime 형식
_STRPTIME = {"%Y": "%Y", "%m": "%m", "%d": "%d", "%H": "%H", "%i": "%M", "%s": "%S", "%f": "%f"}


def _bare(name: str) -> str:
    return name[1:-1].replace('""', '"') if name.startswith('"') else name


def _column(name: str, column_types: dict[str, str] | None) -> str:
    """식 안의 컬럼 이름 → 스키마에 적힌 이름. 없으면 ``ExpressionError``."""
    bare = _bare(name)
    present, _ = _lookup(column_types, bare)
    if not present:
        raise ExpressionError(f"스키마에 '{bare}' 컬럼이 없습니다")
    for key in column_types or {}:
        if key.lower() == bare.lower():
            return key
    return bare


def _literal(tokens: list, at: int) -> tuple[dict, int]:
    """``tokens[at]`` 의 값 → (명세 상수, 차지한 낱말 수). 날짜 · 시각 상수는 ``{"date"}`` · ``{"timestamp"}``."""
    temporal = rule_sql.temporal_literal(tokens, at)
    if temporal:
        return {temporal[0].lower(): temporal[1]}, 2
    kind, value = tokens[at]
    if kind == "str":
        return {"str": value[1:-1].replace("''", "'")}, 1
    return {"num": value}, 1


def condition_tree(text: str, column_types: dict[str, str] | None = None) -> dict:
    """조건 → 평가 트리. 받아들이는 모양은 ``rule_sql.condition_sql`` 과 같다(그쪽이 먼저 검사한다).

    트리: ``{"or": [{"and": [비교, …]}, …]}`` — SQL 처럼 AND 가 OR 보다 먼저 묶인다. 비교는
    ``{"col", "op", "right"}``(``right`` 는 ``{"col"}``·``{"str"}``·``{"num"}``), ``{"col", "op": "IS NULL"|"IS NOT NULL"}``,
    ``{"col", "op": "IN"|"NOT IN", "values": [...]}``.
    """
    rule_sql.condition_sql(text, column_types)  # 모양 검사 — 틀리면 같은 메시지의 ExpressionError
    tokens = _tokens(text)
    groups: list[list[dict]] = [[]]
    i = 0

    def word(at: int) -> str:
        return tokens[at][1].upper() if at < len(tokens) and tokens[at][0] == "name" else ""

    while i < len(tokens):
        col = _column(tokens[i][1], column_types)
        i += 1
        if word(i) == "IS":
            negate = word(i + 1) == "NOT"
            groups[-1].append({"col": col, "op": "IS NOT NULL" if negate else "IS NULL"})
            i += 2 + negate
        elif word(i) == "IN" or (word(i) == "NOT" and word(i + 1) == "IN"):
            negate = word(i) == "NOT"
            i += 2 + negate  # IN ( 다음
            values = []
            while tokens[i][1] != ")":
                if tokens[i][1] == ",":
                    i += 1
                    continue
                lit, used = _literal(tokens, i)
                values.append(lit)
                i += used
            i += 1
            groups[-1].append({"col": col, "op": "NOT IN" if negate else "IN", "values": values})
        else:
            op = "<>" if tokens[i][1] == "!=" else tokens[i][1]
            i += 1
            kind, value = tokens[i]
            if kind == "name" and rule_sql.temporal_literal(tokens, i) is None:
                right, used = {"col": _column(value, column_types)}, 1
            else:
                right, used = _literal(tokens, i)
            groups[-1].append({"col": col, "op": op, "right": right})
            i += used
        if i < len(tokens):
            if word(i) == "OR":
                groups.append([])
            i += 1
    return {"or": [{"and": g} for g in groups]}


def formula_tree(expected: str | None, column_types: dict[str, str] | None = None) -> tuple[dict, float]:
    """FORMULA 기대값 → (계산 트리, 허용 오차). 받아들이는 모양은 ``rule_sql.parse_formula`` 와 같다.

    트리: ``{"col"}`` · ``{"num"}`` · ``{"neg": t}`` · ``{"op": "+"|"-"|"*"|"/", "l": t, "r": t}`` — 곱셈·나눗셈이 먼저.
    """
    _, tolerance = rule_sql.parse_formula(expected, column_types)  # 모양 검사
    tokens = _tokens(str(expected or ""))
    if len(tokens) >= 2 and tokens[-2][1] == "~" and tokens[-1][0] == "num":
        tokens = tokens[:-2]
    pos = 0

    def peek() -> str | None:
        return tokens[pos][1] if pos < len(tokens) else None

    def expr() -> dict:
        nonlocal pos
        node = term()
        while peek() in ("+", "-"):
            op = tokens[pos][1]
            pos += 1
            node = {"op": op, "l": node, "r": term()}
        return node

    def term() -> dict:
        nonlocal pos
        node = unary()
        while peek() in ("*", "/"):
            op = tokens[pos][1]
            pos += 1
            node = {"op": op, "l": node, "r": unary()}
        return node

    def unary() -> dict:
        nonlocal pos
        if peek() == "-":
            pos += 1
            return {"neg": unary()}
        if peek() == "(":
            pos += 1
            node = expr()
            pos += 1  # ")"
            return node
        kind, value = tokens[pos]
        pos += 1
        return {"col": _column(value, column_types)} if kind == "name" else {"num": value}

    return expr(), tolerance


def is_char(column_type: str | None) -> bool:
    """CHAR(n) 컬럼 — 드라이버가 뒤를 공백으로 채워 돌려줄 수 있어 워커가 잘라 낸다(Trino 경로의 rtrim 과 같다)."""
    return bool(_CHAR_RE.match((column_type or "").strip()))


def probe(rule: RuleSpec, column_types: dict[str, str] | None = None) -> dict | None:
    """규칙 → 직접 진단 명세. 직접 진단하지 않는 유형이면 None.

    기대값·컬럼이 잘못된 규칙은 ``rule_sql.compile_rule`` 이 먼저 ``Outcome`` 으로 실패 판정하므로, 이 함수는
    그 검사를 통과한 규칙만 받는다고 본다. 그래도 식을 읽지 못하면 ``ExpressionError``.
    """
    check = rule.check_type
    if check not in PROBE_TYPES:
        return None
    expected = rule.expected_value
    if check == "ROW_COUNT":
        return {"kind": "rows"}
    if check == "LOGICAL":
        m = rule_sql._LOGICAL_RE.match(str(expected or ""))
        if not m:
            raise ExpressionError("expected_value 는 'IF <조건> THEN <조건>' 형식이어야 합니다")
        return {"kind": "logical", "when": condition_tree(m.group(1), column_types),
                "then": condition_tree(m.group(2), column_types)}

    column = _column(rule.column_name or "", column_types)
    _, column_type = _lookup(column_types, column)
    base = {"column": column}
    if check == "NOT_NULL":
        return {**base, "kind": "nulls"}
    if check == "UNIQUE":
        return {**base, "kind": "distinct"}
    if check in ("MIN_VALUE", "MAX_VALUE"):
        return {**base, "kind": "bound", "agg": "min" if check == "MIN_VALUE" else "max",
                "bound": float(expected) if expected else 0.0}
    if check == "COLUMN_COMPARE":
        parsed = rule_sql.parse_compare(expected)
        if parsed is None:
            raise ExpressionError("expected_value 는 '<연산자> <컬럼>' 형식이어야 합니다")
        op, other = parsed
        return {**base, "kind": "compare", "op": op, "other": _column(other, column_types)}
    if check == "FORMULA":
        tree, tolerance = formula_tree(expected, column_types)
        return {**base, "kind": "formula", "formula": tree, "tolerance": tolerance}

    # 문자열로 보는 검사 — 값이 있는 행 가운데 위반을 센다. 정규식은 부분 일치(Trino regexp_like 와 같은 뜻):
    # ``match`` 는 일치하지 않으면 위반, ``forbid`` 는 하나라도 일치하면 위반
    text = {**base, "kind": "text"}
    if check == "ACCEPTED_VALUES":
        return {**text, "accepted": rule_sql.accepted_values(expected)}
    if check == "REGEX":
        return {**text, "match": str(expected or "")}
    if check == "BROKEN_TEXT":
        return {**text, "forbid": [rule_sql.BROKEN_TEXT_PATTERN]}
    if check == "DUMMY_VALUE":
        values = rule_sql.dummy_values(expected)
        if rule_sql.is_numeric_type(column_type):
            values = rule_sql.dummy_numbers(values)
        if not values:
            raise ExpressionError("숫자 컬럼에는 숫자 더미 값(예: 999999, -999)을 적어야 합니다")
        return {**text, "forbid": [rule_sql.dummy_pattern(values)]}
    if check == "WHITESPACE":
        extra = rule_sql.extra_characters_pattern(expected)
        return {**text, "forbid": [rule_sql.WHITESPACE_PATTERN] + ([extra] if extra else [])}
    # DATE_FORMAT
    if column_type.strip().lower().startswith(_TEMPORAL_PREFIXES):
        return {**text, "valid": True}  # 날짜·시각 타입은 DBMS 가 유효성을 보장한다 — 진단 건수만
    spec = rule_sql.date_format_spec(expected)
    date: dict = {"shape": spec.shape}
    if spec.parse_format:
        fmt = spec.parse_format
        for trino, python in _STRPTIME.items():
            fmt = fmt.replace(trino, python)
        date.update({"format": fmt, "suffix": spec.suffix})
    return {**text, "date": date}


# ---------------------------------------------------------------------------
# 원천 집계 — 원천 DB 가 세고 워커는 결과 한 행만 받는다 (TODO-097)
# ---------------------------------------------------------------------------
#
# 집계 SQL 이 DB 마다 같은 뜻이 되는 규칙만 원천에서 센다. 값의 모양을 보는 검사(정규식 · 날짜 형식 · 글자 깨짐 ·
# 공백)와 조건 · 계산식(타입 맞추기 · 3값 논리 · 정수 나눗셈)은 DB 마다 함수와 동작이 달라 지금처럼 행을 읽어
# 워커가 판정한다(``probe``). 집계 식은 Trino 경로의 식과 같은 개수 · 같은 뜻이라 판정(``Compiled.judge``)은 같다.
#
# 문자열 비교는 워커(파이썬 문자열)와 Trino 처럼 대소문자 · 끝 공백을 구분한다:
#   - MySQL · MariaDB 는 기본 정렬 규칙이 대소문자를 무시하므로 ``CAST(CONVERT(c USING utf8mb4) AS BINARY)`` 의 바이트로
#     비교하고, 상수는 UTF-8 바이트의 16진수 상수(``X'..'``)로 적는다 — 역슬래시 이스케이프 모드와도 상관없다.
#   - PostgreSQL 은 결정적 정렬 규칙이라 같음은 바이트 비교다. 크기 비교만 ``COLLATE "C"``(코드 포인트 순서)로 한다.

_NUMERIC_BASES = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "decimal", "dec", "fixed", "numeric",
                  "float", "double", "double precision", "real", "int2", "int4", "int8", "float4", "float8",
                  "serial", "bigserial", "smallserial"}
_INTEGER_BASES = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "int2", "int4", "int8",
                  "serial", "bigserial", "smallserial"}
_TEXT_BASES = {"char", "varchar", "character", "character varying", "text", "tinytext", "mediumtext", "longtext",
               "bpchar", "nchar", "nvarchar", "national char", "national varchar",
               # Oracle · SQL Server
               "varchar2", "nvarchar2", "ntext"}
_TEMPORAL_BASES = {"date", "datetime", "timestamp", "time", "timestamp without time zone", "timestamp with time zone",
                   "time without time zone", "time with time zone", "timestamptz", "timetz"}


def type_family(column_type: str | None) -> str | None:
    """원천 컬럼 타입 → ``numeric`` · ``integer`` · ``text`` · ``enum`` · ``temporal`` · None(그 밖 — 집계하지 않는다).

    ``integer`` 는 ``numeric`` 이기도 하다(``is_numeric``). MySQL ``tinyint(1)`` 은 드라이버가 정수로 주므로 정수다.
    """
    base = _re.sub(r"\(.*\)", "", (column_type or "").lower())
    base = " ".join(w for w in base.split() if w not in ("unsigned", "zerofill", "signed"))
    if base in _INTEGER_BASES:
        return "integer"
    if base in _NUMERIC_BASES:
        return "numeric"
    if base in _TEXT_BASES:
        return "text"
    if base == "enum":
        return "enum"
    if base in _TEMPORAL_BASES:
        return base
    return None


def _has_zone(family: str) -> bool:
    return "time zone" in family and "without" not in family or family in ("timestamptz", "timetz")


def _is_numeric(family: str | None) -> bool:
    return family in ("integer", "numeric")


def _is_mysql(source_type: str) -> bool:
    return source_type in ("mysql", "mariadb")


def _text_key(source_type: str, col: str, family: str | None) -> str:
    """같음 · 고유값을 볼 때의 컬럼 식 — 워커가 보는 문자열과 같은 바이트."""
    if not _is_mysql(source_type):
        return col if family in ("text", "enum") else f"CAST({col} AS TEXT)"
    if family in ("text", "enum"):
        return f"CAST(CONVERT({col} USING utf8mb4) AS BINARY)"
    return f"CAST(CAST({col} AS CHAR) AS BINARY)"


def _text_literal(source_type: str, value: str) -> str:
    if _is_mysql(source_type):
        return "X'" + value.encode("utf-8").hex().upper() + "'" if value else "X''"
    return "'" + value.replace("'", "''") + "'"


def _count_if(condition: str) -> str:
    """조건이 참인 행 수 — PostgreSQL · MySQL 이 함께 읽는다. 행이 없어도 0."""
    return f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0)"


@dataclass(frozen=True)
class Aggregate:
    """원천에서 셀 규칙 하나 — 집계 식(Trino 경로의 ``Compiled.exprs`` 와 같은 개수 · 뜻)과 위반 행 샘플 쿼리."""

    exprs: tuple[str, ...]
    sample_sql: str | None = None


def source_table(source_type: str, schema: str | None, table: str) -> str:
    target = quote_ident(source_type, table)
    return f"{quote_ident(source_type, schema)}.{target}" if schema else target


def aggregate(rule: RuleSpec, column_types: dict[str, str] | None, source_type: str,
              table: tuple[str | None, str], samples: int) -> Aggregate | None:
    """규칙 → 원천 집계. 원천에서 셀 수 없는(DB 마다 뜻이 갈리는) 규칙이면 None — 호출자는 ``probe`` 로 읽는다.

    ``rule_sql.compile_rule`` 의 검사(컬럼 · 기대값)를 통과한 규칙만 받는다고 본다.
    """
    check = rule.check_type
    source_type = (source_type or "").lower()
    if source_type not in DIRECT_SOURCE_TYPES:
        return None
    if source_type not in _FULL_AGGREGATE_TYPES and check not in _PORTABLE_AGGREGATES:
        return None  # 문자열 같음 · 정렬 규칙을 맞추지 않은 DB — 행을 읽어 워커가 센다(probe)
    from_ = source_table(source_type, *table)

    def sample(predicate: str, order_by: str | None = None) -> str:
        order = f" ORDER BY {order_by}" if order_by else ""
        return limited_select(source_type, "*", f"FROM {from_} WHERE {predicate}{order}", samples)

    if check == "ROW_COUNT":
        return Aggregate(())
    if check == "FRESHNESS":
        # 시각 컬럼의 최댓값 — 판정(경과 시간)은 Trino 경로와 같은 rule_sql.judge_freshness
        col = quote_ident(source_type, _column(rule.column_name or "", column_types))
        return Aggregate((f"max({col})", f"count({col})"))
    if check not in ("NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ACCEPTED_VALUES", "COLUMN_COMPARE", "DATE_FORMAT",
                     "DUMMY_VALUE", "CONTINUITY"):
        return None
    column = _column(rule.column_name or "", column_types)
    _, column_type = _lookup(column_types, column)
    family = type_family(column_type)
    col = quote_ident(source_type, column)

    if check == "NOT_NULL":
        return Aggregate((_count_if(f"{col} IS NULL"),), sample(f"{col} IS NULL"))
    if check == "UNIQUE":
        if family is None:
            return None  # JSON · 배열 · 이진 등 — 워커처럼 값의 같음을 맞출 수 없다
        key = _text_key(source_type, col, family) if family in ("text", "enum") else col
        # 중복 키는 파생 테이블로 한 번 감싼다 — MySQL 8 은 IN 하위 쿼리의 GROUP BY 식(바이트 키)을 바깥 행과 잇는
        # 변환에서 ONLY_FULL_GROUP_BY 오류(1055)를 낸다
        dup = (f"{key} IN (SELECT k FROM (SELECT {key} AS k FROM {from_} WHERE {col} IS NOT NULL GROUP BY {key} "
               "HAVING count(*) > 1) d)")
        return Aggregate((f"count(DISTINCT {key})",), sample(dup, order_by=key))
    if check in ("MIN_VALUE", "MAX_VALUE"):
        if not _is_numeric(family):
            return None  # 문자열을 숫자로 읽는 규칙(TRY_CAST)은 DB 마다 다르다
        bound = float(rule.expected_value) if rule.expected_value else 0.0
        agg, op = ("min", "<") if check == "MIN_VALUE" else ("max", ">")
        bad = f"{col} {op} {bound!r}"
        return Aggregate((f"{agg}({col})", f"count({col})", _count_if(bad)), sample(bad))
    if check == "ACCEPTED_VALUES":
        if family not in ("text", "enum", "integer"):
            return None  # 실수 · 날짜의 문자열 모양은 DB 마다 다르다
        accepted = rule_sql.accepted_values(rule.expected_value)
        if not accepted:
            return None  # compile_rule 이 먼저 규칙 오류로 판정한다 — NOT IN () 을 만들지 않는다
        key = _text_key(source_type, col, family)
        bad = f"{col} IS NOT NULL AND {key} NOT IN ({', '.join(_text_literal(source_type, v) for v in accepted)})"
        return Aggregate((f"count({col})", _count_if(bad)), sample(bad))
    if check == "DATE_FORMAT":
        if not column_type.strip().lower().startswith(_TEMPORAL_PREFIXES):
            return None  # 문자열의 날짜 모양 · 달력 검사는 워커가 한다
        return Aggregate((f"count({col})", "0"))
    if check == "DUMMY_VALUE":
        values = rule_sql.dummy_values(rule.expected_value)
        if _is_numeric(family):
            numbers = rule_sql.dummy_numbers(values)
            if not numbers:
                return None  # compile_rule 이 먼저 규칙 오류로 판정한다
            bad = f"{col} IN ({', '.join(numbers)})"
        elif family in ("text", "enum"):
            key = _text_key(source_type, col, family)
            bad = f"{col} IS NOT NULL AND {key} IN ({', '.join(_text_literal(source_type, v) for v in values)})"
        else:
            return None  # 날짜 등의 문자열 모양은 DB 마다 다르다 — 워커가 읽는다
        return Aggregate((f"count({col})", _count_if(bad)), sample(bad))
    if check == "CONTINUITY":
        return _continuity(rule, column_type, family, source_type, col, from_)
    # COLUMN_COMPARE — 두 컬럼이 같은 종류일 때만(워커는 종류가 다르면 규칙 오류로 본다)
    parsed = rule_sql.parse_compare(rule.expected_value)
    if parsed is None:
        return None
    op, other_name = parsed
    other = _column(other_name, column_types)
    _, other_type = _lookup(column_types, other)
    other_family = type_family(other_type)
    ocol = quote_ident(source_type, other)
    if _is_numeric(family) and _is_numeric(other_family):
        left, right = col, ocol
    elif family == "text" and other_family == "text":
        if _is_mysql(source_type):
            left, right = _text_key(source_type, col, family), _text_key(source_type, ocol, other_family)
        else:
            left, right = f'{col} COLLATE "C"', f'{ocol} COLLATE "C"'
    elif family is not None and family == other_family and family in _TEMPORAL_BASES and not _has_zone(family):
        left, right = col, ocol  # 같은 날짜 · 시각 타입(시간대 없는 것 — 워커는 시간대가 섞이면 규칙 오류로 본다)
    else:
        return None
    both = f"{col} IS NOT NULL AND {ocol} IS NOT NULL"
    bad = f"{both} AND NOT ({left} {op} {right})"
    return Aggregate((_count_if(both), _count_if(bad)), sample(bad))


_MYSQL_TRUNC = {"HOUR": "%Y-%m-%d %H:00:00", "DAY": "%Y-%m-%d", "MONTH": "%Y-%m-01", "YEAR": "%Y-01-01"}
_ORACLE_TRUNC = {"HOUR": "HH", "DAY": "DD", "MONTH": "MM", "YEAR": "YYYY"}


def _period_key(source_type: str, unit: str, trunc: str, col: str) -> str:
    """날짜 · 시각 값을 단위의 처음으로 자르는 식 — DB 마다 함수가 다르다. 주(WEEK)는 월요일(ISO 주)로 자른다."""
    if unit == "WEEK":
        if _is_mysql(source_type):
            return f"DATE_SUB(DATE({col}), INTERVAL WEEKDAY({col}) DAY)"  # WEEKDAY: 월요일 0
        if source_type == "oracle":
            return f"TRUNC({col}, 'IW')"
        if source_type == "mssql":
            # 기준일 1900-01-01 이 월요일 — 지난 날 수를 7 로 내려 다시 더한다(DATEFIRST 설정과 상관없음)
            return f"DATEADD(day, DATEDIFF(day, 0, {col}) / 7 * 7, 0)"
        return f"date_trunc('week', {col})"
    if _is_mysql(source_type):
        return f"DATE_FORMAT({col}, '{_MYSQL_TRUNC[unit]}')"
    if source_type == "oracle":
        return f"TRUNC({col}, '{_ORACLE_TRUNC[unit]}')"
    if source_type == "mssql":
        # DATETRUNC 는 2022 부터 — 기준일(1900-01-01)부터 지난 단위 수를 다시 더한다
        part = unit.lower()
        return f"DATEADD({part}, DATEDIFF({part}, 0, {col}), 0)"
    return f"date_trunc('{trunc}', {col})"


def _prefix(source_type: str, col: str, length: int) -> str:
    """앞뒤 공백을 뺀 문자열의 앞 ``length`` 글자."""
    if source_type == "oracle":
        return f"SUBSTR(TRIM({col}), 1, {length})"
    if source_type == "mssql":
        return f"SUBSTRING(LTRIM(RTRIM({col})), 1, {length})"
    return f"SUBSTRING(TRIM({col}) FROM 1 FOR {length})"


def _trimmed(source_type: str, col: str) -> str:
    return f"LTRIM(RTRIM({col}))" if source_type == "mssql" else f"TRIM({col})"


def _continuity(rule: RuleSpec, column_type: str, family: str | None, source_type: str, col: str,
                from_: str) -> Aggregate:
    """데이터 연속성 — ``[고유 기간 수, 최소 기간, 최대 기간]``. 날짜 · 시각 컬럼은 단위로 자르고, 문자열 날짜는 형식의 앞부분
    (단위까지)으로 묶는다. 셀 수 없는 컬럼이면 ``ValueError`` — 행을 읽어 셀 명세가 없다."""
    c = rule_sql.continuity_spec(rule.expected_value)
    if column_type.strip().lower().startswith(_TEMPORAL_PREFIXES) or family in _TEMPORAL_BASES:
        key = _period_key(source_type, c.unit, c.trunc, col)
    elif family == "text" and c.prefix_len and c.unit == "WEEK":
        raise ValueError("원천 직접 진단의 주(WEEK) 단위 연속성은 날짜 · 시각 타입 컬럼만 봅니다 — 문자열 날짜는 DAY 단위로 보거나 "
                         "Trino 가 연결된 데이터셋에서 진단하세요")
    elif family == "text" and c.prefix_len:
        # 형식과 자릿수 · 구분자가 같은 값만 — 다른 모양(2024-01-05 를 YYYYMMDD 로)의 앞부분이 최소 · 최대가 되지 않게
        key = (f"CASE WHEN {_trimmed(source_type, col)} LIKE '{c.like}' "
               f"THEN {_prefix(source_type, col, int(c.prefix_len))} END")
    else:
        raise ValueError(f"'{rule.column_name}' 는 날짜 · 시각 타입이 아닙니다 — 문자열 날짜면 기대값에 형식을 적으세요 "
                         f"(예: {c.unit} YYYYMMDD)")
    return Aggregate((f"count(DISTINCT {key})", f"min({key})", f"max({key})"))


def _group_key(source_type: str, column_types: dict[str, str] | None, column: str) -> Callable[[str], str]:
    """묶을 때의 컬럼 식 — Trino · 워커처럼 대소문자 · 끝 공백을 구분해 묶는다. MySQL 계열 문자열은 바이트로(기본 정렬
    규칙이 대소문자를 무시한다), SQL Server 문자열도 값의 바이트(``VARBINARY``)로 — 기본 정렬 규칙이 대소문자를 무시하고,
    어느 정렬 규칙이든 ``=`` · ``GROUP BY`` 가 끝 공백을 무시한다(프로파일의 고유값 수와 같은 식). PostgreSQL(결정적
    정렬) · Oracle(``NLS_COMP=BINARY`` 기본)은 컬럼 그대로."""
    family = type_family(_lookup(column_types, column)[1])
    if _is_mysql(source_type) and family in ("text", "enum"):
        return lambda c: _text_key(source_type, c, family)
    if source_type == "mssql" and family == "text":
        return lambda c: f"CAST({c} AS VARBINARY(MAX))"
    return lambda c: c


def diversity_sql(source_type: str, table: tuple[str | None, str], column: str,
                  column_types: dict[str, str] | None) -> str:
    """데이터 · 라벨 다양성 — 값마다 행 수를 세어 다시 집계. 결과 한 행 ``[진단 건수, 최빈값 건수, 가장 적은 값 건수, 고유값 수]``."""
    name = _column(column, column_types)
    col = quote_ident(source_type, name)
    key = _group_key(source_type, column_types, name)
    return rule_sql.group_counts_sql(source_table(source_type, *table), [key(col)], f"{col} IS NOT NULL",
                                     rule_sql.DIVERSITY_SELECT)


def composite_unique_sql(source_type: str, table: tuple[str | None, str], expected: str | None,
                         column_types: dict[str, str] | None, samples: int) -> tuple[str, str]:
    """여러 컬럼 키 유일성 — 키 묶음마다 행 수를 세어 다시 집계. (결과 한 행 ``[진단 건수, 키가 겹치는 행 수]`` 쿼리, 위반 행
    샘플 쿼리). 키 컬럼 가운데 하나라도 NULL 인 행은 세지 않는다(SQL 의 UNIQUE 제약과 같다)."""
    names = [_column(c, column_types) for c in rule_sql.composite_columns(expected)]
    quoted = [quote_ident(source_type, n) for n in names]
    keys = [_group_key(source_type, column_types, n) for n in names]
    present = " AND ".join(f"{q} IS NOT NULL" for q in quoted)
    from_ = source_table(source_type, *table)
    count = rule_sql.group_counts_sql(from_, [k(q) for k, q in zip(keys, quoted)], present, rule_sql.COMPOSITE_SELECT)
    # 샘플 조인 — 컬럼마다 묶을 때와 같은 식으로 비교한다
    by_column = dict(zip(quoted, keys))

    def key(c: str) -> str:
        return by_column[c.removeprefix("c.")](c)

    sample = rule_sql.duplicate_keys_sample(from_, quoted, key, present)
    rest = sample.removeprefix("SELECT c.* ")
    return count, limited_select(source_type, "c.*", rest, samples)


def aggregate_sql(source_type: str, table: tuple[str | None, str], exprs: list[str]) -> str:
    """원천 집계 쿼리 — 첫 값은 전체 행 수."""
    return f"SELECT {', '.join(['count(*)', *exprs])} FROM {source_table(source_type, *table)}"


def custom_python(expected: str | None) -> dict:
    """CUSTOM_PYTHON 기대값(JSON) → ``{"module", "fn", "params"}``. 틀리면 ``ExpressionError``.

    모듈·함수 이름은 식별자만 받는다 — 워커가 플러그인 디렉터리 밖의 파일을 불러오지 못하게.
    """
    if not str(expected or "").strip():
        raise ExpressionError('플러그인 설정을 적어야 합니다 — {"module": "…", "fn": "…", "params": {…}}')
    try:
        cfg = json.loads(str(expected))
    except json.JSONDecodeError as e:
        raise ExpressionError(f"플러그인 설정을 JSON 으로 읽을 수 없습니다: {e.msg}") from None
    if not isinstance(cfg, dict):
        raise ExpressionError("플러그인 설정은 JSON 객체여야 합니다")
    module, fn, params = str(cfg.get("module") or ""), str(cfg.get("fn") or ""), cfg.get("params") or {}
    if not _IDENT_RE.fullmatch(module):
        raise ExpressionError(f"module 은 파일 이름(영문·숫자·밑줄, .py 없이)이어야 합니다: {module!r}")
    if not _IDENT_RE.fullmatch(fn):
        raise ExpressionError(f"fn 은 함수 이름(영문·숫자·밑줄)이어야 합니다: {fn!r}")
    if not isinstance(params, dict):
        raise ExpressionError("params 는 JSON 객체여야 합니다")
    return {"module": module, "fn": fn, "params": params}


def referential_sql(source_type: str, child: tuple[str | None, str], column: str,
                    parent: tuple[str | None, str], parent_column: str) -> str:
    """참조관계 — 같은 원본 안에서 자식 값이 부모 컬럼에 있는지. 결과 한 행 ``[진단 건수, 없는 값 수]``.

    Trino 경로의 쿼리와 같은 뜻이다(NULL 인 자식 값은 세지 않는다). PostgreSQL·MySQL 이 함께 읽는 표준 SQL 이다.
    """
    def table(ref: tuple[str | None, str]) -> str:
        schema, name = ref
        quoted = quote_ident(source_type, name)
        return f"{quote_ident(source_type, schema)}.{quoted}" if schema else quoted

    col, pcol = quote_ident(source_type, column), quote_ident(source_type, parent_column)
    return (
        f"SELECT count(*), sum(CASE WHEN p.k IS NULL THEN 1 ELSE 0 END) FROM {table(child)} c "
        f"LEFT JOIN (SELECT DISTINCT {pcol} AS k FROM {table(parent)} WHERE {pcol} IS NOT NULL) p "
        f"ON c.{col} = p.k WHERE c.{col} IS NOT NULL"
    )


def referential_sample_sql(source_type: str, child: tuple[str | None, str], column: str,
                           parent: tuple[str | None, str], parent_column: str, limit: int) -> str:
    """참조관계의 위반 행(부모에 없는 자식 값을 가진 행) 몇 개 — ``referential_sql`` 과 같은 조인."""
    count_sql = referential_sql(source_type, child, column, parent, parent_column)
    joined = count_sql.split(" FROM ", 1)[1]
    return limited_select(source_type, "c.*", f"FROM {joined} AND p.k IS NULL", limit)


def limited_select(source_type: str, columns: str, rest: str, limit: int) -> str:
    """앞 ``limit`` 행만 읽는 SELECT — ``LIMIT n``(PostgreSQL · MySQL), ``FETCH FIRST n ROWS ONLY``(Oracle 12c+),
    ``TOP (n)``(SQL Server)."""
    n = int(limit)
    if source_type == "mssql":
        return f"SELECT TOP ({n}) {columns} {rest}"
    if source_type == "oracle":
        return f"SELECT {columns} {rest} FETCH FIRST {n} ROWS ONLY"
    return f"SELECT {columns} {rest} LIMIT {n}"


def columns_of(probes: list[dict]) -> list[str]:
    """명세들이 읽는 컬럼(처음 나온 순서, 중복 없이)."""
    seen: dict[str, None] = {}

    def tree(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("col"), str):
                seen.setdefault(node["col"])
            for v in node.values():
                tree(v)
        elif isinstance(node, list):
            for v in node:
                tree(v)

    for p in probes:
        for key in ("column", "other"):
            if p.get(key):
                seen.setdefault(p[key])
        for key in ("when", "then", "formula"):
            tree(p.get(key))
    return list(seen)


def quote_ident(source_type: str, name: str) -> str:
    """원천 DB 의 식별자 인용 — MySQL 계열은 백틱, SQL Server 는 대괄호(``]`` 는 두 번), PostgreSQL · Oracle 은
    큰따옴표(안의 같은 문자는 두 번). Oracle 은 인용하면 대소문자를 가리므로 수집한 이름(대개 대문자) 그대로 쓴다."""
    if source_type in ("mysql", "mariadb"):
        return "`" + str(name).replace("`", "``") + "`"
    if source_type == "mssql":
        return "[" + str(name).replace("]", "]]") + "]"
    return '"' + str(name).replace('"', '""') + '"'


def select_sql(source_type: str, schema: str | None, table: str, columns: list[str]) -> str:
    """워커가 원천에 낼 읽기 쿼리. 읽을 컬럼이 없으면(행 수만 보는 규칙) 상수 하나를 읽는다."""
    cols = ", ".join(quote_ident(source_type, c) for c in columns) if columns else "1"
    target = quote_ident(source_type, table)
    if schema:
        target = f"{quote_ident(source_type, schema)}.{target}"
    return f"SELECT {cols} FROM {target}"
