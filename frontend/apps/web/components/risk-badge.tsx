import { ShieldAlert } from "lucide-react"

import { StatusBadge } from "@workspace/ui/components/status-badge"

import { riskMeta } from "@/lib/risk"

/** 위험도 배지(TODO-107) — MCP · AI 에이전트 공용. 값이 없으면 `empty` 를 흐린 글자로. */
export function RiskBadge({
  risk,
  empty = "-",
  size,
  className,
}: {
  risk: string | null | undefined
  empty?: string
  size?: "default" | "sm"
  className?: string
}) {
  if (!risk) return <span className="text-xs text-muted-foreground">{empty}</span>
  return (
    <StatusBadge
      {...riskMeta(risk)}
      icon={risk === "critical" ? ShieldAlert : undefined}
      size={size}
      className={className}
    />
  )
}
