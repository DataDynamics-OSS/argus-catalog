"use client"

import { useEffect, useState } from "react"
import { CheckCircle2, Loader2, XCircle } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"

import { fetchVersions, importFromHuggingFace, type OciModelVersion } from "@/features/oci-hub/api"

import { formatSize } from "./oci-common"

const HF_URL_REGEX = /^https?:\/\/huggingface\.co\/([a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_.-]+)?)(?:\/.*)?$/
const HF_ID_REGEX = /^[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_.-]+)?$/

/** URL 또는 org/model 형식에서 HF 모델 ID 를 뽑는다. */
export function extractHfModelId(input: string): string | null {
  const v = input.trim()
  const m = HF_URL_REGEX.exec(v)
  if (m) return m[1] ?? null
  return HF_ID_REGEX.test(v) ? v : null
}

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

type Job = { name: string; version: number; status: string; error?: string; version_info?: OciModelVersion }

/**
 * HuggingFace 가져오기 (관리자).
 *
 * 서버는 202 로 접수하고 백그라운드에서 내려받는다. 다이얼로그는 버전 상태를 3초마다
 * 확인해 보여 주며, 닫아도 가져오기는 계속된다(목록·버전 탭에서 '가져오는 중' 으로 표시).
 */
export function OciImportDialog({
  open, onOpenChange, onChanged,
}: { open: boolean; onOpenChange: (o: boolean) => void; onChanged: () => void }) {
  const [input, setInput] = useState("")
  const [name, setName] = useState("")
  const [nameTouched, setNameTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<Job | null>(null)

  const hfId = extractHfModelId(input)
  const effectiveName = nameTouched ? name : (hfId ?? "").replace(/\//g, "-")
  const nameError = effectiveName && (!NAME_RE.test(effectiveName) || effectiveName.includes(".."))
    ? "이름은 영문·숫자로 시작하고 영문·숫자·. _ - 만 쓸 수 있습니다."
    : null

  useEffect(() => {
    if (!open) {
      setInput(""); setName(""); setNameTouched(false); setError(null); setJob(null)
    }
  }, [open])

  // 진행 중인 작업 폴링
  const polling = job?.status === "importing"
  const jobName = job?.name
  const jobVersion = job?.version
  useEffect(() => {
    if (!polling || !jobName || jobVersion === undefined) return
    let stopped = false
    const tick = async () => {
      try {
        const v = (await fetchVersions(jobName)).find((x) => x.version === jobVersion)
        if (stopped || !v || v.status === "importing") return
        const err = (v.extra_metadata?.error as string | undefined) ?? undefined
        setJob({ name: jobName, version: jobVersion, status: v.status, error: err, version_info: v })
        if (v.status === "ready") toast.success(`${jobName} v${jobVersion} 버전을 가져왔습니다.`)
        else notifyError(err, `${jobName} v${jobVersion} 버전을 가져오지 못했습니다.`)
        onChanged()
      } catch {
        /* 일시 오류는 다음 주기에 재시도 */
      }
    }
    const id = window.setInterval(tick, 3000)
    return () => { stopped = true; window.clearInterval(id) }
  }, [polling, jobName, jobVersion, onChanged])

  async function submit() {
    if (!hfId) return
    setSubmitting(true)
    setError(null)
    try {
      const r = await importFromHuggingFace({ hf_model_id: hfId, name: effectiveName || undefined })
      setJob({ name: r.name, version: r.version, status: r.status })
      toast.info(`${r.name} v${r.version} 가져오기를 시작했습니다.`)
      onChanged()
    } catch (e) {
      setError(errorMessage(e) ?? "가져오기를 시작하지 못했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!submitting) onOpenChange(o) }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>HuggingFace 에서 가져오기</DialogTitle>
          <DialogDescription>
            서버가 백그라운드로 내려받아 저장합니다. 창을 닫아도 계속 진행되며, 진행 상태는 모델의 버전 탭에서 볼 수 있습니다.
          </DialogDescription>
        </DialogHeader>

        {job ? (
          <div className="py-2 space-y-3">
            <div className="flex items-center gap-2 text-sm">
              {job.status === "importing" && <Loader2 className="h-4 w-4 animate-spin text-sky-600" />}
              {job.status === "ready" && <CheckCircle2 className="h-4 w-4 text-green-600" />}
              {job.status === "failed" && <XCircle className="h-4 w-4 text-destructive" />}
              <span className="font-medium">{job.name} v{job.version}</span>
              <span className="text-muted-foreground">
                {job.status === "importing" ? "가져오는 중…" : job.status === "ready" ? "완료" : "실패"}
              </span>
            </div>
            {job.status === "ready" && job.version_info && (
              <p className="text-sm text-muted-foreground">
                파일 {job.version_info.file_count}개 · {formatSize(job.version_info.total_size)}
              </p>
            )}
            {job.status === "failed" && job.error && (
              <p className="text-sm text-destructive break-all">{job.error}</p>
            )}
          </div>
        ) : (
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label htmlFor="hf-input">HuggingFace URL 또는 모델 ID</Label>
              <Input
                id="hf-input"
                value={input}
                onChange={(e) => { setInput(e.target.value); setError(null) }}
                onKeyDown={(e) => { if (e.key === "Enter" && hfId && !nameError && !submitting) submit() }}
                placeholder="https://huggingface.co/bert-base-uncased"
                disabled={submitting}
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                예: <code>https://huggingface.co/meta-llama/Llama-3.1-8B</code>, <code>bert-base-uncased</code>, <code>org/model</code>
              </p>
              {input && !hfId && <p className="text-xs text-destructive">HuggingFace 모델 URL 또는 ID 형식이 아닙니다.</p>}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="hf-name">허브에 저장할 이름</Label>
              <Input
                id="hf-name"
                value={effectiveName}
                onChange={(e) => { setName(e.target.value); setNameTouched(true) }}
                placeholder="모델 ID 에서 자동 생성"
                disabled={submitting}
              />
              {nameError
                ? <p className="text-xs text-destructive">{nameError}</p>
                : <p className="text-xs text-muted-foreground">같은 이름의 모델이 있으면 다음 버전으로 추가됩니다.</p>}
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        )}

        <DialogFooter>
          {job ? (
            <Button onClick={() => onOpenChange(false)}>{job.status === "importing" ? "닫기 (계속 진행)" : "닫기"}</Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>취소</Button>
              <Button onClick={submit} disabled={!hfId || !!nameError || submitting}>
                {submitting && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
                가져오기
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
