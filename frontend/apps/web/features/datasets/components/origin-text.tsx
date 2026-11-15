import { cn } from "@workspace/ui/lib/utils"
import { originLabel, originMeta } from "@/features/datasets/origin"

/** 환경 — 색 점 + "운영 (PROD)" */
export function OriginText({ origin, className }: { origin: string | null | undefined; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", originMeta(origin).bg)} />
      {originLabel(origin)}
    </span>
  )
}
