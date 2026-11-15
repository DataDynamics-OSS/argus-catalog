# SPDX-License-Identifier: Apache-2.0
"""품질 규칙 → Trino SQL 변환과 판정 (순수 함수 — DB·네트워크를 쓰지 않는다).

규칙 하나는 집계 식 몇 개와 판정 함수로 바뀐다(``compile_rule``). 같은 테이블의 규칙은 식을 모두 한
``SELECT`` 에 모아 **테이블을 한 번만 읽는다**(``scan_sql``). 조인이 필요한 참조관계만 쿼리를 따로 낸다.

판정은 건수로 한다: 진단 건수(``checked_count``)와 오류 건수(``error_count``)를 함께 돌려줘, 데이터 소스
단위 오류율(Σ 오류 / Σ 진단)을 낼 수 있게 한다. 기존 유형(NOT_NULL·UNIQUE·MIN/MAX·ROW_COUNT·ACCEPTED_VALUES·
REGEX)의 통과 기준은 원천 직접 경로(rule_direct)와 같다. 도메인·정합성 검사에서 NULL 은 오류로 세지 않는다
(필수값 검사의 몫).

커스텀 SQL(CUSTOM_SQL)은 사용자가 적은 조회 쿼리를 따로 낸다 — 첫 행 첫 컬럼이 위반 건수. Trino 로 평가하지 않는
유형(CUSTOM_PYTHON)은 ``compile_rule`` 이 ``None`` 을 돌려주고, 호출자는 그 규칙의 직전 결과를 이어받는다.

최신성(FRESHNESS)은 시각 컬럼의 최댓값을 집계하고, 판정 때(결과를 받은 시각 기준) 경과 시간을 잰다. 시간대가 없는
값은 원천 데이터 시간대(설정 ``quality_source_timezone``)의 벽시계 시각으로 본다(``parse_timestamp``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.text_list import decode_list

# 이 모듈이 SQL 로 바꿀 수 있는 유형
TRINO_CHECK_TYPES = (
    "NOT_NULL", "UNIQUE", "MIN_VALUE", "MAX_VALUE", "ROW_COUNT",
    "ACCEPTED_VALUES", "REGEX", "DATE_FORMAT", "COLUMN_COMPARE", "REFERENTIAL",
    "BROKEN_TEXT", "WHITESPACE", "LOGICAL", "FORMULA", "FUNCTION",
    # AI-Ready 품질 지표(TODO-096 5단계) — 통계적 타당성(더미 값 빈도), 적시성(데이터 연속성)
    "DUMMY_VALUE", "CONTINUITY",
)

# Trino 경로가 평가하는 유형 — 위의 기본 유형에 커스텀 SQL(사용자가 적은 조회 쿼리)과 최신성을 더한 것.
# 둘은 품질 지표(indicators — 관리지침 진단 기준)에 들지 않아 지표를 고르지 않은 실행에서만 평가한다.
TRINO_PATH_TYPES = (*TRINO_CHECK_TYPES, "CUSTOM_SQL", "FRESHNESS")

# 컬럼 하나를 보지 않는 유형 — 조건·식 안에 컬럼이 들어 있다
TABLE_LEVEL_TYPES = ("ROW_COUNT", "LOGICAL")

# BROKEN_TEXT — 깨진 글자: 대체 문자(U+FFFD)와 완성형이 아닌 한글(자모만 따로 있는 호환 자모 U+3131–U+318E)
BROKEN_TEXT_PATTERN = "[\uFFFD\u3131-\u318E]"
# WHITESPACE — 값 앞뒤의 공백(전각 공백 포함)과 값 안의 제어 문자(탭·줄바꿈 포함)
WHITESPACE_PATTERN = "^[\\s\u3000]|[\\s\u3000]$|[\\x00-\\x1F\\x7F]"

# DATE_FORMAT 기대값은 아래 낱말과 구분자를 이어 쓴 형식이다 — 예: YYYYMMDD, YYYY/MM/DD, YYYY.MM.DD HH24:MI.
# 낱말 → (자릿수 모양 정규식, date_parse 형식). 긴 것부터 맞춘다(HH24 가 HH 보다, SSS 가 SS 보다 먼저).
# SSS 는 밀리초 3자리(``13:30:00.123``). 시(HH) 바로 뒤의 MM 은 분으로 읽는다 — ``HH:MM:SS`` 로 적는 표기를 받아 준다.
DATE_TOKENS: tuple[tuple[str, str, str], ...] = (
    ("YYYY", r"\d{4}", "%Y"), ("HH24", r"\d{2}", "%H"), ("MM", r"\d{2}", "%m"), ("DD", r"\d{2}", "%d"),
    ("HH", r"\d{2}", "%H"), ("MI", r"\d{2}", "%i"), ("SSS", r"\d{3}", "%f"), ("SS", r"\d{2}", "%s"),
)
DATE_SEPARATORS = "-/.: T"
# 화면이 선택지로 보여 주는 흔한 형식 — 이 밖의 조합도 받는다(date_format_spec)
DATE_FORMATS: tuple[str, ...] = (
    "YYYYMMDD", "YYYY-MM-DD", "YYYY/MM/DD", "YYYY.MM.DD", "YYYYMM", "YYYY-MM", "YYYY",
    "YYYYMMDDHH24MISS", "YYYY-MM-DD HH24:MI:SS", "YYYY-MM-DD HH24:MI:SS.SSS", "YYYY-MM-DD HH24:MI",
    "YYYY-MM-DDTHH24:MI:SS", "HH24:MI:SS",
)

# 조건·계산식의 낱말 — 문자열 상수, 숫자, 이름(큰따옴표 인용 포함), 연산자·괄호·쉼표
_TOKEN_RE = re.compile(
    r"""\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+(?:\.\d+)?)|(?P<name>"(?:[^"]|"")+"|[^\W\d]\w*)"""
    r"""|(?P<op><=|>=|<>|!=|=|<|>|\+|-|\*|/|\(|\)|,|~))""",
    re.UNICODE,
)
_CONDITION_OPS = ("<=", ">=", "<>", "!=", "=", "<", ">")
_LOGICAL_RE = re.compile(r"^\s*IF\b(.+?)\bTHEN\b(.+)$", re.IGNORECASE | re.DOTALL)

_COMPARE_RE = re.compile(r"^\s*(<=|>=|<>|!=|=|<|>)\s*(\S.*?)\s*$")
_TEMPORAL_PREFIXES = ("date", "time", "timestamp", "datetime", "smalldatetime")  # smalldatetime: SQL Server
_CHAR_RE = re.compile(r"^(char|character|nchar|bpchar)\b(?!\s+varying)", re.IGNORECASE)


