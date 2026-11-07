"use client"

// 미등록 에이전트 — 실행 기록(호출·정책 판정)에 나왔지만 카탈로그에 없는 에이전트.
// 초안으로 등록(로그인 사용자 — 소유자·부서·분류 필수, 소유자·부서는 본인 값으로 미리 채움)하거나
// 무시·되돌리기(관리자)한다.
// 설계: design/ai-agent-bulk-registration-design.md §5

import { useCallback, useEffect, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, EyeOff, Plus, Radar, RotateCcw, Search } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"

import { useAuth } from "@/features/auth"
import {
  fetchDiscoveries,
  registerDiscovery,
  setDiscoveryIgnored,
  type AIAgentDiscovery,
  type DiscoveryStatus,
} from "../api"
import { useAIAgents } from "./ai-agents-provider"
import { usePermissions } from "@/features/permissions/use-permissions"

const STATUS_LABEL: Record<DiscoveryStatus, string> = { new: "처리 전", ignored: "무시", registered: "등록됨" }
const STATUS_CLASS: Record<DiscoveryStatus, string> = {
  new: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  ignored: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
  registered: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
}
const PAGE_SIZE = 20

function fmt(ts: string | null) {
  return ts ? new Date(ts).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "-"
}

export function AIAgentsDiscoveries({ onChanged }: { onChanged?: () => void }) {
  const { user } = useAuth()
  const canRegister = usePermissions().canUseFeature("agents.register")
  const isAdmin = !!user?.is_admin
  const { setSelectedAgentName, refreshAgents } = useAIAgents()
  const [status, setStatus] = useState<DiscoveryStatus | "all">("new")
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<AIAgentDiscovery[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  // 등록 대화상자 — 대상 기록과 필수 항목(소유자 이메일·부서·분류)
  const [regTarget, setRegTarget] = useState<AIAgentDiscovery | null>(null)
  const [reg, setReg] = useState({ owner_email: "", department: "", category: "" })
  const [regTouched, setRegTouched] = useState(false)
  const seq = useRef(0)
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (p: { status: DiscoveryStatus | "all"; search: string; page: number }) => {
    const my = ++seq.current
    setLoading(true)
    try {
      const data = await fetchDiscoveries({
        status: p.status === "all" ? "" : p.status, search: p.search || undefined, page: p.page, pageSize: PAGE_SIZE,
      })
      if (my !== seq.current) return
      setItems(data.items)
      setTotal(data.total)
    } catch (e) {
      if (my === seq.current) toast.error(errorMessage(e) ?? String(e))
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    load({ status: "new", search: "", page: 1 })
  }, [load])

  const reload = () => load({ status, search, page })

  function openRegister(d: AIAgentDiscovery) {
    setReg({ owner_email: user?.email ?? "", department: user?.department ?? "", category: "" })
    setRegTouched(false)
    setRegTarget(d)
  }

  const regOwnerValid = /.+@.+/.test(reg.owner_email.trim())
  const regValid = regOwnerValid && !!reg.department.trim() && !!reg.category.trim()

  async function register() {
    const d = regTarget
    if (!d) return
    setRegTouched(true)
    if (!regValid) return
    setBusyId(d.id)
    try {
      const { agent } = await registerDiscovery(d.id, {
        owner_email: reg.owner_email.trim(),
        department: reg.department.trim(),
        category: reg.category.trim(),
      })
      setRegTarget(null)
      toast.success(`'${agent.name}' 을(를) 초안으로 등록했습니다.`, {
        action: { label: "상세 보기", onClick: () => setSelectedAgentName(agent.name) },
      })
      await Promise.all([reload(), refreshAgents()])
      onChanged?.()
    } catch (e) {
      toast.error(errorMessage(e) ?? String(e))
    } finally {
      setBusyId(null)
    }
  }

  async function toggleIgnore(d: AIAgentDiscovery, ignored: boolean) {
    setBusyId(d.id)
    try {
      await setDiscoveryIgnored(d.id, ignored)
      toast.success(ignored ? `'${d.name}' 을(를) 무시합니다.` : `'${d.name}' 을(를) 처리 전으로 되돌렸습니다.`)
      await reload()
      onChanged?.()
    } catch (e) {
      toast.error(errorMessage(e) ?? String(e))
    } finally {
      setBusyId(null)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        실행 환경이 보낸 호출 기록·정책 판정 기록 중 카탈로그에 없는 에이전트입니다. 등록하면 초안 상태의 에이전트가
        만들어지고(소유자·부서는 등록 대화상자에서 지정 — 기본값은 등록하는 사람), 이후 기록은 정상 수집됩니다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-xs flex-1">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="이름 검색..."
            value={search}
            onChange={(e) => {
              const v = e.target.value
              setSearch(v)
              if (debounce.current) clearTimeout(debounce.current)
              debounce.current = setTimeout(() => {
                setPage(1)
                load({ status, search: v, page: 1 })
              }, 400)
            }}
            className="h-9 pl-8"
          />
        </div>
        <Select
          value={status}
          onValueChange={(v) => {
            const s = v as DiscoveryStatus | "all"
            setStatus(s)
            setPage(1)
            load({ status: s, search, page: 1 })
          }}
        >
          <SelectTrigger size="sm" className="w-[130px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="new">처리 전</SelectItem>
            <SelectItem value="ignored">무시</SelectItem>
            <SelectItem value="registered">등록됨</SelectItem>
            <SelectItem value="all">전체</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">총 {total}개</span>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead className="text-right">기록 수</TableHead>
              <TableHead>처음 발견</TableHead>
              <TableHead>마지막 발견</TableHead>
              <TableHead>보낸 계정 · 호출자</TableHead>
              <TableHead className="text-right">처리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">불러오는 중...</TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  <Radar className="mx-auto mb-1 h-5 w-5" />
                  {status === "new" ? "처리할 미등록 에이전트가 없습니다." : "해당하는 기록이 없습니다."}
                </TableCell>
              </TableRow>
            ) : (
              items.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-mono text-sm">{d.name}</span>
                      {d.status === "registered" && d.agent_name && (
                        <button
                          type="button"
                          className="w-fit text-left text-xs text-primary hover:underline"
                          onClick={() => setSelectedAgentName(d.agent_name!)}
                        >
                          등록된 에이전트 보기
                        </button>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[d.status]}`}>
                      {STATUS_LABEL[d.status]}
                    </span>
                    {d.status_changed_by && (
                      <div className="mt-0.5 text-[11px] text-muted-foreground">{d.status_changed_by}</div>
                    )}
                  </TableCell>
                  <TableCell className="text-right text-sm">
                    <div>{d.seen_count.toLocaleString()}</div>
                    <div className="text-[11px] text-muted-foreground">
                      호출 {d.invocation_count} · 판정 {d.hook_event_count}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmt(d.first_seen_at)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmt(d.last_seen_at)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <div>{d.reported_by || "-"}</div>
                    <div>{d.last_consumer || "-"}</div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      {d.status !== "registered" && (
                        <Button size="sm" variant="outline" disabled={busyId === d.id || !canRegister} title={canRegister ? undefined : "에이전트 등록 권한이 없습니다"} onClick={() => openRegister(d)}>
                          <Plus className="mr-1 h-3.5 w-3.5" /> 등록
                        </Button>
                      )}
                      {isAdmin && d.status === "new" && (
                        <Button size="sm" variant="ghost" disabled={busyId === d.id} onClick={() => toggleIgnore(d, true)}>
                          <EyeOff className="mr-1 h-3.5 w-3.5" /> 무시
                        </Button>
                      )}
                      {isAdmin && d.status === "ignored" && (
                        <Button size="sm" variant="ghost" disabled={busyId === d.id} onClick={() => toggleIgnore(d, false)}>
                          <RotateCcw className="mr-1 h-3.5 w-3.5" /> 되돌리기
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <span className="text-sm">{page} / {totalPages} 페이지</span>
          <Button
            variant="outline"
            className="size-8 p-0"
            disabled={page <= 1}
            onClick={() => {
              setPage(page - 1)
              load({ status, search, page: page - 1 })
            }}
          >
            <span className="sr-only">이전 페이지</span>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            className="size-8 p-0"
            disabled={page >= totalPages}
            onClick={() => {
              setPage(page + 1)
              load({ status, search, page: page + 1 })
            }}
          >
            <span className="sr-only">다음 페이지</span>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      <Dialog open={!!regTarget} onOpenChange={(v) => !v && setRegTarget(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>미등록 에이전트 등록</DialogTitle>
            <DialogDescription>
              <span className="font-mono">{regTarget?.name}</span> 을(를) 초안(draft)으로 등록합니다.
              소유자·부서·분류는 필수입니다(화면 등록과 같은 기준).
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            {(
              [
                ["owner_email", "소유자 이메일", "예: hong@corp.example", !regOwnerValid, "이메일 형식으로 입력하세요."],
                ["department", "부서", "예: 고객지원팀", !reg.department.trim(), "부서를 입력하세요."],
                ["category", "분류", "예: customer-service", !reg.category.trim(), "분류를 입력하세요."],
              ] as const
            ).map(([key, label, ph, bad, msg]) => (
              <div key={key} className="grid gap-1.5">
                <Label htmlFor={`disc-reg-${key}`}>
                  {label} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id={`disc-reg-${key}`}
                  placeholder={ph}
                  value={reg[key]}
                  onChange={(e) => setReg((r) => ({ ...r, [key]: e.target.value }))}
                  aria-invalid={regTouched && bad}
                />
                {regTouched && bad && <p className="text-xs text-destructive">{msg}</p>}
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              같은 이름의 에이전트가 이미 있으면 새로 만들지 않고 그 에이전트에 연결만 합니다.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRegTarget(null)} disabled={busyId === regTarget?.id}>
              취소
            </Button>
            <Button onClick={register} disabled={busyId === regTarget?.id}>
              {busyId === regTarget?.id ? "등록 중..." : "등록"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
