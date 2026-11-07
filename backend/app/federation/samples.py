# SPDX-License-Identifier: Apache-2.0
"""페더레이션 미러 샘플 데이터의 로컬 저장소.

로컬 데이터셋 샘플(``data_dir/samples/{datasource_id}/{name}/sample.parquet``)과
**완전히 분리된 네임스페이스**를 쓴다 — peer 에서 가져온 미러 데이터이기 때문이다.

    경로: ``data_dir/federation/samples/{instance_id}/{federation_dataset_id}.json``

- 파일명을 미러 행 id 로 쓰므로 URN 의 ``.``/``/`` 정규화가 필요 없고,
  인스턴스/데이터셋 단위 정리(cleanup)가 단순하다.
- peer 가 준 JSON(``{format, columns, rows, row_count}``)을 그대로 저장한다.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


def _root() -> Path:
    """미러 샘플 저장 루트(``data_dir/federation/samples``)."""
    return settings.data_dir / "federation" / "samples"


def sample_path(instance_id: int, federation_dataset_id: int) -> Path:
    """미러 데이터셋 1건의 샘플 JSON 경로."""
    return _root() / str(instance_id) / f"{federation_dataset_id}.json"


def write_sample(
    instance_id: int, federation_dataset_id: int, sample: dict
) -> None:
    """샘플 JSON 을 저장(부모 디렉터리 생성)."""
    p = sample_path(instance_id, federation_dataset_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    logger.debug(
        "미러 샘플 저장: instance_id=%s federation_dataset_id=%s",
        instance_id, federation_dataset_id,
    )


def read_sample(instance_id: int, federation_dataset_id: int) -> dict | None:
    """저장된 샘플 JSON 을 읽는다. 없거나 손상 시 None."""
    p = sample_path(instance_id, federation_dataset_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # noqa: BLE001 — 손상 파일은 미보유로 취급
        logger.warning("미러 샘플 읽기 실패(미보유로 취급): path=%s error=%s", p, e)
        return None


def delete_sample(instance_id: int, federation_dataset_id: int) -> None:
    """미러 데이터셋 1건의 샘플 파일을 삭제(prune 시)."""
    try:
        sample_path(instance_id, federation_dataset_id).unlink(missing_ok=True)
    except OSError as e:
        # 삭제 실패는 무시(다음 prune 에서 재시도) — 원인 추적용으로만 남긴다.
        logger.warning(
            "미러 샘플 삭제 실패: instance_id=%s federation_dataset_id=%s error=%s",
            instance_id, federation_dataset_id, e,
        )


def delete_instance_samples(instance_id: int) -> None:
    """인스턴스의 샘플 디렉터리를 통째로 삭제(인스턴스 삭제 시)."""
    d = _root() / str(instance_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        logger.info("인스턴스 미러 샘플 디렉터리 삭제: instance_id=%s", instance_id)
