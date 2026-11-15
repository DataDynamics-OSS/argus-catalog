import { StatusBadge } from "@workspace/ui/components/status-badge"

/** 데이터를 바꾸는 MCP 도구 · 도구 묶음 표시 — 주의 톤 작은 배지(TODO-107). `children` 이 없으면 "변경". */
export function MutatingBadge({ children }: { children?: React.ReactNode }) {
  return (
    <StatusBadge tone="warning" size="sm" title="데이터를 바꾸는 도구">
      {children ?? "변경"}
    </StatusBadge>
  )
}
