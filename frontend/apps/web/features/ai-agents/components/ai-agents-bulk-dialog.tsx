"use client"

// 일괄 변경 — 목록에서 고른 에이전트의 상태·소유자·부서·분류·태그를 한 번에 바꾼다(POST /ai-agents/bulk).
// 미리 보기를 해야 '반영' 버튼이 켜진다. 입력을 바꾸면 미리 보기가 무효가 된다.
// 설계: design/ai-agent-ownership-design.md §6

import { useState } from "react"
import { Loader2 } from "lucide-react"
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
import { bulkUpdateAgents, type BulkResult, type BulkSet } from "../api"
import { AGENT_STATUS_LABEL } from "../data/schema"
import { useAIAgents } from "./ai-agents-provider"

const KEEP = "__keep__"
const ACTION_LABEL = { updated: "갱신", unchanged: "변경 없음", failed: "실패" } as const
const ACTION_CLASS = {
  updated: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  unchanged: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
} as const

const splitTags = (v: string) => v.split(",").map((t) => t.trim()).filter(Boolean)

export function AIAgentsBulkDialog() {
  const { user } = useAuth()
  const { open, setOpen, selectedNames, setSelectedNames, refreshAgents, bumpStats } = useAIAgents()
  const [status, setStatus] = useState(KEEP)
  const [owner, setOwner] = useState("")
  const [department, setDepartment] = useState("")
  const [category, setCategory] = useState("")
  const [addTags, setAddTags] = useState("")
  const [removeTags, setRemoveTags] = useState("")
  const [result, setResult] = useState<BulkResult | null>(null)
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null)
  const [error, setError] = useState<string | null>(null)

  const set: BulkSet = {
    ...(status !== KEEP && { status }),
    ...(owner.trim() && { owner: owner.trim() }),
    ...(department.trim() && { department: department.trim() }),
    ...(category.trim() && { category: category.trim() }),
    ...(splitTags(addTags).length && { add_tags: splitTags(addTags) }),
    ...(splitTags(removeTags).length && { remove_tags: splitTags(removeTags) }),
  }
  const hasChange = Object.keys(set).length > 0
  const previewed = !!result?.dry_run
  const applied = !!result && !result.dry_run

  function reset() {
    setStatus(KEEP)
    setOwner("")
    setDepartment("")
    setCategory("")
    setAddTags("")
    setRemoveTags("")
    setResult(null)
    setError(null)
  }
  function close() {
    reset()
    setOpen(null)
  }
  // 입력이 바뀌면 이전 미리 보기는 무효
  const edit = <T,>(fn: (v: T) => void) => (v: T) => {
    fn(v)
    setResult(null)
    setError(null)
  }

  async function run(dryRun: boolean) {
    setBusy(dryRun ? "preview" : "apply")
    setError(null)
    try {
      const r = await bulkUpdateAgents(selectedNames, set, dryRun)
      setResult(r)
      if (!dryRun) {
        const { updated, failed } = r.summary
        if (failed) toast.warning(`일괄 변경 — 갱신 ${updated} · 실패 ${failed}`)
        else toast.success(`일괄 변경 — 갱신 ${updated}`)
        setSelectedNames([])
        await refreshAgents()
        bumpStats()
      }
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog open={open === "bulk"} onOpenChange={(v) => !v && close()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>일괄 변경</DialogTitle>
          <DialogDescription>
            고른 에이전트 {applied ? result?.results.length : selectedNames.length}개에 같은 값을 넣습니다. 비워 둔 항목은
            바꾸지 않습니다. 권한(등록자·소유자·관리자)이 없는 에이전트는 실패로 표시됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label>상태</Label>
              <Select value={status} onValueChange={edit(setStatus)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={KEEP}>바꾸지 않음</SelectItem>
                  {Object.entries(AGENT_STATUS_LABEL)
                    .filter(([k]) => k !== "blocked" || user?.is_admin)
                    .map(([k, label]) => (
                      <SelectItem key={k} value={k}>
                        {label}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="bulk-owner">소유자 이메일</Label>
              <Input id="bulk-owner" value={owner} placeholder="바꾸지 않음" onChange={(e) => edit(setOwner)(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="bulk-dept">부서</Label>
              <Input id="bulk-dept" value={department} placeholder="바꾸지 않음" onChange={(e) => edit(setDepartment)(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="bulk-cat">분류</Label>
              <Input id="bulk-cat" value={category} placeholder="바꾸지 않음" onChange={(e) => edit(setCategory)(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="bulk-add">태그 추가(쉼표)</Label>
              <Input id="bulk-add" value={addTags} onChange={(e) => edit(setAddTags)(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="bulk-remove">태그 빼기(쉼표)</Label>
              <Input id="bulk-remove" value={removeTags} onChange={(e) => edit(setRemoveTags)(e.target.value)} />
            </div>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          {result && (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{result.dry_run ? "미리 보기 결과" : "반영 결과"}</span>
                {(Object.keys(ACTION_LABEL) as (keyof typeof ACTION_LABEL)[]).map((k) => (
                  <span key={k} className={`rounded-full px-2 py-0.5 text-xs ${ACTION_CLASS[k]}`}>
                    {ACTION_LABEL[k]} {result.summary[k]}
                  </span>
                ))}
              </div>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[220px]">이름</TableHead>
                      <TableHead className="w-[90px]">결과</TableHead>
                      <TableHead>내용</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.results.map((r) => (
                      <TableRow key={r.name}>
                        <TableCell className="font-mono text-xs">{r.name}</TableCell>
                        <TableCell>
                          <span className={`rounded-full px-2 py-0.5 text-xs ${ACTION_CLASS[r.action]}`}>
                            {ACTION_LABEL[r.action]}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs">
                          {r.error ? (
                            <span className="text-destructive">{r.error}</span>
                          ) : r.changes?.length ? (
                            <ul className="space-y-0.5">
                              {r.changes.map((c) => (
                                <li key={c} className="break-all">{c}</li>
                              ))}
                              {r.note && <li className="text-amber-700 dark:text-amber-400">{r.note}</li>}
                            </ul>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={close}>
            {applied ? "닫기" : "취소"}
          </Button>
          {!applied && (
            <>
              <Button variant="secondary" disabled={!hasChange || !!busy} onClick={() => run(true)}>
                {busy === "preview" && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                미리 보기
              </Button>
              <Button disabled={!hasChange || !previewed || !!busy} onClick={() => run(false)}>
                {busy === "apply" && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                반영
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
