# SPDX-License-Identifier: Apache-2.0
"""Temporal 워커 진입점.

별도 프로세스로 기동한다:

    python -m app.change_mgmt.worker

API 서버(FastAPI) 와 분리하여 워크플로우/액티비티 실행만 담당한다.
스케일은 워커 프로세스 수로 조절.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

# ChangeRequest.dataset_id 는 catalog_datasets 를 FK 로 참조한다. 워커 프로세스가
# 액티비티에서 CR 을 flush/commit 할 때 FK 대상 테이블 메타데이터가 같은 Base 에
# 등록돼 있어야 하므로 catalog 모델을 먼저 로드한다 (FastAPI lifespan 과 동일 역할).
import app.catalog.models  # noqa: F401
import app.change_mgmt.models  # noqa: F401
from app.change_mgmt import temporal_client
from app.change_mgmt.activities import (
    analyze_impact,
    apply_change,
    record_decision,
    send_notifications,
    update_cr_status,
    wait_acks_summary,
)
from app.change_mgmt.workflow import ChangeApprovalWorkflow

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await temporal_client.connect()

    worker = Worker(
        client,
        task_queue=temporal_client.TASK_QUEUE,
        workflows=[ChangeApprovalWorkflow],
        activities=[
            analyze_impact,
            record_decision,
            update_cr_status,
            send_notifications,
            wait_acks_summary,
            apply_change,
        ],
    )
    logger.info("변경관리 워커 시작: queue=%s", temporal_client.TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
