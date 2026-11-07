/**
 * MCP Server 카탈로그 API client.
 * 백엔드 `/api/v1/mcp-servers` (서버 정의) · `/api/v1/mcp-deployments` (배포 인스턴스) 와 통신한다.
 */

import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import type {
  McpAuditEvent,
  McpAuditIngestResult,
  McpCollectResult,
  McpControlOverview,
  McpCredential,
  McpDeploymentAgent,
  McpDeploymentDetail,
  McpDeploymentPolicy,
  McpDeploymentSummary,
  McpEffectiveState,
  McpFinding,
  McpImportResult,
  McpIngestToken,
  McpPolicyBundle,
  McpPolicyHistory,
  McpPolicyUpdate,
  McpPrompt,
  McpResource,
  McpResourceScope,
  McpScopeInput,
  McpServerDetail,
  McpServerSummary,
  McpServerVersion,
  McpServerVersionDetail,
  McpSimulateLayer,
  McpSimulateResult,
  McpStats,
  McpStatusHistory,
  McpSyncLog,
  McpTool,
  McpUnregistered,
  Paginated,
} from "./data/schema"

const SERVERS = "/api/v1/mcp-servers"
const DEPLOYMENTS = "/api/v1/mcp-deployments"

const enc = encodeURIComponent

async function getJson<T>(url: string, fallback: string): Promise<T> {
  const res = await authFetch(url)
  if (!res.ok) await throwOnError(res, fallback)
  return res.json()
}

async function sendJson<T>(url: string, method: string, body: unknown, fallback: string): Promise<T> {
  const res = await authFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) await throwOnError(res, fallback)
  return res.json()
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v))
  }
  return q.toString()
}

// ---------------------------------------------------------------------------
// 서버 정의
// ---------------------------------------------------------------------------

export type ServerListParams = {
  search?: string
  status?: string
  provider_type?: string
  framework?: string
  page?: number
  pageSize?: number
}

export function fetchMcpServers(p: ServerListParams = {}): Promise<Paginated<McpServerSummary>> {
  return getJson(
    `${SERVERS}?${qs({ search: p.search, status: p.status, provider_type: p.provider_type,
      framework: p.framework, page: p.page ?? 1, page_size: p.pageSize ?? 20 })}`,
    "MCP 서버 목록을 불러오지 못했습니다.",
  )
}

export function fetchMcpStats(): Promise<McpStats> {
  return getJson(`${SERVERS}/stats`, "MCP 통계를 불러오지 못했습니다.")
}

export function fetchMcpServer(name: string): Promise<McpServerDetail> {
  return getJson(`${SERVERS}/${enc(name)}`, "MCP 서버 상세를 불러오지 못했습니다.")
}

export type McpServerPayload = Partial<{
  name: string
  display_name: string | null
  description: string | null
  status: string
  version: string
  owner_email: string | null
  department: string | null
  category: string | null
  tags: string[] | null
  provider_type: string
  vendor: string | null
  framework: string
  target_platform: string | null
  registry_name: string | null
  title: string | null
  website_url: string | null
  repository_url: string | null
  license: string | null
  supported_transports: string[] | null
  status_note: string | null
}>

export function createMcpServer(payload: McpServerPayload): Promise<McpServerSummary> {
  return sendJson(SERVERS, "POST", payload, "MCP 서버를 등록하지 못했습니다.")
}

export function updateMcpServer(name: string, payload: McpServerPayload): Promise<McpServerSummary> {
  return sendJson(`${SERVERS}/${enc(name)}`, "PATCH", payload, "MCP 서버를 수정하지 못했습니다.")
}

