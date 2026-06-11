"""Sakila rental 데이터셋용 커스텀 품질 체크 (CUSTOM_PYTHON 플러그인 예시).

함수 규약:
    fn(df, params) -> (passed: bool, actual: str, detail: str)

- ``df`` 는 실행 엔진의 DataFrame — pandas(python-quality.py) 또는
  PySpark(dataset-quality.py). 양쪽을 지원하려면 함수 안에서
  ``hasattr(df, "toPandas")`` 로 분기한다 (아래 예시 참고).
- ``params`` 는 규칙의 expected_value JSON 중 ``params`` 객체.

규칙 등록 예 (검증 유형 CUSTOM_PYTHON, 기대값):
    {"module": "rental_checks", "fn": "rental_duration_outlier",
     "params": {"max_outlier_pct": 1.0}}
"""

from __future__ import annotations


def rental_duration_outlier(df, params: dict) -> tuple[bool, str, str]:
    """대여 기간(반납-대여)의 IQR 이상치 비율 검사.

    SQL 로 표현하기 어려운 통계 검증의 예시 — 사분위수 기반 이상치(1.5×IQR
    범위 밖) 비율이 허용치(max_outlier_pct, 기본 1%)를 넘으면 실패한다.
    데이터 입력 오류(반납일 오타 등)로 생기는 극단값을 잡아낸다.
    """
    import pandas as pd

    max_pct = float(params.get("max_outlier_pct", 1.0))

    # PySpark DataFrame 이면 필요한 두 컬럼만 pandas 로 변환
    # (대여 이력 수준의 행 수는 드라이버 메모리로 충분 — 초대용량이면
    #  approxQuantile 기반 구현으로 교체할 것)
    if hasattr(df, "toPandas"):
        pdf = df.select("rental_date", "return_date").toPandas()
    else:
        pdf = df[["rental_date", "return_date"]]

    durations = (
        (pd.to_datetime(pdf["return_date"]) - pd.to_datetime(pdf["rental_date"]))
        .dt.total_seconds() / 86400.0
    ).dropna()

    if len(durations) == 0:
        return True, "0건", "반납 완료 건이 없어 이상치 검사를 생략합니다"

    q1, q3 = durations.quantile(0.25), durations.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = int(((durations < lo) | (durations > hi)).sum())
    pct = outliers / len(durations) * 100

    passed = pct <= max_pct
    return passed, f"{pct:.2f}%", (
        f"대여 기간 IQR 이상치 {outliers}건 ({pct:.2f}%, 허용 {max_pct}%) — "
        f"정상 범위 [{lo:.1f}, {hi:.1f}]일, 반납 완료 {len(durations):,}건 기준"
    )


def negative_duration(df, params: dict) -> tuple[bool, str, str]:
    """반납일이 대여일보다 빠른 행 검출 (CUSTOM_SQL 데모와 동일 검증의 Python 판).

    같은 검증을 SQL/Python 양쪽으로 구현해 두 방식의 차이를 비교하는 용도.
    """
    import pandas as pd

    if hasattr(df, "toPandas"):
        pdf = df.select("rental_date", "return_date").toPandas()
    else:
        pdf = df[["rental_date", "return_date"]]

    returned = pdf.dropna(subset=["return_date"])
    bad = int((pd.to_datetime(returned["return_date"])
               < pd.to_datetime(returned["rental_date"])).sum())
    return bad == 0, f"위반 {bad}행", f"반납일 < 대여일 {bad}건 (전체 데이터 검사)"