@dataclass(frozen=True)
class TableRef:
    """Trino 안에서의 테이블 — ``catalog.schema.table``."""

    catalog: str
    schema: str
    table: str

    @property
    def sql(self) -> str:
        return ".".join(quote_ident(p) for p in (self.catalog, self.schema, self.table))

    @property
    def display(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"


@dataclass(frozen=True)
class RuleSpec:
    """변환에 필요한 규칙 내용 (ORM 과 분리)."""

    id: int
    check_type: str
    column_name: str | None = None
    expected_value: str | None = None
    threshold: float = 100.0


@dataclass(frozen=True)
class Outcome:
    """규칙 하나의 판정."""

    passed: bool
    actual_value: str
    detail: str
    checked_count: int | None = None
    error_count: int | None = None


@dataclass(frozen=True)
class Compiled:
    """변환된 규칙. ``exprs`` 는 스캔 ``SELECT`` 에 들어갈 집계 식, ``judge(total_rows, values)`` 가 판정한다.

    ``own_sql`` 이 있으면 스캔에 넣지 않고 그 쿼리를 따로 실행한다(결과 첫 행이 ``values``).
    ``sample_sql`` 은 위반 행 몇 개를 읽는 쿼리 — 판정이 실패하면 워커가 이어서 낸다(위반 샘플). 없으면 샘플을 남기지 않는다.
    """

    rule_id: int
    exprs: tuple[str, ...]
    judge: Callable[[int, list], Outcome]
    own_sql: str | None = None
    label: str = ""
    sample_sql: str | None = None


def quote_ident(name: str) -> str:
    """Trino 식별자 인용 — 큰따옴표, 안의 큰따옴표는 두 번."""
    return '"' + str(name).replace('"', '""') + '"'


def string_literal(value: str) -> str:
    """Trino 문자열 상수 — 작은따옴표, 안의 작은따옴표는 두 번."""
    return "'" + str(value).replace("'", "''") + "'"


def accepted_values(expected: str | None) -> list[str]:
    """ACCEPTED_VALUES 기대값 → 허용 값 목록 (앞뒤 공백 · 빈 항목 · 중복 제외) — 배치와 같은 규칙.

    기대값은 JSON 배열 문자열(``["1,000","N/A"]`` — 값 안의 쉼표 보존, TODO-143). 예전 쉼표 구분 문자열도 읽는다."""
    return decode_list(expected)


def parse_compare(expected: str | None) -> tuple[str, str] | None:
    """COLUMN_COMPARE 기대값 ``"<= end_ymd"`` → (연산자, 상대 컬럼). 형식이 틀리면 None."""
    m = _COMPARE_RE.match(str(expected or ""))
    if not m:
        return None
    op = "<>" if m.group(1) == "!=" else m.group(1)
    return op, m.group(2)


def parse_reference(expected: str | None) -> tuple[str, str] | None:
    """REFERENTIAL 기대값 ``"<참조 데이터셋 URN 또는 ID>#<컬럼>"`` → (데이터셋 식별자, 컬럼)."""
    text = str(expected or "").strip()
    ref, sep, column = text.rpartition("#")
    if not sep or not ref.strip() or not column.strip():
        return None
    return ref.strip(), column.strip()


class ExpressionError(ValueError):
    """조건·계산식을 읽지 못함 — 메시지는 사용자에게 그대로 보인다."""


def _tokens(text: str) -> list[tuple[str, str]]:
    """식을 낱말로 자른다 — (종류, 값). 모르는 글자가 있으면 ``ExpressionError``."""
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ExpressionError(f"읽을 수 없는 부분: '{text[pos:pos + 20].strip()}'")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def _column_sql(name: str, column_types: dict[str, str] | None) -> str:
    """식 안의 컬럼 이름 → 인용한 식별자. 스키마에 없으면 ``ExpressionError``."""
    bare = name[1:-1].replace('""', '"') if name.startswith('"') else name
    present, _ = _lookup(column_types, bare)
    if not present:
        raise ExpressionError(f"스키마에 '{bare}' 컬럼이 없습니다")
    return quote_ident(bare)


# 날짜 · 시각 상수 — DATE '2024-01-01', TIMESTAMP '2024-01-01 09:00:00[.fff]'
_DATE_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?$")
TEMPORAL_LITERAL_WORDS = ("DATE", "TIMESTAMP")


def temporal_literal(tokens: list[tuple[str, str]], at: int) -> tuple[str, str] | None:
    """``tokens[at]`` 에서 시작하는 날짜 · 시각 상수 → (``DATE``|``TIMESTAMP``, 값). 상수가 아니면 None.

    ``DATE`` · ``TIMESTAMP`` 바로 뒤에 작은따옴표 문자열이 올 때만 상수다(``date`` 라는 컬럼과 구분). 값이 달력에 없거나
    모양이 틀리면 ``ExpressionError``.
    """
    if at + 1 >= len(tokens) or tokens[at][0] != "name" or tokens[at + 1][0] != "str":
        return None
    word = tokens[at][1].upper()
    if word not in TEMPORAL_LITERAL_WORDS:
        return None
    value = tokens[at + 1][1][1:-1].replace("''", "'").strip()
    pattern = _DATE_LITERAL_RE if word == "DATE" else _TIMESTAMP_LITERAL_RE
    if not pattern.match(value):
        example = "DATE '2024-01-31'" if word == "DATE" else "TIMESTAMP '2024-01-31 09:00:00'"
        raise ExpressionError(f"{word} 상수의 모양이 틀렸습니다: '{value}' (예: {example})")
    try:
        if word == "DATE":
            datetime.strptime(value, "%Y-%m-%d")
        else:
            datetime.fromisoformat(value.replace("T", " "))
    except ValueError:
        raise ExpressionError(f"{word} 상수가 달력에 없는 값입니다: '{value}'") from None
    return word, value.replace("T", " ")


def condition_sql(text: str, column_types: dict[str, str] | None = None) -> str:
    """조건 → Trino SQL. 정해진 모양만 받아들여 임의의 SQL 이 섞이지 못하게 한다.

    모양: ``<컬럼> <연산자> <값 또는 컬럼>`` · ``<컬럼> IS [NOT] NULL`` · ``<컬럼> [NOT] IN (<값>, …)`` 을
    ``AND`` · ``OR`` 로 잇는다. 값은 작은따옴표 문자열, 숫자, 날짜 · 시각 상수(``DATE '2024-01-01'``,
    ``TIMESTAMP '2024-01-01 09:00:00'``). 괄호로 묶는 것은 받지 않는다.
    """
    tokens = _tokens(text)
    if not tokens:
        raise ExpressionError("조건이 비어 있습니다")
    parts: list[str] = []
    i = 0

    def word(at: int) -> str:
        return tokens[at][1].upper() if at < len(tokens) and tokens[at][0] == "name" else ""

    def literal(at: int) -> tuple[str, int]:
        """값 하나 → (SQL, 차지한 낱말 수)."""
        temporal = temporal_literal(tokens, at)
        if temporal:
            return f"{temporal[0]} {string_literal(temporal[1])}", 2
        if at >= len(tokens) or tokens[at][0] not in ("str", "num"):
            raise ExpressionError("값(작은따옴표 문자열, 숫자, DATE '…' · TIMESTAMP '…')이 와야 합니다")
        return tokens[at][1], 1

    while True:
        if i >= len(tokens) or tokens[i][0] != "name" or word(i) in ("AND", "OR", "IS", "IN", "NOT", "NULL"):
            raise ExpressionError("조건은 컬럼 이름으로 시작해야 합니다")
        col = _column_sql(tokens[i][1], column_types)
        i += 1
        if word(i) == "IS":
            negate = word(i + 1) == "NOT"
            if word(i + 1 + negate) != "NULL":
                raise ExpressionError("IS 뒤에는 NULL 또는 NOT NULL 이 와야 합니다")
            parts.append(f"{col} IS {'NOT ' if negate else ''}NULL")
            i += 2 + negate
        elif word(i) == "IN" or (word(i) == "NOT" and word(i + 1) == "IN"):
            negate = word(i) == "NOT"
            i += 1 + negate
            if i >= len(tokens) or tokens[i][1] != "(":
                raise ExpressionError("IN 뒤에는 (값, …) 이 와야 합니다")
            values = []
            i += 1
            while True:
                value, used = literal(i)
                values.append(value)
                i += used
                if i < len(tokens) and tokens[i][1] == ",":
                    i += 1
                    continue
                break
            if i >= len(tokens) or tokens[i][1] != ")":
                raise ExpressionError("IN 목록의 닫는 괄호가 없습니다")
            i += 1
            parts.append(f"{col} {'NOT ' if negate else ''}IN ({', '.join(values)})")
        elif i < len(tokens) and tokens[i][0] == "op" and tokens[i][1] in _CONDITION_OPS:
            op = "<>" if tokens[i][1] == "!=" else tokens[i][1]
            i += 1
            if (i < len(tokens) and tokens[i][0] == "name" and word(i) not in ("AND", "OR")
                    and temporal_literal(tokens, i) is None):
                right, used = _column_sql(tokens[i][1], column_types), 1
            else:
                right, used = literal(i)
            i += used
            parts.append(f"{col} {op} {right}")
        else:
            raise ExpressionError("컬럼 뒤에는 비교 연산자, IS [NOT] NULL, [NOT] IN (…) 이 와야 합니다")
        if i >= len(tokens):
            break
        if word(i) not in ("AND", "OR"):
            raise ExpressionError(f"조건 사이에는 AND 또는 OR 가 와야 합니다: '{tokens[i][1]}'")
        parts.append(word(i))
        i += 1
    return " ".join(parts)


def parse_logical(expected: str | None, column_types: dict[str, str] | None = None) -> tuple[str, str]:
    """LOGICAL 기대값 ``"IF <조건> THEN <조건>"`` → (전제 SQL, 결론 SQL). 틀리면 ``ExpressionError``."""
    m = _LOGICAL_RE.match(str(expected or ""))
    if not m:
        raise ExpressionError("expected_value 는 'IF <조건> THEN <조건>' 형식이어야 합니다 (예: IF cancel_yn = 'Y' THEN cancel_ymd IS NOT NULL)")
    return condition_sql(m.group(1), column_types), condition_sql(m.group(2), column_types)


def parse_formula(expected: str | None, column_types: dict[str, str] | None = None) -> tuple[str, float]:
    """FORMULA 기대값 ``"supply_amt + vat_amt"`` → (계산식 SQL, 허용 오차). 끝에 ``~ 0.01`` 로 오차를 적을 수 있다.

    컬럼 이름·숫자·사칙연산·괄호만 받는다. 틀리면 ``ExpressionError``.
    """
    tokens = _tokens(str(expected or ""))
    tolerance = 0.0
    if len(tokens) >= 2 and tokens[-2][1] == "~" and tokens[-1][0] == "num":
        tolerance = float(tokens[-1][1])
        tokens = tokens[:-2]
    if not tokens:
        raise ExpressionError("expected_value 에 계산식이 필요합니다 (예: supply_amt + vat_amt)")
    parts: list[str] = []
    depth = 0
    want_operand = True
    for kind, value in tokens:
        if want_operand:
            if kind == "name":
                parts.append(_column_sql(value, column_types))
                want_operand = False
            elif kind == "num":
                parts.append(value)
                want_operand = False
            elif value == "(":
                parts.append("(")
                depth += 1
            elif value == "-":
                parts.append("-")
            else:
                raise ExpressionError(f"계산식의 '{value}' 자리에는 컬럼 이름이나 숫자가 와야 합니다")
        elif kind == "op" and value in ("+", "-", "*", "/"):
            parts.append(value)
            want_operand = True
        elif value == ")" and depth > 0:
            parts.append(")")
            depth -= 1
        else:
            raise ExpressionError(f"계산식의 '{value}' 자리에는 연산자(+ - * /)가 와야 합니다")
    if want_operand or depth:
        raise ExpressionError("계산식이 끝나지 않았습니다(연산자 뒤의 값이나 닫는 괄호가 없음)")
    return " ".join(parts), tolerance


@dataclass(frozen=True)
class DateFormat:
    """읽어 낸 날짜 형식. ``parse_format`` 이 None 이면 모양만 본다(연도만 있는 형식)."""

    name: str                 # 대문자로 다듬은 형식 — 화면·메시지에 쓴다
    shape: str                # 자릿수와 구분자 정규식
    parse_format: str | None  # date_parse 형식 — 달력·시각에 있는 값인지 본다
    suffix: str = ""          # 파싱 전에 값 뒤에 붙일 것(일이 없는 형식에 1일을 채운다)


def date_format_spec(expected: str | None) -> DateFormat:
    """DATE_FORMAT 기대값 → 검사에 쓸 모양과 파싱 형식. 틀리면 ``ExpressionError``.

    낱말 ``YYYY`` ``MM`` ``DD`` ``HH24``(=``HH``) ``MI`` ``SS`` ``SSS``(밀리초 3자리)와 구분자 ``- / . :`` 공백 ``T`` 를
    이어 쓴다. 같은 낱말은 한 번만. 대소문자는 가리지 않는다. 시 바로 뒤의 ``MM`` 은 분이다(``HH:MM:SS`` = ``HH24:MI:SS``).
    """
    text = str(expected or "").strip().upper()
    if not text:
        raise ExpressionError("expected_value 에 날짜 형식이 필요합니다 (예: YYYYMMDD, YYYY/MM/DD, YYYY-MM-DD HH24:MI:SS)")
    shape, parse, seen = "^", "", []
    pos = 0
    while pos < len(text):
        for word, pattern, directive in DATE_TOKENS:
            if text.startswith(word, pos):
                if word == "MM" and seen and seen[-1] == "%H":
                    directive = "%i"  # 시 바로 뒤의 MM — 분
                if directive in seen:
                    raise ExpressionError(f"날짜 형식에 {word} 가 두 번 나옵니다: {text}")
                seen.append(directive)
                shape += pattern
                parse += directive
                pos += len(word)
                break
        else:
            ch = text[pos]
            if ch not in DATE_SEPARATORS:
                raise ExpressionError(
                    f"날짜 형식을 읽을 수 없습니다: '{text[pos:]}' — YYYY MM DD HH24 MI SS SSS 와 구분자(- / . : 공백 T)만 쓸 수 있습니다")
            shape += re.escape(ch) if ch != " " else " "
            parse += ch
            pos += 1
    if not seen:
        raise ExpressionError(f"날짜 형식에 YYYY MM DD HH24 MI SS 가 하나도 없습니다: {text}")
    if seen == ["%Y"]:
        return DateFormat(text, shape + "$", None)
    if "%m" in seen and "%d" not in seen:
        # 일이 없는 형식 — 1일을 붙여 달(13월 등)이 달력에 있는지 본다
        return DateFormat(text, shape + "$", parse + " %d", " 01")
    return DateFormat(text, shape + "$", parse)


# Trino 에 미리 등록한 함수(UDF)의 이름 — 이름만, 또는 스키마.이름, 카탈로그.스키마.이름
_FUNCTION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")


def function_ref(expected: str | None) -> str:
    """FUNCTION 기대값(Trino 에 등록한 함수 이름) → SQL 의 함수 참조. 이름 모양이 아니면 ``ExpressionError``.

    이름만 받는다 — 인자·괄호·식은 받지 않아 다른 SQL 이 끼어들 수 없다. 함수가 실제로 있는지는 진단할 때 Trino 가 본다.
    """
    name = (expected or "").strip()
    if not name:
        raise ExpressionError("Trino 함수 이름을 적어야 합니다 (예: memory.default.is_valid_brno)")
    if not _FUNCTION_NAME_RE.match(name):
        raise ExpressionError(
            f"함수 이름만 적습니다 — 영문·숫자·밑줄, 점으로 카탈로그.스키마.함수 까지 (괄호·인자 없이): {name}")
    return ".".join(quote_ident(part.lower()) for part in name.split("."))


# 커스텀 SQL 에서 받지 않는 낱말 — 조회 전용의 2차 방어선(1차는 Trino·원본 계정을 읽기 전용으로 두는 것)
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec|execute)\b",
    re.IGNORECASE,
)


