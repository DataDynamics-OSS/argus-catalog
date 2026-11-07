"use client"

/**
 * manifest 가져오기 (설계 §6.2) — 서버 정의(정적 manifest, 정의만) 또는 배포(정의 + 노출·그룹·범위 관측값).
 */

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Textarea } from "@workspace/ui/components/textarea"

import { importDeploymentManifest, importServerManifest } from "../../api"
import type { McpCollectResult } from "../../data/schema"
import { DiffView } from "../mcp-deployment-detail"

const EXAMPLE = `{
  "server": {"name": "hive-mcp", "version": "1.2.0"},
  "groups": [{"key": "dml", "config_key": "allow.dml", "enabled": false, "mutating": true, "risk": "high"}],
  "tools": [{"name": "execute", "group": "dml", "mutating": true, "exposed": false, "description": "..."}],
  "resource_scopes": [{"resource_type": "table", "action": "write", "effect": "protect", "patterns": ["prod.*"]}],
  "limits": {"limits.max_rows": 200}
}`

export function ManifestImportDialog({
  open, onOpenChange, target, name, onApplied,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  target: "server" | "deployment"
  name: string
  onApplied: () => Promise<void>
}) {
  const [content, setContent] = useState("")
  const [preview, setPreview] = useState<McpCollectResult | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) setPreview(null)
  }, [open])

  const run = async (dryRun: boolean) => {
    setBusy(true)
    try {
      const fn = target === "server" ? importServerManifest : importDeploymentManifest
      const r = await fn(name, { content, dry_run: dryRun })
      if (dryRun) setPreview(r)
      else {
        toast.success(`manifest 를 반영했습니다 (도구 ${r.tool_count}개)`)
        onOpenChange(false)
        setContent("")
        await onApplied()
      }
    } catch (e) {
      notifyError(e, "manifest 를 가져오지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>manifest 가져오기 — {target === "server" ? "서버 정의" : "배포"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {target === "server"
              ? "릴리즈 산출물(mcp-manifest.json)로 도구·그룹 정의를 갱신합니다. 배포별 노출 상태는 바꾸지 않습니다."
              : "서버가 보고한 manifest(GET /api/v1/manifest 응답 또는 파일)로 정의와 이 배포의 노출·그룹 on/off·리소스 범위·limits·보안 경고를 갱신합니다."}
            {" "}꺼진 그룹의 도구까지(exposed=false) 실리므로 추론 없이 확정됩니다. manifest 에 빠진 도구는 정의에서 제거된 것으로 표시합니다.
          </p>
          <Input type="file" accept=".json,application/json" onChange={async (e) => {
            const f = e.target.files?.[0]
            if (f) { setContent(await f.text()); setPreview(null) }
          }} />
          <Textarea value={content} onChange={(e) => { setContent(e.target.value); setPreview(null) }} rows={12}
            className="font-mono text-xs" placeholder={EXAMPLE} />
          {preview && (
            <div className="rounded-md border p-3">
              <div className="mb-2 text-xs text-muted-foreground">
                도구 {preview.tool_count}개{target === "deployment" ? ` · 노출 ${preview.exposed_count}개` : ""}
              </div>
              {preview.diff && <DiffView diff={preview.diff} />}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>닫기</Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy || !content.trim()}>
            {busy && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}미리보기
          </Button>
          <Button onClick={() => run(false)} disabled={busy || !preview}>반영</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
