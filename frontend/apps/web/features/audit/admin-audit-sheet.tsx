"use client"

/**
 * 관리 감사 로그 — 사용자 관리·설정·권한 관리 화면의 "변경 이력" 버튼과 패널, 관리 › 감사 로그 화면(TODO-152)의 목록.
 *
 * GET /api/v1/admin/audit?category=… (관리자 또는 audit.view) 를 최신순으로 보여 준다. 행을 누르면 바뀐
 * 항목(이전 → 이후)을 펼친다. 비밀값·전화번호는 서버가 값 없이 "(변경됨)"만 기록한다.
 * MCP 감사 이벤트(호출 인자 포함)는 이 로그가 아니라 MCP 배포 화면에서 배포 소유자 · 관리자만 본다(DEF-124).
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState } from "react"
import { ChevronDown, ChevronRight, History, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@workspace/ui/components/sheet"
import { authFetch } from "@/features/auth/auth-fetch"
import { errorMessage } from "@/lib/notify"
import { usePermissions } from "@/features/permissions/use-permissions"
import { SearchInput } from "@/components/search-input"

export type AuditCategory = "users" | "settings" | "permissions" | "operations" | "catalogs"

type AuditItem = {
  id: number
  created_at: string
  actor: string | null
  category: AuditCategory
  action: string
  target: string | null
  summary: string | null
  detail: unknown
  client_ip: string | null
}

const PAGE_SIZE = 20

export const CATEGORY_LABEL: Record<AuditCategory, string> = {
  users: "사용자 관리",
  settings: "설정",
  permissions: "권한 관리",
  operations: "운영 작업",
  catalogs: "카탈로그",
}

const ACTION_LABEL: Record<string, string> = {
  "user.create": "사용자 생성",
  "user.modify": "정보 수정",
  "user.role": "역할 변경",
  "user.password_reset": "비밀번호 재설정",
  "user.password_change": "본인 비밀번호 변경",
  "user.activate": "활성화",
  "user.deactivate": "비활성화",
  "user.delete": "삭제",
  "user.unlock": "잠금 해제",
  "permissions.menus": "메뉴 권한",
  "permissions.features": "기능 권한",
  "permissions.menu-views": "메뉴 보기 전용",
  "permission.denied": "권한 거부",
  "role.create": "역할 추가",
  "role.update": "역할 변경",
  "role.delete": "역할 삭제",
  "alerts.purge": "닫힌 알림 정리",
  "apikey.create": "API 키 발급",
  "apikey.modify": "API 키 수정",
  "apikey.rotate": "API 키 재발급",
  "apikey.revoke": "API 키 폐기",
  "settings.keycloak_initialize": "Keycloak 초기화",
  "settings.object_storage_initialize": "버킷 초기화",
  "settings.cache": "캐시",
  "catalog.create": "카탈로그 등록",
  "catalog.modify": "카탈로그 수정",
  "catalog.delete": "카탈로그 삭제",
  "catalog.connect": "엔진 연결",
  "catalog.disconnect": "엔진 연결 해제",
  "source-map.create": "원천 매핑",
  "source-map.modify": "원천 매핑 변경",
  "source-map.delete": "원천 매핑 해제",
  "source-map.import-schema": "원천 스키마 가져오기",
}

// 설정 범주(update_config 의 category) → 설정 화면 탭 이름
const SETTINGS_LABEL: Record<string, string> = {
  auth: "인증",
  cors: "CORS",
  object_storage: "오브젝트 스토리지",
  embedding: "임베딩",
  llm: "LLM / AI",
  assistant: "AI 어시스턴트",
  mail: "이메일",
  notify: "알림",
  change: "변경관리",
}

export function actionLabel(action: string): string {
  if (ACTION_LABEL[action]) return ACTION_LABEL[action]
  if (action.startsWith("settings.")) {
    const cat = action.slice("settings.".length)
    return `${SETTINGS_LABEL[cat] ?? cat} 설정`
  }
  return action
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "(없음)"
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(없음)"
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

/** detail 을 "항목: 이전 → 이후" 줄들로. changes 가 없으면 키·값 나열. */
export function detailLines(detail: unknown): string[] {
  if (!detail || typeof detail !== "object") return detail ? [String(detail)] : []
  const d = detail as Record<string, unknown>
  const changes = d.changes as Record<string, { old: unknown; new: unknown }> | undefined
  if (changes && typeof changes === "object") {
    return Object.entries(changes).map(([k, c]) => `${k}: ${fmtValue(c?.old)} → ${fmtValue(c?.new)}`)
  }
  if ("old" in d && "new" in d) return [`${fmtValue(d.old)} → ${fmtValue(d.new)}`]
  return Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}: ${fmtValue(v)}`)
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function AdminAuditButton({ category }: { category: AuditCategory }) {
  const [open, setOpen] = useState(false)
  // 감사 로그는 관리자, 또는 audit.view(명시 허용형)를 위임받은 역할만(서버와 같은 기준, TODO-123)
  const canView = usePermissions().canUseFeature("audit.view")
  if (!canView) return null
  return (
    <>
      <Button variant="outline" size="sm" className="h-8" onClick={() => setOpen(true)}>
        <History className="mr-1 size-4" />
        변경 이력
      </Button>
      <AdminAuditSheet category={category} open={open} onOpenChange={setOpen} />
    </>
  )
}

export function AdminAuditSheet({
  category,
  open,
  onOpenChange,
}: {
  category: AuditCategory
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full data-[side=right]:sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>{CATEGORY_LABEL[category]} 변경 이력</SheetTitle>
          <SheetDescription>
            누가 언제 무엇을 바꿨는지 최신순으로 보여 줍니다. 행을 누르면 바뀐 항목을 펼칩니다.
            비밀값과 전화번호는 값 없이 &ldquo;(변경됨)&rdquo;으로만 남습니다.
          </SheetDescription>
        </SheetHeader>
        <AuditLogList category={category} active={open} className="px-4" />
      </SheetContent>
    </Sheet>
  )
}

const ALL = "all"

/** 날짜(YYYY-MM-DD, 브라우저 시간대) → 그날 0시의 ISO 시각. days 만큼 더한다(끝 날짜는 다음 날 0시 미만). */
export function dayStartIso(day: string, days = 0): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!m) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + days)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/**
 * 감사 로그 목록 — 검색 · 페이지 · 펼치기. ``category`` 를 주면 그 범주로 고정하고(변경 이력 패널),
 * ``filters`` 면 범주 · 작업자 · 기간을 화면에서 고른다(관리 › 감사 로그).
 */
export function AuditLogList({
  category,
  active = true,
  filters = false,
  className = "",
}: {
  category?: AuditCategory
  active?: boolean
  filters?: boolean
  className?: string
}) {
  const [items, setItems] = useState<AuditItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [q, setQ] = useState("")
  const [query, setQuery] = useState("")
  const [cat, setCat] = useState<string>(category ?? ALL)
  const [actor, setActor] = useState("")
  const [actorQuery, setActorQuery] = useState("")
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")
  const [expanded, setExpanded] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
      if (cat !== ALL) params.set("category", cat)
      if (query) params.set("q", query)
      if (actorQuery) params.set("actor", actorQuery)
      const from = since ? dayStartIso(since) : null
      const to = until ? dayStartIso(until, 1) : null
      if (from) params.set("since", from)
      if (to) params.set("until", to)
      const res = await authFetch(`/api/v1/admin/audit?${params}`)
      if (!res.ok) throw new Error(`감사 로그를 불러오지 못했습니다: ${res.status}`)
      const body = (await res.json()) as { items: AuditItem[]; total: number }
      setItems(body.items)
      setTotal(body.total)
    } catch (e) {
      setError(errorMessage(e) ?? "감사 로그를 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [cat, page, query, actorQuery, since, until])

  useEffect(() => {
    if (active) load()
  }, [active, load])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const showCategory = !category

  return (
    <div className={`flex min-h-0 flex-1 flex-col gap-2 ${className}`}>
      {filters && (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={cat} onValueChange={(v) => { setCat(v); setPage(1) }}>
            <SelectTrigger size="sm" className="h-8 w-[140px] text-sm" aria-label="범주">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>전체 범주</SelectItem>
              {(Object.keys(CATEGORY_LABEL) as AuditCategory[]).map((c) => (
                <SelectItem key={c} value={c}>{CATEGORY_LABEL[c]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            className="h-8 w-[140px] text-sm"
            placeholder="작업자 아이디"
            aria-label="작업자 아이디"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { setPage(1); setActorQuery(actor.trim()) } }}
            onBlur={() => { if (actor.trim() !== actorQuery) { setPage(1); setActorQuery(actor.trim()) } }}
          />
          <Input type="date" className="h-8 w-[150px] text-sm" aria-label="시작 날짜" value={since}
            onChange={(e) => { setSince(e.target.value); setPage(1) }} />
          <span className="text-sm text-muted-foreground">~</span>
          <Input type="date" className="h-8 w-[150px] text-sm" aria-label="끝 날짜" value={until}
            onChange={(e) => { setUntil(e.target.value); setPage(1) }} />
        </div>
      )}
      <div className="flex items-center gap-2">
        <SearchInput
          size="sm"
          className="flex-1"
          value={q}
          onValueChange={setQ}
          onSearch={() => {
            setPage(1)
            setQuery(q.trim())
          }}
          onClear={() => {
            setQ("")
            setPage(1)
            setQuery("")
          }}
          placeholder="대상·요약·작업자 검색…"
        />
        <Tip label="새로 고침"><Button variant="ghost" size="sm" className="h-8" onClick={() => load()} aria-label="새로 고침">
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </Button></Tip>
      </div>
      <div className="flex-1 overflow-auto">
        {error && <p className="py-4 text-sm text-destructive">{error}</p>}
        {!error && !loading && items.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">기록이 없습니다.</p>
        )}
        <ul className="divide-y">
          {items.map((it) => {
            const lines = detailLines(it.detail)
            const isOpen = expanded === it.id
            return (
              <li key={it.id} className="py-2 text-sm">
                <button
                  type="button"
                  className="flex w-full items-start gap-2 text-left"
                  onClick={() => setExpanded(isOpen ? null : it.id)}
                  disabled={lines.length === 0}
                >
                  <span className="mt-0.5 w-4 shrink-0">
                    {lines.length > 0 && (isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
                  </span>
                  <span className="w-40 shrink-0 whitespace-nowrap tabular-nums">{fmtTime(it.created_at)}</span>
                  {showCategory && (
                    <span className="w-24 shrink-0 text-muted-foreground">{CATEGORY_LABEL[it.category] ?? it.category}</span>
                  )}
                  <span className="w-32 shrink-0 font-medium">{actionLabel(it.action)}</span>
                  <span className="min-w-0 flex-1 break-words">{it.summary || it.target || "-"}</span>
                  <span className="w-24 shrink-0 truncate text-right" title={it.client_ip ?? undefined}>
                    {it.actor ?? "-"}
                  </span>
                </button>
                {isOpen && (
                  <ul className="mt-1 ml-6 space-y-0.5 rounded bg-muted/50 px-3 py-2 font-mono text-sm">
                    {lines.map((l, i) => (
                      <li key={i} className="break-all">{l}</li>
                    ))}
                    {it.client_ip && <li >IP: {it.client_ip}</li>}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      </div>
      <div className="flex items-center justify-between border-t py-3 text-sm">
        <span>총 {total}건</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            이전
          </Button>
          <span className="tabular-nums">{page} / {pages}</span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
            다음
          </Button>
        </div>
      </div>
    </div>
  )
}
