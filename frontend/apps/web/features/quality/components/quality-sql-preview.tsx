"use client"

import { useEffect, useState } from "react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { CodeViewer } from "@/components/code-viewer"
import { previewQualitySql, type QualitySqlPreview, type QualitySqlPreviewRequest } from "@/features/quality/api"
import { errorMessage } from "@/lib/notify"

// Monaco 한 줄의 높이(글꼴 14px 기준)와 위아래 여백 — 쿼리 줄 수에 맞춰 뷰어 높이를 정한다
const LINE_PX = 19
const PADDING_PX = 16 + 14  // 위아래 여백 + 가로 스크롤 막대

/**
 * 이 유형·기준값으로 진단할 때 실행하는 Trino 쿼리와 통과 판정 방법.
 * 서버가 진단과 같은 코드로 만들어 준다 — 쿼리를 실행하지는 않는다. 입력이 바뀌면 잠시 뒤 다시 만든다.
 */
export function QualitySqlPreviewBox({ request, className = "" }: { request: QualitySqlPreviewRequest | null; className?: string }) {
  const [preview, setPreview] = useState<QualitySqlPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = request ? JSON.stringify(request) : ""

  useEffect(() => {
    if (!key) {
      setPreview(null)
      setError(null)
      return
    }
    let stale = false
    const timer = setTimeout(() => {
      previewQualitySql(JSON.parse(key) as QualitySqlPreviewRequest)
        .then((p) => {
          if (stale) return
          setPreview(p)
          setError(null)
        })
        .catch((e) => {
          if (stale) return
          setPreview(null)
          setError(errorMessage(e) ?? String(e))
        })
    }, 300)
    return () => {
      stale = true
      clearTimeout(timer)
    }
  }, [key])

  return (
    <div className={`grid gap-1.5 ${className}`}>
      <span className="text-sm font-medium">진단 쿼리 (Trino)</span>
      {error ? (
        <p className="rounded border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">{error}</p>
      ) : !preview ? (
        <div className="h-20 animate-pulse rounded border bg-muted/40" />
      ) : (
        <>
          {preview.sql && (
            // 읽기 전용 Monaco(글꼴 text-sm) — 복사 버튼은 뷰어 오른쪽 위에 마우스를 올리면 나온다
            <CodeViewer
              code={preview.sql}
              language="sql"
              copyLabel="진단 쿼리"
              border
              className="rounded"
              height={Math.min(320, Math.max(96, preview.sql.split("\n").length * LINE_PX + PADDING_PX))}
              options={{ folding: false, glyphMargin: false, lineDecorationsWidth: 8 }}
            />
          )}
          {preview.judgement && <p className="text-xs"><b>판정</b> — {preview.judgement}</p>}
          {preview.note && <p className="text-xs">{preview.note}</p>}
          {preview.sql && (
            <p className="text-xs">
              실제 진단은 같은 테이블의 규칙들을 이 쿼리 하나로 묶어 품질 워커가 Trino 에서 실행합니다.
            </p>
          )}
        </>
      )}
    </div>
  )
}

/** 진단 쿼리를 대화상자로 본다 — 규칙·지표 목록의 "쿼리 보기". */
export function QualitySqlPreviewDialog({ title, description, request, onClose }: {
  title: string
  description?: string
  /** null 이면 닫힌 상태 */
  request: QualitySqlPreviewRequest | null
  onClose: () => void
}) {
  return (
    <Dialog open={request != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <QualitySqlPreviewBox request={request} />
      </DialogContent>
    </Dialog>
  )
}
