import { StatusBadge } from "@workspace/ui/components/status-badge"

import { SEVERITY_LABEL, severityTone } from "@/lib/severity"

/** 심각도 배지(DEF-116) — 알림 · 품질 규칙 · 품질 지표 공용. 치명 빨강 · 경고 주황 · 정보 테두리. */
export function SeverityBadge({ severity, size, className }: { severity: string | null | undefined; size?: "default" | "sm"; className?: string }) {
  return <StatusBadge tone={severityTone(severity)} size={size} label={SEVERITY_LABEL[severity ?? ""] ?? severity ?? "-"} className={className} />
}