export async function retireMcpServer(name: string): Promise<void> {
  const res = await authFetch(`${SERVERS}/${enc(name)}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "MCP 서버를 폐기하지 못했습니다.")
}

export function fetchMcpServerStatusHistory(name: string): Promise<McpStatusHistory[]> {
  return getJson(`${SERVERS}/${enc(name)}/status-history`, "상태 이력을 불러오지 못했습니다.")
}

export function fetchMcpServerJson(name: string): Promise<Record<string, unknown>> {
  return getJson(`${SERVERS}/${enc(name)}/server-json`, "server.json을 내보내지 못했습니다.")
}

export function addMcpTool(
  name: string,
  payload: { name: string; description?: string; group_key?: string; risk?: string; requires_approval?: boolean },
): Promise<McpTool> {
  return sendJson(`${SERVERS}/${enc(name)}/tools`, "POST", payload, "도구를 등록하지 못했습니다.")
}

export function updateMcpTool(
  name: string,
  toolId: number,
  payload: Partial<{
    group_key: string | null
    risk: string | null
    risk_confirmed: boolean
    requires_approval: boolean
    resource_types: string[] | null
    notes: string | null
  }>,
): Promise<McpTool> {
  return sendJson(`${SERVERS}/${enc(name)}/tools/${toolId}`, "PATCH", payload, "도구를 수정하지 못했습니다.")
}

export async function deleteMcpTool(name: string, toolId: number): Promise<void> {
  const res = await authFetch(`${SERVERS}/${enc(name)}/tools/${toolId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "도구를 삭제하지 못했습니다.")
}

export function addMcpResource(
  name: string,
  payload: { uri: string; is_template?: boolean; name?: string; description?: string; mime_type?: string },
): Promise<McpResource> {
  return sendJson(`${SERVERS}/${enc(name)}/resources`, "POST", payload, "리소스를 등록하지 못했습니다.")
}

export async function deleteMcpResource(name: string, resourceId: number): Promise<void> {
  const res = await authFetch(`${SERVERS}/${enc(name)}/resources/${resourceId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "리소스를 삭제하지 못했습니다.")
}

export async function deleteMcpPrompt(name: string, promptId: number): Promise<void> {
  const res = await authFetch(`${SERVERS}/${enc(name)}/prompts/${promptId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "프롬프트를 삭제하지 못했습니다.")
}

export function addMcpPrompt(
  name: string,
  payload: { name: string; description?: string },
): Promise<McpPrompt> {
  return sendJson(`${SERVERS}/${enc(name)}/prompts`, "POST", payload, "프롬프트를 등록하지 못했습니다.")
}

export function importMcpDocuments(payload: {
  format: "server_json" | "mcp_json"
  content: unknown
  dry_run: boolean
  environment?: string
}): Promise<McpImportResult> {
  return sendJson(`${SERVERS}/import`, "POST", payload, "MCP 문서를 가져오지 못했습니다.")
}

export function seedForgeServers(): Promise<{ servers: McpImportResult["items"] }> {
  return sendJson(`${SERVERS}/seed-forge`, "POST", undefined, "Forge 서버를 등록하지 못했습니다.")
}

export function fetchUnregisteredMcp(): Promise<McpUnregistered[]> {
  return getJson(`${SERVERS}/unregistered`, "미등록 MCP 목록을 불러오지 못했습니다.")
}

// ---------------------------------------------------------------------------
// 배포
// ---------------------------------------------------------------------------

export type DeploymentListParams = {
  search?: string
  server?: string
  environment?: string
  status?: string
  health_status?: string
  mutating_enabled?: boolean
  page?: number
  pageSize?: number
}

export function fetchMcpDeployments(p: DeploymentListParams = {}): Promise<Paginated<McpDeploymentSummary>> {
  return getJson(
    `${DEPLOYMENTS}?${qs({ search: p.search, server: p.server, environment: p.environment,
      status: p.status, health_status: p.health_status, mutating_enabled: p.mutating_enabled,
      page: p.page ?? 1, page_size: p.pageSize ?? 20 })}`,
    "MCP 배포 목록을 불러오지 못했습니다.",
  )
}

export function fetchMcpDeployment(name: string): Promise<McpDeploymentDetail> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}`, "MCP 배포 상세를 불러오지 못했습니다.")
}

export type McpDeploymentPayload = Partial<{
  name: string
  server_name: string
  display_name: string | null
  description: string | null
  environment: string
  status: string
  owner_email: string | null
  department: string | null
  tags: string[] | null
  transport: string
  endpoint: string | null
  rest_api_base: string | null
  auth_method: string
  deployment_scope: string
  host: string | null
  target_endpoint: string | null
  service_principal: string | null
  policy_mode: string
  collect_interval_minutes: number | null
  audit_retention_days: number
  status_note: string | null
}>

export function createMcpDeployment(payload: McpDeploymentPayload): Promise<McpDeploymentSummary> {
  return sendJson(DEPLOYMENTS, "POST", payload, "MCP 배포를 등록하지 못했습니다.")
}

export function updateMcpDeployment(name: string, payload: McpDeploymentPayload): Promise<McpDeploymentSummary> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}`, "PATCH", payload, "MCP 배포를 수정하지 못했습니다.")
}

