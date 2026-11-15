/** 알림 화면 공용 한국어 라벨 — 알림 페이지·규칙 다이얼로그·헤더 벨이 같은 용어를 쓰도록. */
import type { StatusTone } from "@workspace/ui/components/status-badge"

/** 심각도 이름은 품질 규칙과 공용(`lib/severity.ts`) */
export { SEVERITY_LABEL } from "@/lib/severity"

export const STATUS_LABEL: Record<string, string> = {
  OPEN: "미확인",
  ACKNOWLEDGED: "확인",
  RESOLVED: "해결",
  DISMISSED: "무시",
}

/** 처리 상태 톤 — 데이터 신고(`components/comments/report-status.tsx`)와 같은 기준(TODO-107). 심각도는 따로 `lib/severity.ts`. */
export const STATUS_TONE: Record<string, StatusTone> = {
  OPEN: "warning",
  ACKNOWLEDGED: "info",
  RESOLVED: "success",
  DISMISSED: "muted",
}

export const ALERT_TYPE_LABEL: Record<string, string> = {
  SCHEMA_CHANGE: "스키마 변경",
  QUALITY_FAILED: "품질 진단 실패",
  MCP_POLICY_DRIFT: "MCP 정책 위반·드리프트",
  MCP_SECURITY_WARNING: "MCP 보안 경고",
  SYNC_FAILED: "수집 실패",
  SYNC_STALE: "수집 지연",
  AI_READY_UNMET: "AI-Ready 최소 요건 미충족",
  REPORT_RECEIVED: "오류 신고 접수",
  REPORT_DUE: "신고 처리 기한",
}

export const SCOPE_LABEL: Record<string, string> = {
  ALL: "전체",
  DATASET: "데이터셋",
  TAG: "태그",
  LINEAGE: "리니지",
  DATASOURCE: "데이터 소스",
}

export const TRIGGER_LABEL: Record<string, string> = {
  ANY: "모든 변경",
  SCHEMA_CHANGE: "스키마 변경(유형 선택)",
  COLUMN_WATCH: "컬럼 감시",
  MAPPING_BROKEN: "매핑 컬럼 변경",
  QUALITY_FAILED: "품질 진단 실패",
  MCP_POLICY_DRIFT: "MCP 정책 위반·드리프트",
  MCP_SECURITY_WARNING: "MCP 보안 경고",
  SYNC_FAILED: "수집 실패",
  SYNC_STALE: "수집 지연(성공한 수집 없음)",
  AI_READY_UNMET: "AI-Ready 최소 요건 미충족",
  REPORT_RECEIVED: "오류 신고·개선 요청 접수",
  REPORT_DUE: "신고 처리 기한 임박·넘김",
}

/** AI-Ready 최소 요건 조건 (trigger_config.conditions) */
export const AI_READY_CONDITION_LABEL: Record<string, string> = {
  missing: "결측률",
  metadata: "필수 메타데이터",
  freshness: "갱신 주기",
  format: "표준 포맷",
}
/** 신고 분류 (trigger_config.categories) */
export const REPORT_CATEGORY_LABEL: Record<string, string> = {
  bug: "오류 신고",
  suggestion: "개선 요청",
}

export const CHANNEL_LABEL: Record<string, string> = {
  IN_APP: "앱 내",
  WEBHOOK: "Webhook",
  EMAIL: "이메일(미지원)",
}

/** MCP 트리거 — 범위는 전체·데이터 소스만. */
export const MCP_TRIGGERS = new Set(["MCP_POLICY_DRIFT", "MCP_SECURITY_WARNING"])
/** 데이터 소스 수집 트리거 — 범위는 전체·데이터 소스만. */
export const SYNC_TRIGGERS = new Set(["SYNC_FAILED", "SYNC_STALE"])
/** AI-Ready 트리거 — 데이터셋 범위. 수신자에 데이터셋 생성자가 더해진다. */
export const AI_READY_TRIGGERS = new Set(["AI_READY_UNMET", "REPORT_RECEIVED", "REPORT_DUE"])

export function labelOf(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return ""
  return map[key] ?? key
}
