// 데이터셋 상태 이름 · 톤 — 목록 표 · 카드 · 메타데이터 요약이 같이 쓴다(DEF-113, TODO-107).
import { statusMeta, type StatusMeta } from "@workspace/ui/components/status-badge"

export const DATASET_STATUS: Record<string, StatusMeta> = {
  active: { label: "활성", tone: "success" },
  inactive: { label: "비활성", tone: "neutral" },
  deprecated: { label: "사용 중단", tone: "warning" },
  removed: { label: "삭제됨", tone: "danger" },
}

export const datasetStatus = (status: string | null | undefined) => statusMeta(DATASET_STATUS, status)

/** 품질 상태 — 메타데이터 요약 · 업스트림 품질 경고(TODO-107). 예전 경고는 BAD · WARN 코드를 그대로 보였다. */
export const DATASET_QUALITY_STATUS: Record<string, StatusMeta> = {
  GOOD: { label: "양호", tone: "success" },
  WARN: { label: "주의", tone: "warning" },
  BAD: { label: "불량", tone: "danger" },
  UNKNOWN: { label: "미확인", tone: "neutral" },
}

export const datasetQualityStatus = (status: string | null | undefined) => statusMeta(DATASET_QUALITY_STATUS, status)
