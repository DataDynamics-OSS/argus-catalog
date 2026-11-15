"""파티션 · 분산 속성 — 스키마 변경 감지와 변경 요청 되돌리기(DEF-132 · TODO-132).

- 카탈로그 필드끼리 비교(직접 편집 · 변경 요청 적용)는 파티션 · 분산 변경도 MODIFY 로 잡는다.
- 원본 DB 동기화(`sync.ColumnInfo`)는 이 속성을 읽지 않으므로 비교에서 빠진다(늘 달라 보이면 안 된다).
- 변경 요청의 schema_before 자동 채움에 들어간 속성은 되돌리기로 복원된다.
"""
from types import SimpleNamespace

from app.catalog.service import detect_schema_changes
from app.catalog.sync import ColumnInfo
from app.change_mgmt.activities import reverse_schema_spec


def _field(path: str, **kw) -> SimpleNamespace:
    base = dict(field_path=path, field_type="INT", native_type="int", nullable="true", is_primary_key="false",
                is_unique="false", is_indexed="false", is_partition_key="false", is_distribution_key="false")
    base.update(kw)
    return SimpleNamespace(**base)


def test_catalog_compare_detects_partition_and_distribution() -> None:
    old = [_field("dt"), _field("id")]
    new = [_field("dt", is_partition_key="true"), _field("id", is_distribution_key="true")]
    changes = detect_schema_changes(old, new)
    assert [(c["type"], c["field"], c["after"]) for c in changes] == [
        ("MODIFY", "dt", {"is_partition_key": "true"}),
        ("MODIFY", "id", {"is_distribution_key": "true"}),
    ]


def test_sync_compare_ignores_unknown_flags() -> None:
    # 카탈로그에서 손으로 파티션을 켰어도, 동기화 결과에 그 속성이 없으면 변경이 아니다
    old = [_field("dt", is_partition_key="true")]
    new = [ColumnInfo(name="dt", data_type="INT", native_type="int", nullable=True, ordinal=0)]
    assert detect_schema_changes(old, new, from_sync=True) == []


def test_cr_apply_namespace_compares_flags() -> None:
    # 변경 요청 적용은 파티션 · 분산을 가진 객체를 넘긴다(apply_schema_spec) — 이때는 비교한다
    old = [_field("dt")]
    new = [SimpleNamespace(name="dt", data_type="INT", native_type="int", nullable=True, is_primary_key=False,
                           is_unique=False, is_indexed=False, is_partition_key=True, is_distribution_key=False,
                           ordinal=0)]
    changes = detect_schema_changes(old, new, from_sync=True)
    assert len(changes) == 1 and changes[0]["after"] == {"is_partition_key": "true"}


def test_reverse_restores_partition() -> None:
    before = ('{"fields":[{"name":"dt","type":"DATE","nullable":true,"primary_key":false,'
              '"is_partition_key":"true","is_distribution_key":"false"}]}')
    after = '{"fields":[{"field_path":"dt","field_type":"DATE","nullable":"true","is_partition_key":"false"}]}'
    ups, drops = reverse_schema_spec(before, after)
    assert drops == []
    assert ups[0]["is_partition_key"] == "true" and ups[0]["nullable"] == "true"
