# SPDX-License-Identifier: Apache-2.0
"""페더레이션 정보 항목(capability) 레지스트리 — 노출자/소비자 공용 단일 출처.

페더레이션 상세에서 다룰 수 있는 모든 정보 항목을 **필드 단위**로 정의한다.

- 노출자(provider)는 이 레지스트리에서 외부에 줄 항목을 고른다(export 필터링).
- 소비자(hub)는 노출자가 advertise 한 항목을 가져와, 그중 화면에 표시할 것을 고른다.
- 상세 화면은 (노출자 노출 ∩ 소비자 선택) 의 항목만 보여준다.

키는 점 표기(`schema.pii`)로 섹션 하위 필드를 표현한다. 이름/URN/데이터소스 같은
식별 필드는 항상 포함되며 레지스트리 대상이 아니다.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 레지스트리 — (key, label, group, group_label)
# ---------------------------------------------------------------------------

# 그룹 표시 순서/라벨
_GROUPS: list[tuple[str, str]] = [
    ("basic", "기본정보"),
    ("schema", "스키마"),
    ("governance", "거버넌스/관계"),
    ("data", "데이터"),
]

# (key, label, group)
_ITEMS: list[tuple[str, str, str]] = [
    # 기본정보
    ("description", "설명", "basic"),
    ("qualified_name", "정규화 이름", "basic"),
    ("table_type", "테이블 유형", "basic"),
    ("storage_format", "저장 포맷", "basic"),
    ("status", "상태", "basic"),
    ("origin", "환경(origin)", "basic"),
    ("is_synced", "동기화 여부", "basic"),
    # 확장 메타데이터 전체(생명주기·물리·거버넌스·비즈니스·사용·품질·요약·비고)를 한 번에
    ("extended", "확장 메타데이터", "basic"),
    # 스키마 (schema 가 마스터; 하위는 컬럼 단위)
    ("schema", "스키마(컬럼 목록)", "schema"),
    ("schema.type", "컬럼 타입", "schema"),
    ("schema.nullable", "Null 허용", "schema"),
    ("schema.pk", "기본키(PK)", "schema"),
    ("schema.unique", "Unique", "schema"),
    ("schema.indexed", "Indexed", "schema"),
    ("schema.partition", "Partition Key", "schema"),
    ("schema.distribution", "Distribution Key", "schema"),
    ("schema.pii", "PII 타입", "schema"),
    ("schema.description", "컬럼 설명", "schema"),
    # 거버넌스/관계
    ("tags", "태그", "governance"),
    ("owners", "오너", "governance"),
    ("properties", "속성(properties)", "governance"),
    ("glossary", "용어집(glossary)", "governance"),
    ("lineage", "리니지", "governance"),
    # 데이터
    ("sample", "샘플 데이터", "data"),
    ("ddl", "DDL", "data"),
]

# capability 키 → 스키마 컬럼 dict 의 실제 키(들)
_SCHEMA_COLUMN_KEYS: dict[str, tuple[str, ...]] = {
    "schema.type": ("field_type", "native_type"),
    "schema.nullable": ("nullable",),
    "schema.pk": ("is_primary_key",),
    "schema.unique": ("is_unique",),
    "schema.indexed": ("is_indexed",),
    "schema.partition": ("is_partition_key",),
    "schema.distribution": ("is_distribution_key",),
    "schema.pii": ("pii_type",),
    "schema.description": ("description",),
}

# 스키마 항목에서 항상 유지하는 식별 컬럼(논리명 포함 — 로컬 상세와 동일하게 표시)
_SCHEMA_IDENTITY_KEYS = ("field_path", "display_name", "ordinal")

# 최상위 메타데이터에서 항상 유지하는 식별 필드
_METADATA_IDENTITY_KEYS = ("dataset_id", "urn", "name", "datasource")

_ALL_KEYS = [k for k, _, _ in _ITEMS]
_KEY_SET = set(_ALL_KEYS)


def all_keys() -> list[str]:
    """레지스트리의 모든 capability 키."""
    return list(_ALL_KEYS)


def registry_items() -> list[dict]:
    """API 응답용 레지스트리(그룹 메타 포함)."""
    group_labels = dict(_GROUPS)
    return [
        {"key": k, "label": label, "group": group, "group_label": group_labels[group]}
        for k, label, group in _ITEMS
    ]


def groups() -> list[dict]:
    """그룹 표시 순서/라벨."""
    return [{"group": g, "label": label} for g, label in _GROUPS]


def validate(keys: list[str] | None) -> list[str]:
    """알 수 없는 키를 제거하고 레지스트리 순서로 정규화한다."""
    if not keys:
        return []
    given = set(keys)
    return [k for k in _ALL_KEYS if k in given]


def default_exposed(exclude_pii: bool = False) -> list[str]:
    """노출자 기본값 — 전체 노출(거버넌스로 PII 제외 시 schema.pii 만 제외)."""
    keys = list(_ALL_KEYS)
    if exclude_pii:
        keys = [k for k in keys if k != "schema.pii"]
    return keys


def filter_metadata(metadata: dict, exposed: list[str] | set[str]) -> dict:
    """메타데이터 dict 를 노출 키 집합으로 필터링한다(식별 필드는 항상 유지).

    - 최상위 항목(description/tags/owners/properties/ddl/glossary/...)은 키가 없으면 제거.
    - ``schema`` 키가 없으면 schema 전체 제거. 있으면 각 컬럼에서 미노출 속성 제거.
    - sample/lineage 는 별도 엔드포인트라 여기서 다루지 않는다(엔드포인트에서 게이팅).
    """
    allow = set(exposed)
    out: dict = {}

    # 식별 필드는 무조건 유지
    for k in _METADATA_IDENTITY_KEYS:
        if k in metadata:
            out[k] = metadata[k]

    # 단순 최상위 항목 (키 == capability 키)
    for cap in (
        "description", "qualified_name", "table_type", "storage_format",
        "status", "origin", "is_synced", "extended", "tags", "owners",
        "properties", "ddl", "glossary",
    ):
        if cap in allow and cap in metadata:
            out[cap] = metadata[cap]

    # 스키마
    if "schema" in allow and "schema" in metadata:
        out["schema"] = [_filter_schema_field(f, allow) for f in metadata["schema"]]

    return out


def _filter_schema_field(field: dict, allow: set[str]) -> dict:
    """스키마 컬럼 dict 에서 미노출 속성을 제거한다(식별 컬럼은 유지)."""
    kept: dict = {}
    for k in _SCHEMA_IDENTITY_KEYS:
        if k in field:
            kept[k] = field[k]
    for cap, col_keys in _SCHEMA_COLUMN_KEYS.items():
        if cap in allow:
            for ck in col_keys:
                if ck in field:
                    kept[ck] = field[ck]
    return kept


def is_exposed(key: str, exposed: list[str] | set[str]) -> bool:
    """단일 capability 키가 노출 집합에 포함되는지(별도 엔드포인트 게이팅용)."""
    return key in _KEY_SET and key in set(exposed)