def custom_sql(expected: str | None) -> str:
    """커스텀 SQL 기대값 → 낼 쿼리(주석·끝의 ``;`` 를 걷어 낸 것). 조회 쿼리가 아니면 ``ExpressionError``.

    규약: 첫 행 첫 컬럼이 위반 건수(0 이면 통과). ``SELECT``·``WITH`` 로 시작하는 문장 하나만 받는다.
    """
    text = re.sub(r"--[^\n]*", "", str(expected or ""))
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S).strip().rstrip(";").strip()
    if not text:
        raise ExpressionError("검증 SQL 을 적어야 합니다 — 첫 행 첫 컬럼이 위반 건수인 SELECT (예: SELECT count(*) FROM t WHERE …)")
    if ";" in text:
        raise ExpressionError("SQL 문장은 하나만 적습니다(; 로 여러 문장을 잇지 않음)")
    if text.split(None, 1)[0].lower() not in ("select", "with"):
        raise ExpressionError("SELECT 또는 WITH 로 시작하는 조회 쿼리만 적습니다")
    found = _FORBIDDEN_SQL.search(text)
    if found:
        raise ExpressionError(f"조회 쿼리에 쓸 수 없는 낱말입니다: {found.group(1)}")
    return text


def check_expression(check_type: str | None, expected: str | None) -> None:
    """규칙을 저장하기 전에 날짜 형식·조건·계산식·커스텀 SQL 의 모양을 본다(컬럼이 실제로 있는지는 진단할 때 본다).
    틀리면 ``ExpressionError``."""
    if check_type == "CUSTOM_SQL":
        custom_sql(expected)
    elif check_type == "CUSTOM_PYTHON":
        from app.quality.rule_direct import custom_python

        custom_python(expected)
    elif check_type == "DATE_FORMAT":
        date_format_spec(expected)
    elif check_type == "LOGICAL":
        parse_logical(expected)
    elif check_type == "FORMULA":
        parse_formula(expected)
    elif check_type == "FUNCTION":
        function_ref(expected)
    elif check_type == "FRESHNESS":
        freshness_hours(expected)
    elif check_type == "CONTINUITY":
        continuity_spec(expected)


