"""Reusable assertion helpers for dataset/schema comparisons.

플랫폼별 테스트가 같은 패턴(동기화 후 카탈로그 응답 검증) 을 반복하므로
공통 헬퍼로 추출한다.
"""

from __future__ import annotations

from typing import Any

from lib.argus_client import ArgusClient


def assert_dataset_exists(
    client: ArgusClient,
    urn: str,
) -> dict[str, Any]:
    """URN 으로 dataset 을 가져오고 존재하면 반환, 없으면 ``AssertionError``."""
    ds = client.get_dataset_by_urn(urn)
    assert ds is not None, f"Dataset with URN {urn!r} not found in catalog"
    return ds


def assert_schema_fields(
    dataset: dict[str, Any],
    expected: list[dict[str, Any]],
) -> None:
    """``expected`` 의 각 항목이 ``dataset.schema_fields`` 안에 동일한 값으로 존재.

    expected 항목은 비교할 키만 포함하면 됨 (예: ``{"field_path": "id", "field_type": "BIGINT"}``).
    """
    by_path = {f["field_path"]: f for f in dataset.get("schema_fields", [])}
    for want in expected:
        path = want["field_path"]
        assert path in by_path, (
            f"Expected schema field {path!r} missing. "
            f"Got: {sorted(by_path.keys())}"
        )
        got = by_path[path]
        for key, expected_val in want.items():
            assert got.get(key) == expected_val, (
                f"Schema field {path!r} mismatch on {key!r}: "
                f"want={expected_val!r} got={got.get(key)!r}"
            )


def assert_property(
    dataset: dict[str, Any],
    key: str,
    expected: str | None = None,
) -> str:
    """dataset.properties 에서 key 를 찾고 (선택적으로) 값까지 검증.

    ``expected=None`` 이면 존재 여부만 확인하고 실제 값을 반환.
    """
    for p in dataset.get("properties", []) or []:
        if p["property_key"] == key:
            if expected is not None:
                assert p["property_value"] == expected, (
                    f"Property {key!r}: want={expected!r} got={p['property_value']!r}"
                )
            return p["property_value"]
    raise AssertionError(
        f"Property {key!r} not found. "
        f"Got keys: {[p['property_key'] for p in dataset.get('properties', []) or []]}"
    )
