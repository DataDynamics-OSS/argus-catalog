import { z } from "zod"

export const datasetOriginSchema = z.union([
  z.literal("PROD"),
  z.literal("DEV"),
  z.literal("STAGING"),
])
export type DatasetOrigin = z.infer<typeof datasetOriginSchema>

export const datasetStatusSchema = z.union([
  z.literal("active"),
  z.literal("inactive"),
  z.literal("deprecated"),
  z.literal("removed"),
])
export type DatasetStatus = z.infer<typeof datasetStatusSchema>

export const ownerTypeSchema = z.union([
  z.literal("TECHNICAL_OWNER"),
  z.literal("BUSINESS_OWNER"),
  z.literal("DATA_STEWARD"),
])
export type OwnerType = z.infer<typeof ownerTypeSchema>

export const schemaFieldSchema = z.object({
  id: z.number(),
  field_path: z.string(),
  display_name: z.string().nullable().optional(),
  field_type: z.string(),
  native_type: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  nullable: z.string(),
  is_primary_key: z.string().optional().default("false"),
  is_unique: z.string().optional().default("false"),
  is_indexed: z.string().optional().default("false"),
  is_partition_key: z.string().optional().default("false"),
  is_distribution_key: z.string().optional().default("false"),
  ordinal: z.number(),
})
export type SchemaField = z.infer<typeof schemaFieldSchema>

export const tagSchema = z.object({
  id: z.number(),
  name: z.string(),
  description: z.string().nullable().optional(),
  color: z.string(),
  created_at: z.string(),
  /** 태그 목록에서만 — 붙은 데이터셋 수(removed 제외) */
  dataset_count: z.number().nullable().optional(),
})
export type Tag = z.infer<typeof tagSchema>

export const glossaryTermSchema = z.object({
  id: z.number(),
  name: z.string(),
  description: z.string().nullable().optional(),
  parent_id: z.number().nullable().optional(),
  term_type: z.string().optional().default("TERM"),
  created_at: z.string(),
  /** 용어 목록에서만 — 연결된 데이터셋 수(removed 제외) */
  dataset_count: z.number().nullable().optional(),
  updated_at: z.string(),
})
export type GlossaryTerm = z.infer<typeof glossaryTermSchema>

export const ownerSchema = z.object({
  id: z.number(),
  dataset_id: z.number(),
  owner_name: z.string(),
  owner_type: z.string(),
  created_at: z.string(),
})
export type Owner = z.infer<typeof ownerSchema>

export const datasourceSchema = z.object({
  id: z.number(),
  datasource_id: z.string(),
  name: z.string(),
  type: z.string(),
  logo_url: z.string().nullable().optional(),
  origin: z.string().optional().default("DEV"),
  created_at: z.string(),
})
export type Datasource = z.infer<typeof datasourceSchema>

