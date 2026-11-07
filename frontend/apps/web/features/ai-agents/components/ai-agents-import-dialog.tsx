"use client"

// 정의 파일 가져오기 — 정의 파일(agent.yaml)·표 형식 파일(.csv)을 올려 미리 보기 → 반영 → 결과 확인.
// 서버 경로는 하나(POST /ai-agents/apply). 표 형식 파일은 기본 항목만 정의 내용으로 바꿔 200개씩 나눠 보낸다.
// 외부 에이전트 주소를 주면 소개 정보(에이전트 카드)를 읽어 정의 내용으로 채운다(POST /ai-agents/import/card).
// 설계: design/ai-agent-bulk-registration-design.md §4.5

import { useCallback, useRef, useState } from "react"
import { Download, FileUp, Globe, Loader2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Input } from "@workspace/ui/components/input"
import { Textarea } from "@workspace/ui/components/textarea"

import { applyAgentManifests, importAgentCard, type ApplyAction, type ApplyResult } from "../api"
import { CSV_COLUMNS, csvTemplate, csvToDocs, mergeResults } from "../manifest-import"
import { useAIAgents } from "./ai-agents-provider"

const ACTION_LABEL: Record<ApplyAction, string> = {
  created: "생성", updated: "갱신", unchanged: "변경 없음", failed: "실패",
}
const ACTION_CLASS: Record<ApplyAction, string> = {
  created: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  updated: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  unchanged: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
}

function downloadCsvTemplate() {
  const blob = new Blob(["\uFEFF" + csvTemplate()], { type: "text/csv;charset=utf-8" })
  const a = document.createElement("a")
  a.href = URL.createObjectURL(blob)
  a.download = "ai-agents-template.csv"
  a.click()
  URL.revokeObjectURL(a.href)
}

const CHUNK = 200

