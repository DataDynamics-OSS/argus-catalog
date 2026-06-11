import { z } from "zod"

export const modelSummarySchema = z.object({
  id: z.number(),
  name: z.string(),
  description: z.string().nullable().optional(),
  owner: z.string().nullable().optional(),
  max_version_number: z.number(),
  status: z.string(),
  latest_version_status: z.string().nullable().optional(),
  sklearn_version: z.string().nullable().optional(),
  python_version: z.string().nullable().optional(),
  model_size_bytes: z.number().nullable().optional(),
  download_count: z.number().optional().default(0),
  updated_at: z.coerce.date(),
})
export type ModelSummary = z.infer<typeof modelSummarySchema>
