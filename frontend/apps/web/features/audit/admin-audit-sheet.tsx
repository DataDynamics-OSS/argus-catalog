"use client"

/**
 * 관리 감사 로그 — 사용자 관리·설정·권한 관리 화면의 "변경 이력" 버튼과 패널.
 *
 * GET /api/v1/admin/audit?category=… (관리자 전용) 를 최신순으로 보여 준다. 행을 누르면 바뀐
 * 항목(이전 → 이후)을 펼친다. 비밀값·전화번호는 서버가 값 없이 "(변경됨)"만 기록한다.
 */

import { useCallback, useEffect, useState } from "react"
import { ChevronDown, ChevronRight, History, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@workspace/ui/components/sheet"
import { authFetch } from "@/features/auth/auth-fetch"
import { errorMessage } from "@/lib/notify"

export type AuditCategory = "users" | "settings" | "permissions"

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

const CATEGORY_LABEL: Record<AuditCategory, string> = {
  users: "사용자 관리",
  settings: "설정",
  permissions: "권한 관리",
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
  "settings.keycloak_initialize": "Keycloak 초기화",
  "settings.object_storage_initialize": "버킷 초기화",
  "settings.cache": "캐시",
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
  const [items, setItems] = useState<AuditItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [q, setQ] = useState("")
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ category, page: String(page), page_size: String(PAGE_SIZE) })
      if (query) params.set("q", query)
      const res = await authFetch(`/api/v1/admin/audit?${params}`)
      if (!res.ok) throw new Error(`변경 이력을 불러오지 못했습니다: ${res.status}`)
      const body = (await res.json()) as { items: AuditItem[]; total: number }
      setItems(body.items)
      setTotal(body.total)
    } catch (e) {
      setError(errorMessage(e) ?? "변경 이력을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [category, page, query])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

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
        <div className="flex items-center gap-2 px-4">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setPage(1)
                setQuery(q.trim())
              }
            }}
            placeholder="대상·요약·작업자 검색 (Enter)"
            className="h-8"
          />
          <Button variant="ghost" size="sm" className="h-8" onClick={() => load()} aria-label="새로 고침">
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
        <div className="flex-1 overflow-auto px-4">
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
                    <span className="mt-0.5 w-4 shrink-0 text-muted-foreground">
                      {lines.length > 0 && (isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
                    </span>
                    <span className="w-40 shrink-0 whitespace-nowrap tabular-nums text-muted-foreground">{fmtTime(it.created_at)}</span>
                    <span className="w-32 shrink-0 font-medium">{actionLabel(it.action)}</span>
                    <span className="min-w-0 flex-1 break-words">{it.summary || it.target || "-"}</span>
                    <span className="w-24 shrink-0 truncate text-right text-muted-foreground" title={it.client_ip ?? undefined}>
                      {it.actor ?? "-"}
                    </span>
                  </button>
                  {isOpen && (
                    <ul className="mt-1 ml-6 space-y-0.5 rounded bg-muted/50 px-3 py-2 font-mono text-xs">
                      {lines.map((l, i) => (
                        <li key={i} className="break-all">{l}</li>
                      ))}
                      {it.client_ip && <li className="text-muted-foreground">IP: {it.client_ip}</li>}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
        <div className="flex items-center justify-between border-t px-4 py-3 text-sm text-muted-foreground">
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
      </SheetContent>
    </Sheet>
  )
}
