# SPDX-License-Identifier: Apache-2.0
"""변경 관리(change-mgmt) 엔드투엔드 통합 테스트.

Temporal 워크플로우 엔진과 실제 DB 를 사용해 CR 라이프사이클 전체 + 액티비티
실로직(영향 분석 / 스키마 적용 / 통지)을 검증한다. Temporal 워커를 별도 프로세스로
띄우지 않고 같은 이벤트 루프에서 in-process 워커로 가동하므로 단일 실행으로 완결된다.

격리: 테스트 전용 데이터셋 2개(source/target)와 리니지를 만들고, 종료 시 데이터셋만
삭제하면 ondelete=CASCADE 로 스키마/스냅샷/CR/결재/통지/소비자/리니지/매핑이 전부
함께 정리된다. 운영 데이터는 건드리지 않는다.

전제(prerequisite):
  1. PostgreSQL 가동 + 카탈로그 스키마 생성 (catalog_datasources 에 최소 1개 데이터소스)
  2. Temporal 가동:
        docker compose -f backend/deploy/temporal/docker-compose.yml up -d
  3. 설정 디렉터리 지정:
        export ARGUS_CATALOG_SERVER_CONFIG_DIR=$(pwd)/packaging/config

실행:
    cd backend
    PYTHONPATH=$(pwd) .venv/bin/python -m tests.test_change_mgmt_e2e

검증 시나리오:
  - A: 2단계 결재 전부 승인 → APPLIED
       · 영향 분석: 다운스트림 1개 데이터셋 + 컬럼 수준 영향(target.email_addr) 산정
       · 스키마 적용: email 타입 변경 + phone 추가 + legacy_col 삭제가 실제 반영
       · 통지: 소비자 1 × 채널 2 = 2건 기록 + ACK 처리
  - B: 1단계 반려 → REJECTED (이후 단계 진행 안 함)
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import delete, select
from temporalio.worker import Worker

import app.catalog.models  # noqa: F401  (FK 대상 메타데이터 등록)
import app.change_mgmt.models  # noqa: F401
from app.catalog.models import (
    Dataset,
    DatasetColumnMapping,
    DatasetLineage,
    DatasetSchema,
    Datasource,
)
from app.change_mgmt import schemas, service, temporal_client
from app.change_mgmt.activities import (
    analyze_impact,
    apply_change,
    record_decision,
    send_notifications,
    update_cr_status,
    wait_acks_summary,
)
from app.change_mgmt.workflow import ChangeApprovalWorkflow
from app.core.database import async_session

POLL_INTERVAL = 0.4
POLL_MAX = 60
TAG = "[E2E-CM]"


# ---------------------------------------------------------------------------
# 픽스처 (테스트 전용 데이터셋/리니지)
# ---------------------------------------------------------------------------

async def _setup_fixture() -> tuple[int, int]:
    """source/target 데이터셋 + 리니지 + 컬럼 매핑을 생성하고 (source_id, target_id) 반환."""
    async with async_session() as s:
        datasource_id = await s.scalar(select(Datasource.id).order_by(Datasource.id).limit(1))
        if datasource_id is None:
            raise RuntimeError("catalog_datasources 가 비어 있습니다.")

        source = Dataset(urn="urn:e2e:cm:source", name=f"{TAG}_source", datasource_id=datasource_id)
        target = Dataset(urn="urn:e2e:cm:target", name=f"{TAG}_target", datasource_id=datasource_id)
        s.add_all([source, target])
        await s.flush()

        # source 초기 스키마: id, email(VARCHAR(100)), legacy_col(TEXT)
        s.add_all([
            DatasetSchema(dataset_id=source.id, field_path="id", field_type="BIGINT", ordinal=0),
            DatasetSchema(dataset_id=source.id, field_path="email", field_type="VARCHAR(100)", ordinal=1),
            DatasetSchema(dataset_id=source.id, field_path="legacy_col", field_type="TEXT", ordinal=2),
        ])
        # target 스키마
        s.add_all([
            DatasetSchema(dataset_id=target.id, field_path="email_addr", field_type="VARCHAR(100)", ordinal=0),
        ])

        lineage = DatasetLineage(
            source_dataset_id=source.id, target_dataset_id=target.id,
            relation_type="ETL", lineage_source="MANUAL",
        )
        s.add(lineage)
        await s.flush()
        s.add(DatasetColumnMapping(
            dataset_lineage_id=lineage.id, source_column="email",
            target_column="email_addr", transform_type="DIRECT",
        ))
        await s.commit()
        return source.id, target.id


async def _teardown_fixture(dataset_ids: list[int]) -> None:
    """데이터셋 삭제 → CASCADE 로 연관 행 전부 정리."""
    async with async_session() as s:
        for ds_id in dataset_ids:
            await s.execute(delete(Dataset).where(Dataset.id == ds_id))
        await s.commit()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

async def _status(cr_id: int) -> str:
    async with async_session() as s:
        cr = await service.get_cr(s, cr_id)
        return cr.status if cr else "<deleted>"


async def _wait_status(cr_id: int, targets: set[str]) -> str:
    for _ in range(POLL_MAX):
        st = await _status(cr_id)
        if st in targets:
            return st
        await asyncio.sleep(POLL_INTERVAL)
    return await _status(cr_id)


async def _make_cr(dataset_id: int, schema_after: str, chain: list[tuple[int, str]]) -> tuple[int, str]:
    async with async_session() as session:
        data = schemas.ChangeRequestCreate(
            title=f"{TAG} email 확장 + phone 추가 + legacy 삭제",
            description="자동 통합 테스트 CR",
            dataset_id=dataset_id,
            change_type=schemas.ChangeType.BREAKING,
            priority=schemas.Priority.HIGH,
            schema_after=schema_after,
            rollback_plan="이전 스키마로 복원",
            business_justification="테스트",
            approval_chain=[
                schemas.ApprovalStepInput(step_order=o, approver=a) for o, a in chain
            ],
        )
        cr = await service.create_cr(session, data, requested_by="e2e-tester")
        await session.commit()
        wf_id = await service.submit_cr(session, cr, ["EMAIL", "IN_APP"])
        await session.commit()
        return cr.id, wf_id


async def _fields(dataset_id: int) -> dict[str, str]:
    async with async_session() as s:
        rows = (await s.execute(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
        )).scalars().all()
    return {f.field_path: f.field_type for f in rows}


# ---------------------------------------------------------------------------
# 시나리오
# ---------------------------------------------------------------------------

async def scenario_approve(source_id: int, target_id: int) -> bool:
    failures: list[str] = []

    async with async_session() as s:
        await service.create_consumer(s, schemas.ConsumerCreate(
            dataset_id=source_id, consumer_name=f"{TAG} downstream-etl",
            consumer_type="SYSTEM", usage="ETL", criticality="MISSION_CRITICAL",
            contact_emails="ops@downstream.io",
        ))
        await s.commit()

    schema_after = json.dumps({
        "fields": [
            {"field_path": "email", "field_type": "VARCHAR(255)", "native_type": "varchar"},
            {"field_path": "phone", "field_type": "VARCHAR(20)"},
        ],
        "drop": ["legacy_col"],
    })
    cr_id, wf_id = await _make_cr(source_id, schema_after, [(1, "owner@argus.io"), (2, "dg@argus.io")])

    if await _wait_status(cr_id, {"APPROVING"}) != "APPROVING":
        failures.append("A: analyze_impact 후 APPROVING 도달 실패")

    # 영향 분석 결과 검증
    async with async_session() as s:
        cr = await service.get_cr(s, cr_id)
        report = json.loads(cr.impact_report) if cr.impact_report else {}
    if report.get("affected_downstream_count") != 1:
        failures.append(f"A: 다운스트림 1개 기대, 실제={report.get('affected_downstream_count')}")
    if f"{target_id}.email_addr" not in report.get("affected_columns", []):
        failures.append(f"A: 컬럼 영향 {target_id}.email_addr 누락, 실제={report.get('affected_columns')}")
    if set(report.get("changed_columns", [])) != {"email", "phone", "legacy_col"}:
        failures.append(f"A: changed_columns 불일치, 실제={report.get('changed_columns')}")
    if report.get("mission_critical_consumers") != 1:
        failures.append(f"A: 중대 소비자 1 기대, 실제={report.get('mission_critical_consumers')}")

    for step in (1, 2):
        await service.submit_decision_signal(wf_id, step, "APPROVED", f"ok {step}", f"approver{step}")
        await asyncio.sleep(1.0)

    if await _wait_status(cr_id, {"APPLIED", "REJECTED", "CANCELLED"}) != "APPLIED":
        failures.append(f"A: 최종 APPLIED 실패, 실제={await _status(cr_id)}")

    # 스키마가 실제로 적용됐는지
    fields = await _fields(source_id)
    if fields.get("email") != "VARCHAR(255)":
        failures.append(f"A: email 타입 미반영, 실제={fields.get('email')}")
    if "phone" not in fields:
        failures.append("A: phone 컬럼 미추가")
    if "legacy_col" in fields:
        failures.append("A: legacy_col 미삭제")

    # 통지 + ACK
    async with async_session() as s:
        logs = await service.list_notifications(s, cr_id)
    if len(logs) != 2:
        failures.append(f"A: 통지 2건 기대, 실제={len(logs)}")
    if logs:
        async with async_session() as s:
            acked = await service.ack_notification(s, logs[0].id, "대응 완료")
            await s.commit()
        if acked.status != "ACKED":
            failures.append(f"A: ACK 실패, 실제={acked.status}")

    ok = not failures
    print(f"[시나리오 A] {'PASS' if ok else 'FAIL'} (cr={cr_id})")
    for f in failures:
        print(f"   ✗ {f}")
    return ok


async def scenario_reject(source_id: int) -> bool:
    failures: list[str] = []
    schema_after = json.dumps({"fields": [{"field_path": "noop", "field_type": "INT"}]})
    cr_id, wf_id = await _make_cr(source_id, schema_after, [(1, "owner@argus.io"), (2, "dg@argus.io")])

    await _wait_status(cr_id, {"APPROVING"})
    await service.submit_decision_signal(wf_id, 1, "REJECTED", "변경 불가", "owner@argus.io")

    if await _wait_status(cr_id, {"REJECTED", "APPLIED", "CANCELLED"}) != "REJECTED":
        failures.append(f"B: REJECTED 기대, 실제={await _status(cr_id)}")

    async with async_session() as s:
        steps = await service.list_approval_steps(s, cr_id)
    step2 = next((x for x in steps if x.step_order == 2), None)
    if step2 and step2.decision != "PENDING":
        failures.append(f"B: 2단계 PENDING 기대, 실제={step2.decision}")
    # 반려 시 스키마는 그대로여야 함 (noop 컬럼 미생성)
    if "noop" in await _fields(source_id):
        failures.append("B: 반려인데 noop 컬럼이 적용됨")

    ok = not failures
    print(f"[시나리오 B] {'PASS' if ok else 'FAIL'} (cr={cr_id})")
    for f in failures:
        print(f"   ✗ {f}")
    return ok


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------

async def main() -> int:
    client = await temporal_client.connect()
    source_id, target_id = await _setup_fixture()
    print(f"픽스처: source={source_id} target={target_id}, Temporal={temporal_client._target()}")

    worker = Worker(
        client,
        task_queue=temporal_client.TASK_QUEUE,
        workflows=[ChangeApprovalWorkflow],
        activities=[
            analyze_impact, record_decision, update_cr_status,
            send_notifications, wait_acks_summary, apply_change,
        ],
    )

    try:
        async with worker:
            ok_a = await scenario_approve(source_id, target_id)
            ok_b = await scenario_reject(source_id)
    finally:
        await _teardown_fixture([source_id, target_id])
        print("정리 완료: 테스트 데이터셋 삭제 (CASCADE)")

    all_ok = ok_a and ok_b
    print("=" * 50)
    print("RESULT:", "ALL PASS ✅" if all_ok else "FAILED ❌")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