export async function retireMcpDeployment(name: string): Promise<void> {
  const res = await authFetch(`${DEPLOYMENTS}/${enc(name)}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "MCP 배포를 폐기하지 못했습니다.")
}

export function fetchMcpDeploymentStatusHistory(name: string): Promise<McpStatusHistory[]> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/status-history`, "상태 이력을 불러오지 못했습니다.")
}

export function fetchMcpCredential(name: string): Promise<McpCredential | null> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/credential`, "자격증명을 불러오지 못했습니다.")
}

export function putMcpCredential(
  name: string,
  payload: { kind: string; secret: string; header_name?: string; username?: string },
): Promise<McpCredential> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/credential`, "PUT", payload, "자격증명을 저장하지 못했습니다.")
}

export async function deleteMcpCredential(name: string): Promise<void> {
  const res = await authFetch(`${DEPLOYMENTS}/${enc(name)}/credential`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "자격증명을 삭제하지 못했습니다.")
}

export function collectMcpDeployment(
  name: string,
  payload: { source: "auto" | "mcp" | "forge_rest" | "manifest"; dry_run: boolean },
): Promise<McpCollectResult> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/collect`, "POST", payload, "도구를 수집하지 못했습니다.")
}

export function healthCheckMcpDeployment(name: string): Promise<McpDeploymentSummary> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/health-check`, "POST", undefined, "헬스 체크를 실행하지 못했습니다.")
}

export function importMcpConfig(
  name: string,
  payload: { content: string; dry_run: boolean; fill_endpoint: boolean },
): Promise<McpCollectResult> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/config-import`, "POST", payload, "설정을 가져오지 못했습니다.")
}

export function fetchMcpSyncLog(name: string): Promise<McpSyncLog[]> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/sync-log`, "수집 이력을 불러오지 못했습니다.")
}

export function fetchMcpEffective(name: string): Promise<McpEffectiveState> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/effective`, "실효 상태를 불러오지 못했습니다.")
}

export function addMcpScope(
  name: string,
  payload: { resource_type: string; action: string; effect: string; patterns: string[]; description?: string },
): Promise<McpResourceScope> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/scopes`, "POST", payload, "리소스 범위를 추가하지 못했습니다.")
}

export async function deleteMcpScope(name: string, scopeId: number): Promise<void> {
  const res = await authFetch(`${DEPLOYMENTS}/${enc(name)}/scopes/${scopeId}`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "리소스 범위를 삭제하지 못했습니다.")
}

export function simulateMcpAccess(
  name: string,
  payload: {
    resource_type: string
    resource: string
    action: string
    layer?: McpSimulateLayer
    agent_link_id?: number | null
  },
): Promise<McpSimulateResult> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/simulate`, "POST", payload, "접근 시뮬레이션을 실행하지 못했습니다.")
}

export function fetchMcpDeploymentAgents(name: string): Promise<McpDeploymentAgent[]> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/agents`, "사용 에이전트를 불러오지 못했습니다.")
}

/** 에이전트별 리소스 범위 저장 — 목록 전체 교체 (승인 정책과 같은 권한). */
export function saveMcpAgentScopes(name: string, linkId: number, scopes: McpScopeInput[]): Promise<McpResourceScope[]> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/agents/${linkId}/scopes`, "PUT", { scopes }, "에이전트 리소스 범위를 저장하지 못했습니다.")
}

export function linkUnregisteredMcp(name: string, linkIds: number[]): Promise<{ linked: number }> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/link-unregistered`, "POST", { link_ids: linkIds }, "미등록 MCP를 연결하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Phase 2 — manifest · 버전
// ---------------------------------------------------------------------------

export function importServerManifest(
  name: string, payload: { content: unknown; dry_run: boolean },
): Promise<McpCollectResult> {
  return sendJson(`${SERVERS}/${enc(name)}/manifest-import`, "POST", payload, "manifest를 가져오지 못했습니다.")
}

export function importDeploymentManifest(
  name: string, payload: { content: unknown; dry_run: boolean },
): Promise<McpCollectResult> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/manifest-import`, "POST", payload, "manifest를 가져오지 못했습니다.")
}

export function fetchMcpServerVersions(name: string): Promise<McpServerVersion[]> {
  return getJson(`${SERVERS}/${enc(name)}/versions`, "버전 이력을 불러오지 못했습니다.")
}

export function fetchMcpServerVersion(name: string, id: number): Promise<McpServerVersionDetail> {
  return getJson(`${SERVERS}/${enc(name)}/versions/${id}`, "버전을 불러오지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Phase 2 — 승인 정책 · finding
// ---------------------------------------------------------------------------

export function fetchMcpPolicy(name: string): Promise<McpDeploymentPolicy> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/policy`, "승인 정책을 불러오지 못했습니다.")
}

