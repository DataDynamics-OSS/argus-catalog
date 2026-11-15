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
  if (!res.ok) throw new Error(`오브젝트 스토리지 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) await throwOnError(res, "오브젝트 스토리지 설정을 수정하지 못했습니다.")
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
  if (!res.ok) await throwOnError(res, "오브젝트 스토리지를 초기화하지 못했습니다.")
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
  if (!res.ok) throw new Error(`오브젝트 스토리지 연결을 테스트하지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) throw new Error(`임베딩 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateEmbeddingConfig(config: EmbeddingConfig): Promise<void> {
  const res = await authFetch(`${BASE}/embedding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "임베딩 설정을 수정하지 못했습니다.")
}

export async function testEmbedding(
  config: EmbeddingConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/embedding/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`임베딩 연결을 테스트하지 못했습니다. (HTTP ${res.status})`)
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
    mcp_server?: number
  }
  provider: string | null
  model: string | null
  dimension: number | null
}

export async function fetchEmbeddingStats(): Promise<EmbeddingStats> {
  const res = await authFetch("/api/v1/catalog/search/embeddings/stats")
  if (!res.ok) throw new Error(`임베딩 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function backfillEmbeddings(): Promise<{
  total: number; embedded: number; skipped: number; errors: number
}> {
  const res = await authFetch("/api/v1/catalog/search/embeddings/backfill", { method: "POST" })
  if (!res.ok) await throwOnError(res, "임베딩을 백필하지 못했습니다.")
  return res.json()
}

export async function clearEmbeddings(): Promise<{ deleted: number }> {
  const res = await authFetch("/api/v1/catalog/search/embeddings", { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "임베딩을 초기화하지 못했습니다.")
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
  if (!res.ok) await throwOnError(res, "관계를 재계산하지 못했습니다.")
  return res.json()
}

// 쿼리 lineage 의 dataset_id 백필 — 테이블명→데이터셋 해석(리니지 그래프 노출용).
// unresolved_catalog: 쿼리한 엔진은 알지만 카탈로그 연결(엔진 쪽 이름)이 없어 해석하지 못한 칸 수.
export async function resolveLineage(): Promise<{
  resolved: number
  unresolved_catalog?: number
  remaining_unresolved: number
}> {
  const res = await authFetch("/api/v1/catalog/lineage/resolve", { method: "POST" })
  if (!res.ok) await throwOnError(res, "lineage를 해석하지 못했습니다.")
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
  if (!res.ok) throw new Error(`인증 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function fetchAuthSecret(): Promise<string> {
  const res = await authFetch(`${BASE}/auth/secret`)
  if (!res.ok) throw new Error(`시크릿을 불러오지 못했습니다. (HTTP ${res.status})`)
  const data = await res.json()
  return data.client_secret
}

/** 저장하면 현재 로그인이 끊기는 인증 설정 변경 — 사용자 확인 후 confirm=true 로 다시 저장한다. */
export class AuthChangeConfirmError extends Error {}

export async function updateAuthConfig(config: AuthConfig, confirm = false): Promise<void> {
  const res = await authFetch(`${BASE}/auth`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...config, confirm }),
  })
  if (res.status === 409) {
    const body = (await res.json().catch(() => ({}))) as { detail?: { code?: string; message?: string } }
    if (body.detail?.code === "confirm_required") {
      throw new AuthChangeConfirmError(body.detail.message ?? "저장하면 다시 로그인해야 합니다.")
    }
  }
  if (!res.ok) await throwOnError(res, "인증 설정을 수정하지 못했습니다.")
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
  if (!res.ok) await throwOnError(res, "Keycloak을 초기화하지 못했습니다.")
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
  if (!res.ok) throw new Error(`인증 서버 연결을 테스트하지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) throw new Error(`CORS 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateCorsConfig(config: CorsConfig): Promise<void> {
  const res = await authFetch(`${BASE}/cors`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "CORS 설정을 수정하지 못했습니다.")
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
  if (!res.ok) throw new Error(`LLM 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateLLMConfig(config: LLMConfig): Promise<void> {
  const res = await authFetch(`${BASE}/llm`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "LLM 설정을 수정하지 못했습니다.")
}

export async function testLLM(
  config: LLMConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/llm/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`LLM 연결을 테스트하지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) throw new Error(`AI 통계를 불러오지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) await throwOnError(res, "AI 메타데이터를 일괄 생성하지 못했습니다.")
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
  if (!res.ok) throw new Error(`어시스턴트 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateAssistantConfig(config: AssistantConfig): Promise<void> {
  const res = await authFetch(`${BASE}/assistant`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "어시스턴트 설정을 수정하지 못했습니다.")
}

export async function testAssistant(
  config: AssistantConfig,
): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/assistant/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error(`어시스턴트 연결을 테스트하지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// ---------------------------------------------------------------------------
// 수집 서비스 (metadata-sync)
// ---------------------------------------------------------------------------

export type MetadataSyncConfig = { base_url: string; api_token_set: boolean; run_retention_days: number }

export async function fetchMetadataSyncConfig(): Promise<MetadataSyncConfig> {
  const res = await authFetch(`${BASE}/metadata-sync`)
  if (!res.ok) await throwOnError(res, "수집 서비스 설정을 불러오지 못했습니다.")
  return res.json()
}

/** api_token: undefined 면 기존 토큰 유지, "" 면 지움 */
export async function updateMetadataSyncConfig(config: {
  base_url: string
  api_token?: string
  run_retention_days: number
}): Promise<void> {
  const res = await authFetch(`${BASE}/metadata-sync`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "수집 서비스 설정을 저장하지 못했습니다.")
}

export async function testMetadataSync(config: {
  base_url: string
  api_token?: string
}): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/metadata-sync/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "수집 서비스 연결을 테스트하지 못했습니다.")
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
  if (!res.ok) throw new Error(`메일 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateMailConfig(config: MailConfig): Promise<void> {
  const res = await authFetch(`${BASE}/mail`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "메일 설정을 수정하지 못했습니다.")
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
  if (!res.ok) throw new Error(`테스트 메일을 보내지 못했습니다. (HTTP ${res.status})`)
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
  if (!res.ok) throw new Error(`알림 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateNotifyConfig(config: NotifyConfig): Promise<void> {
  const res = await authFetch(`${BASE}/notify`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "알림 설정을 수정하지 못했습니다.")
}

export async function testNotify(req: { text?: string }): Promise<{ success: boolean; message: string }> {
  const res = await authFetch(`${BASE}/notify/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`테스트 알림을 보내지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

// 변경관리(참조자 통지) 설정
export type ChangeMgmtConfig = {
  notify_enabled: boolean
  notify_channel: "email" | "slack" | "mattermost"
}

export async function fetchChangeMgmtConfig(): Promise<ChangeMgmtConfig> {
  const res = await authFetch(`${BASE}/change`)
  if (!res.ok) throw new Error(`변경관리 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateChangeMgmtConfig(config: ChangeMgmtConfig): Promise<void> {
  const res = await authFetch(`${BASE}/change`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "변경관리 설정을 수정하지 못했습니다.")
}


// ---------------------------------------------------------------------------
// 카탈로그 정책 — 일반 사용자의 데이터셋 직접 등록 허용 (기본 꺼짐)
// ---------------------------------------------------------------------------

export type CatalogPolicyConfig = {
  dataset_create_by_users: boolean
}

export async function fetchCatalogPolicyConfig(): Promise<CatalogPolicyConfig> {
  const res = await authFetch(`${BASE}/catalog`)
  if (!res.ok) throw new Error(`데이터셋 등록 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateCatalogPolicyConfig(config: CatalogPolicyConfig): Promise<void> {
  const res = await authFetch(`${BASE}/catalog`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "데이터셋 등록 설정을 저장하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// 사이드바 브랜드 — 제품명 · 회사명 문구와 눌렀을 때 갈 주소(비우면 데이터셋 카드 보기)
// ---------------------------------------------------------------------------

export type BrandingConfig = {
  title: string
  subtitle: string
  link_url: string
}

export const DEFAULT_BRANDING: BrandingConfig = { title: "Argus Catalog", subtitle: "Data Dynamics Inc", link_url: "" }

/** 저장하면 사이드바가 새로 그리도록 알리는 창 이벤트(detail: BrandingConfig) */
export const BRANDING_CHANGED_EVENT = "argus:branding-changed"

export async function fetchBrandingConfig(): Promise<BrandingConfig> {
  const res = await authFetch(`${BASE}/branding`)
  if (!res.ok) throw new Error(`사이드바 표시 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

/** 로그인 전(로그인 화면) — 토큰 없이 읽는 공개 조회(TODO-146) */
export async function fetchPublicBranding(): Promise<BrandingConfig> {
  const res = await fetch(`${BASE}/branding/public`)
  if (!res.ok) throw new Error(`사이드바 표시 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateBrandingConfig(config: BrandingConfig): Promise<BrandingConfig> {
  const res = await authFetch(`${BASE}/branding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "사이드바 표시 설정을 저장하지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// API 키 (프로그램용 자격 — 수집·AI·품질·SDK·NiFi 등)
// ---------------------------------------------------------------------------

export type ApiKeyStatus = "active" | "disabled" | "revoked"

export type ApiKeyInfo = {
  id: number
  name: string
  description: string | null
  key_prefix: string
  role_id: string
  scopes: string[]
  status: ApiKeyStatus
  expires_at: string | null
  last_used_at: string | null
  last_used_ip: string | null
  created_by: string | null
  created_at: string | null
  rotated_at: string | null
  revoked_at: string | null
  revoked_by: string | null
}

export type ApiKeyCreated = ApiKeyInfo & { api_key: string }
export type ApiKeyScope = { code: string; label: string; description: string }

const KEYS = "/api/v1/api-keys"

export async function fetchApiKeys(includeRevoked: boolean): Promise<ApiKeyInfo[]> {
  const res = await authFetch(`${KEYS}?include_revoked=${includeRevoked}`)
  if (!res.ok) await throwOnError(res, "API 키 목록을 불러오지 못했습니다.")
  return res.json()
}

export async function fetchApiKeyScopes(): Promise<ApiKeyScope[]> {
  const res = await authFetch(`${KEYS}/scopes`)
  if (!res.ok) await throwOnError(res, "API 키 범위를 불러오지 못했습니다.")
  return res.json()
}

export async function createApiKey(body: {
  name: string
  description?: string
  role: string
  scopes: string[]
  expires_in_days?: number | null
}): Promise<ApiKeyCreated> {
  const res = await authFetch(KEYS, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "API 키를 발급하지 못했습니다.")
  return res.json()
}

export async function updateApiKey(
  id: number,
  body: { name?: string; description?: string; role?: string; scopes?: string[]; enabled?: boolean },
): Promise<ApiKeyInfo> {
  const res = await authFetch(`${KEYS}/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, "API 키를 수정하지 못했습니다.")
  return res.json()
}

export async function rotateApiKey(id: number): Promise<ApiKeyCreated> {
  const res = await authFetch(`${KEYS}/${id}/rotate`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "API 키를 재발급하지 못했습니다.")
  return res.json()
}

export async function revokeApiKey(id: number): Promise<ApiKeyInfo> {
  const res = await authFetch(`${KEYS}/${id}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "API 키를 폐기하지 못했습니다.")
  return res.json()
}


// ---------------------------------------------------------------------------
// AI-Ready 기관 기본값 — 데이터셋에 값이 없을 때 쓰는 제공기관·언어·라이선스·연락처 (TODO-096)
// ---------------------------------------------------------------------------

export type AiReadyConfig = {
  publisher: string
  language: string
  license: string
  license_url: string
  contact_email: string
  /** AI-Ready 점검의 최소 요건 — 전체 결측률 상한(%, 미만이어야 충족) */
  max_missing_pct: number
  /** AI-Ready 점검의 최소 요건 — 갱신 주기를 몇 번까지 놓쳐도 되는지 */
  max_missed_cycles: number
  /** 오류 신고·개선 요청 처리 기한(일) — 접수 때 기한으로 저장 */
  report_due_days: number
  /** 관련법령 목록 — 한 줄에 하나(`법령명 | 해당하는 경우`). AI 초안은 이 안에서만 관련법령을 고른다 */
  laws: string
  /** 외부 공개 주소 방식 — DCAT · Croissant · 데이터 카드에 넣는 이 서버 주소 */
  public_mode: PublicUrlMode
  /** FQDN · 바인딩 IP 방식의 스킴 */
  public_scheme: "http" | "https"
  /** FQDN · 바인딩 IP 방식의 포트 — 비우면(null) 백엔드 포트 */
  public_port: number | null
  /** 직접 입력 방식의 URL */
  public_url: string
}

export type PublicUrlMode = "REQUEST" | "FQDN" | "IP" | "CUSTOM"

/** 방식별로 실제 어떤 주소가 되는지(`current` 는 저장된 설정의 주소) */
export type PublicUrlPreview = Record<Exclude<PublicUrlMode, "CUSTOM">, string> & { current: string }

export async function fetchPublicUrlPreview(scheme: string, port: number | null): Promise<PublicUrlPreview> {
  const q = new URLSearchParams({ scheme })
  if (port) q.set("port", String(port))
  const res = await authFetch(`${BASE}/ai-ready/public-url?${q}`)
  if (!res.ok) await throwOnError(res, "공개 주소를 확인하지 못했습니다.")
  return res.json()
}

export async function fetchAiReadyConfig(): Promise<AiReadyConfig> {
  const res = await authFetch(`${BASE}/ai-ready`)
  if (!res.ok) throw new Error(`AI-Ready 설정을 불러오지 못했습니다. (HTTP ${res.status})`)
  return res.json()
}

export async function updateAiReadyConfig(config: AiReadyConfig): Promise<void> {
  const res = await authFetch(`${BASE}/ai-ready`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) await throwOnError(res, "AI-Ready 설정을 저장하지 못했습니다.")
}
