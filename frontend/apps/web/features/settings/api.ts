import { authFetch, throwOnError } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const BASE = "/api/v1/settings"

export type ObjectStorageConfig = {
  endpoint: string
  access_key: string
  secret_key: string
  region: string
  use_ssl: boolean
  bucket: string
  presigned_url_expiry: number
}

export async function fetchObjectStorageConfig(): Promise<ObjectStorageConfig> {
  const res = await authFetch(`${BASE}/object-storage`)
  if (!res.ok) throw new Error(`설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateObjectStorageConfig(
  config: ObjectStorageConfig,
): Promise<void> {
  const res = await authFetch(`${BASE}/object-storage`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "설정 수정 실패")
}

export type InitStep = {
  step: string
  status: "ok" | "skip" | "created" | "error"
  message: string
}

export async function initializeObjectStorage(
  endpoint: string, accessKey: string, secretKey: string, region: string, bucket: string,
): Promise<{ steps: InitStep[] }> {
  const res = await authFetch(`${BASE}/object-storage/initialize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint, access_key: accessKey, secret_key: secretKey, region, bucket }),
  })
  if (!res.ok) await throwOnError(res, "초기화 실패")
  return res.json()
}

export async function testObjectStorage(
  endpoint: string,
  accessKey: string,
  secretKey: string,
  region: string,
  bucket: string,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/object-storage/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      endpoint,
      access_key: accessKey,
      secret_key: secretKey,
      region,
      bucket,
    }),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}


// ---------------------------------------------------------------------------
// Embedding configuration
// ---------------------------------------------------------------------------

export type EmbeddingConfig = {
  enabled: boolean
  auto_on_write: boolean
  provider: string
  model: string
  api_key: string
  api_url: string
  dimension: number
}

export async function fetchEmbeddingConfig(): Promise<EmbeddingConfig> {
  const res = await authFetch(`${BASE}/embedding`)
  if (!res.ok) throw new Error(`임베딩 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateEmbeddingConfig(config: EmbeddingConfig): Promise<void> {
  const res = await authFetch(`${BASE}/embedding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "임베딩 설정 수정 실패")
}

export async function testEmbedding(
  config: EmbeddingConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/embedding/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}

export type EmbeddingStats = {
  total_datasets: number
  embedded_datasets: number
  coverage_pct: number
  embedded_entities?: {
    glossary_term: number
    ai_agent: number
    api: number
  }
  provider: string | null
  model: string | null
  dimension: number | null
}

export async function fetchEmbeddingStats(): Promise<EmbeddingStats> {
  const res = await authFetch("/api/v1/catalog/search/embeddings/stats")
  if (!res.ok) throw new Error(`통계 조회 실패: ${res.status}`)
  return res.json()
}

export async function backfillEmbeddings(): Promise<{
  total: number; embedded: number; skipped: number; errors: number
}> {
  const res = await authFetch("/api/v1/catalog/search/embeddings/backfill", { method: "POST" })
  if (!res.ok) await throwOnError(res, "백필 실패")
  return res.json()
}

