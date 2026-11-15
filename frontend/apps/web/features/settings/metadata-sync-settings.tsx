"use client"

import { useCallback, useEffect, useState } from "react"
import { Activity, Check, Loader2, Play, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"

import { fetchMetadataSyncConfig, testMetadataSync, updateMetadataSyncConfig } from "./api"
import { errorMessage, notifyError, notifySuccess } from "@/lib/notify"

/**
 * 수집 서비스(metadata-sync) 접속 설정 — 데이터 소스 연결 테스트·원본 탐색·수집 작업·Impala 프로파일이 이 주소로 간다.
 * 저장하면 다음 요청부터 적용된다(재시작 불필요). API 토큰은 보여 주지 않고, 비워 두면 기존 값을 유지한다.
 */
export function MetadataSyncSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const [baseUrl, setBaseUrl] = useState("")
  const [token, setToken] = useState("")
  const [tokenSet, setTokenSet] = useState(false)
  const [clearToken, setClearToken] = useState(false)
  const [retention, setRetention] = useState("90")

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const cfg = await fetchMetadataSyncConfig()
      setBaseUrl(cfg.base_url)
      setTokenSet(cfg.api_token_set)
      setRetention(String(cfg.run_retention_days))
      setToken("")
      setClearToken(false)
    } catch (e) {
      setMessage({ type: "error", text: errorMessage(e) ?? "수집 서비스 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const days = Number(retention)
  const urlValid = /^https?:\/\/\S+$/.test(baseUrl.trim())
  const daysValid = Number.isInteger(days) && days >= 1 && days <= 3650
  // 입력한 토큰 > 지우기 > 그대로(undefined)
  const tokenPayload = token.trim() ? token.trim() : clearToken ? "" : undefined

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateMetadataSyncConfig({ base_url: baseUrl.trim(), api_token: tokenPayload, run_retention_days: days })
      notifySuccess("수집 서비스 설정을 저장했습니다.")
      await load()
    } catch (e) {
      notifyError(e, "수집 서비스 설정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setMessage(null)
    try {
      const r = await testMetadataSync({ base_url: baseUrl.trim(), api_token: tokenPayload })
      setMessage({ type: r.success ? "success" : "error", text: r.message })
    } catch (e) {
      setMessage({ type: "error", text: errorMessage(e) ?? "연결을 테스트하지 못했습니다." })
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="max-w-2xl space-y-4">
      {message && (
        <div className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
          message.type === "success" ? "border border-emerald-200 bg-emerald-50 text-emerald-700" : "border border-red-200 bg-red-50 text-red-700"
        }`}>
          {message.type === "success" ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {message.text}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4" /> 수집 서비스 (metadata-sync)
          </CardTitle>
          <CardDescription>
            Hive·Iceberg REST·Trino·Kudu·StarRocks·Greenplum 의 연결 테스트, 원본 탐색, 백그라운드 수집과
            Impala 프로파일 분석이 이 서비스로 갑니다. 저장하면 다음 요청부터 적용됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="ms-url">서비스 주소</Label>
            <Input id="ms-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:4610" />
            {!urlValid && <p className="text-xs text-destructive">http:// 또는 https:// 로 시작하는 주소를 입력하세요.</p>}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ms-token">API 토큰</Label>
            <Input id="ms-token" type="password" value={token} autoComplete="new-password"
              onChange={(e) => { setToken(e.target.value); setClearToken(false) }}
              placeholder={tokenSet && !clearToken ? "설정됨 — 바꾸려면 새 값을 입력" : "수집 서비스의 server.api_token 과 같은 값 (없으면 비움)"} />
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>{tokenSet ? (clearToken ? "저장하면 토큰을 지웁니다." : "토큰이 설정되어 있습니다.") : "토큰이 없습니다."}</span>
              {tokenSet && !clearToken && !token && (
                <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => setClearToken(true)}>
                  토큰 지우기
                </Button>
              )}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ms-retention">수집 실행 이력 보관 기간</Label>
            <div className="flex items-center gap-2">
              <Input id="ms-retention" type="number" min={1} max={3650} value={retention}
                onChange={(e) => setRetention(e.target.value)} className="w-28" />
              <span className="text-sm">일 — 지난 실행과 실패 항목은 1시간마다 정리합니다.</span>
            </div>
            {!daysValid && <p className="text-xs text-destructive">1~3650 사이의 정수를 입력하세요.</p>}
          </div>

          <p className="text-xs">
            반대 방향(수집 서비스 → 카탈로그 진행 보고)의 카탈로그 주소와 계정은 수집 서비스의 설정 파일
            (<code>catalog.base_url</code>, <code>ARGUS_CATALOG_USERNAME</code>/<code>PASSWORD</code>)에서 정합니다.
          </p>

          <div className="flex gap-2">
            <Button variant="outline" onClick={handleTest} disabled={testing || !urlValid}>
              {testing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />}
              연결 테스트
            </Button>
            <Button onClick={handleSave} disabled={saving || !urlValid || !daysValid}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
              저장
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
