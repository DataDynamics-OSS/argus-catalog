"use client"

import { useEffect, useRef, useState } from "react"
import { Check, ChevronsUpDown, Loader2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Command, CommandGroup, CommandInput, CommandItem, CommandList } from "@workspace/ui/components/command"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import { fetchUsers } from "@/features/users/api"
import type { User } from "@/features/users/data/schema"

import type { ApiOwnerInfo } from "../api"

/** "성이름 (username)" — 이름이 없으면 username. */
export function ownerDisplayName(user: Pick<User, "lastName" | "firstName" | "username">): string {
  const fullName = `${user.lastName ?? ""}${user.firstName ?? ""}`.trim()
  return fullName ? `${fullName} (${user.username})` : user.username
}

/** 백엔드가 내려준 소유자 정보의 표시명 — "성이름 (username)". */
export function ownerInfoName(o: ApiOwnerInfo): string {
  return ownerDisplayName({ lastName: o.last_name ?? "", firstName: o.first_name ?? "", username: o.username })
}

// 소유자 툴팁 본문 — 목록·카드·상세(OwnerValue)와 소유자 선택 항목(OwnerPicker)이 같은 형식을 쓴다.
function OwnerTooltipBody({ organization, department, username, email, unregistered }: {
  organization?: string | null
  department?: string | null
  username?: string | null
  email: string
  unregistered?: boolean
}) {
  return (
    <div className="space-y-0.5">
      <div>소속: {organization || "—"}</div>
      <div>소속부서: {department || "—"}</div>
      <div>Username: {username || "—"}</div>
      <div>Email: {email}</div>
      {unregistered && <div className="text-muted-foreground">※ 사용자 관리에 등록되지 않은 이메일</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 소유자 검색 선택 — 입력하면 서버에서 활성 사용자를 검색(이름·username·이메일·소속 부분 일치)해 고른다.
// 전체 사용자 목록을 받지 않고 최대 OWNER_SEARCH_LIMIT 명만 조회하므로 사용자 수와 무관하게 동작.
// 저장값은 항상 등록 사용자의 이메일(직접 입력 불가). 등록 다이얼로그·개요 편집 공용.
// ---------------------------------------------------------------------------
export type OwnerPick = { email: string; label: string; department: string | null }

export function userToOwnerPick(u: User): OwnerPick {
  return { email: u.email, label: ownerDisplayName(u), department: u.department || null }
}

const OWNER_SEARCH_LIMIT = 20
const OWNER_SEARCH_DEBOUNCE_MS = 300

export function OwnerPicker({ id, value, label, onChange, size = "default" }: {
  /** 라벨 연결용(트리거 버튼에 부여) */
  id?: string
  /** 현재 소유자 이메일("" = 미지정) */
  value: string
  /** 현재 소유자 표시명 — 없으면 이메일 표시 */
  label?: string | null
  /** 선택 결과 — null 이면 미지정 */
  onChange: (pick: OwnerPick | null) => void
  /** sm: 표 안 인라인 편집(h-8) · default: 폼 기본 높이 */
  size?: "sm" | "default"
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<User[]>([])
  const [status, setStatus] = useState<"idle" | "loading" | "loaded" | "error">("idle")
  // 마지막 검색 번호 — 입력이 빠르게 바뀔 때 늦게 도착한 이전 응답 무시
  const seqRef = useRef(0)

  useEffect(() => {
    if (!open) return
    const seq = ++seqRef.current
    const timer = setTimeout(() => {
      setStatus("loading")
      fetchUsers({ search: query.trim() || undefined, status: "active", pageSize: OWNER_SEARCH_LIMIT })
        .then((r) => { if (seq === seqRef.current) { setResults(r.items); setStatus("loaded") } })
        .catch(() => { if (seq === seqRef.current) setStatus("error") })
    }, query ? OWNER_SEARCH_DEBOUNCE_MS : 0)
    return () => clearTimeout(timer)
  }, [open, query])

  const pick = (p: OwnerPick | null) => { onChange(p); setOpen(false) }
  const current = value.toLowerCase()

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQuery("") }}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn("w-full justify-between font-normal", size === "sm" ? "h-8 text-sm" : "h-9")}
        >
          <span className={cn("truncate", !value && "text-muted-foreground")}>{value ? (label || value) : "미지정"}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      {/* 항목을 자르지 않도록 내용 폭만큼 넓어짐(최소: 트리거 폭·300px) */}
      <PopoverContent className="w-auto min-w-[max(300px,var(--radix-popover-trigger-width))] max-w-[calc(100vw-2rem)] p-0" align="start">
        <TooltipProvider delayDuration={150}>
          <Command shouldFilter={false}>
            <CommandInput placeholder="이름·username·이메일로 검색" value={query} onValueChange={setQuery} />
            <CommandList>
              <CommandGroup>
                <CommandItem value="__none__" onSelect={() => pick(null)}>
                  <Check className={cn("mr-2 h-4 w-4", value ? "opacity-0" : "opacity-100")} />
                  <span className="text-muted-foreground">미지정</span>
                </CommandItem>
                {status === "loaded" && results.map((u) => (
                  <CommandItem key={u.id} value={u.email} onSelect={() => pick(userToOwnerPick(u))}>
                    <Check className={cn("mr-2 h-4 w-4 shrink-0", u.email.toLowerCase() === current ? "opacity-100" : "opacity-0")} />
                    {/* "이름 (username) 소속부서" 는 자르지 않고 그대로, 상세(소속·부서·Username·Email)는 hover 툴팁으로 */}
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="flex-1 whitespace-nowrap">
                          {ownerDisplayName(u)}
                          {u.department && <span className="ml-2 text-xs text-muted-foreground">{u.department}</span>}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">
                        <OwnerTooltipBody organization={u.organization} department={u.department} username={u.username} email={u.email} />
                      </TooltipContent>
                    </Tooltip>
                  </CommandItem>
                ))}
              </CommandGroup>
              {(status === "idle" || status === "loading") && (
                <div className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> 검색 중...</div>
              )}
              {status === "error" && <div className="px-3 py-2 text-sm text-destructive">사용자 검색에 실패했습니다. 다시 시도해 주세요.</div>}
              {status === "loaded" && results.length === 0 && <div className="px-3 py-2 text-sm text-muted-foreground">일치하는 사용자가 없습니다.</div>}
              {status === "loaded" && results.length >= OWNER_SEARCH_LIMIT && (
                <div className="px-3 py-1.5 text-[11px] text-muted-foreground">상위 {OWNER_SEARCH_LIMIT}명만 표시합니다. 검색어를 더 입력해 범위를 좁히세요.</div>
              )}
            </CommandList>
          </Command>
        </TooltipProvider>
      </PopoverContent>
    </Popover>
  )
}

// 소유자 표시 — AI 에이전트·데이터셋 상세와 동일하게 이름 + hover 툴팁(소속/소속부서/Username/Email).
// 백엔드가 owner_email 로 조인한 사용자 정보(owner)를 쓰고, 없으면(미등록 이메일) 이메일을 그대로 표시.
export function OwnerValue({ email, owner, className }: {
  email: string | null
  /** API 응답의 owner — null 이면 사용자 관리에 등록되지 않은 이메일. */
  owner: ApiOwnerInfo | null | undefined
  className?: string
}) {
  if (!email) return <span className="text-muted-foreground">-</span>
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-2", className)}>
            {owner ? ownerInfoName(owner) : email}
          </span>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">
          <OwnerTooltipBody
            organization={owner?.organization}
            department={owner?.department}
            username={owner?.username}
            email={owner?.email || email}
            unregistered={!owner}
          />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