# ---------------------------------------------------------------------------
# AI-Ready 품질 지표 (TODO-096 5단계) — 더미 값 빈도(통계적 타당성), 데이터 연속성(적시성)
# ---------------------------------------------------------------------------

# 더미 값 — 기대값을 비우면 쓰는 흔한 자리채움 값(가이드라인 §2.3 "999999 같은 더미 값 빈도")
DEFAULT_DUMMY_VALUES: tuple[str, ...] = (
    "999999", "9999999", "99999999", "999999999", "-999", "-9999", "-99999",
    "N/A", "NA", "n/a", "NULL", "null", "None", "-", ".", "?", "없음", "미상", "0000-00-00", "00000000",
)
_NUMBER_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
_NUMERIC_TYPE_RE = re.compile(
    r"^(tinyint|smallint|mediumint|int|integer|bigint|decimal|dec|numeric|number|float|double|real|int[248]|float[48]|serial|bigserial)\b",
    re.IGNORECASE)


def dummy_values(expected: str | None) -> list[str]:
    """DUMMY_VALUE 기대값(JSON 배열, 예전 쉼표 구분도) → 더미 값 목록. 비우면 ``DEFAULT_DUMMY_VALUES``."""
    values = accepted_values(expected)
    return values or list(DEFAULT_DUMMY_VALUES)


def is_numeric_type(column_type: str | None) -> bool:
    return bool(_NUMERIC_TYPE_RE.match((column_type or "").strip()))


def dummy_numbers(values: list[str]) -> list[str]:
    """숫자 컬럼과 비교할 더미 값 — 숫자 모양인 것만(문자 더미 값은 숫자 컬럼에 들어갈 수 없다). ``00000000`` 처럼 0 으로
    시작하는 값은 문자열에서만 더미 값이다 — 숫자로 비교하면 정상 값 0 과 같아진다."""
    return [v for v in values if _NUMBER_LITERAL_RE.match(v) and not re.match(r"^-?0\d", v)]


def dummy_pattern(values: list[str]) -> str:
    """원천 직접 진단(워커)의 텍스트 검사용 — 값 전체가 더미 값 하나와 같으면 일치. 숫자는 ``.0`` 꼬리를 허용한다
    (워커는 실수 999999 를 '999999.0', decimal 을 '999999.00' 으로 읽는다)."""
    parts = []
    for v in values:
        part = re.escape(v)
        if _NUMBER_LITERAL_RE.match(v) and "." not in v:
            part += r"(?:\.0+)?"
        parts.append(part)
    return "^(?:" + "|".join(parts) + ")$"


def judge_ratio(checked: int, errors: int, threshold: float, what: str, scope: str) -> Outcome:
    """오류 비율로 판정 — 정상 비율(진단 − 오류)/진단 × 100 이 임계값 이상이면 통과. 임계값 100 이면 오류 0건이어야 한다."""
    ok = max(checked - errors, 0)
    pct = errors / checked * 100 if checked else 0.0
    passed = _meets(ok, checked, threshold) if checked else True
    return Outcome(
        passed, f"{errors:,}건 ({pct:.2f}%)",
        f"{what} {errors:,}건 / 진단 {checked:,}건 — {pct:.2f}% (임계값: 정상 {threshold}% 이상, {scope})",
        checked_count=checked, error_count=errors,
    )


# 데이터 연속성 — 단위와 Trino date_trunc 단위, 문자열 날짜에서 그 단위까지의 낱말
CONTINUITY_UNITS: dict[str, tuple[str, str]] = {
    "HOUR": ("hour", "HH24"), "DAY": ("day", "DD"), "MONTH": ("month", "MM"), "YEAR": ("year", "YYYY"),
}
CONTINUITY_LABELS = {"HOUR": "시간", "DAY": "일", "MONTH": "월", "YEAR": "연"}
_PREFIX_DIRECTIVES = {"YYYY": "%Y", "MM": "%m", "DD": "%d", "HH24": "%H", "HH": "%H"}


@dataclass(frozen=True)
class Continuity:
    """데이터 연속성 규칙. ``prefix_len``·``prefix_format`` 은 문자열 날짜 컬럼에서 단위까지 자른 앞부분과 그 strptime 형식."""

    unit: str
    trunc: str
    date_format: str | None = None
    prefix_len: int | None = None
    prefix_format: str | None = None
    like: str | None = None  # 형식 전체의 LIKE 모양(자리마다 ``_``) — 원천 직접 집계가 모양이 다른 값을 거른다


def date_like_pattern(fmt: str) -> str:
    """날짜 형식 → SQL LIKE 모양. 낱말은 자릿수만큼 ``_``, 구분자는 그대로(``YYYY-MM-DD`` → ``____-__-__``).
    숫자인지는 보지 않는다 — 자릿수와 구분자만으로 길이가 다른 형식(``2024-01-05`` 와 ``YYYYMMDD``)을 가른다."""
    out, pos = "", 0
    while pos < len(fmt):
        for word, pattern, _ in DATE_TOKENS:
            if fmt.startswith(word, pos):
                out += "_" * int(pattern[pattern.index("{") + 1:-1])
                pos += len(word)
                break
        else:
            out += fmt[pos]
            pos += 1
    return out