export const datasetSummarySchema = z.object({
  id: z.number(),
  urn: z.string(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  datasource_name: z.string(),
  datasource_type: z.string(),
  summary: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  origin: z.string(),
  status: z.string(),
  is_synced: z.string().optional().default("false"),
  tag_count: z.number(),
  owner_count: z.number(),
  schema_field_count: z.number(),
  // 생성자(소유자) — 행 ⋮ "삭제" 노출 판단용
  created_by: z.string().nullable().optional(),
  created_at: z.coerce.date(),
  updated_at: z.coerce.date(),
  // 카드 보기용(목록 조회에 card=true 일 때만) — 앞쪽 컬럼 이름
  column_preview: z.array(z.string()).nullable().optional(),
})
export type DatasetSummary = z.infer<typeof datasetSummarySchema>

export const datasetPropertySchema = z.object({
  id: z.number(),
  property_key: z.string(),
  property_value: z.string(),
})
export type DatasetProperty = z.infer<typeof datasetPropertySchema>

export const datasetDetailSchema = z.object({
  id: z.number(),
  urn: z.string(),
  created_by: z.string().nullable().optional(),
  name: z.string(),
  display_name: z.string().nullable().optional(),
  datasource: datasourceSchema,
  summary: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  origin: z.string(),
  qualified_name: z.string().nullable().optional(),
  table_type: z.string().nullable().optional(),
  storage_format: z.string().nullable().optional(),
  ddl: z.string().nullable().optional(),
  status: z.string(),
  is_synced: z.string().optional().default("false"),
  // --- 확장 메타데이터 ---
  ingestion_frequency: z.string().nullable().optional(),
  ingestion_time: z.string().nullable().optional(),
  ingestion_day: z.string().nullable().optional(),
  ingestion_timezone: z.string().nullable().optional(),
  ingestion_cron: z.string().nullable().optional(),
  ingestion_mode: z.string().nullable().optional(),
  update_type: z.string().nullable().optional(),
  freshness_sla: z.string().nullable().optional(),
  last_ingested_at: z.string().nullable().optional(),
  retention_days: z.number().nullable().optional(),
  purge_days: z.number().nullable().optional(),
  data_category: z.string().nullable().optional(),
  data_format: z.string().nullable().optional(),
  compression: z.string().nullable().optional(),
  encoding: z.string().nullable().optional(),
  row_count: z.number().nullable().optional(),
  byte_size: z.number().nullable().optional(),
  file_count: z.number().nullable().optional(),
  sensitivity: z.string().nullable().optional(),
  contains_pii: z.boolean().nullable().optional(),
  pii_fields: z.array(z.string()).nullable().optional(),   // TODO-143 — 목록(값 안의 쉼표 보존)
  compliance_tags: z.array(z.string()).nullable().optional(),
  tier: z.string().nullable().optional(),
  certification: z.string().nullable().optional(),
  steward: z.string().nullable().optional(),
  view_count: z.number().optional().default(0),
  query_count: z.number().optional().default(0),
  last_accessed_at: z.string().nullable().optional(),
  quality_score: z.number().nullable().optional(),
  quality_status: z.string().nullable().optional(),
  show_quality_score: z.boolean().nullable().optional(),
  note: z.string().nullable().optional(),
  // --- AI-Ready (TODO-096) — 비어 있으면 기관 기본값(GET /catalog/ai-ready/defaults)을 보여 준다 ---
  legal_basis: z.string().nullable().optional(),
  publisher: z.string().nullable().optional(),
  landing_page: z.string().nullable().optional(),
  contact_email: z.string().nullable().optional(),
  data_language: z.string().nullable().optional(),
  persistent_id: z.string().nullable().optional(),
  temporal_start: z.string().nullable().optional(), // YYYY-MM-DD
  temporal_end: z.string().nullable().optional(),
  spatial_coverage: z.string().nullable().optional(),
  data_version: z.string().nullable().optional(),
  version_notes: z.string().nullable().optional(),
  license: z.string().nullable().optional(),
  license_url: z.string().nullable().optional(),
  rights: z.string().nullable().optional(),
  allowed_uses: z.array(z.string()).nullable().optional(), // null = 정하지 않음, [] = 모두 허용하지 않음
  contains_synthetic: z.boolean().nullable().optional(),
  contains_machine_annotation: z.boolean().nullable().optional(),
  data_biases: z.string().nullable().optional(),
  known_limitations: z.string().nullable().optional(),
  missing_data_info: z.string().nullable().optional(),
  missing_value_notation: z.string().nullable().optional(),
  privacy_measures: z.string().nullable().optional(),
  datasource_properties: z.record(z.string(), z.unknown()).nullable().optional(),
  schema_fields: z.array(schemaFieldSchema),
  tags: z.array(tagSchema),
  owners: z.array(ownerSchema),
  glossary_terms: z.array(glossaryTermSchema),
  properties: z.array(datasetPropertySchema).optional(),
  created_at: z.coerce.date(),
  updated_at: z.coerce.date(),
})
export type DatasetDetail = z.infer<typeof datasetDetailSchema>
