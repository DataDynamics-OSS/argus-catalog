"""LLM 이 낸 컬럼 설명을 한 줄 평문으로 정리 (DEF-197)."""

import pytest

from app.ai.service import plain_column_description


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("영화의 **language** 를 가리키는 `language_id` 입니다.", "영화의 language 를 가리키는 language_id 입니다."),
        ("*대여 기간*(일)", "대여 기간(일)"),
        ("## 설명\n- 고객 번호\n- 기본 키", "설명 고객 번호 기본 키"),
        ("```\ncustomer_id\n```\n고객 식별자", "customer_id 고객 식별자"),
        ("1. 주문 금액\n2. 원 단위", "주문 금액 원 단위"),
        ("단가 * 수량 = 금액", "단가 * 수량 = 금액"),
        ("first_name 과 last_name 을 합친 값", "first_name 과 last_name 을 합친 값"),
        ("  주문   일시  ", "주문 일시"),
        ("", ""),
        (None, ""),
        (123, ""),
    ],
)
def test_plain_column_description(raw, expected):
    assert plain_column_description(raw) == expected
