"""Temporal 워크플로우 — 결재/통지/적용 오케스트레이션.

흐름:
    1) 영향 분석(analyze_impact)
    2) 결재 단계별 Signal 대기 (approver 가 결재 시 ``submit_decision`` 호출)
       - 단계 중 REJECTED → 워크플로우 종료, CR 상태 REJECTED
       - 모든 단계 APPROVED → 다음 단계로
    3) 사전 통지: T-7 / T-1 (단순화) — scheduled_at 기준
    4) 예정 시각까지 대기 후 apply_change
    5) 변경 직후 통지(APPLIED)

워크플로우는 반드시 결정적(deterministic) 이어야 하므로 I/O 는 모두 액티비티로 분리한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.change_mgmt.activities import (
        ApplyInput,
        NotifyInput,
        analyze_impact,
        apply_change,
        record_decision,
        send_notifications,
        update_cr_status,
    )


@dataclass
class WorkflowInput:
    cr_id: int
    approver_chain: list[str]                    # 결재 순서대로
    scheduled_at_iso: str | None = None          # ISO8601 문자열
    notify_channels: list[str] = field(default_factory=lambda: ["EMAIL", "IN_APP"])


@dataclass
class DecisionSignal:
    step_order: int
    decision: str                                 # APPROVED / REJECTED / DELEGATED
    comment: str | None = None
    decided_by: str | None = None


# 액티비티 공통 재시도 정책 (DB 일시 장애 등 흡수)
_RETRY = RetryPolicy(maximum_attempts=5)
_ACTIVITY_OPTS = dict(start_to_close_timeout=timedelta(minutes=5), retry_policy=_RETRY)


@workflow.defn(name="ChangeApprovalWorkflow")
class ChangeApprovalWorkflow:
    """변경 결재 + 통지 + 적용 오케스트레이터."""

    def __init__(self) -> None:
        self._pending: dict[int, DecisionSignal] = {}
        self._cancelled: bool = False

    # ---- Signals -----------------------------------------------------------

    @workflow.signal(name="submit_decision")
    def submit_decision(self, signal: DecisionSignal) -> None:
        self._pending[signal.step_order] = signal

    @workflow.signal(name="cancel")
    def cancel(self) -> None:
        self._cancelled = True

    # ---- Run ---------------------------------------------------------------

    @workflow.run
    async def run(self, input: WorkflowInput) -> str:
        # 1) 영향 분석
        await workflow.execute_activity(analyze_impact, input.cr_id, **_ACTIVITY_OPTS)
        await workflow.execute_activity(
            update_cr_status, args=[input.cr_id, "APPROVING"], **_ACTIVITY_OPTS
        )

        # 2) 결재 — 단계별 Signal 대기 (단계 당 최대 7일)
        for idx, approver in enumerate(input.approver_chain, start=1):
            workflow.logger.info("결재 대기: step=%d approver=%s", idx, approver)

            try:
                await workflow.wait_condition(
                    lambda: idx in self._pending or self._cancelled,
                    timeout=timedelta(days=7),
                )
            except TimeoutError:
                await workflow.execute_activity(
                    update_cr_status, args=[input.cr_id, "REJECTED"], **_ACTIVITY_OPTS
                )
                return "TIMEOUT"

            if self._cancelled:
                await workflow.execute_activity(
                    update_cr_status, args=[input.cr_id, "CANCELLED"], **_ACTIVITY_OPTS
                )
                return "CANCELLED"

            sig = self._pending.pop(idx)
            await workflow.execute_activity(
                record_decision,
                args=[input.cr_id, idx, sig.decision, sig.comment, sig.decided_by or approver],
                **_ACTIVITY_OPTS,
            )

            if sig.decision == "REJECTED":
                await workflow.execute_activity(
                    update_cr_status, args=[input.cr_id, "REJECTED"], **_ACTIVITY_OPTS
                )
                return "REJECTED"

        # 3) 사전 통지 + 예정 시각 대기
        await workflow.execute_activity(
            update_cr_status, args=[input.cr_id, "APPROVED"], **_ACTIVITY_OPTS
        )

        if input.scheduled_at_iso:
            scheduled = datetime.fromisoformat(input.scheduled_at_iso)
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            await self._notify_phased(input, scheduled)

            now = workflow.now()
            remaining = (scheduled - now).total_seconds()
            if remaining > 0:
                await workflow.sleep(remaining)

        # 4) 적용
        await workflow.execute_activity(
            apply_change, ApplyInput(cr_id=input.cr_id), **_ACTIVITY_OPTS
        )

        # 5) 적용 완료 통지
        await workflow.execute_activity(
            send_notifications,
            NotifyInput(cr_id=input.cr_id, stage="APPLIED", channels=input.notify_channels),
            **_ACTIVITY_OPTS,
        )

        return "APPLIED"

    # ---- Internals ---------------------------------------------------------

    async def _notify_phased(self, input: WorkflowInput, scheduled: datetime) -> None:
        """T-7 / T-1 시점 통지. 시점이 이미 지난 경우 즉시 발송."""
        now = workflow.now()
        for stage, delta in [("T_MINUS_7", timedelta(days=7)), ("T_MINUS_1", timedelta(days=1))]:
            send_at = scheduled - delta
            wait_s = (send_at - now).total_seconds()
            if wait_s > 0:
                await workflow.sleep(wait_s)
                now = workflow.now()
            await workflow.execute_activity(
                send_notifications,
                NotifyInput(cr_id=input.cr_id, stage=stage, channels=input.notify_channels),
                **_ACTIVITY_OPTS,
            )