def continuity_spec(expected: str | None) -> Continuity:
    """CONTINUITY 기대값 ``<단위> [<날짜 형식>]`` → 규칙. 단위는 HOUR·DAY·MONTH·YEAR.

    날짜 형식은 문자열로 저장한 날짜 컬럼일 때만 적는다(예: ``DAY YYYYMMDD``, ``MONTH YYYY-MM``) — 형식 앞에서부터 단위까지를
    잘라 같은 기간을 묶는다. 그래서 형식이 큰 단위부터(연 → 월 → 일 → 시) 이어져야 한다. 틀리면 ``ExpressionError``.
    """
    text = str(expected or "").strip()
    if not text:
        raise ExpressionError("expected_value 에 단위가 필요합니다 — HOUR · DAY · MONTH · YEAR (문자열 날짜면 뒤에 형식, 예: DAY YYYYMMDD)")
    unit, _, fmt = text.partition(" ")
    unit = unit.upper()
    if unit not in CONTINUITY_UNITS:
        raise ExpressionError(f"연속성 단위는 HOUR · DAY · MONTH · YEAR 중 하나입니다: {unit}")
    trunc, token = CONTINUITY_UNITS[unit]
    fmt = fmt.strip().upper()
    if not fmt:
        return Continuity(unit, trunc)
    date_format_spec(fmt)  # 형식 자체가 맞는지
    order = ["YYYY", "MM", "DD", "HH24"]
    pos, prefix, seen, length = 0, "", [], 0  # length — 값에서 단위까지의 글자 수(HH24 는 값에서 두 자리)
    want = order[: order.index(token) + 1]
    while pos < len(fmt) and len(seen) < len(want):
        word = next((w for w in ("HH24", "YYYY", "MM", "DD", "HH") if fmt.startswith(w, pos)), None)
        if word is None:
            if fmt[pos] not in DATE_SEPARATORS:
                break
            prefix += fmt[pos]
            pos += 1
            length += 1
            continue
        if (word if word != "HH" else "HH24") != want[len(seen)]:
            raise ExpressionError(
                f"연속성을 보려면 날짜 형식이 연 → 월 → 일 → 시 순서여야 합니다(단위 {unit} 까지): {fmt}")
        seen.append(word)
        prefix += _PREFIX_DIRECTIVES[word]
        pos += len(word)
        length += 4 if word == "YYYY" else 2
    if len(seen) < len(want):
        raise ExpressionError(f"날짜 형식 {fmt} 에 단위 {unit} 까지의 낱말이 없습니다")
    return Continuity(unit, trunc, fmt, length, prefix, date_like_pattern(fmt))


def _as_datetime(v, c: Continuity) -> datetime | None:
    """집계가 돌려준 최소·최대값 → datetime. 날짜·시각 값, ISO 문자열, 문자열 날짜의 앞부분을 받는다."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    text = str(v).strip()
    if c.prefix_format:
        try:
            return datetime.strptime(text, c.prefix_format)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace(" UTC", "")).replace(tzinfo=None)
    except ValueError:
        return None


def continuity_periods(first: datetime, last: datetime, unit: str) -> int:
    """첫 기간부터 마지막 기간까지의 기간 수(양 끝 포함)."""
    if unit == "YEAR":
        return last.year - first.year + 1
    if unit == "MONTH":
        return (last.year - first.year) * 12 + last.month - first.month + 1
    days = (last.date() - first.date()).days
    if unit == "DAY":
        return days + 1
    return days * 24 + last.hour - first.hour + 1


def judge_continuity(c: Continuity, threshold: float, scope: str) -> Callable[[int, list], Outcome]:
    """집계 ``[기간 수(고유), 최소, 최대]`` → 판정. 진단 건수 = 처음~마지막 기간 수, 오류 건수 = 빠진 기간 수."""
    label = CONTINUITY_LABELS[c.unit]

    def judge(_total: int, values: list) -> Outcome:
        present = _int(values[0])
        if present == 0:
            return Outcome(True, "값 없음", f"{label} 단위 연속성 — 날짜 값이 없어 진단할 기간이 없습니다 ({scope})",
                           checked_count=0, error_count=0)
        first, last = _as_datetime(values[1], c), _as_datetime(values[2], c)
        if first is None or last is None:
            return Outcome(False, "N/A", f"최소·최대값을 날짜로 읽을 수 없습니다({values[1]} ~ {values[2]}) — "
                                         "문자열 날짜면 기대값에 형식을 적고, 형식이 틀린 값은 날짜 형식 규칙으로 먼저 진단하세요")
        expected = continuity_periods(first, last, c.unit)
        missing = max(expected - present, 0)
        cut = {"YEAR": 4, "MONTH": 7, "DAY": 10, "HOUR": 13}[c.unit]
        span = f"{first:%Y-%m-%d %H}"[:cut] + " ~ " + f"{last:%Y-%m-%d %H}"[:cut]
        out = judge_ratio(expected, missing, threshold, f"{label} 단위 빈 기간({span}, 있는 기간 {present:,}개)", scope)
        return Outcome(out.passed, f"빈 {label} {missing:,}개", out.detail, checked_count=expected, error_count=missing)

    return judge


def extra_characters_pattern(expected: str | None) -> str | None:
    """WHITESPACE 기대값(쓰면 안 되는 문자를 이어 적은 것, 예 ``#$%``) → 정규식 문자 클래스. 없으면 None."""
    chars = "".join(dict.fromkeys(c for c in str(expected or "") if not c.isspace()))
    if not chars:
        return None
    return "[" + "".join("\\" + c if c in "\\]^-[" else c for c in chars) + "]"


# ---------------------------------------------------------------------------
# 최신성(FRESHNESS)의 시각 해석 — 원천 데이터 시간대(설정 quality_source_timezone)
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_TIMEZONE = "Asia/Seoul"
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def resolve_timezone(name: str | None) -> tzinfo:
    """시간대 이름 → tzinfo. IANA 이름(``Asia/Seoul``·``UTC``) 또는 고정 오프셋(``+09:00``). 빈 값·알 수 없는 이름은 ``ValueError``."""
    text = (name or "").strip()
    if not text:
        raise ValueError("시간대가 비어 있습니다")
    m = _OFFSET_RE.match(text)
    if m:
        sign, hh, mm = m.groups()
        if int(hh) > 23 or int(mm) > 59:
            raise ValueError(f"잘못된 시간대 오프셋: {text}")
        delta = timedelta(hours=int(hh), minutes=int(mm))
        return timezone(-delta if sign == "-" else delta)
    if text.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"알 수 없는 시간대: {text} (예: Asia/Seoul, UTC, +09:00)") from e


def _default_tzinfo() -> tzinfo:
    try:
        return resolve_timezone(DEFAULT_SOURCE_TIMEZONE)
    except ValueError:  # tzdata 가 없는 환경 — 한국 표준시는 일광 절약 시간이 없어 고정 오프셋과 같다
        return timezone(timedelta(hours=9))


DEFAULT_SOURCE_TZINFO = _default_tzinfo()


def source_tzinfo(name: str | None) -> tzinfo:
    """설정값 → tzinfo. 비었거나 해석할 수 없으면 기본(Asia/Seoul)."""
    try:
        return resolve_timezone(name) if (name or "").strip() else DEFAULT_SOURCE_TZINFO
    except ValueError:
        return DEFAULT_SOURCE_TZINFO