export async function clearEmbeddings(): Promise<{ deleted: number }> {
  const res = await authFetch("/api/v1/catalog/search/embeddings", { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "초기화 실패")
  return res.json()
}

// 컬럼 관계 전체 재계산 — 수집된 모든 쿼리 이력을 재분석(metadata-sync 트리거 프록시).
export async function recomputeRelationships(reset = true): Promise<{
  reset_deleted: number | null
  queries_analyzed: number
  pairs_pushed: number
  per_platform: Record<string, number>
  unresolved_tables: string[]
}> {
  const res = await authFetch(`/api/v1/catalog/relationships/recompute?reset=${reset}`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "재계산 실패")
  return res.json()
}

// 쿼리 lineage 의 dataset_id 백필 — 테이블명→데이터셋 해석(리니지 그래프 노출용).
export async function resolveLineage(): Promise<{ resolved: number; remaining_unresolved: number }> {
  const res = await authFetch("/api/v1/catalog/lineage/resolve", { method: "POST" })
  if (!res.ok) await throwOnError(res, "lineage 해석 실패")
  return res.json()
}


// ---------------------------------------------------------------------------
// Authentication (Keycloak) configuration
// ---------------------------------------------------------------------------

export type AuthConfig = {
  type: string
  server_url: string
  realm: string
  client_id: string
  client_secret: string
  admin_role: string
  superuser_role: string
  user_role: string
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  const res = await authFetch(`${BASE}/auth`)
  if (!res.ok) throw new Error(`인증 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function fetchAuthSecret(): Promise<string> {
  const res = await authFetch(`${BASE}/auth/secret`)
  if (!res.ok) throw new Error(`시크릿 조회 실패: ${res.status}`)
  const data = await res.json()
  return data.client_secret
}

export async function updateAuthConfig(config: AuthConfig): Promise<void> {
  const res = await authFetch(`${BASE}/auth`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "인증 설정 수정 실패")
}

export type KeycloakInitRequest = {
  server_url: string
  admin_username: string
  admin_password: string
  realm: string
  client_id: string
  client_secret: string
  roles: string[]
}

export async function initializeKeycloak(
  req: KeycloakInitRequest,
): Promise<{ steps: InitStep[] }> {
  const res = await authFetch(`${BASE}/auth/initialize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) await throwOnError(res, "초기화 실패")
  return res.json()
}

export async function testAuthConnection(
  config: AuthConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/auth/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}


// ---------------------------------------------------------------------------
// CORS configuration
// ---------------------------------------------------------------------------

export type CorsConfig = {
  origins: string
}

export async function fetchCorsConfig(): Promise<CorsConfig> {
  const res = await authFetch(`${BASE}/cors`)
  if (!res.ok) throw new Error(`CORS 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateCorsConfig(config: CorsConfig): Promise<void> {
  const res = await authFetch(`${BASE}/cors`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "CORS 설정 수정 실패")
}


// ---------------------------------------------------------------------------
// LLM (AI metadata generation) configuration
// ---------------------------------------------------------------------------

export type LLMConfig = {
  enabled: boolean
  provider: string
  model: string
  api_key: string
  api_url: string
  temperature: number
  max_tokens: number
  auto_generate_on_sync: boolean
  language: string
}

export async function fetchLLMConfig(): Promise<LLMConfig> {
  const res = await authFetch(`${BASE}/llm`)
  if (!res.ok) throw new Error(`LLM 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateLLMConfig(config: LLMConfig): Promise<void> {
  const res = await authFetch(`${BASE}/llm`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "LLM 설정 수정 실패")
}

export async function testLLM(
  config: LLMConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/llm/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}

export type AIStats = {
  total_generations: number
  applied_count: number
  pending_count: number
  total_prompt_tokens: number
  total_completion_tokens: number
  description_coverage: {
    total_datasets: number
    described_datasets: number
    coverage_pct: number
  }
  by_type: Record<string, number>
  provider: string | null
  model: string | null
}

export async function fetchAIStats(): Promise<AIStats> {
  const res = await authFetch("/api/v1/ai/stats")
  if (!res.ok) throw new Error(`AI 통계 조회 실패: ${res.status}`)
  return res.json()
}

export type BulkGenerateRequest = {
  generation_types: string[]
  apply: boolean
  language?: string
  datasource_id?: number
  empty_only: boolean
}

export type BulkGenerateResult = {
  total: number
  processed: number
  errors: number
}

export async function bulkGenerate(req: BulkGenerateRequest): Promise<BulkGenerateResult> {
  const res = await authFetch("/api/v1/ai/bulk-generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) await throwOnError(res, "일괄 생성 실패")
  return res.json()
}

// ---------------------------------------------------------------------------
// AI 어시스턴트 (agent serve 프록시)
// ---------------------------------------------------------------------------

export type AssistantConfig = {
  enabled: boolean
  agent_url: string
}

export async function fetchAssistantConfig(): Promise<AssistantConfig> {
  const res = await authFetch(`${BASE}/assistant`)
  if (!res.ok) throw new Error(`어시스턴트 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateAssistantConfig(config: AssistantConfig): Promise<void> {
  const res = await authFetch(`${BASE}/assistant`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "어시스턴트 설정 수정 실패")
}

export async function testAssistant(
  config: AssistantConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/assistant/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`연결 테스트 실패: ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Email (SMTP)
// ---------------------------------------------------------------------------

export type MailConfig = {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  use_tls: boolean
  use_ssl: boolean
  timeout_seconds: number
  from_email: string
  from_name: string
  smtp_user: string
  smtp_password: string
  subject_prefix: string
  default_recipients: string
}

export async function fetchMailConfig(): Promise<MailConfig> {
  const res = await authFetch(`${BASE}/mail`)
  if (!res.ok) throw new Error(`메일 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateMailConfig(config: MailConfig): Promise<void> {
  const res = await authFetch(`${BASE}/mail`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "메일 설정 수정 실패")
}

export async function testMail(req: {
  to?: string
  subject?: string
  body?: string
}): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/mail/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Notify (Slack / Mattermost)
// ---------------------------------------------------------------------------

export type NotifyWebhookConfig = {
  webhook_url: string
  channel: string
  username: string
  icon_emoji: string
}

export type NotifyConfig = {
  enabled: boolean
  provider: "slack" | "mattermost"
  timeout_seconds: number
  slack: NotifyWebhookConfig
  mattermost: NotifyWebhookConfig
}

export async function fetchNotifyConfig(): Promise<NotifyConfig> {
  const res = await authFetch(`${BASE}/notify`)
  if (!res.ok) throw new Error(`알림 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateNotifyConfig(config: NotifyConfig): Promise<void> {
  const res = await authFetch(`${BASE}/notify`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "알림 설정 수정 실패")
}

export async function testNotify(req: { text?: string }): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/notify/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`테스트 실패: ${res.status}`)
  return res.json()
}

// 변경관리(참조자 통지) 설정
export type ChangeMgmtConfig = {
  notify_enabled: boolean
  notify_channel: "email" | "slack" | "mattermost"
}

export async function fetchChangeMgmtConfig(): Promise<ChangeMgmtConfig> {
  const res = await authFetch(`${BASE}/change`)
  if (!res.ok) throw new Error(`변경관리 설정 조회 실패: ${res.status}`)
  return res.json()
}

export async function updateChangeMgmtConfig(config: ChangeMgmtConfig): Promise<void> {
  const res = await authFetch(`${BASE}/change`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "변경관리 설정 수정 실패")
}

