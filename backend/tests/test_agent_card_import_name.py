"""A2A 소개 정보 가져오기 — 카드 이름을 카탈로그 이름으로 바꿀 수 없을 때의 이름 후보 (DEF-189)."""
from app.agents.card_import import card_to_doc


def _name(card_name: str, url: str) -> tuple[str, list[str]]:
    doc, notes = card_to_doc({"name": card_name}, url)
    return doc["metadata"]["name"], notes


def test_korean_card_name_uses_last_path_segment():
    name, notes = _name("데이터 분석 에이전트", "http://192.0.2.50:25310/agents/data-analyst")
    assert name == "data-analyst"
    assert any("data-analyst" in n for n in notes)


def test_well_known_card_file_is_not_a_name():
    name, _ = _name("데이터 분석 에이전트", "http://agents.example.com/data-analyst/.well-known/agent.json")
    assert name == "data-analyst"


def test_falls_back_to_host_when_path_is_empty():
    name, _ = _name("데이터 분석 에이전트", "http://agents.example.com/.well-known/agent-card.json")
    assert name == "agents.example.com"


def test_ascii_card_name_is_kept():
    name, _ = _name("Data Analyst", "http://agents.example.com/agents/x")
    assert name == "data-analyst"
