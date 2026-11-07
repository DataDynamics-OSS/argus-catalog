"use client"

import { Fragment, type ReactNode, useEffect, useState } from "react"
import Link from "next/link"
import { ChevronDown, ChevronRight, ChevronUp, Info, Pencil, Plus, Star, Trash2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import {
  type ApiCredential, type ApiDetail, type ApiEndpoint, addApiFavorite, deleteEndpoint, fetchApiFavorites, removeApiFavorite,
} from "../api"
import { JsonBlock, MethodBadge, TabEmpty, paramDataType } from "./apis-detail-shared"
import { EndpointEditDialog, protocolOps } from "./endpoint-edit-dialog"
import { EndpointTryIt, GraphQLTryIt, SoapTryIt } from "./endpoint-try-it"

// 파라미터 표시 — 이름 / 위치 / 데이터 타입 / 필수 / 설명 표(읽기 전용).
function ParametersView({ parameters }: { parameters: Record<string, unknown>[] | null }) {
  if (!parameters || parameters.length === 0) return <span className="text-sm text-black">없음</span>
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40"><tr>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">파라미터</th>
          <th className="w-[80px] px-3 py-2 text-left font-medium text-muted-foreground">위치</th>
          <th className="w-[120px] px-3 py-2 text-left font-medium text-muted-foreground">데이터 타입</th>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">설명</th>
        </tr></thead>
        <tbody>
          {parameters.map((p, i) => {
            const pp = p as { name?: string; in?: string; required?: boolean; description?: string; type?: string; format?: string; schema?: { type?: string; format?: string; items?: { type?: string } } }
            const dataType = paramDataType(pp)
            return (
              <tr key={`${pp.in}:${pp.name}:${i}`} className="border-t align-top">
                <td className="px-3 py-2"><span className="font-mono text-sm text-black">{pp.name}</span>{pp.required && <span className="ml-1 text-red-600" title="필수">*</span>}</td>
                <td className="px-3 py-2"><Badge variant="outline" className="text-sm">{pp.in || "-"}</Badge></td>
                <td className="px-3 py-2 font-mono text-sm text-muted-foreground">{dataType}</td>
                <td className="px-3 py-2 text-sm break-words">{pp.description || "-"}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 응답 표시 — 상태코드 / 설명 / 본문 스키마로 분리(상태코드만/본문만/둘 다 모두 자연스럽게).
function ResponsesView({ responses }: { responses: Record<string, unknown> | null }) {
  if (!responses || typeof responses !== "object" || Object.keys(responses).length === 0) {
    return <span className="text-sm text-black">없음</span>
  }
  const keys = Object.keys(responses)
  // 상태코드(또는 default) 형태가 아니면 원본 JSON 으로 표시(fallback).
  if (!keys.every((k) => /^\d{3}$/.test(k) || k === "default")) {
    return <JsonBlock value={responses} />
  }
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40"><tr>
          <th className="w-[90px] px-3 py-2 text-left font-medium text-muted-foreground">상태코드</th>
          <th className="w-[200px] px-3 py-2 text-left font-medium text-muted-foreground">설명</th>
          <th className="px-3 py-2 text-left font-medium text-muted-foreground">본문 스키마</th>
        </tr></thead>
        <tbody>
          {keys.map((code) => {
            const d = (responses[code] && typeof responses[code] === "object" ? responses[code] : {}) as { description?: string; content?: Record<string, { schema?: unknown }>; schema?: unknown }
            let body: unknown = null
            if (d.content && typeof d.content === "object") {
              const first = Object.values(d.content)[0] as { schema?: unknown } | undefined
              body = first?.schema ?? null
            } else if (d.schema !== undefined) { body = d.schema }
            const color = /^2|^3/.test(code) ? "text-emerald-600" : /^4|^5/.test(code) ? "text-red-600" : "text-foreground"
            return (
              <tr key={code} className="border-t align-top">
                <td className="px-3 py-2"><span className={cn("text-sm font-semibold", color)}>{code}</span></td>
                <td className="w-[200px] px-3 py-2 text-sm break-words">{d.description || "-"}</td>
                <td className="px-3 py-2">{body !== null && body !== undefined ? <JsonBlock value={body} /> : <span className="text-sm text-black">없음</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 엔드포인트 목록 (행 확장 → 파라미터/요청/응답)
// ---------------------------------------------------------------------------
const ENDPOINT_UNTAGGED = "__untagged__"

const isAbsoluteUrl = (u: string) => /^https?:\/\//i.test(u)

/**
 * 호출(Try-it)에 쓸 서버 주소 목록 — 직접 지정한 Base URL 을 먼저, 상대 경로 서버(/v1 등)는
 * 절대 Base URL 기준으로 풀어서 쓰고 풀 수 없으면 뺀다(상대 주소로는 호출할 수 없음).
 */
function callServerOptions(api: ApiDetail): string[] {
  const base = (api.base_url ?? "").trim()
  const absBase = isAbsoluteUrl(base) ? base : null
  const out: string[] = []
  const push = (u: string) => { const v = u.replace(/\/+$/, ""); if (v && !out.includes(v)) out.push(v) }
  if (absBase && api.base_url_overridden === "true") push(absBase)
  for (const s of api.servers) {
    const u = (s.url ?? "").trim()
    if (!u) continue
    if (isAbsoluteUrl(u)) push(u)
    else if (absBase) {
      try { push(new URL(u, absBase.endsWith("/") ? absBase : `${absBase}/`).toString()) } catch { /* 잘못된 주소는 건너뜀 */ }
    }
  }
  if (absBase) push(absBase)
  return out
}

function EndpointRows({ endpoints, open, toggle, keyOf, api, server, credId, favorites, onToggleFav, canEdit, onEdit, onDelete }: {
  endpoints: ApiEndpoint[]
  open: Set<string>
  toggle: (k: string) => void
  keyOf: (e: ApiEndpoint) => string
  api: ApiDetail
  server: string
  credId: string
  favorites: Set<string>
  onToggleFav: (method: string, path: string) => void
  canEdit?: boolean
  onEdit?: (e: ApiEndpoint) => void
  onDelete?: (e: ApiEndpoint) => void
}) {
  const protocol = api.protocol || "REST"
  const canTry = ["REST", "GraphQL", "SOAP"].includes(protocol)
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full text-sm">
        <tbody>
          {endpoints.map((e) => {
            const k = keyOf(e)
            const isOpen = open.has(k)
            return (
              <Fragment key={k}>
                <tr data-ep-id={e.id} className="cursor-pointer scroll-mt-4 border-b hover:bg-muted/40" onClick={() => toggle(k)}>
                  <td className="w-6 py-2 pl-3 align-middle text-muted-foreground">{isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</td>
                  <td className="py-2 pr-3 align-middle">
                    <div className="flex items-center gap-2">
                      <MethodBadge method={e.method} />
                      <span className="text-sm">{e.path}</span>
                      {e.summary && <span className="text-sm text-muted-foreground">— {e.summary}</span>}
                    </div>
                  </td>
                  <td className="py-2 pr-3 align-middle text-right whitespace-nowrap">
                    {canEdit && (
                      <>
                        <button type="button" onClick={(ev) => { ev.stopPropagation(); onEdit?.(e) }} title="수정" aria-label="수정" className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-muted"><Pencil className="h-4 w-4" /></button>
                        <button type="button" onClick={(ev) => { ev.stopPropagation(); onDelete?.(e) }} title="삭제" aria-label="삭제" className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-muted"><Trash2 className="h-4 w-4" /></button>
                      </>
                    )}
                    {(() => {
                      const fav = favorites.has(`${e.method} ${e.path}`)
                      return (
                        <button
                          type="button"
                          onClick={(ev) => { ev.stopPropagation(); onToggleFav(e.method, e.path) }}
                          title={fav ? "즐겨찾기 해제" : "즐겨찾기"}
                          aria-label={fav ? "즐겨찾기 해제" : "즐겨찾기"}
                          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted"
                        >
                          <Star className={cn("h-4 w-4", fav ? "fill-amber-400 text-amber-400" : "text-muted-foreground")} />
                        </button>
                      )
                    })()}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b bg-muted/20">
                    <td />
                    <td colSpan={2} className="px-3 py-3">
                      <Tabs defaultValue="doc">
                        <TabsList>
                          <TabsTrigger value="doc">문서</TabsTrigger>
                          {canTry && <TabsTrigger value="try">호출</TabsTrigger>}
                        </TabsList>
                        <TabsContent value="doc" className="mt-3">
                          <div className="overflow-hidden rounded-md border bg-background">
                            <table className="w-full table-fixed border-collapse text-sm">
                              <colgroup><col className="w-[120px]" /><col /></colgroup>
                              <tbody>
                                {([
                                  { label: "설명", node: <span className="text-sm text-black">{e.description || "없음"}</span> },
                                  ...(e.extra && Object.keys(e.extra).length > 0 ? [{ label: "추가 정보", node: <JsonBlock value={e.extra} /> }] : []),
                                  // 파라미터/인자: REST·GraphQL 만 / 요청 바디·응답(상태코드): REST 만
                                  ...(["REST", "GraphQL"].includes(protocol) ? [{ label: protocol === "GraphQL" ? "인자" : "파라미터", node: <ParametersView parameters={e.parameters} /> }] : []),
                                  ...(protocol === "REST" ? [
                                    { label: "요청 바디", node: <JsonBlock value={e.request_body} /> },
                                    { label: "응답", node: <ResponsesView responses={e.responses} /> },
                                  ] : []),
                                ] as { label: string; node: ReactNode }[]).map((r) => (
                                  <tr key={r.label}>
                                    <th className="border bg-muted/50 px-3 py-2 text-left align-top font-medium text-black">{r.label}</th>
                                    <td className="border px-3 py-2 align-top break-words">{r.node}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </TabsContent>
                        {canTry && (
                          <TabsContent value="try" className="mt-3">
                            {protocol === "GraphQL" ? <GraphQLTryIt api={api} endpoint={e} server={server} credId={credId} />
                              : protocol === "SOAP" ? <SoapTryIt api={api} endpoint={e} server={server} credId={credId} />
                              : <EndpointTryIt api={api} endpoint={e} server={server} credId={credId} />}
                          </TabsContent>
                        )}
                      </Tabs>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 엔드포인트 탭 헤더 — 페이지 설명 + HTTP 메서드 색상 범례 + 모두 접기.
function EndpointsHeader({ hasOpen, onCollapseAll, legendMethods = ["GET", "POST", "PUT", "PATCH", "DELETE"] }: { hasOpen?: boolean; onCollapseAll?: () => void; legendMethods?: string[] }) {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {legendMethods.map((m) => (
            <span key={m} className="inline-flex items-center gap-1.5"><MethodBadge method={m} /></span>
          ))}
        </div>
        <details className="rounded-md border bg-muted/20 p-2">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">범례 도움말 (펼치기)</summary>
          <div className="mt-2 space-y-2 text-xs text-muted-foreground">
            <p>색상 배지는 각 엔드포인트의 <b>HTTP 메서드(REST)</b> 또는 <b>오퍼레이션 유형(GraphQL)</b>을 나타냅니다. 동작의 성격(조회/변경 등)을 색으로 빠르게 구분하기 위한 것입니다.</p>
            <div>
              <p className="font-medium text-foreground">REST 메서드</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>GET</b> — 리소스 조회(읽기, 부수효과 없음)</li>
                <li><b>POST</b> — 리소스 생성·작업 실행</li>
                <li><b>PUT</b> — 리소스 전체 교체(멱등)</li>
                <li><b>PATCH</b> — 리소스 부분 수정</li>
                <li><b>DELETE</b> — 리소스 삭제</li>
                <li><b>HEAD · OPTIONS</b> — 헤더 조회·사전 요청(CORS 등)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">GraphQL 오퍼레이션</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>QUERY</b> — 데이터 조회(읽기)</li>
                <li><b>MUTATION</b> — 데이터 변경(생성·수정·삭제)</li>
                <li><b>SUBSCRIPTION</b> — 실시간 구독(WebSocket 기반, HTTP 콘솔로는 실행되지 않을 수 있음)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">gRPC RPC 유형</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>UNARY</b> — 단일 요청 · 단일 응답</li>
                <li><b>SERVER-STREAMING</b> — 단일 요청 · 응답 스트림(서버→클라이언트)</li>
                <li><b>CLIENT-STREAMING</b> — 요청 스트림 · 단일 응답(클라이언트→서버)</li>
                <li><b>BIDI-STREAMING</b> — 양방향 스트림</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">SOAP</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>OPERATION</b> — WSDL 오퍼레이션(입력/출력 XML 메시지 + SOAPAction)</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-foreground">Webhook / 이벤트</p>
              <ul className="ml-4 list-disc space-y-0.5">
                <li><b>PUBLISH</b> — 이벤트 발행(API 가 구독자에게 전송)</li>
                <li><b>SUBSCRIBE</b> — 이벤트 수신(외부 이벤트 구독)</li>
              </ul>
            </div>
          </div>
        </details>
      </div>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          스펙에서 추출한 엔드포인트(메서드·경로)를 카테고리(태그)별로 묶어 보여주는 화면입니다. 각 행을 펼치면 문서(파라미터·요청 바디·응답 스키마)와 호출(Try-it)을 함께 확인할 수 있습니다. 호출에 쓸 서버·자격증명은 아래에서 한 번만 지정하면 모든 엔드포인트에 공유됩니다.
        </p>
        {onCollapseAll && (
          <Button size="sm" variant="outline" className="shrink-0" disabled={!hasOpen} onClick={onCollapseAll}>
            <ChevronUp className="mr-1 h-4 w-4" /> 모두 접기
          </Button>
        )}
      </div>
    </div>
  )
}

export function EndpointsTab({ api, credentials, endpointFilter, hideHeader, canEdit, onEndpointsChanged, favoriteKeys, onToggleFavorite, focus }: {
  api: ApiDetail; credentials: ApiCredential[]; endpointFilter?: Set<string>; hideHeader?: boolean; canEdit?: boolean; onEndpointsChanged?: () => Promise<void> | void
  /** 즐겨찾기("METHOD path")를 상위가 관리할 때(즐겨찾기 화면) — 주면 자체 조회를 하지 않는다 */
  favoriteKeys?: Set<string>
  /** favoriteKeys 와 함께 — ★ 클릭 처리를 상위에 맡긴다 */
  onToggleFavorite?: (method: string, path: string) => void
  /** 다른 탭(관측)에서 요청한 엔드포인트 — seq 가 바뀔 때마다 그 행을 펼치고 스크롤 */
  focus?: { method: string; path: string; seq: number } | null
}) {
  const askConfirm = useConfirm()
  const protocol = api.protocol || "REST"
  const [editOpen, setEditOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<ApiEndpoint | null>(null)
  const openAdd = () => { setEditTarget(null); setEditOpen(true) }
  const openEdit = (e: ApiEndpoint) => { setEditTarget(e); setEditOpen(true) }
  const onDelete = async (e: ApiEndpoint) => {
    if (!(await askConfirm({
      title: "엔드포인트 삭제",
      description: `${e.method} ${e.path} 엔드포인트를 삭제하시겠습니까?`,
      confirmText: "삭제",
      destructive: true,
    }))) return
    try { await deleteEndpoint(api.name, e.id) }
    catch (err) { notifyError(err, "엔드포인트를 삭제하지 못했습니다."); return }
    toast.success("엔드포인트를 삭제했습니다.")
    try { await onEndpointsChanged?.() }
    catch { toast.warning("삭제했지만 목록을 새로고침하지 못했습니다.") }
  }
  const legendMethods = protocol === "REST" ? ["GET", "POST", "PUT", "PATCH", "DELETE"] : protocolOps(api.protocol).typeOptions
  const manualBar = canEdit ? (
    <div className="flex justify-end">
      <Button size="sm" onClick={openAdd}><Plus className="mr-1 h-4 w-4" /> 엔드포인트 추가</Button>
    </div>
  ) : null
  const editDialog = canEdit ? (
    <EndpointEditDialog apiName={api.name} protocol={api.protocol} endpoint={editTarget} open={editOpen} onOpenChange={setEditOpen} onSaved={async () => { await onEndpointsChanged?.() }} />
  ) : null
  const baseEndpoints = endpointFilter ? api.endpoints.filter((e) => endpointFilter.has(`${e.method} ${e.path}`)) : api.endpoints
  const tagDefs = api.tag_defs ?? []
  const servers = callServerOptions(api)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [serverPick, setServer] = useState(servers[0] ?? "")
  // 서버 목록이 바뀌면(새 스펙 버전 등) 목록에 없는 선택은 첫 항목으로
  const server = servers.length === 0 || servers.includes(serverPick) ? serverPick : servers[0]!
  const [credId, setCredId] = useState<string>("none")
  const [ownFavorites, setFavorites] = useState<Set<string>>(new Set())
  const favorites = favoriteKeys ?? ownFavorites
  // 상세 화면 — 즐겨찾기한 엔드포인트만 보기
  const [favOnly, setFavOnly] = useState(false)
  // 관측 탭 등에서 요청한 엔드포인트 펼치기(렌더 중 반영 — 이펙트에서 동기 setState 하지 않음)
  const [appliedFocus, setAppliedFocus] = useState(0)
  const focusTarget = focus ? api.endpoints.find((e) => e.method === focus.method && e.path === focus.path) : undefined
  if (focus && focus.seq !== appliedFocus) {
    setAppliedFocus(focus.seq)
    if (focusTarget) {
      setFavOnly(false)
      const tags = focusTarget.tags && focusTarget.tags.length > 0 ? focusTarget.tags : [ENDPOINT_UNTAGGED]
      setOpen((s) => new Set([...s, ...tags.map((t) => `${t}#${focusTarget.id}`)]))
    }
  }
  const focusId = focusTarget?.id
  useEffect(() => {
    if (!focus?.seq || focusId == null) return
    // 탭 전환 후 그려진 다음 스크롤
    const t = window.setTimeout(() => {
      document.querySelector(`[data-ep-id="${focusId}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" })
    }, 50)
    return () => window.clearTimeout(t)
  }, [focus?.seq, focusId])
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })

  const favKey = (m: string, p: string) => `${m} ${p}`
  const controlled = favoriteKeys !== undefined
  useEffect(() => {
    if (controlled) return
    fetchApiFavorites(api.name).then((fs) => setFavorites(new Set(fs.map((f) => favKey(f.method, f.path))))).catch(() => {})
  }, [api.name, controlled])
  const toggleFav = async (method: string, path: string) => {
    if (onToggleFavorite) { onToggleFavorite(method, path); return }
    const k = favKey(method, path)
    const has = favorites.has(k)
    try {
      if (has) await removeApiFavorite(api.name, method, path)
      else await addApiFavorite(api.name, method, path)
      setFavorites((s) => { const n = new Set(s); if (has) n.delete(k); else n.add(k); return n })
    } catch (e) { notifyError(e, "즐겨찾기를 변경하지 못했습니다.") }
  }

  if (baseEndpoints.length === 0) {
    return <div className="space-y-4">{!hideHeader && <EndpointsHeader legendMethods={legendMethods} />}{manualBar}<TabEmpty>엔드포인트가 없습니다.{canEdit ? " 엔드포인트 추가 버튼으로 오퍼레이션을 등록하세요." : ""}</TabEmpty>{editDialog}</div>
  }

  // 상세 화면 전용(즐겨찾기 화면은 상위가 거른다) — 즐겨찾기만 보기 · 현재 목록에 없는 즐겨찾기 안내
  const allKeys = new Set(api.endpoints.map((e) => favKey(e.method, e.path)))
  const favCount = api.endpoints.filter((e) => favorites.has(favKey(e.method, e.path))).length
  const missingFavCount = [...favorites].filter((k) => !allKeys.has(k)).length
  const endpoints = !controlled && favOnly ? baseEndpoints.filter((e) => favorites.has(favKey(e.method, e.path))) : baseEndpoints
  const favBar = controlled ? null : (
    <div className="space-y-2">
      <Button
        size="sm"
        variant={favOnly ? "secondary" : "outline"}
        aria-pressed={favOnly}
        disabled={favCount === 0 && !favOnly}
        onClick={() => setFavOnly((v) => !v)}
        title={favCount === 0 ? "즐겨찾기한 엔드포인트가 없습니다(행 오른쪽 ★ 로 추가)" : undefined}
      >
        <Star className={cn("mr-1 h-4 w-4", favOnly && "fill-amber-400 text-amber-400")} /> 즐겨찾기만 보기 ({favCount})
      </Button>
      {missingFavCount > 0 && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Info className="h-3.5 w-3.5 shrink-0" />
          즐겨찾기한 엔드포인트 중 {missingFavCount}개가 현재 목록에 없습니다(스펙 변경·경로 수정 등).
          <Link href="/dashboard/apis/favorites" className="text-primary hover:underline">즐겨찾기 화면에서 정리</Link>
        </p>
      )}
    </div>
  )
  const favEmpty = endpoints.length === 0 ? <TabEmpty>즐겨찾기한 엔드포인트가 없습니다.</TabEmpty> : null

  // 호출(Try-it) 공유 컨트롤 — 서버/자격증명은 API 레벨이라 모든 행이 공유한다.
  const controls = (
    <div className="grid gap-3 rounded-md border bg-muted/20 p-3 sm:grid-cols-2">
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">서버 / Base URL (호출 시 사용)</p>
        {servers.length > 0 ? (
          <Select value={server} onValueChange={setServer}>
            <SelectTrigger className="h-9 text-sm"><SelectValue placeholder="서버 선택" /></SelectTrigger>
            <SelectContent>{servers.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
          </Select>
        ) : (
          <>
            <Input className="h-9 text-sm" placeholder="https://api.example.com" value={server} onChange={(e) => setServer(e.target.value)} />
            <p className="text-[11px] text-muted-foreground">스펙에 절대 서버 주소가 없습니다. 개요 탭에서 Base URL 을 지정하면 여기서 선택할 수 있고 자격증명도 쓸 수 있습니다.</p>
          </>
        )}
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground" title="시크릿 유출을 막기 위해 서버 목록·Base URL 과 같은 주소로 호출할 때만 주입합니다.">
          자격증명 (시크릿은 서버 보관·주입, 이 API 의 서버 주소로만 전송)
        </p>
        <Select value={credId} onValueChange={setCredId}>
          <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none">사용 안 함(수동 헤더)</SelectItem>
            {credentials.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.label} ({c.type})</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
    </div>
  )

  // 카테고리(태그)별 그룹화 — tagDefs 순서를 우선, 그 외 태그·미분류는 뒤에 배치.
  const descOf = new Map(tagDefs.map((t) => [t.name, t.description]))
  const order: string[] = tagDefs.map((t) => t.name)
  const groups = new Map<string, ApiEndpoint[]>()
  const push = (tag: string, e: ApiEndpoint) => {
    if (!groups.has(tag)) { groups.set(tag, []); if (!order.includes(tag)) order.push(tag) }
    groups.get(tag)!.push(e)
  }
  for (const e of endpoints) {
    const tags = e.tags && e.tags.length > 0 ? e.tags : [ENDPOINT_UNTAGGED]
    for (const t of tags) push(t, e)
  }
  const visible = order.filter((t) => (groups.get(t)?.length ?? 0) > 0)

  // 태그가 전혀 없으면(미분류 단일 그룹) 그룹 헤더 없이 단순 목록으로 표시.
  if (visible.length === 1 && visible[0] === ENDPOINT_UNTAGGED) {
    return (
      <div className="space-y-4">
        {!hideHeader && <EndpointsHeader hasOpen={open.size > 0} onCollapseAll={() => setOpen(new Set())} legendMethods={legendMethods} />}
        {controls}
        {favBar}
        {manualBar}
        {favEmpty}
        <EndpointRows endpoints={endpoints} open={open} toggle={toggle} keyOf={(e) => `${ENDPOINT_UNTAGGED}#${e.id}`} api={api} server={server} credId={credId} favorites={favorites} onToggleFav={toggleFav} canEdit={canEdit} onEdit={openEdit} onDelete={onDelete} />
        {editDialog}
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-4">
        {!hideHeader && <EndpointsHeader hasOpen={open.size > 0} onCollapseAll={() => setOpen(new Set())} legendMethods={legendMethods} />}
        {controls}
        {favBar}
        {manualBar}
        {favEmpty}
        {visible.map((tag) => {
          const eps = groups.get(tag) ?? []
          const desc = descOf.get(tag)
          const name = tag === ENDPOINT_UNTAGGED ? "기타" : tag
          return (
            <div key={tag} className="space-y-1.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                {desc ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="cursor-help text-sm font-semibold text-foreground underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">{name}</span>
                    </TooltipTrigger>
                    <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">{desc}</TooltipContent>
                  </Tooltip>
                ) : (
                  <span className="text-sm font-semibold text-foreground">{name}</span>
                )}
                <span className="text-xs text-muted-foreground">{eps.length}개</span>
              </div>
              <EndpointRows endpoints={eps} open={open} toggle={toggle} keyOf={(e) => `${tag}#${e.id}`} api={api} server={server} credId={credId} favorites={favorites} onToggleFav={toggleFav} canEdit={canEdit} onEdit={openEdit} onDelete={onDelete} />
            </div>
          )
        })}
        {editDialog}
      </div>
    </TooltipProvider>
  )
}
