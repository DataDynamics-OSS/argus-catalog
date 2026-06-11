# SPDX-License-Identifier: Apache-2.0
"""Hive 테이블에서 sample row 를 뽑는 헬퍼.

현재 통합 테스트의 seed 는 HMS metadata 만 등록하고 S3/MinIO warehouse 에는 실제
데이터 파일을 쓰지 않는다. 따라서 SQL 으로도, PyArrow + S3 로도 읽을 행이 없다.
호출 측에서는 매번 ``EmptyWarehouseError`` 를 받고 sample 업로드를 skip 한다.
"""

from __future__ import annotations


class EmptyWarehouseError(RuntimeError):
    """Hive warehouse 에 실데이터가 없어 sample 을 만들 수 없음을 알리는 예외."""


def fetch_sample_csv(_hms_client, _qualified: str) -> bytes:
    """Hive 의 seed 가 데이터를 쓰지 않으므로 항상 ``EmptyWarehouseError`` 를 raise.

    catalog 서버에 빈 CSV 라도 올릴 수는 있지만, parquet 변환이 빈 본문에서 실패할
    수 있어 호출 측에서 명시적으로 skip 하는 편이 안전하다.
    """
    raise EmptyWarehouseError(
        "Hive integration test seed only creates HMS metadata, no warehouse data",
    )
