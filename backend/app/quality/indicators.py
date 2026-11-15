# SPDX-License-Identifier: Apache-2.0
"""진단 기준 — 규칙을 공공데이터 품질진단 기준의 이름으로 묶는다.

용어 — 화면에서는 이 묶음을 관리지침 용어대로 **진단 기준** 이라 부른다(예전 화면 이름 "분류"). 화면의 **품질 지표** 는
미리 정해 컬럼에 할당하는 기준(``definitions.py``)이고, 진단 기준 하나에 지표 여럿이 속한다(날짜 → "날짜 YYYYMMDD"·
"날짜 YYYY-MM-DD"). 코드·API·DB 는 진단 기준을 예전 이름대로 indicator(정의의 ``category``)라 부른다
(``/quality/indicators``, ``catalog_quality_jobs.indicators``). 아래 설명의 "지표" 는 이 진단 기준을 가리킨다.

품질 진단 작업은 "어떤 품질을 측정할지" 를 규칙 유형이 아니라 이 지표로 고른다. 지표는 관리지침의 진단 기준 16개
(정합성 5 · 완결성 4 · 유효성 7, ``design/public-data-quality-guideline-review.md`` §4)와 1:1 이고, 기준 밖의
"건수" 가 하나 더 있다. Trino 로 평가할 수 있는 유형(``rule_sql.TRINO_CHECK_TYPES``)만 지표에 들어간다 — 나머지
유형(FRESHNESS·CUSTOM_SQL·CUSTOM_PYTHON)은 Trino 진단 대상이 아니다.

규칙 유형 하나가 기준 여럿에 걸치는 경우가 있다(허용 값 목록 = 코드·여부, 숫자 범위 = 금액·수량·율, 컬럼 간 비교 =
시간순서·선후관계). 이때는 규칙의 **도메인**(``catalog_quality_rule.domain``)이 어느 기준인지 정한다. 도메인을
적지 않은 규칙은 컬럼 이름의 끝 단어(공통표준용어 영문약어 — YN, AMT, CNT, RT …)로 정하고, 그래도 모르면 유형의
기본 도메인으로 본다(``resolve_domain``).

규칙 유형이 늘면 여기 한곳에 지표를 정한다. 지표에 넣지 않은 Trino 유형이 있으면 테스트가 실패한다.

**AI-Ready 품질 지표** (TODO-096 5단계) — 같은 진단 기준을 「공공데이터의 인공지능 친화적 관리 가이드라인」 §2.3 의 6개 지표
(완전성 · 일관성 · 정확성 · 적시성 · 유효성 · 유일성)와 세부 지표로도 묶는다(``Indicator.ai_ready``). 리포트가 두 기준으로
같은 진단 결과를 보여 준다. 관리지침 기준 밖의 두 기준(더미 값 — 통계적 타당성, 데이터 연속성 — 적시성)은 AI-Ready 를 위해 더했다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Indicator:
    key: str
    name: str
    area: str                    # 진단 영역 키
    check_types: tuple[str, ...]
    description: str
    domain: str | None = None    # 같은 유형을 나누는 도메인(= 키) — 없으면 유형만으로 정해진다
    ai_ready: tuple[str, str] | None = None  # AI-Ready 품질 지표 (지표 키, 세부 지표) — 가이드라인 §2.3


# 진단 영역 — 관리지침 품질진단 기준의 세 묶음과, 기준 밖의 항목
AREAS: dict[str, str] = {
    "COMPLETENESS": "완결성",
    "VALIDITY": "유효성",
    "CONSISTENCY": "정합성",
    "OTHER": "기타",
}

# AI-Ready 품질 지표 — 가이드라인 §2.3 (design/ai-ready-public-data-guideline-review.md §3.4 · §6.3)
AI_READY_AREAS: dict[str, str] = {
    "AI_COMPLETENESS": "완전성",
    "AI_CONSISTENCY": "일관성",
    "AI_ACCURACY": "정확성",
    "AI_TIMELINESS": "적시성",
    "AI_VALIDITY": "유효성",
    "AI_UNIQUENESS": "유일성",
}

_RANGE = ("MIN_VALUE", "MAX_VALUE", "REGEX", "FUNCTION")

INDICATORS: tuple[Indicator, ...] = (
    # 완결성 4
    Indicator("BROKEN_TEXT", "글자 깨짐", "COMPLETENESS", ("BROKEN_TEXT",), "값에 깨진 글자(대체 문자 �)나 낱자만 있는 한글(ㄱ·ㅏ)이 들어 있는지", ai_ready=("AI_VALIDITY", "형식 유효성")),
    Indicator("WHITESPACE", "공백·특수문자", "COMPLETENESS", ("WHITESPACE",), "값 앞뒤의 공백, 값 안의 탭·줄바꿈, 쓰면 안 되는 특수문자가 있는지", ai_ready=("AI_VALIDITY", "형식 유효성")),
    Indicator("REQUIRED", "필수값", "COMPLETENESS", ("NOT_NULL",), "반드시 있어야 하는 값이 비어 있는지(NULL)", ai_ready=("AI_COMPLETENESS", "필수 속성 충족도")),
    Indicator("DUPLICATE", "중복데이터", "COMPLETENESS", ("UNIQUE",), "겹치면 안 되는 컬럼에 같은 값이 여러 번 있는지", ai_ready=("AI_UNIQUENESS", "데이터 유일성")),
    # 유효성 7
    Indicator("DATE", "날짜", "VALIDITY", ("DATE_FORMAT", "REGEX", "FUNCTION"), "문자·숫자로 저장한 날짜·일시가 정한 형식(예: YYYYMMDD)이고 달력에 있는 날짜인지 — 등록일, 생년월일, 처리일시",
              domain="DATE", ai_ready=("AI_VALIDITY", "형식 유효성")),
    Indicator("NUMBER", "번호", "VALIDITY", ("REGEX", "FUNCTION"), "전화번호·우편번호·사업자등록번호처럼 자릿수와 모양이 정해진 번호가 그 모양에 맞는지",
              domain="NUMBER", ai_ready=("AI_VALIDITY", "형식 유효성")),
    Indicator("YN", "여부", "VALIDITY", ("ACCEPTED_VALUES", "REGEX", "FUNCTION"), "예/아니오 둘 중 하나만 갖는 컬럼(사용여부, 삭제여부)에 정한 두 값(Y/N 등)이 아닌 값이 있는지", domain="YN", ai_ready=("AI_ACCURACY", "기준 정합성")),
    Indicator("CODE", "코드", "VALIDITY", ("ACCEPTED_VALUES", "REGEX", "FUNCTION"), "정해진 코드값만 갖는 컬럼(상태코드, 구분코드, 등급)에 코드값 목록에 없는 값이 있는지", domain="CODE", ai_ready=("AI_ACCURACY", "기준 정합성")),
    Indicator("AMOUNT", "금액", "VALIDITY", _RANGE, "돈의 크기를 나타내는 값(판매금액, 수수료, 단가)이 정한 범위(예: 0 이상)를 벗어나는지", domain="AMOUNT", ai_ready=("AI_VALIDITY", "수치 범위 유효성")),
    Indicator("QUANTITY", "수량", "VALIDITY", _RANGE, "개수·크기를 나타내는 값(수량, 건수, 면적, 나이)이 정한 범위(예: 0 이상)를 벗어나는지", domain="QUANTITY", ai_ready=("AI_VALIDITY", "수치 범위 유효성")),
    Indicator("RATE", "율", "VALIDITY", _RANGE, "전체에 대한 비율을 나타내는 값 — 이름이 '율·률'로 끝나는 컬럼(할인율, 세율, 진행률, 점유율)이 정한 범위(예: 0~100 %)를 벗어나는지", domain="RATE", ai_ready=("AI_VALIDITY", "수치 범위 유효성")),
    # 정합성 5
    Indicator("TIME_ORDER", "시간순서", "CONSISTENCY", ("COLUMN_COMPARE",), "시간순서가 있는 두 컬럼이 뒤바뀌었는지(시작일 ≤ 종료일)",
              domain="TIME_ORDER", ai_ready=("AI_CONSISTENCY", "관계 일관성")),
    Indicator("SEQUENCE", "선후관계", "CONSISTENCY", ("COLUMN_COMPARE",), "업무상 선후가 있는 두 컬럼이 뒤바뀌었는지(접수일 ≤ 처리일)",
              domain="SEQUENCE", ai_ready=("AI_CONSISTENCY", "관계 일관성")),
    Indicator("LOGICAL", "논리관계", "CONSISTENCY", ("LOGICAL",), "조건이 참이면 따라야 하는 다른 조건을 지키는지(해지 여부가 Y 면 해지일이 있어야 함)", ai_ready=("AI_CONSISTENCY", "관계 일관성")),
    Indicator("FORMULA", "계산식", "CONSISTENCY", ("FORMULA",), "계산해서 저장한 값이 계산식과 맞는지(합계 = 공급가 + 부가세)", ai_ready=("AI_ACCURACY", "규칙 정확성")),
    Indicator("REFERENCE", "참조관계", "CONSISTENCY", ("REFERENTIAL",), "다른 테이블을 가리키는 값이 그 테이블에 실제로 있는지", ai_ready=("AI_CONSISTENCY", "참조 무결성")),
    # 기준 밖
    Indicator("PATTERN", "문자열 형식", "OTHER", ("REGEX", "FUNCTION"), "값이 정한 모양(정규식)이나 검증 함수에 맞는지 — 진단 기준 16개 어디에도 들지 않는 형식 검사(이메일, URL)",
              domain="PATTERN", ai_ready=("AI_VALIDITY", "형식 유효성")),
    Indicator("VOLUME", "건수", "OTHER", ("ROW_COUNT",), "테이블의 행 수가 정한 하한 이상인지"),
    # AI-Ready 품질 지표를 위해 더한 기준(관리지침 16개 밖, TODO-096 5단계)
    Indicator("DUMMY", "더미 값", "OTHER", ("DUMMY_VALUE",),
              "999999 · -999 · N/A 처럼 값이 없음을 대신 적은 자리채움 값이 얼마나 섞여 있는지 — 임계값으로 허용 비율을 정한다",
              ai_ready=("AI_VALIDITY", "통계적 타당성")),
    Indicator("CONTINUITY", "데이터 연속성", "OTHER", ("CONTINUITY",),
              "날짜 · 일시 컬럼의 처음부터 마지막까지 시간 · 일 · 월 · 연 단위로 빠진 기간(시계열 공백)이 있는지",
              ai_ready=("AI_TIMELINESS", "데이터 연속성")),
)

_BY_KEY = {i.key: i for i in INDICATORS}

# 유형 → 고를 수 있는 도메인(첫 번째가 기본). 여기 없는 유형은 도메인이 없다.
DOMAINS: dict[str, tuple[str, ...]] = {
    "ACCEPTED_VALUES": ("CODE", "YN"),
    "MIN_VALUE": ("QUANTITY", "AMOUNT", "RATE"),
    "MAX_VALUE": ("QUANTITY", "AMOUNT", "RATE"),
    "COLUMN_COMPARE": ("TIME_ORDER", "SEQUENCE"),
    # 정규식은 어느 분류의 값이든 모양을 볼 수 있다 — 적지 않으면 번호(예전부터의 기본). 컬럼 이름으로 추정하지 않는다
    "REGEX": ("NUMBER", "DATE", "YN", "CODE", "AMOUNT", "QUANTITY", "RATE", "PATTERN"),
    # Trino 에 등록한 함수(UDF)도 정규식처럼 어느 분류의 값이든 검증할 수 있다
    "FUNCTION": ("NUMBER", "DATE", "YN", "CODE", "AMOUNT", "QUANTITY", "RATE", "PATTERN"),
}
# 도메인을 컬럼 이름으로 추정하지 않는 유형 — 적어 둔 도메인이 없으면 언제나 기본 도메인
_NO_GUESS: frozenset[str] = frozenset({"REGEX", "FUNCTION"})
DOMAIN_NAMES: dict[str, str] = {i.domain: i.name for i in INDICATORS if i.domain}

# 컬럼 이름의 끝 단어 → 도메인 (공통표준용어 영문약어와 흔한 영어 이름). 컬럼 간 비교는 이름으로 가르지 않는다.
_SUFFIX_DOMAINS: dict[str, str] = {
    "YN": "YN", "FLAG": "YN", "FLG": "YN",
    "CD": "CODE", "CODE": "CODE", "SE": "CODE", "TYPE": "CODE", "STATUS": "CODE",
    "AMT": "AMOUNT", "AMOUNT": "AMOUNT", "FEE": "AMOUNT", "PRC": "AMOUNT", "PRICE": "AMOUNT", "COST": "AMOUNT",
    "CNT": "QUANTITY", "COUNT": "QUANTITY", "QTY": "QUANTITY", "QY": "QUANTITY", "AR": "QUANTITY", "AGE": "QUANTITY",
    "RT": "RATE", "RATE": "RATE", "PCT": "RATE", "RATIO": "RATE",
}
_WORD_RE = re.compile(r"[A-Za-z]+")

# 미리 정의해 컬럼에 할당할 수 있는 유형(``definitions.py``) — 컬럼 하나와 기준값만으로 정해지는 것.
# 나머지(컬럼 간 비교·논리관계·계산식·참조관계·건수)는 테이블마다 내용이 달라 데이터셋에서 직접 만든다.
DEFINABLE_TYPES: frozenset[str] = frozenset({
    "BROKEN_TEXT", "WHITESPACE", "NOT_NULL", "UNIQUE", "DATE_FORMAT", "REGEX", "ACCEPTED_VALUES", "MIN_VALUE", "MAX_VALUE",
    "FUNCTION", "DUMMY_VALUE", "CONTINUITY",
})

# 분류 → 그 분류의 컬럼에 흔한 이름 끝 단어. 할당할 컬럼을 추천할 때 쓴다(``suggests``).
_SUFFIX_HINTS: dict[str, frozenset[str]] = {
    "DATE": frozenset({"YMD", "YM", "DT", "DE", "DATE", "DAY", "DTTM", "DTM", "TM", "TIME"}),
    # 연속성 — 시계열의 기준 날짜 컬럼(기준일자·기준연월 등)
    "CONTINUITY": frozenset({"YMD", "YM", "DT", "DE", "DATE", "DAY", "DTTM", "DTM"}),
    "NUMBER": frozenset({"NO", "NUM", "TELNO", "TEL", "PHONE", "ZIP", "FAX", "FAXNO", "BRNO", "BIZNO", "POSTAL", "MOBILE"}),
}

# 이전 지표 키(묶여 있던 때) → 지금의 키. 저장된 품질 진단 작업·실행의 지표를 읽을 때 쓴다.
_LEGACY: dict[str, tuple[str, ...]] = {
    "FORMAT": ("NUMBER",),
    "RANGE": ("AMOUNT", "QUANTITY", "RATE"),
    "ORDER": ("TIME_ORDER", "SEQUENCE"),
}


def _last_word(column_name: str | None) -> str:
    """컬럼 이름의 끝 단어(대문자) — ``cancel_yn`` → ``YN``, ``totalAmt`` → ``AMT``."""
    words = _WORD_RE.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", column_name or ""))
    return words[-1].upper() if words else ""


def resolve_domain(check_type: str | None, domain: str | None = None, column_name: str | None = None) -> str | None:
    """규칙의 도메인. 적어 둔 도메인 → 컬럼 이름의 끝 단어 → 유형의 기본 도메인 순. 도메인이 없는 유형은 None."""
    choices = DOMAINS.get(check_type or "")
    if not choices:
        return None
    if domain and domain.upper() in choices:
        return domain.upper()
    if check_type in _NO_GUESS:
        return choices[0]
    guessed = _SUFFIX_DOMAINS.get(_last_word(column_name))
    return guessed if guessed in choices else choices[0]


def get(key: str | None) -> Indicator | None:
    """키로 지표(분류)를 찾는다. 없으면 None."""
    return _BY_KEY.get(str(key or "").strip().upper())


def definable() -> list[Indicator]:
    """미리 정의할 수 있는 분류 — 유형이 모두 ``DEFINABLE_TYPES`` 인 것."""
    return [i for i in INDICATORS if all(t in DEFINABLE_TYPES for t in i.check_types)]


def hint_words(key: str) -> frozenset[str]:
    """이 분류의 컬럼에 흔한 이름 끝 단어(대문자). 이름으로 추정하지 않는 분류는 빈 집합."""
    return _SUFFIX_HINTS.get(key, frozenset()) | {w for w, d in _SUFFIX_DOMAINS.items() if d == key}


def suggests(key: str, column_name: str | None) -> bool:
    """컬럼 이름으로 보아 이 분류의 지표를 할당할 만한가(이름 끝 단어로 추정)."""
    word = _last_word(column_name)
    if not word:
        return False
    return word in hint_words(key)


def validate_domain(check_type: str | None, domain: str | None) -> str | None:
    """저장할 도메인 값. 빈 값은 None(자동). 그 유형에 없는 도메인은 ``ValueError``."""
    value = (domain or "").strip().upper()
    if not value:
        return None
    choices = DOMAINS.get(check_type or "")
    if not choices:
        raise ValueError(f"{check_type} 유형은 도메인을 정하지 않습니다")
    if value not in choices:
        raise ValueError(f"{check_type} 유형의 도메인이 아닙니다: {value} ({', '.join(choices)})")
    return value


def indicator_of(
    check_type: str | None, domain: str | None = None, column_name: str | None = None,
) -> Indicator | None:
    """규칙이 속한 지표. 지표에 없는 유형(Trino 진단 대상이 아님)은 None."""
    resolved = resolve_domain(check_type, domain, column_name)
    if resolved:
        found = _BY_KEY.get(resolved)  # 도메인은 분류의 키와 같다
        return found if found and check_type in found.check_types else None
    # 도메인으로 갈리지 않는 유형 — 그 유형을 가진 분류는 하나다
    return next((i for i in INDICATORS if check_type in i.check_types), None)


def normalize(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    """지표 키 목록을 검증하고 정해진 순서로 돌려준다. 모르는 키는 ``ValueError``. 빈 값은 빈 목록(= 전체)."""
    if not keys:
        return []
    wanted: set[str] = set()
    for k in keys:
        key = str(k).strip().upper()
        if key:
            wanted.update(_LEGACY.get(key, (key,)))
    unknown = sorted(wanted - _BY_KEY.keys())
    if unknown:
        raise ValueError(f"알 수 없는 진단 기준: {', '.join(unknown)} ({', '.join(_BY_KEY)})")
    return [i.key for i in INDICATORS if i.key in wanted]


def dump(keys: list[str] | None) -> str | None:
    """DB 컬럼 값(쉼표 구분). 전체(선택 없음)는 NULL."""
    return ",".join(keys) if keys else None


def load(value: str | None) -> list[str]:
    """DB 컬럼 값 → 지표 키 목록. 이전 키는 지금의 키로 풀고, 지금은 없는 키는 버린다."""
    wanted: set[str] = set()
    for k in (value or "").split(","):
        wanted.update(_LEGACY.get(k, (k,)))
    return [i.key for i in INDICATORS if i.key in wanted]


def check_types_for(keys: list[str] | None) -> frozenset[str] | None:
    """고른 지표에 걸친 규칙 유형. 고르지 않았으면(전체) None.

    유형만으로는 지표가 정해지지 않을 수 있다(같은 유형의 다른 도메인) — 규칙 하나가 고른 지표에 드는지는
    ``wants`` 로 본다. 이 함수는 DB 에서 후보 규칙을 미리 거르는 데 쓴다.
    """
    if not keys:
        return None
    return frozenset(t for k in keys for t in _BY_KEY[k].check_types)


def wants(
    keys: list[str] | None, check_type: str | None, domain: str | None = None, column_name: str | None = None,
) -> bool:
    """고른 지표(``keys``)로 이 규칙을 진단하는가. 고르지 않았으면(전체) 모든 규칙을 진단한다.

    지표에 들지 않는 유형(커스텀 SQL·커스텀 Python 등)은 지표를 고르지 않은 실행에서만 진단한다 — 그 유형을 경로가
    평가하는지는 호출자가 본다.
    """
    indicator = indicator_of(check_type, domain, column_name)
    if indicator is None:
        return not keys
    return not keys or indicator.key in keys


def ai_ready_of(key: str | None) -> tuple[str, str] | None:
    """진단 기준 → AI-Ready 품질 지표 (지표 키, 세부 지표). AI-Ready 지표에 들지 않는 기준(건수)은 None."""
    found = get(key)
    return found.ai_ready if found else None