export function saveMcpPolicy(name: string, payload: McpPolicyUpdate): Promise<McpDeploymentPolicy> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/policy`, "PUT", payload, "승인 정책을 저장하지 못했습니다.")
}

export function adoptMcpObserved(
  name: string,
  payload: { groups: boolean; scopes: boolean; limits: boolean; version: boolean; note?: string | null },
): Promise<McpDeploymentPolicy> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/policy/adopt-observed`, "POST", payload, "관측값을 채택하지 못했습니다.")
}

/** 정책 번들 미리보기 (소유자·관리자 — 서버 pull 기록은 남기지 않음) */
export function fetchMcpPolicyBundle(name: string): Promise<McpPolicyBundle> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/policy-bundle`, "정책 번들을 불러오지 못했습니다.")
}

export function fetchMcpPolicyHistory(name: string): Promise<McpPolicyHistory[]> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/policy/history`, "정책 이력을 불러오지 못했습니다.")
}

export type FindingListParams = {
  status?: string
  severity?: string
  kind?: string
  page?: number
  pageSize?: number
}

export function fetchMcpFindings(name: string, p: FindingListParams = {}): Promise<Paginated<McpFinding>> {
  return getJson(
    `${DEPLOYMENTS}/${enc(name)}/findings?${qs({ status: p.status, severity: p.severity, kind: p.kind,
      page: p.page ?? 1, page_size: p.pageSize ?? 100 })}`,
    "finding 목록을 불러오지 못했습니다.",
  )
}

export function fetchAllMcpFindings(p: FindingListParams = {}): Promise<Paginated<McpFinding>> {
  return getJson(
    `${SERVERS}/findings?${qs({ status: p.status ?? "unresolved", severity: p.severity, kind: p.kind,
      page: p.page ?? 1, page_size: p.pageSize ?? 50 })}`,
    "finding 목록을 불러오지 못했습니다.",
  )
}

export function evaluateMcpFindings(name: string): Promise<Paginated<McpFinding>> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/findings/evaluate`, "POST", undefined, "finding을 재계산하지 못했습니다.")
}

export function updateMcpFinding(
  name: string, id: number, payload: { status: "open" | "acknowledged"; note?: string | null },
): Promise<McpFinding> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/findings/${id}`, "PATCH", payload, "finding을 갱신하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Phase 2 — 감사 이벤트 · 수집 토큰
// ---------------------------------------------------------------------------

export type AuditListParams = {
  tool?: string
  user?: string
  decision?: string
  ok?: boolean
  resource?: string
  page?: number
  pageSize?: number
}

export function fetchMcpAuditEvents(name: string, p: AuditListParams = {}): Promise<Paginated<McpAuditEvent>> {
  return getJson(
    `${DEPLOYMENTS}/${enc(name)}/audit-events?${qs({ tool: p.tool, user: p.user, decision: p.decision, ok: p.ok,
      resource: p.resource, page: p.page ?? 1, page_size: p.pageSize ?? 50 })}`,
    "감사 이벤트를 불러오지 못했습니다.",
  )
}

export function uploadMcpAuditEvents(name: string, jsonl: string): Promise<McpAuditIngestResult> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/audit-events`, "POST", { jsonl }, "감사 이벤트를 업로드하지 못했습니다.")
}

export function fetchMcpIngestToken(name: string): Promise<McpIngestToken> {
  return getJson(`${DEPLOYMENTS}/${enc(name)}/ingest-token`, "수집 토큰을 불러오지 못했습니다.")
}

export function issueMcpIngestToken(name: string): Promise<McpIngestToken> {
  return sendJson(`${DEPLOYMENTS}/${enc(name)}/ingest-token`, "POST", undefined, "수집 토큰을 발급하지 못했습니다.")
}

export async function revokeMcpIngestToken(name: string): Promise<void> {
  const res = await authFetch(`${DEPLOYMENTS}/${enc(name)}/ingest-token`, { method: "DELETE" })
  if (!res.ok) await throwOnError(res, "수집 토큰을 폐기하지 못했습니다.")
}

// ---------------------------------------------------------------------------
// Phase 2 — 통제 현황
// ---------------------------------------------------------------------------

export function fetchMcpControlOverview(): Promise<McpControlOverview> {
  return getJson(`${SERVERS}/control-overview`, "통제 현황을 불러오지 못했습니다.")
}
