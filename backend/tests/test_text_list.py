# SPDX-License-Identifier: Apache-2.0
"""문자열 컬럼에 담는 값 목록 (TODO-143) — JSON 배열로 저장해 값 안의 쉼표를 지키고, 예전 쉼표 문자열도 읽는다.

실행:
    cd backend
    pytest tests/test_text_list.py
"""
from __future__ import annotations

import pytest

from app.core.text_list import decode_list, encode_list
from app.quality import rule_sql


@pytest.mark.parametrize(("stored", "values"), [
    (None, []),
    ("", []),
    ("Y, N,", ["Y", "N"]),                       # 예전 쉼표 문자열
    ('["1,000", " N/A ", "", "1,000"]', ["1,000", "N/A"]),  # JSON — 쉼표 보존, 공백 · 빈 값 · 중복 제거
    ('[1, 2]', ["1", "2"]),
    ("[A], [B]", ["[A]", "[B]"]),                 # [ 로 시작해도 JSON 이 아니면 예전 규칙
    ('{"a": 1}', ['{"a": 1}']),
])
def test_decode_list(stored, values):
    assert decode_list(stored) == values


def test_encode_list_round_trip():
    assert encode_list([]) is None
    assert encode_list(None) is None
    stored = encode_list(["환불, 결제 문의", "주소"])
    assert stored == '["환불, 결제 문의", "주소"]'
    assert decode_list(stored) == ["환불, 결제 문의", "주소"]
    assert encode_list("a, b") == '["a", "b"]'


def test_quality_list_values_keep_commas():
    assert rule_sql.accepted_values('["1,000", "2,000"]') == ["1,000", "2,000"]
    assert rule_sql.accepted_values("Y,N") == ["Y", "N"]
    assert rule_sql.dummy_values('["9,999,999"]') == ["9,999,999"]
    assert rule_sql.dummy_values(None) == list(rule_sql.DEFAULT_DUMMY_VALUES)


# --- TODO-144: 남은 쉼표 입력(알림 구독자 · 에이전트 목록 필터) ---------------------------------

def test_alert_subscribers_accept_list_and_reject_comma():
    from app.alert.schemas import normalize_subscribers

    assert normalize_subscribers(["alice", " bob ", "alice", ""]) == "alice,bob"
    assert normalize_subscribers("alice, bob\ncarol") == "alice,bob,carol"  # 예전 클라이언트
    assert normalize_subscribers([]) is None
    with pytest.raises(ValueError):
        normalize_subscribers(["a,b"])


def test_agent_tag_filter_keeps_commas_in_values():
    import datetime as dt
    from types import SimpleNamespace

    from app.agents.governance import AgentFacts, filter_and_sort

    now = dt.datetime(2026, 11, 15, tzinfo=dt.timezone.utc)
    a = SimpleNamespace(id=1, name="a", tags=["환불, 결제", "VIP"], updated_at=now, usage_count=0, last_invoked_at=None)
    b = SimpleNamespace(id=2, name="b", tags=["환불", "결제"], updated_at=now, usage_count=0, last_invoked_at=None)
    facts = {1: AgentFacts(owner_state="ok", completeness=100), 2: AgentFacts(owner_state="ok", completeness=100)}
    assert [x.id for x in filter_and_sort([a, b], facts, tag='["환불, 결제"]')] == [1]
    assert [x.id for x in filter_and_sort([a, b], facts, tag="환불, 결제")] == [2]  # 예전 쉼표 질의
