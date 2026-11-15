"use client"

// 정의 파일 보기 — 현재 에이전트 내용을 정의 파일(agent.yaml) 형식으로 보여 주고 복사·내려받기.
// 내용은 읽기 전용 Monaco(CodeViewer, YAML 강조·줄 번호, 글꼴 text-sm)로 표시한다.
// 내려받은 파일을 그대로 반영하면 '변경 없음'이 된다(GET /ai-agents/{name}/manifest).

import { useEffect, useState } from "react"
import { Copy, Download, Loader2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"
import { CodeViewer } from "@/components/code-viewer"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import { fetchAgentManifest } from "../api"
import { copyText } from "@/lib/clipboard"

export function AIAgentsManifestDialog({
  agentName,
  open,
  onOpenChange,
}: {
  agentName: string
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setText(null)
    setError(null)
    fetchAgentManifest(agentName)
      .then(setText)
      .catch((e) => setError(errorMessage(e) ?? String(e)))
  }, [open, agentName])

  const missingRequired = !!text && /^\s+(owner|department|category): null$/m.test(text)

  async function copy() {
    if (!text) return
    try {
      await copyText(text)
      toast.success("정의 파일 내용을 클립보드에 복사했습니다.")
    } catch {
      toast.error("클립보드에 복사하지 못했습니다.")
    }
  }

  function download() {
    if (!text) return
    const blob = new Blob([text], { type: "application/yaml;charset=utf-8" })
    const a = document.createElement("a")
    a.href = URL.createObjectURL(blob)
    a.download = `${agentName}.yaml`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>정의 파일 보기</DialogTitle>
          <DialogDescription>
            현재 내용을 정의 파일 형식으로 보여 줍니다. 소스 저장소에 두고 &lsquo;가져오기&rsquo;나 명령줄 도구로 반영하면
            이후 이 파일 기준으로 관리됩니다. 버전 기록의 지시문은 포함하지 않습니다.
          </DialogDescription>
        </DialogHeader>
        {missingRequired && (
          <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
            소유자·부서·분류 중 비어 있는 항목(null)이 있습니다. 정의 파일로 반영하려면 먼저 채워야 합니다.
          </p>
        )}
        {error ? (
          <p className="rounded-md border p-4 text-sm text-destructive">{error}</p>
        ) : text == null ? (
          <div className="flex h-32 items-center justify-center rounded-md border text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 불러오는 중…
          </div>
        ) : (
          // 대화상자(max-h 90vh) 안에서 머리·안내·버튼을 뺀 높이. Monaco 가 자체 스크롤한다.
          <CodeViewer
            code={text}
            language="yaml"
            height="min(60vh, 640px)"
            copyLabel="정의 파일"
            border
          />
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>닫기</Button>
          <Button variant="secondary" disabled={!text} onClick={copy}>
            <Copy className="mr-1 h-4 w-4" /> 복사
          </Button>
          <Button disabled={!text} onClick={download}>
            <Download className="mr-1 h-4 w-4" /> 내려받기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
