// 페더레이션 상태 이름 · 톤 — 동기화 이력 · 관측 · 인스턴스 화면이 같이 쓴다(DEF-114, TODO-107).
import { statusMeta, type StatusMeta } from "@workspace/ui/components/status-badge"

import type { FederationMode, FederationStatus } from "./api"

export const SYNC_STATUS: Record<string, StatusMeta> = {
  SUCCESS: { label: "성공", tone: "success" },
  FAILED: { label: "실패", tone: "danger" },
}

export const INSTANCE_STATUS: Record<FederationStatus, StatusMeta> = {
  ACTIVE: { label: "활성", tone: "success" },
  PAUSED: { label: "일시 중지", tone: "neutral" },
}

/** 연합 모드 이름(TODO-109) — 코드는 API · 문서용으로 선택 항목 옆에만 작게 보인다 */
export const MODE_LABEL: Record<FederationMode, string> = {
  HYBRID: "혼합",
  HARVEST: "가져오기",
  LIVE: "실시간",
}

export const modeLabel = (mode: string | null | undefined) => MODE_LABEL[(mode ?? "") as FederationMode] ?? mode ?? ""

export const syncStatus = (status: string | null | undefined) => statusMeta(SYNC_STATUS, status)
export const instanceStatus = (status: string | null | undefined) => statusMeta(INSTANCE_STATUS, status)
