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
