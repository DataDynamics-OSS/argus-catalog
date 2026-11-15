import type { ReactNode } from "react"

import { Button } from "@workspace/ui/components/button"

/** 표가 비었을 때의 한 줄 — 불러오는 중 · 실패(다시 시도) · 없음을 구분한다. */
export function EmptyRow({ colSpan, loading, error, onRetry, children }: {
  colSpan: number; loading: boolean; error: string | null; onRetry?: () => void; children: ReactNode
}) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3 py-8 text-center">
        {error ? (
          <span className="flex flex-col items-center gap-2">
            <span className="text-destructive">불러오지 못했습니다: {error}</span>
            {onRetry && <Button variant="outline" size="sm" onClick={onRetry}>다시 시도</Button>}
          </span>
        ) : loading ? "불러오는 중…" : children}
      </td>
    </tr>
  )
}

/** 목록은 있는데 최근 새로고침이 실패했을 때 — 보이는 내용이 예전 것임을 알린다. */
export function StaleNote({ error, show }: { error: string | null; show: boolean }) {
  if (!error || !show) return null
  return (
    <p className="text-xs text-destructive" role="status">
      새로고침하지 못했습니다 — 마지막으로 읽은 내용입니다. ({error})
    </p>
  )
}
