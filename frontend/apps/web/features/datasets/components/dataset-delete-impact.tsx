"use client"

// 데이터셋 삭제 대화상자의 '함께 삭제되는 변경 요청' 안내 — 상세·목록 삭제 대화상자 공용(DEF-059).
// 변경 요청은 데이터셋 FK CASCADE 로 함께 지워지고, 진행 중 결재 워크플로우는 서버가 삭제 뒤 취소한다.

import { useEffect, useState } from "react"

import { listChangeRequests, type ChangeRequest, type CRStatus } from "@/features/change-mgmt/api"

/** 삭제 뒤 결재 워크플로우가 취소되는 상태 — 서버 change_mgmt.service._IN_FLIGHT_STATUSES 와 같다. */
export const IN_FLIGHT_CR_STATUSES: readonly CRStatus[] = ["SUBMITTED", "APPROVING", "APPROVED", "SCHEDULED"]

export function summarizeChangeRequests(crs: Pick<ChangeRequest, "status">[]): { total: number; inFlight: number } {
  return {
    total: crs.length,
    inFlight: crs.filter((cr) => IN_FLIGHT_CR_STATUSES.includes(cr.status)).length,
  }
}

/** 삭제 대화상자 설명 — 함께 지워지는 항목 */
export const DATASET_DELETE_DESCRIPTION =
  "이 작업은 되돌릴 수 없습니다. 데이터셋과 관련된 스키마/속성/태그/소유자/리니지 정보, 댓글, " +
  "이 데이터셋 범위의 알림 규칙, 변경 요청이 모두 함께 삭제되고 진행 중인 변경 요청의 결재는 취소됩니다."

/** 변경 요청이 있으면 건수를 보여 준다. 조회 실패·0건이면 아무것도 보이지 않는다(설명 문구로 충분). */
export function DatasetDeleteImpact({ datasetId }: { datasetId: number | null }) {
  const [impact, setImpact] = useState<{ datasetId: number; total: number; inFlight: number } | null>(null)

  useEffect(() => {
    if (datasetId == null) return
    let cancelled = false
    listChangeRequests({ dataset_id: datasetId })
      .then((crs) => { if (!cancelled) setImpact({ datasetId, ...summarizeChangeRequests(crs) }) })
      .catch((err) => console.warn("Failed to load change requests for delete impact", { datasetId, err }))
    return () => { cancelled = true }
  }, [datasetId])

  if (!impact || impact.datasetId !== datasetId || impact.total === 0) return null
  return (
    <p className="text-sm text-destructive">
      변경 요청 {impact.total}건이 함께 삭제됩니다
      {impact.inFlight > 0 && ` (진행 중인 결재 ${impact.inFlight}건 취소)`}.
    </p>
  )
}
