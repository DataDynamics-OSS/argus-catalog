"use client"

import { useRouter } from "next/navigation"
import { useCallback, useEffect, useState } from "react"
import { Bell, Brain, Search } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { Separator } from "@workspace/ui/components/separator"
import { SidebarTrigger } from "@workspace/ui/components/sidebar"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import { authFetch } from "@/features/auth/auth-fetch"

type AlertSummary = {
  total_open: number
  breaking_count: number
  warning_count: number
  info_count: number
}

type AlertItem = {
  id: number
  severity: string
  change_summary: string
  source_dataset_name: string | null
  source_datasource_type: string | null
  affected_dataset_name: string | null
  affected_datasource_type: string | null
  created_at: string
}

interface DashboardHeaderProps {
  title: string
}

export function DashboardHeader({ title }: DashboardHeaderProps) {
  const router = useRouter()
  const [query, setQuery] = useState("")
  const [summary, setSummary] = useState<AlertSummary | null>(null)
  const [recentAlerts, setRecentAlerts] = useState<AlertItem[]>([])
  const [popoverOpen, setPopoverOpen] = useState(false)

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && query.trim()) {
      router.push(`/dashboard/search?q=${encodeURIComponent(query.trim())}`)
    }
  }

  // Fetch alert summary periodically
  const fetchSummary = useCallback(async () => {
    try {
      const resp = await authFetch("/api/v1/alerts/summary")
      if (resp.ok) setSummary(await resp.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    fetchSummary()
    const interval = setInterval(fetchSummary, 30000) // every 30s
    return () => clearInterval(interval)
  }, [fetchSummary])

  // Fetch recent alerts when popover opens
  useEffect(() => {
    if (!popoverOpen) return
    authFetch("/api/v1/alerts?status=OPEN&page_size=5")
      .then(r => r.json())
      .then(data => setRecentAlerts(data.items || []))
      .catch(() => {})
  }, [popoverOpen])

  const totalOpen = summary?.total_open ?? 0

  return (
    <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center gap-2 border-b bg-background px-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <div className="flex flex-1 items-center gap-2">
        <h1 className="text-xl font-semibold leading-none">{title}</h1>
      </div>
      <div className="flex items-center gap-2 ml-auto">
        {/* Notification Bell */}
        <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="relative h-9 w-9">
              <Bell className="h-4 w-4" />
              {totalOpen > 0 && (
                <span className={`absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-sm font-bold text-white ${
                  (summary?.breaking_count ?? 0) > 0
                    ? "bg-red-500"
                    : "bg-amber-500"
                }`}>
                  {totalOpen > 99 ? "99+" : totalOpen}
                </span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-[460px] p-0" align="end">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-sm font-medium">알림</span>
              <div className="flex items-center gap-1.5">
                {(summary?.breaking_count ?? 0) > 0 && (
                  <Badge className="bg-red-500 text-white text-sm px-1.5 py-0 border-0">
                    치명 {summary!.breaking_count}
                  </Badge>
                )}
                {(summary?.warning_count ?? 0) > 0 && (
                  <Badge className="bg-amber-500 text-white text-sm px-1.5 py-0 border-0">
                    경고 {summary!.warning_count}
                  </Badge>
                )}
              </div>
            </div>

            <div className="max-h-80 overflow-y-auto">
              {recentAlerts.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                  열려 있는 알림이 없습니다
                </div>
              ) : (
                recentAlerts.map(alert => (
                  <button
                    key={alert.id}
                    type="button"
                    onClick={() => {
                      setPopoverOpen(false)
                      router.push("/dashboard/alerts")
                    }}
                    className="w-full text-left px-4 py-3 border-b last:border-b-0 hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex items-start gap-2">
                      <SeverityDot severity={alert.severity} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">
                          {alert.change_summary}
                        </p>
                        <p className="text-sm text-muted-foreground mt-0.5">
                          {alert.source_datasource_type && `${alert.source_datasource_type}`}
                          {alert.source_dataset_name && `.${alert.source_dataset_name}`}
                          {alert.affected_dataset_name && ` → ${alert.affected_datasource_type}.${alert.affected_dataset_name}`}
                        </p>
                        <p className="text-sm text-muted-foreground mt-0.5">
                          {formatTimeAgo(alert.created_at)}
                        </p>
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>

            {totalOpen > 0 && (
              <div className="border-t px-4 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full text-sm"
                  onClick={() => {
                    setPopoverOpen(false)
                    router.push("/dashboard/alerts")
                  }}
                >
                  전체 알림 보기
                </Button>
              </div>
            )}
          </PopoverContent>
        </Popover>

        {/* Search */}
        <div className="relative hidden md:block">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            type="search"
            placeholder="이름·설명·의미로 데이터셋 검색..."
            className="pl-8 w-96 h-9 text-sm"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="hidden md:flex items-center">
              <Brain className="h-4 w-4 text-purple-500" />
            </div>
          </TooltipTrigger>
          <TooltipContent>하이브리드 검색 (키워드 + 의미 기반)</TooltipContent>
        </Tooltip>
      </div>
    </header>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SeverityDot({ severity }: { severity: string }) {
  const color = severity === "BREAKING"
    ? "bg-red-500"
    : severity === "WARNING"
      ? "bg-amber-500"
      : "bg-blue-400"

  return <span className={`mt-1.5 flex-shrink-0 w-2 h-2 rounded-full ${color}`} />
}

function formatTimeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "방금 전"
  if (mins < 60) return `${mins}분 전`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.floor(hours / 24)
  return `${days}일 전`
}
