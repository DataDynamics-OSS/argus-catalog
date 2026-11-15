import type { ReactNode } from "react"
import { cn } from "@workspace/ui/lib/utils"

/**
 * 키-값 표 — 왼쪽 열(키)은 회색 배경, 오른쪽 열은 값. 테두리 한 겹, 줄 사이 구분선.
 * 값이 비어 있는(null · undefined · "" · false) 항목은 건너뛴다. 모두 비면 아무것도 그리지 않는다.
 * 사이드바처럼 가벼운 목록은 `FactList`(components/model-hub/detail-layout.tsx)를 쓴다.
 */
export function KeyValueTable({
  items,
  keyWidth = "8rem",
  className,
}: {
  items: [string, ReactNode][]
  /** 키 열 너비(CSS 값) */
  keyWidth?: string
  className?: string
}) {
  const shown = items.filter(
    ([, v]) => v !== null && v !== undefined && v !== "" && v !== false
  )
  if (shown.length === 0) return null
  return (
    <div className={cn("overflow-hidden rounded-md border", className)}>
      <table className="w-full table-fixed border-collapse text-sm">
        <colgroup>
          <col style={{ width: keyWidth }} />
          <col />
        </colgroup>
        <tbody>
          {shown.map(([k, v]) => (
            <tr key={k} className="border-b last:border-b-0">
              <th
                scope="row"
                className="bg-muted px-3 py-2 text-left align-top font-medium"
              >
                {k}
              </th>
              <td className="min-w-0 px-3 py-2 align-top break-words">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
