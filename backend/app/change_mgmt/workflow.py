# SPDX-License-Identifier: Apache-2.0
"""Temporal 워크플로우 — 결재/통지/적용 오케스트레이션.

흐름:
    1) 영향 분석(analyze_impact) 후 CR 상태 APPROVING
    2) 결재 단계별 Signal 대기 (approver 가 결재 시 ``submit_decision`` 호출, 단계당 최대 7일)
       - 단계 중 REJECTED → 워크플로우 종료, CR 상태 REJECTED
       - 7일 내 결정 없음 → CR 상태 REJECTED, 결과 "TIMEOUT"
       - APPROVED / DELEGATED → 다음 단계로 (모든 단계 통과 시 CR 상태 APPROVED)
    3) 사전 통지: T-7 / T-1 (단순화) — scheduled_at 기준 (scheduled_at 이 없으면 생략)
    4) 예정 시각까지 대기 후 apply_change (scheduled_at 이 없으면 즉시)
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
    """워크플로우 시작 입력 — ``service.submit_cr`` 가 만든다."""
    cr_id: int
    approver_chain: list[str]                    # 결재 순서대로
    scheduled_at_iso: str | None = None          # ISO8601 문자열
    notify_channels: list[str] = field(default_factory=lambda: ["EMAIL", "IN_APP"])


@dataclass
class DecisionSignal:
    """``submit_decision`` Signal 페이로드 — ``service.submit_decision_signal`` 가 보낸다."""
    step_order: int
    decision: str                                 # APPROVED / REJECTED / DELEGATED
    comment: str | None = None
    decided_by: str | None = None
    delegated_to: str | None = None               # DELEGATED 일 때 대결자


# 액티비티 공통 재시도 정책 (DB 일시 장애 등 흡수)
_RETRY = RetryPolicy(maximum_attempts=5)
_ACTIVITY_OPTS = dict(start_to_close_timeout=timedelta(minutes=5), retry_policy=_RETRY)


@workflow.defn(name="ChangeApprovalWorkflow")
class ChangeApprovalWorkflow:
    """변경 결재 + 통지 + 적용 오케스트레이터.

    취소(``cancel`` Signal)는 적용(apply_change) 전 어느 단계에서든 받는다 — 결재 대기뿐 아니라
    영향 분석 직후(결재선 없는 CR), 승인 후 사전 통지·예정 시각 대기 중에도 확인한다.
    """

    def __init__(self) -> None:
        self._pending: dict[int, DecisionSignal] = {}
        self._cancelled: bool = False

    # ---- Signals -----------------------------------------------------------

    @workflow.signal(name="submit_decision")
    def submit_decision(self, signal: DecisionSignal) -> None:
        """결재 결정 수신 — step_order 별로 보관했다가 run 루프가 해당 단계 차례에 꺼낸다."""
        self._pending[signal.step_order] = signal

    @workflow.signal(name="cancel")
    def cancel(self) -> None:
        """취소 플래그를 세운다 — 각 대기 지점에서 확인해 CANCELLED 로 종료한다."""
        self._cancelled = True

    # ---- Run ---------------------------------------------------------------

    @workflow.run
    async def run(self, input: WorkflowInput) -> str:
        """결재·통지·적용을 순서대로 진행하고 최종 결과 문자열을 반환한다.

        Returns: "APPLIED" / "REJECTED" / "TIMEOUT" / "CANCELLED"
        """
        # 1) 영향 분석
        await workflow.execute_activity(analyze_impact, input.cr_id, **_ACTIVITY_OPTS)
        await workflow.execute_activity(
            update_cr_status, args=[input.cr_id, "APPROVING"], **_ACTIVITY_OPTS
        )
        if self._cancelled:  # 영향 분석 중 취소(결재선 없는 CR 은 아래 대기가 없다)
            return await self._finish_cancelled(input)

        # 2) 결재 — 단계별 Signal 대기 (단계 당 최대 7일)
        for idx, approver in enumerate(input.approver_chain, start=1):
            workflow.logger.info("결재 대기: step=%d approver=%s", idx, approver)

            try:
                await workflow.wait_condition(
                    lambda: idx in self._pending or self._cancelled,
                    timeout=timedelta(days=7),
                )
            except TimeoutError:
                workflow.logger.warning("결재 기한 초과 → 반려 처리: cr_id=%d step=%d", input.cr_id, idx)
                await workflow.execute_activity(
                    update_cr_status, args=[input.cr_id, "REJECTED"], **_ACTIVITY_OPTS
                )
                return "TIMEOUT"

            if self._cancelled:
                return await self._finish_cancelled(input)

            sig = self._pending.pop(idx)
            # 대결자는 위임일 때만 6번째 인자로 넘긴다 — 위임이 아니면 예전과 같은 인자(진행 중 워크플로우·
            # 기존 워커 호환). 위임은 아직 대결자 라우팅 없이 다음 단계로 진행한다(승인과 같은 흐름).
            decision_args = [input.cr_id, idx, sig.decision, sig.comment, sig.decided_by or approver]
            if sig.delegated_to:
                decision_args.append(sig.delegated_to)
            await workflow.execute_activity(record_decision, args=decision_args, **_ACTIVITY_OPTS)

            if sig.decision == "REJECTED":
                workflow.logger.info("결재 반려로 종료: cr_id=%d step=%d", input.cr_id, idx)
                await workflow.execute_activity(
                    update_cr_status, args=[input.cr_id, "REJECTED"], **_ACTIVITY_OPTS
                )
                return "REJECTED"

        # 3) 사전 통지 + 예정 시각 대기 — 대기 중 취소되면 적용하지 않는다
        if self._cancelled:
            return await self._finish_cancelled(input)
        await workflow.execute_activity(
            update_cr_status, args=[input.cr_id, "APPROVED"], **_ACTIVITY_OPTS
        )

        if input.scheduled_at_iso:
            scheduled = datetime.fromisoformat(input.scheduled_at_iso)
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if await self._notify_phased(input, scheduled):
                return await self._finish_cancelled(input)

            now = workflow.now()
            remaining = (scheduled - now).total_seconds()
            if remaining > 0 and await self._sleep_or_cancel(remaining):
                return await self._finish_cancelled(input)

        # 4) 적용 (이 확인 이후의 취소는 반영하지 않는다 — 라우터는 적용 전 상태
        #    SUBMITTED·APPROVING·APPROVED·SCHEDULED 에서만 취소를 받는다)
        if self._cancelled:
            return await self._finish_cancelled(input)
        await workflow.execute_activity(
            apply_change, ApplyInput(cr_id=input.cr_id), **_ACTIVITY_OPTS
        )

        # 5) 적용 완료 통지
        await workflow.execute_activity(
            send_notifications,
            NotifyInput(cr_id=input.cr_id, stage="APPLIED", channels=input.notify_channels),
            **_ACTIVITY_OPTS,
        )

        workflow.logger.info("변경 적용 워크플로우 완료: cr_id=%d", input.cr_id)
        return "APPLIED"

    # ---- Internals ---------------------------------------------------------

    async def _finish_cancelled(self, input: WorkflowInput) -> str:
        """CR 상태를 CANCELLED 로 바꾸고 "CANCELLED" 를 반환한다."""
        workflow.logger.info("취소 Signal 로 워크플로우 종료: cr_id=%d", input.cr_id)
        await workflow.execute_activity(
            update_cr_status, args=[input.cr_id, "CANCELLED"], **_ACTIVITY_OPTS
        )
        return "CANCELLED"

    async def _sleep_or_cancel(self, seconds: float) -> bool:
        """``seconds`` 동안 대기하되 취소 Signal 이 오면 즉시 깨어난다. 취소됐으면 True."""
        try:
            await workflow.wait_condition(lambda: self._cancelled, timeout=timedelta(seconds=seconds))
        except TimeoutError:
            pass
        return self._cancelled

    async def _notify_phased(self, input: WorkflowInput, scheduled: datetime) -> bool:
        """T-7 / T-1 시점 통지. 시점이 이미 지난 경우 즉시 발송. 대기 중 취소되면 True."""
        now = workflow.now()
        for stage, delta in [("T_MINUS_7", timedelta(days=7)), ("T_MINUS_1", timedelta(days=1))]:
            if self._cancelled:
                return True
            send_at = scheduled - delta
            wait_s = (send_at - now).total_seconds()
            if wait_s > 0:
                if await self._sleep_or_cancel(wait_s):
                    return True
                now = workflow.now()
            await workflow.execute_activity(
                send_notifications,
                NotifyInput(cr_id=input.cr_id, stage=stage, channels=input.notify_channels),
                **_ACTIVITY_OPTS,
            )
        return self._cancelled
