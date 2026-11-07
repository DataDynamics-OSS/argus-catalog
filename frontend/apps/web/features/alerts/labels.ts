/** 알림 화면 공용 한국어 라벨 — 알림 페이지·규칙 다이얼로그·헤더 벨이 같은 용어를 쓰도록. */

export const SEVERITY_LABEL: Record<string, string> = {
  BREAKING: "심각",
  WARNING: "경고",
  INFO: "정보",
}

export const STATUS_LABEL: Record<string, string> = {
  OPEN: "미확인",
  ACKNOWLEDGED: "확인",
  RESOLVED: "해결",
  DISMISSED: "무시",
}

export const ALERT_TYPE_LABEL: Record<string, string> = {
  SCHEMA_CHANGE: "스키마 변경",
  QUALITY_FAILED: "품질 검증 실패",
  MCP_POLICY_DRIFT: "MCP 정책 위반·드리프트",
  MCP_SECURITY_WARNING: "MCP 보안 경고",
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
  QUALITY_FAILED: "품질 검증 실패",
  MCP_POLICY_DRIFT: "MCP 정책 위반·드리프트",
  MCP_SECURITY_WARNING: "MCP 보안 경고",
  SYNC_STALE: "동기화 지연(미지원)",
}

export const CHANNEL_LABEL: Record<string, string> = {
  IN_APP: "앱 내",
  WEBHOOK: "Webhook",
  EMAIL: "이메일(미지원)",
}

/** MCP 트리거 — 범위는 전체·데이터 소스만. */
export const MCP_TRIGGERS = new Set(["MCP_POLICY_DRIFT", "MCP_SECURITY_WARNING"])

export function labelOf(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return ""
  return map[key] ?? key
}
