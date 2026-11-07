"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2, Save, Trash2, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Switch } from "@workspace/ui/components/switch"

import { useConfirm } from "@/components/confirm-provider"
import { authFetch } from "@/features/auth/auth-fetch"
import { notifyError, notifySuccess } from "@/lib/notify"

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

interface CacheConfig {
  max_size: number
  ttl_seconds: number
  enabled: boolean
  current_size: number
}

interface CacheStats {
  size: number
  max_size: number
  ttl_seconds: number
  hits: number
  misses: number
  hit_rate: number
  total_requests: number
}

async function fetchCacheConfig(): Promise<CacheConfig> {
  const res = await authFetch("/api/v1/external/cache/config")
  if (!res.ok) throw new Error("캐시 설정을 불러오지 못했습니다.")
  return res.json()
}

async function updateCacheConfig(data: { max_size: number; ttl_seconds: number; enabled: boolean }): Promise<CacheConfig> {
  const res = await authFetch("/api/v1/external/cache/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error("캐시 설정을 저장하지 못했습니다.")
  return res.json()
}

async function fetchCacheStats(): Promise<CacheStats> {
  const res = await authFetch("/api/v1/external/cache/stats")
  if (!res.ok) throw new Error("캐시 통계를 불러오지 못했습니다.")
  return res.json()
}

async function clearCache(): Promise<{ cleared: number }> {
  const res = await authFetch("/api/v1/external/cache", { method: "DELETE" })
  if (!res.ok) throw new Error("캐시를 비우지 못했습니다.")
  return res.json()
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CacheSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const askConfirm = useConfirm()

  const [enabled, setEnabled] = useState(true)
  const [maxSize, setMaxSize] = useState(1000)
  const [ttl, setTtl] = useState(300)

  const [stats, setStats] = useState<CacheStats | null>(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      setLoadError(null)
      const [cfg, st] = await Promise.all([
        fetchCacheConfig(),
        fetchCacheStats().catch(() => null),
      ])
      setEnabled(cfg.enabled)
      setMaxSize(cfg.max_size)
      setTtl(cfg.ttl_seconds)
      setStats(st)
    } catch {
      setLoadError("캐시 설정을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateCacheConfig({ max_size: maxSize, ttl_seconds: ttl, enabled })
      notifySuccess("캐시 설정을 저장했습니다.")
      const st = await fetchCacheStats().catch(() => null)
      setStats(st)
    } catch (e) {
      notifyError(e, "캐시 설정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const handleClear = async () => {
    if (!(await askConfirm({
      title: "캐시 비우기",
      description: "캐시된 모든 메타데이터를 비울까요? 캐시가 다시 채워질 때까지 후속 요청이 느려집니다.",
      confirmText: "비우기",
      destructive: true,
    }))) return
    setClearing(true)
    try {
      const result = await clearCache()
      notifySuccess(`캐시를 비웠습니다. (${result.cleared}개 항목 제거)`)
      const st = await fetchCacheStats().catch(() => null)
      setStats(st)
    } catch (e) {
      notifyError(e, "캐시를 비우지 못했습니다.")
    } finally {
      setClearing(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 캐시 설정을 불러오는 중...
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {/* Message */}
      {loadError && (
        <div className="flex items-center gap-2 rounded-md px-3 py-2 text-sm bg-red-50 text-red-700 border border-red-200">
          <X className="h-4 w-4" />
          {loadError}
        </div>
      )}

      {/* Configuration Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">외부 메타데이터 캐시</CardTitle>
          <CardDescription>
            외부 시스템용 메타데이터 API의 캐시 설정입니다. 캐시를 사용하면 반복 요청의 응답 시간이 대폭 향상됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Enabled toggle */}
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-sm font-medium">캐시 활성화</Label>
              <p className="text-xs text-muted-foreground">비활성화하면 모든 요청이 DB에서 직접 조회됩니다</p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>

          {/* Max size */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="max-size" className="text-sm">최대 크기 (항목 수)</Label>
              <Input
                id="max-size"
                type="number"
                min={10}
                max={100000}
                value={maxSize}
                onChange={(e) => setMaxSize(Number(e.target.value))}
                className="h-9"
              />
              <p className="text-xs text-muted-foreground">
                최대 캐시 항목 수. 초과 시 가장 오래된 항목이 LRU 방식으로 제거됩니다.
              </p>
            </div>

            {/* TTL */}
            <div className="space-y-2">
              <Label htmlFor="ttl" className="text-sm">TTL (초)</Label>
              <Input
                id="ttl"
                type="number"
                min={10}
                max={86400}
                value={ttl}
                onChange={(e) => setTtl(Number(e.target.value))}
                className="h-9"
              />
              <p className="text-xs text-muted-foreground">
                캐시 항목 만료 시간. {ttl >= 60 ? `${Math.floor(ttl / 60)}분` : `${ttl}초`} 후 자동 갱신됩니다.
              </p>
            </div>
          </div>

          {/* Save button */}
          <div className="flex gap-2 pt-2">
            <Button onClick={handleSave} disabled={saving} size="sm">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              저장
            </Button>
            <Button onClick={handleClear} disabled={clearing} variant="outline" size="sm">
              {clearing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
              캐시 비우기
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Statistics Card */}
      {stats && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">캐시 통계</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatItem label="현재 크기" value={`${stats.size} / ${stats.max_size}`} />
              <StatItem label="적중률" value={`${stats.hit_rate}%`} highlight={stats.hit_rate >= 80} />
              <StatItem label="적중" value={stats.hits.toLocaleString()} />
              <StatItem label="미적중" value={stats.misses.toLocaleString()} />
            </div>
            <div className="mt-3 text-xs text-muted-foreground">
              총 요청: {stats.total_requests.toLocaleString()}건 · TTL: {stats.ttl_seconds}초
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function StatItem({ label, value, highlight }: { label: string; value: string | number; highlight?: boolean }) {
  return (
    <div className="text-center">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={`text-lg font-semibold ${highlight ? "text-green-600" : ""}`}>{value}</div>
    </div>
  )
}
