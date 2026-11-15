import type { StatusMeta } from "@workspace/ui/components/status-badge"
import { z } from "zod"

import { LIFECYCLE_LABEL } from "@/lib/lifecycle"
import { RISK_LABEL as SHARED_RISK_LABEL } from "@/lib/risk"

export const aiAgentSummarySchema = z.object({
  id: z.number(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  version: z.string(),
  status: z.string(),
  owner_email: z.string().nullable().optional(),
  created_by: z.string().nullable().optional(),
  department: z.string().nullable().optional(),
  category: z.string().nullable().optional(),
  base_model: z.string().nullable().optional(),
  framework: z.string().nullable().optional(),
  execution_policy: z.string().nullable().optional(),
  reputation_score: z.number().nullable().optional(),
  tags: z.array(z.string()).nullable().optional(),
  updated_at: z.coerce.date(),
  /** 관리 방식 — ui(화면) / manifest(정의 파일) / discovered(발견 후 등록) */
  managed_by: z.string().optional(),
  usage_count: z.number().optional(),
  last_invoked_at: z.string().nullable().optional(),
  /** 조회 때 계산 — 책임자 상태 ok/missing/unknown/inactive */
  owner_state: z.string().nullable().optional(),
  /** 정보 완성도 0~100 */
  completeness: z.number().nullable().optional(),
  /** 완성도에서 빠진 항목 키(COMPLETENESS_LABEL) */
  missing_fields: z.array(z.string()).optional(),
  /** 보유 도구의 최고 위험도 */
  max_risk: z.string().nullable().optional(),
  risk_tier: z.string().nullable().optional(),
  autonomy_level: z.string().nullable().optional(),
  /** owner_email 로 조회한 등록 사용자 — 이름 표시용. 미지정·미등록이면 null */
  owner: z.object({
    email: z.string(),
    username: z.string(),
    first_name: z.string().nullable().default(null),
    last_name: z.string().nullable().default(null),
    organization: z.string().nullable().default(null),
    department: z.string().nullable().default(null),
  }).nullable().optional(),
})

export type AIAgentSummary = z.infer<typeof aiAgentSummarySchema>

/** 관리 방식 표시 이름. */
export const MANAGED_BY_LABEL: Record<string, string> = {
  ui: "화면",
  manifest: "정의 파일",
  discovered: "발견",
}

/** 책임자 상태 표시 이름·설명. */
export const OWNER_STATE_LABEL: Record<string, string> = {
  ok: "확인됨",
  missing: "소유자 없음",
  unknown: "사용자 목록에 없음",
  inactive: "비활성 계정",
}
export const OWNER_STATE_HINT: Record<string, string> = {
  missing: "소유자 이메일이 비어 있습니다.",
  unknown: "사용자 목록에 없는 이메일입니다(퇴사·오타·외부 인원, 또는 인증 서버 사용 시 아직 로그인하지 않은 사람).",
  inactive: "사용자 목록에서 비활성 상태인 계정입니다.",
}

/** 정보 완성도 항목 이름(백엔드 governance.COMPLETENESS_ITEMS 와 같은 키). */
export const COMPLETENESS_LABEL: Record<string, string> = {
  ownership: "소유자·부서·분류",
  description: "설명(20자 이상)",
  endpoint: "호출 주소",
  model: "기반 모델",
  tool_risk: "도구 위험도",
  io_schema: "입출력 형식",
  safeguards: "안전장치(개인정보 처리 + 가드레일 또는 사람 승인)",
  evaluation: "평가 기록",
}

// 위험도 · 상태 이름과 톤은 MCP 와 공용(`lib/risk.ts` · `lib/lifecycle.ts`, TODO-107)
export const RISK_LABEL: Record<string, string> = SHARED_RISK_LABEL
export const AGENT_STATUS_LABEL: Record<string, string> = LIFECYCLE_LABEL

/** 집행 훅 단계 · 판정(DEF-116) — 판정은 허용 초록 · 거부 빨강 · 마스킹/승인 필요 주의 · 수정 진행 */
export const HOOK_STAGE_LABEL: Record<string, string> = {
  access: "접근",
  pre_exec: "실행 전",
  post_exec: "실행 후",
}
export const HOOK_DECISION: Record<string, StatusMeta> = {
  allow: { label: "허용", tone: "success" },
  approved: { label: "승인됨", tone: "success" },
  modified: { label: "수정됨", tone: "info" },
  mask: { label: "마스킹", tone: "warning" },
  require_approval: { label: "승인 필요", tone: "warning" },
  deny: { label: "거부", tone: "danger" },
}

/** 에이전트 연결 관계 · 대상 종류(TODO-113) */
export const AGENT_LINK_RELATION_LABEL: Record<string, string> = {
  depends_on: "의존",
  consumed_by: "호출됨",
  related: "연관",
}
export const AGENT_LINK_TARGET_LABEL: Record<string, string> = {
  agent: "에이전트",
  model: "모델",
  dataset: "데이터셋",
}
