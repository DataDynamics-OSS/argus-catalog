# SPDX-License-Identifier: Apache-2.0
"""데이터셋 목록 카드 보기용 필드 (TODO-111) — in-memory SQLite.

- ``card=true`` 면 항목마다 순서상 앞쪽 컬럼 이름 3개(``column_preview``)를 채운다.
- ``card`` 를 주지 않으면 비어 있다(다른 화면의 목록 조회는 그 비용을 치르지 않는다).
"""

from __future__ import annotations

import app.catalog.models as cm
from tests.test_ai_ready_check import V1, env  # noqa: F401 — 픽스처 재사용


def test_card_fields(env) -> None:  # noqa: F811
    client, _who, ids, run = env

    async def _columns(db):
        # ordinal 순서가 입력 순서와 다르게 — 미리보기는 ordinal 순
        for ordinal, path in [(5, "f"), (0, "a"), (2, "c"), (1, "b"), (3, "d"), (4, "e")]:
            db.add(cm.DatasetSchema(dataset_id=ids["bare"], field_path=path, field_type="STRING", ordinal=ordinal))

    run(_columns)

    plain = client.get(f"{V1}/catalog/datasets").json()
    assert plain["items"] and all(i["column_preview"] is None for i in plain["items"])
    assert all("ai_ready_required" not in i for i in plain["items"])

    body = client.get(f"{V1}/catalog/datasets", params={"card": "true"}).json()
    items = {i["id"]: i for i in body["items"]}
    assert set(items) == {ids["bare"], ids["other"]}   # removed 는 상태 미지정이면 제외

    bare = items[ids["bare"]]
    assert bare["column_preview"] == ["a", "b", "c"]
    assert bare["schema_field_count"] == 6
    assert items[ids["other"]]["column_preview"] == []
