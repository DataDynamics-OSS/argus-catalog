# SPDX-License-Identifier: Apache-2.0
"""CR ``schema_after`` 해석 — 삭제만 있는 구조화 명세 (자기완결형, 순수 함수).

예전에는 ``{"drop": ["phone"]}`` 처럼 "fields" 없이 삭제만 적으면 평면 맵으로 읽어
"drop" 이라는 컬럼을 추가(upsert)하려 했고 영향 분석도 ``changed_columns=["drop"]`` 이었다(IT CHG-T11).

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_cr_schema_after_parse
"""

from __future__ import annotations

from app.change_mgmt.activities import _parse_schema_after


def test_drop_only_structured() -> None:
    assert _parse_schema_after('{"drop": ["phone"]}') == ([], ["phone"])


def test_structured_and_flat_maps_unchanged() -> None:
    ups, drops = _parse_schema_after(
        '{"fields": [{"field_path": "email", "field_type": "VARCHAR(255)"}], "drop": ["legacy_flag"]}')
    assert [u["field_path"] for u in ups] == ["email"] and drops == ["legacy_flag"]
    assert _parse_schema_after('{"created_at": "TIMESTAMP"}') == (
        [{"field_path": "created_at", "field_type": "TIMESTAMP"}], [])
    # 평면 맵에서 이름이 drop 인 컬럼(값이 목록이 아님)은 여전히 컬럼 추가로 읽는다
    assert _parse_schema_after('{"drop": "INT"}') == ([{"field_path": "drop", "field_type": "INT"}], [])


if __name__ == "__main__":
    test_drop_only_structured()
    test_structured_and_flat_maps_unchanged()
    print("test_cr_schema_after_parse OK")
