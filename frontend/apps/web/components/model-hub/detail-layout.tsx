"use client"

import Link from "next/link"
import { ArrowLeft } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { cn } from "@workspace/ui/lib/utils"

/** 상세 화면 상단 "목록으로" 링크. */
export function BackLink({ href, onClick, label = "모델 목록으로" }: { href?: string; onClick?: () => void; label?: string }) {
  const cls = "h-7 -ml-2 self-start px-2 text-muted-foreground hover:text-foreground"
  if (onClick) {
    return (
      <Button variant="ghost" size="sm" className={cls} onClick={onClick}>
        <ArrowLeft className="h-4 w-4 mr-1" />{label}
      </Button>
    )
  }
  return (
    <Button variant="ghost" size="sm" className={cls} asChild>
      <Link href={href ?? "#"}><ArrowLeft className="h-4 w-4 mr-1" />{label}</Link>
    </Button>
  )
}

/** 상세 헤더 — 1줄: 제목·배지·동작, 2줄: 설명(두 줄까지). 나머지 메타는 개요 사이드바로 보낸다. */
export function DetailHeader({
  icon, title, subtitle, badges, actions, description,
}: {
  icon?: React.ReactNode
  title: string
  subtitle?: string | null
  badges?: React.ReactNode
  actions?: React.ReactNode
  description?: string | null
}) {
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        {icon}
        <h1 className="text-xl font-bold leading-tight">{title}</h1>
        {subtitle && <span className="font-mono text-xs text-muted-foreground">{subtitle}</span>}
        {badges}
        {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>
      {description && <p className="max-w-4xl text-sm text-muted-foreground line-clamp-2" title={description}>{description}</p>}
    </div>
  )
}

/** 개요 사이드바 카드. */
export function SideCard({ title, action, children, className }: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <Card className={cn("gap-3 py-4", className)}>
      <CardHeader className="flex flex-row items-center justify-between px-4 pb-0">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {action}
      </CardHeader>
      <CardContent className="px-4">{children}</CardContent>
    </Card>
  )
}

/** 사이드바 키-값 목록 — 값이 비어 있는 항목은 건너뛴다. */
export function FactList({ items }: { items: [string, React.ReactNode][] }) {
  const shown = items.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
  if (shown.length === 0) return <p className="text-sm text-muted-foreground">정보가 없습니다.</p>
  return (
    <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-2 text-sm">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** 비어 있는 섹션 안내 (아이콘 + 문장 + 선택 동작). */
export function EmptyState({ icon: Icon, text, action }: {
  icon?: React.ComponentType<{ className?: string }>
  text: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">
      {Icon && <Icon className="h-8 w-8 text-muted-foreground/40" />}
      <div>{text}</div>
      {action}
    </div>
  )
}

/** 탭 공통 순서·이름 — MLflow 와 OCI 상세가 같은 체계를 쓴다. */
export const DETAIL_TAB_LABELS = {
  overview: "개요",
  versions: "버전",
  performance: "성능",
  files: "파일",
  lineage: "리니지",
  usage: "사용법",
  activity: "활동",
} as const
