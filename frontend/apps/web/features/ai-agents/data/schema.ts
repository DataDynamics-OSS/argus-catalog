import { z } from "zod"

export const aiAgentSummarySchema = z.object({
  id: z.number(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  version: z.string(),
  status: z.string(),
  owner_email: z.string().nullable().optional(),
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
})

export type AIAgentSummary = z.infer<typeof aiAgentSummarySchema>

/** 상태 배지 색상 매핑. */
export const AGENT_STATUS_VARIANTS: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  staging: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  draft: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  blocked: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  deprecated: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  retired: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
}

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

export const RISK_LABEL: Record<string, string> = { low: "낮음", medium: "보통", high: "높음", critical: "매우 높음" }
export const RISK_CLASS: Record<string, string> = {
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  critical: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
}

export const AGENT_STATUS_LABEL: Record<string, string> = {
  draft: "초안", staging: "스테이징", active: "활성",
  blocked: "차단", deprecated: "사용 중단", retired: "폐기",
}