def parse_timestamp(value, source_tz: tzinfo | None = None) -> datetime | None:
    """집계한 최댓값 → UTC aware datetime. 해석할 수 없으면 None.

    받는 모양: ISO 형식(``2024-01-02 03:04:05.123``, ``2024-01-02``, ``...+09:00``)과 Trino 의 시간대 붙은 시각
    (``2024-01-02 03:04:05.000 UTC``, ``... Asia/Seoul``). 시간대가 없는 값은 ``source_tz``(기본 Asia/Seoul)의
    벽시계 시각으로 본다 — 서버 프로세스의 로컬 시간대와 상관없다.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        head, _, zone = text.rpartition(" ")
        try:
            dt = datetime.fromisoformat(head).replace(tzinfo=resolve_timezone(zone))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=source_tz or DEFAULT_SOURCE_TZINFO)
    return dt.astimezone(timezone.utc)


def freshness_hours(expected: str | None) -> float:
    """최신성 기대값(허용 경과 시간, 시간 단위) — 비면 24. 숫자가 아니거나 음수면 ``ExpressionError``."""
    try:
        hours = float(expected) if expected not in (None, "") else 24.0
    except (TypeError, ValueError):
        raise ExpressionError(f"잘못된 기대값: {expected} — 허용 경과 시간(시간)을 숫자로 적습니다") from None
    if not math.isfinite(hours) or hours < 0:
        raise ExpressionError(f"잘못된 기대값: {expected} — 허용 경과 시간(시간)을 숫자로 적습니다")
    return hours


def judge_freshness(column: str, max_hours: float, source_tz: tzinfo | None, scope: str,
                    now: Callable[[], datetime] | None = None) -> Callable[[int, list], Outcome]:
    """``values = [최댓값, 값이 있는 행 수]`` 를 판정 시각 기준 경과 시간으로 판정한다."""

    def judge(_total: int, values: list) -> Outcome:
        raw, checked = values[0], _int(values[1])
        if raw is None:
            return Outcome(False, "N/A", f"'{column}' 에 값이 없어 최신 시각을 알 수 없습니다 ({scope})", checked_count=checked)
        latest = parse_timestamp(raw, source_tz)
        if latest is None:
            return Outcome(False, "N/A", f"'{column}' 의 최댓값을 시각으로 해석할 수 없습니다: {raw}", checked_count=checked)
        current = now() if now else datetime.now(timezone.utc)
        age = max(0.0, (current - latest).total_seconds() / 3600)  # 미래 시각(시간대 차이 등)은 0
        return Outcome(age <= max_hours, f"{age:.1f}h",
                       f"최신 데이터({column} 최댓값 {raw}) 이후 {age:.1f}시간 (허용 {max_hours:g}시간, {scope})",
                       checked_count=checked)

    return judge


def _lookup(column_types: dict[str, str] | None, name: str) -> tuple[bool, str]:
    """(스키마에 있는가, 타입). 스키마 정보가 없으면 있다고 본다(Trino 가 최종 판단)."""
    if not column_types:
        return True, ""
    for key, value in column_types.items():
        if key.lower() == name.lower():
            return True, value or ""
    return False, ""


def _as_text(column: str, column_type: str) -> str:
    """컬럼을 문자열로 — CHAR 는 Trino 가 뒤 공백을 채워 돌려주므로 잘라 낸다."""
    cast = f"CAST({quote_ident(column)} AS varchar)"
    return f"rtrim({cast})" if _CHAR_RE.match(column_type.strip()) else cast


def _int(value) -> int:
    return int(value) if value is not None else 0


def _meets(count: int, total: int, threshold: float) -> bool:
    """count/total×100 ≥ threshold 를 반올림 없이 비교한다."""
    return Decimal(count) * 100 >= Decimal(str(threshold)) * total


# 판정 설명 끝에 붙는 검사 범위 — 경로마다 다르다
TRINO_SCOPE = "Trino 전체 데이터 검사"
DIRECT_SCOPE = "원천 직접 검사"


def _violations(checked: int, errors: int, what: str, scope: str = TRINO_SCOPE) -> Outcome:
    return Outcome(
        errors == 0, f"위반 {errors:,}건",
        f"{what} 기준 위반 {errors:,}건 / 진단 {checked:,}건 ({scope})",
        checked_count=checked, error_count=errors,
    )


def compile_rule(
    rule: RuleSpec,
    table: TableRef,
    column_types: dict[str, str] | None = None,
    reference: tuple[TableRef, str] | str | None = None,
    scope: str = TRINO_SCOPE,
    source_timezone: str | None = None,
) -> Compiled | Outcome | None:
    """규칙을 Trino 집계 식으로 바꾼다.

    - ``Compiled``: 실행할 식과 판정 함수
    - ``Outcome``: 쿼리 없이 바로 실패로 판정(컬럼 없음, 기대값 형식 오류 등)
    - ``None``: Trino 로 평가하지 않는 유형 — 호출자가 직전 결과를 이어받는다

    ``reference`` 는 REFERENTIAL 전용 — 참조 테이블과 컬럼, 또는 해석하지 못한 사유(문자열).
    ``scope`` 는 판정 설명 끝의 검사 범위 — 직접 진단은 ``DIRECT_SCOPE``.
    ``source_timezone`` 은 FRESHNESS 전용 — 시간대 없는 시각 값을 해석할 원천 데이터 시간대(비면 Asia/Seoul).
    """
    check = rule.check_type
    if check not in TRINO_PATH_TYPES:
        return None

    def sample(predicate: str) -> str:
        return sample_sql(table, predicate)

    if check == "CUSTOM_SQL":
        return _compile_custom_sql(rule, scope)
    column = rule.column_name or ""
    expected = rule.expected_value
    label = f"{check}({column})" if column else check

    if check == "ROW_COUNT":
        try:
            minimum = int(expected) if expected else 0
        except (TypeError, ValueError):
            return Outcome(False, "N/A", f"잘못된 기대값: {expected}")

        def judge_rows(total: int, _values: list) -> Outcome:
            return Outcome(total >= minimum, str(total), f"행 수 {total} (기대 ≥ {minimum})")

        return Compiled(rule.id, (), judge_rows, label=label)

    if check == "LOGICAL":
        try:
            when, then = parse_logical(expected, column_types)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))

        def judge_logical(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"논리관계 {str(expected).strip()}", scope)

        # 전제가 참인 행이 진단 대상, 그 가운데 결론이 거짓인 행이 오류(결론이 NULL 이면 오류로 세지 않는다)
        return Compiled(
            rule.id, (f"count_if({when})", f"count_if(({when}) AND NOT ({then}))"), judge_logical, label="LOGICAL",
            sample_sql=sample(f"({when}) AND NOT ({then})"),
        )

    if not column:
        if check == "FRESHNESS":
            return Outcome(False, "N/A", "최신성 검사는 적재 · 수정 시각 컬럼을 지정해야 합니다")
        return Outcome(False, "N/A", "검사할 컬럼을 지정해야 합니다")
    present, column_type = _lookup(column_types, column)
    if not present:
        return Outcome(False, "N/A", f"스키마에 '{column}' 컬럼이 없습니다")
    col = quote_ident(column)

    if check == "FRESHNESS":
        try:
            max_hours = freshness_hours(expected)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))
        judge = judge_freshness(column, max_hours, source_tzinfo(source_timezone), scope)
        return Compiled(rule.id, (f"max({col})", f"count({col})"), judge, label=label)

    if check == "NOT_NULL":
        def judge_not_null(total: int, values: list) -> Outcome:
            nulls = _int(values[0])
            non_null = max(total - nulls, 0)
            pct = non_null / total * 100 if total else 100.0
            passed = _meets(non_null, total, rule.threshold) if total else True
            return Outcome(
                passed, f"{pct:.1f}%",
                f"비-NULL {pct:.1f}%, NULL {nulls:,}건 / {total:,}행 (임계값 {rule.threshold}%)",
                checked_count=total, error_count=nulls,
            )

        return Compiled(rule.id, (f"count_if({col} IS NULL)",), judge_not_null, label=label,
                        sample_sql=sample(f"{col} IS NULL"))

    if check == "UNIQUE":
        def judge_unique(total: int, values: list) -> Outcome:
            distinct = _int(values[0])
            pct = distinct / total * 100 if total else 100.0
            passed = _meets(distinct, total, rule.threshold) if total else True
            return Outcome(
                passed, f"{pct:.1f}%",
                f"고유값 {pct:.1f}%, 고유 {distinct:,}개 / {total:,}행 (임계값 {rule.threshold}%)",
                checked_count=total, error_count=max(total - distinct, 0),
            )

        # 중복 값을 가진 행 — 같은 값끼리 모이게 정렬한다
        duplicated = f"{col} IN (SELECT {col} FROM {table.sql} WHERE {col} IS NOT NULL GROUP BY {col} HAVING count(*) > 1)"
        return Compiled(rule.id, (f"count(DISTINCT {col})",), judge_unique, label=label,
                        sample_sql=sample_sql(table, duplicated, order_by=col))

    if check in ("MIN_VALUE", "MAX_VALUE"):
        try:
            bound = float(expected) if expected else 0.0
        except (TypeError, ValueError):
            return Outcome(False, "N/A", f"잘못된 기대값: {expected}")
        if not math.isfinite(bound):
            return Outcome(False, "N/A", f"잘못된 기대값: {expected}")
        number = f"TRY_CAST({col} AS double)"
        is_min = check == "MIN_VALUE"
        agg, op, word, sign = ("min", "<", "최소값", "≥") if is_min else ("max", ">", "최대값", "≤")

        def judge_bound(_total: int, values: list) -> Outcome:
            extreme, checked, errors = values[0], _int(values[1]), _int(values[2])
            if extreme is None:
                return Outcome(False, "N/A", f"{word}을 구할 수 없습니다", checked_count=checked, error_count=errors)
            actual = float(extreme)
            passed = actual >= bound if is_min else actual <= bound
            return Outcome(
                passed, str(actual),
                f"{word} {actual} (기대 {sign} {bound}), 벗어난 값 {errors:,}건 / 진단 {checked:,}건",
                checked_count=checked, error_count=errors,
            )

        return Compiled(
            rule.id,
            (f"{agg}({number})", f"count({col})", f"count_if({number} {op} {bound!r})"),
            judge_bound, label=label, sample_sql=sample(f"{number} {op} {bound!r}"),
        )

    text = _as_text(column, column_type)

    if check == "ACCEPTED_VALUES":
        accepted = accepted_values(expected)
        if not accepted:
            return Outcome(False, "N/A", "expected_value 에 허용 값 목록(쉼표 구분)이 필요합니다")
        in_list = ", ".join(string_literal(v) for v in accepted)

        def judge_accepted(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"허용 값 {accepted}", scope)

        return Compiled(
            rule.id,
            (f"count({col})", f"count_if({col} IS NOT NULL AND {text} NOT IN ({in_list}))"),
            judge_accepted, label=label, sample_sql=sample(f"{col} IS NOT NULL AND {text} NOT IN ({in_list})"),
        )

    if check == "REGEX":
        if not expected:
            return Outcome(False, "N/A", "expected_value 에 정규식이 필요합니다")

        def judge_regex(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"정규식 '{expected}'", scope)

        # regexp_like 는 부분 일치 — 원천 직접 경로(워커의 re.search)와 같은 시맨틱
        bad = f"{col} IS NOT NULL AND NOT regexp_like({text}, {string_literal(expected)})"
        return Compiled(rule.id, (f"count({col})", f"count_if({bad})"), judge_regex, label=label, sample_sql=sample(bad))

    if check == "FUNCTION":
        try:
            function = function_ref(expected)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))

        def judge_function(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"함수 {expected.strip()}", scope)

        # 함수는 컬럼 값을 그대로 받아 boolean 을 돌려준다 — true 가 정상. NULL 을 돌려주면 판단하지 못한 것이라 오류로 센다
        bad = f"{col} IS NOT NULL AND NOT coalesce({function}({col}), false)"
        return Compiled(rule.id, (f"count({col})", f"count_if({bad})"), judge_function, label=label,
                        sample_sql=sample(bad))

    if check == "DATE_FORMAT":
        try:
            spec = date_format_spec(expected)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))

        def judge_date(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"날짜 형식 {spec.name}", scope)

        if column_type.strip().lower().startswith(_TEMPORAL_PREFIXES):
            # 날짜·시각 타입 컬럼은 DBMS 가 유효성을 보장한다 — 진단 건수만 센다
            return Compiled(rule.id, (f"count({col})", "0"), judge_date, label=label)
        # 모양을 먼저 보는 이유: date_parse 는 자릿수가 모자란 값('2024-1-5')도 받아들인다
        bad = f"NOT regexp_like({text}, {string_literal(spec.shape)})"
        if spec.parse_format:
            value = f"{text} || {string_literal(spec.suffix)}" if spec.suffix else text
            bad += f" OR try(date_parse({value}, {string_literal(spec.parse_format)})) IS NULL"
        return Compiled(
            rule.id, (f"count({col})", f"count_if({col} IS NOT NULL AND ({bad}))"), judge_date, label=label,
            sample_sql=sample(f"{col} IS NOT NULL AND ({bad})"),
        )

    if check == "COLUMN_COMPARE":
        parsed = parse_compare(expected)
        if parsed is None:
            return Outcome(False, "N/A", "expected_value 는 '<연산자> <컬럼>' 형식이어야 합니다 (예: <= end_ymd)")
        op, other = parsed
        other_present, _ = _lookup(column_types, other)
        if not other_present:
            return Outcome(False, "N/A", f"스키마에 '{other}' 컬럼이 없습니다")
        other_col = quote_ident(other)

        def judge_compare(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"{column} {op} {other}", scope)

        # 한쪽이라도 NULL 이면 비교 결과가 NULL 이라 오류로 세지 않는다
        return Compiled(
            rule.id,
            (f"count_if({col} IS NOT NULL AND {other_col} IS NOT NULL)",
             f"count_if(NOT ({col} {op} {other_col}))"),
            judge_compare, label=label, sample_sql=sample(f"NOT ({col} {op} {other_col})"),
        )

    if check == "BROKEN_TEXT":
        def judge_broken(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), "글자 깨짐(대체 문자·낱자 한글)", scope)

        bad = f"{col} IS NOT NULL AND regexp_like({text}, {string_literal(BROKEN_TEXT_PATTERN)})"
        return Compiled(rule.id, (f"count({col})", f"count_if({bad})"), judge_broken, label=label, sample_sql=sample(bad))

    if check == "WHITESPACE":
        extra = extra_characters_pattern(expected)
        bad = f"regexp_like({text}, {string_literal(WHITESPACE_PATTERN)})"
        if extra:
            bad += f" OR regexp_like({text}, {string_literal(extra)})"
        what = "앞뒤 공백·제어 문자" + (f"·금지 문자 {str(expected).strip()}" if extra else "")

        def judge_whitespace(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), what, scope)

        return Compiled(
            rule.id, (f"count({col})", f"count_if({col} IS NOT NULL AND ({bad}))"), judge_whitespace, label=label,
            sample_sql=sample(f"{col} IS NOT NULL AND ({bad})"),
        )

    if check == "DUMMY_VALUE":
        values = dummy_values(expected)
        what = f"더미 값({', '.join(values[:6])}{' …' if len(values) > 6 else ''})"
        if is_numeric_type(column_type):
            numbers = dummy_numbers(values)
            if not numbers:
                return Outcome(False, "N/A", "숫자 컬럼에는 숫자 더미 값(예: 999999, -999)을 적어야 합니다")
            bad = f"{col} IN ({', '.join(numbers)})"
        else:
            bad = f"{col} IS NOT NULL AND {text} IN ({', '.join(string_literal(v) for v in values)})"

        def judge_dummy(_total: int, values_: list) -> Outcome:
            return judge_ratio(_int(values_[0]), _int(values_[1]), rule.threshold, what, scope)

        return Compiled(rule.id, (f"count({col})", f"count_if({bad})"), judge_dummy, label=label, sample_sql=sample(bad))

    if check == "CONTINUITY":
        try:
            c = continuity_spec(expected)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))
        temporal = column_type.strip().lower().startswith(_TEMPORAL_PREFIXES)
        if temporal:
            key = f"date_trunc('{c.trunc}', CAST({col} AS timestamp))"
        elif c.prefix_len:
            shape = date_format_spec(c.date_format).shape
            key = f"CASE WHEN regexp_like({text}, {string_literal(shape)}) THEN substr({text}, 1, {c.prefix_len}) END"
        else:
            return Outcome(False, "N/A", f"'{column}' 는 날짜·시각 타입이 아닙니다 — 문자열 날짜면 기대값에 형식을 적으세요 (예: {c.unit} YYYYMMDD)")
        return Compiled(rule.id, (f"count(DISTINCT {key})", f"min({key})", f"max({key})"),
                        judge_continuity(c, rule.threshold, scope), label=label)

    if check == "FORMULA":
        try:
            formula, tolerance = parse_formula(expected, column_types)
        except ExpressionError as e:
            return Outcome(False, "N/A", str(e))
        # try: 0 으로 나누기 같은 계산 오류는 NULL 로 — 진단 대상에서 빠진다
        value = f"try({formula})"
        if tolerance:
            differs = f"abs(CAST({col} AS double) - CAST({value} AS double)) > {tolerance!r}"
        else:
            differs = f"{col} <> {value}"

        def judge_formula(_total: int, values: list) -> Outcome:
            return _violations(_int(values[0]), _int(values[1]), f"계산식 {column} = {str(expected).strip()}", scope)

        return Compiled(
            rule.id,
            (f"count_if({col} IS NOT NULL AND {value} IS NOT NULL)", f"count_if({differs})"),
            judge_formula, label=label, sample_sql=sample(differs),
        )

    # REFERENTIAL
    if reference is None or isinstance(reference, str):
        return Outcome(False, "N/A", reference or "참조 대상을 해석하지 못했습니다")
    parent, parent_column = reference
    pcol = quote_ident(parent_column)

    def judge_reference(_total: int, values: list) -> Outcome:
        return _violations(_int(values[0]), _int(values[1]), f"참조 {parent.display}.{parent_column}", scope)

    joined = (
        f"FROM {table.sql} c "
        f"LEFT JOIN (SELECT DISTINCT {pcol} AS k FROM {parent.sql} WHERE {pcol} IS NOT NULL) p "
        f"ON c.{col} = p.k WHERE c.{col} IS NOT NULL"
    )
    own_sql = f"SELECT count(*), count_if(p.k IS NULL) {joined}"
    return Compiled(rule.id, (), judge_reference, own_sql=own_sql, label=label,
                    sample_sql=f"SELECT c.* {joined} AND p.k IS NULL LIMIT {SAMPLE_ROWS}")


def _compile_custom_sql(rule: RuleSpec, scope: str) -> Compiled | Outcome:
    try:
        sql = custom_sql(rule.expected_value)
    except ExpressionError as e:
        return Outcome(False, "N/A", str(e))

    def judge_custom(_total: int, values: list) -> Outcome:
        value = values[0] if values else None
        try:
            bad = int(value)
        except (TypeError, ValueError):
            return Outcome(False, str(value), "커스텀 SQL 의 첫 컬럼이 숫자(위반 건수)가 아닙니다")
        return Outcome(bad == 0, f"위반 {bad:,}건", f"커스텀 SQL 기준 위반 {bad:,}건 ({scope})", error_count=bad)

    return Compiled(rule.id, (), judge_custom, own_sql=sql, label="CUSTOM_SQL")


# 위반 샘플 — 실패한 규칙마다 위반 행을 이만큼까지 남긴다(5행)
SAMPLE_ROWS = 5


def sample_sql(table: TableRef, predicate: str, *, order_by: str | None = None) -> str:
    """위반 행 몇 개를 읽는 쿼리 — 행 전체(``*``), ``SAMPLE_ROWS`` 행까지."""
    order = f" ORDER BY {order_by}" if order_by else ""
    return f"SELECT * FROM {table.sql} WHERE {predicate}{order} LIMIT {SAMPLE_ROWS}"


def scan_sql(table: TableRef, compiled: list[Compiled]) -> str:
    """스캔 쿼리 — 첫 컬럼은 전체 행 수, 이어서 규칙별 식을 순서대로 놓는다."""
    exprs = ["count(*)"]
    for c in compiled:
        exprs.extend(c.exprs)
    return f"SELECT {', '.join(exprs)} FROM {table.sql}"


def format_scan(table: TableRef, compiled: list[Compiled]) -> str:
    """``scan_sql`` 을 읽기 좋게 — 식마다 한 줄, 규칙의 첫 식 뒤에 그 규칙 이름. 주석·줄바꿈 말고는 같은 쿼리다."""
    exprs = [("count(*)", "전체 행 수")]
    for c in compiled:
        exprs += [(e, c.label if n == 0 else "") for n, e in enumerate(c.exprs)]
    lines = []
    for n, (expr, note) in enumerate(exprs):
        comma = "," if n < len(exprs) - 1 else ""
        lines.append(f"  {expr}{comma}" + (f"  -- {note}" if note else ""))
    return "SELECT\n" + "\n".join(lines) + f"\nFROM {table.sql}"


def format_own(sql: str) -> str:
    """따로 실행하는 쿼리(참조관계)를 절마다 줄바꿈한다."""
    head, sep, tail = sql.partition(" FROM ")
    if sep:
        sql = head + "\nFROM " + tail
    return (sql.replace(" LEFT JOIN (", "\nLEFT JOIN (").replace(") p ON ", ") p\n  ON ")
            .replace(" WHERE c.", "\nWHERE c."))


def read_scan(compiled: list[Compiled], row: list) -> dict[int, Outcome]:
    """스캔 결과 한 행 → 규칙별 판정. ``row`` 는 ``scan_sql`` 의 컬럼 순서."""
    total = _int(row[0])
    outcomes: dict[int, Outcome] = {}
    pos = 1
    for c in compiled:
        values = list(row[pos:pos + len(c.exprs)])
        pos += len(c.exprs)
        outcomes[c.rule_id] = c.judge(total, values)
    return outcomes


def read_own(compiled: Compiled, row: list) -> Outcome:
    """따로 실행한 쿼리(``own_sql``)의 첫 행 → 판정."""
    return compiled.judge(0, list(row))