export function AIAgentsImportDialog() {
  const { open, setOpen, refreshAgents } = useAIAgents()
  const fileRef = useRef<HTMLInputElement>(null)
  const [yamlText, setYamlText] = useState("")
  const [csvDocs, setCsvDocs] = useState<Record<string, unknown>[]>([])
  const [fileNames, setFileNames] = useState<string[]>([])
  const [busy, setBusy] = useState<"preview" | "apply" | "card" | null>(null)
  const [cardUrl, setCardUrl] = useState("")
  const [cardNotes, setCardNotes] = useState<string[]>([])
  const [result, setResult] = useState<ApplyResult | null>(null)
  const [previewed, setPreviewed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reset = useCallback(() => {
    setYamlText("")
    setCsvDocs([])
    setFileNames([])
    setResult(null)
    setPreviewed(false)
    setError(null)
    setCardUrl("")
    setCardNotes([])
    if (fileRef.current) fileRef.current.value = ""
  }, [])

  const close = () => {
    reset()
    setOpen(null)
  }

  // 입력이 바뀌면 이전 미리 보기는 무효
  const invalidate = () => {
    setResult(null)
    setPreviewed(false)
    setError(null)
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return
    invalidate()
    const yamlParts: string[] = []
    const docs: Record<string, unknown>[] = []
    const errs: string[] = []
    for (const f of Array.from(files)) {
      const text = await f.text()
      if (/\.csv$/i.test(f.name)) {
        const parsed = csvToDocs(text)
        if (parsed.error) errs.push(`${f.name}: ${parsed.error}`)
        else docs.push(...parsed.docs)
      } else {
        yamlParts.push(text.trim())
      }
    }
    setFileNames(Array.from(files).map((f) => f.name))
    setYamlText(yamlParts.filter(Boolean).join("\n---\n"))
    setCsvDocs(docs)
    if (errs.length) setError(errs.join(" / "))
  }

  async function loadCard() {
    const url = cardUrl.trim()
    if (!url) return
    setBusy("card")
    setError(null)
    try {
      const card = await importAgentCard(url)
      invalidate()
      setYamlText((prev) => (prev.trim() ? `${prev.trim()}\n---\n${card.yaml}` : card.yaml))
      const notes = [...card.notes]
      if (card.missing.length) {
        notes.unshift(`비어 있는 필수 항목(${card.missing.join(", ")})을 아래 내용에서 채운 뒤 미리 보기하세요.`)
      }
      setCardNotes(notes)
      setCardUrl("")
      toast.success(`'${card.name}' 소개 정보를 불러왔습니다 — ${card.source_url}`)
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setBusy(null)
    }
  }

  async function run(dryRun: boolean) {
    if (!yamlText.trim() && csvDocs.length === 0) return
    setBusy(dryRun ? "preview" : "apply")
    setError(null)
    try {
      const parts: ApplyResult[] = []
      if (yamlText.trim()) parts.push(await applyAgentManifests({ yaml: yamlText }, dryRun))
      for (let i = 0; i < csvDocs.length; i += CHUNK) {
        parts.push(await applyAgentManifests({ agents: csvDocs.slice(i, i + CHUNK) }, dryRun))
      }
      const merged = mergeResults(parts, dryRun)
      setResult(merged)
      if (dryRun) {
        setPreviewed(true)
      } else {
        const { created, updated, failed } = merged.summary
        if (failed) toast.warning(`반영 완료 — 생성 ${created} · 갱신 ${updated} · 실패 ${failed}`)
        else toast.success(`반영 완료 — 생성 ${created} · 갱신 ${updated}`)
        setPreviewed(false)
        await refreshAgents()
      }
    } catch (e) {
      setError(errorMessage(e) ?? String(e))
    } finally {
      setBusy(null)
    }
  }

  const hasInput = !!yamlText.trim() || csvDocs.length > 0
  const applied = result && !result.dry_run

  return (
    <Dialog open={open === "import"} onOpenChange={(v) => !v && close()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>정의 파일 가져오기</DialogTitle>
          <DialogDescription>
            정의 파일(<code>agent.yaml</code>, 여러 개 가능)이나 표 형식 파일(<code>.csv</code>)을 올리면 에이전트를 한 번에
            생성·갱신합니다. 먼저 미리 보기로 바뀔 내용을 확인한 뒤 반영하세요.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".yaml,.yml,.csv"
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />
            <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
              <FileUp className="mr-1 h-4 w-4" /> 파일 선택
            </Button>
            <Button variant="ghost" size="sm" onClick={downloadCsvTemplate}>
              <Download className="mr-1 h-4 w-4" /> 표 형식 양식 내려받기
            </Button>
            {fileNames.length > 0 && (
              <span className="text-xs text-muted-foreground">
                {fileNames.join(", ")}
                {csvDocs.length > 0 && ` · 표 형식 ${csvDocs.length}행`}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={cardUrl}
              onChange={(e) => setCardUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  loadCard()
                }
              }}
              placeholder="외부 에이전트 주소 (예: https://agents.corp.example/refund)"
              className="h-8 max-w-md flex-1 text-xs"
            />
            <Button variant="outline" size="sm" disabled={!cardUrl.trim() || !!busy} onClick={loadCard}>
              {busy === "card" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Globe className="mr-1 h-4 w-4" />}
              소개 정보 불러오기
            </Button>
          </div>
          {cardNotes.length > 0 && (
            <ul className="space-y-0.5 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              {cardNotes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}

          <Textarea
            value={yamlText}
            onChange={(e) => {
              setYamlText(e.target.value)
              invalidate()
            }}
            placeholder={"정의 파일 내용을 직접 붙여 넣을 수도 있습니다.\napiVersion: argus.catalog/v1\nkind: Agent\nmetadata:\n  name: cs.refund-helper\n  owner: hong@corp.example\n  department: 고객지원팀\n  category: customer-service\nspec:\n  version: 1.0.0\n  status: active"}
            className="min-h-[160px] font-mono text-xs"
          />
          <p className="text-xs text-muted-foreground">
            표 형식 파일은 기본 항목만 받습니다({CSV_COLUMNS.join(", ")} — tags 는 세미콜론 구분). 도구·도구 서버·계보는
            정의 파일로 올리세요. 정의 파일로 반영한 에이전트는 이후에도 정의 파일 기준으로 맞춰집니다. 소개 정보 불러오기는
            그 주소의 <code>/.well-known/agent-card.json</code>(또는 <code>agent.json</code>)을 읽어 내용을 채우기만 하며, 저장은 반영 때 합니다.
          </p>

          {error && <p className="text-sm text-destructive">{error}</p>}

          {result && (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{result.dry_run ? "미리 보기 결과" : "반영 결과"}</span>
                {(Object.keys(ACTION_LABEL) as ApplyAction[]).map((k) => (
                  <span key={k} className={`rounded-full px-2 py-0.5 text-xs ${ACTION_CLASS[k]}`}>
                    {ACTION_LABEL[k]} {result.summary[k] ?? 0}
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
                    {result.results.map((r, i) => (
                      <TableRow key={`${r.name}-${i}`}>
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
          {previewed && !applied && (
            <Badge variant="outline" className="mr-auto self-center text-xs">
              미리 보기 완료 — 반영할 수 있습니다
            </Badge>
          )}
          <Button variant="outline" onClick={close}>
            {applied ? "닫기" : "취소"}
          </Button>
          <Button variant="secondary" disabled={!hasInput || !!busy} onClick={() => run(true)}>
            {busy === "preview" && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            미리 보기
          </Button>
          <Button disabled={!hasInput || !previewed || !!busy} onClick={() => run(false)}>
            {busy === "apply" && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            반영
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
