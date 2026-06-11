"use client"

import { useCallback, useEffect, useState } from "react"
import { Brain, Check, Eye, EyeOff, Loader2, Play, RefreshCw, Save, Trash2, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"

import {
  backfillEmbeddings, clearEmbeddings,
  fetchEmbeddingConfig, fetchEmbeddingStats,
  testEmbedding, updateEmbeddingConfig,
  type EmbeddingStats,
} from "./api"

const PROVIDER_MODELS: Record<string, { label: string; models: { id: string; label: string; dim: number }[] }> = {
  local: {
    label: "Local (sentence-transformers)",
    models: [
      { id: "all-MiniLM-L6-v2", label: "all-MiniLM-L6-v2 (English, 80MB)", dim: 384 },
      { id: "paraphrase-multilingual-MiniLM-L12-v2", label: "multilingual-MiniLM-L12-v2 (Korean+English, 470MB)", dim: 384 },
      { id: "bge-small-en-v1.5", label: "bge-small-en-v1.5 (English, 130MB)", dim: 384 },
    ],
  },
  openai: {
    label: "OpenAI API",
    models: [
      { id: "text-embedding-3-small", label: "text-embedding-3-small (1536d)", dim: 1536 },
      { id: "text-embedding-3-large", label: "text-embedding-3-large (3072d)", dim: 3072 },
      { id: "text-embedding-ada-002", label: "text-embedding-ada-002 (1536d)", dim: 1536 },
    ],
  },
  ollama: {
    label: "Ollama (Local API)",
    models: [
      { id: "all-minilm", label: "all-minilm (384d)", dim: 384 },
      { id: "nomic-embed-text", label: "nomic-embed-text (768d)", dim: 768 },
      { id: "mxbai-embed-large", label: "mxbai-embed-large (1024d)", dim: 1024 },
    ],
  },
}

export function EmbeddingSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [backfilling, setBackfilling] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const [enabled, setEnabled] = useState(false)
  const [autoOnWrite, setAutoOnWrite] = useState(false)
  const [provider, setProvider] = useState("local")
  const [model, setModel] = useState("all-MiniLM-L6-v2")
  const [apiKey, setApiKey] = useState("")
  const [apiUrl, setApiUrl] = useState("")
  const [dimension, setDimension] = useState(384)
  const [showApiKey, setShowApiKey] = useState(false)

  const [stats, setStats] = useState<EmbeddingStats | null>(null)

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const [cfg, st] = await Promise.all([
        fetchEmbeddingConfig(),
        fetchEmbeddingStats().catch(() => null),
      ])
      setEnabled(cfg.enabled)
      setAutoOnWrite(cfg.auto_on_write)
      setProvider(cfg.provider)
      setModel(cfg.model)
      setApiKey(cfg.api_key)
      setApiUrl(cfg.api_url)
      setDimension(cfg.dimension)
      setStats(st)
    } catch {
      setMessage({ type: "error", text: "임베딩 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleProviderChange = (val: string) => {
    setProvider(val)
    const models = PROVIDER_MODELS[val]?.models
    if (models?.length) {
      setModel(models[0]!.id)
      setDimension(models[0]!.dim)
    }
  }

  const handleModelChange = (val: string) => {
    setModel(val)
    const m = PROVIDER_MODELS[provider]?.models.find((m) => m.id === val)
    if (m) setDimension(m.dim)
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateEmbeddingConfig({ enabled, auto_on_write: autoOnWrite, provider, model, api_key: apiKey, api_url: apiUrl, dimension })
      setMessage({ type: "success", text: "임베딩 설정을 저장했습니다." })
      const st = await fetchEmbeddingStats().catch(() => null)
      setStats(st)
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "저장에 실패했습니다." })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setMessage(null)
    try {
      const result = await testEmbedding({ enabled, auto_on_write: autoOnWrite, provider, model, api_key: apiKey, api_url: apiUrl, dimension })
      setMessage({ type: result.success ? "success" : "error", text: result.message })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "연결 테스트에 실패했습니다." })
    } finally {
      setTesting(false)
    }
  }

  const handleBackfill = async () => {
    setBackfilling(true)
    setMessage(null)
    try {
      const result = await backfillEmbeddings()
      setMessage({
        type: "success",
        text: `백필 완료: ${result.embedded}건 임베딩, ${result.skipped}건 건너뜀, ${result.errors}건 오류 (총 ${result.total}건)`,
      })
      const st = await fetchEmbeddingStats().catch(() => null)
      setStats(st)
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "백필에 실패했습니다." })
    } finally {
      setBackfilling(false)
    }
  }

  const handleClear = async () => {
    if (!confirm("모든 임베딩을 삭제할까요? 다시 사용하려면 백필을 실행해야 합니다.")) return
    setMessage(null)
    try {
      const result = await clearEmbeddings()
      setMessage({ type: "success", text: `${result.deleted}개 임베딩을 삭제했습니다.` })
      const st = await fetchEmbeddingStats().catch(() => null)
      setStats(st)
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "삭제에 실패했습니다." })
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
    <div className="space-y-4 max-w-2xl">
      {/* Status message */}
      {message && (
        <div className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
          message.type === "success" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-red-50 text-red-700 border border-red-200"
        }`}>
          {message.type === "success" ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
          {message.text}
        </div>
      )}

      {/* Embedding Coverage Stats */}
      {stats && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Brain className="h-4 w-4" /> 임베딩 상태
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-6 text-sm">
              <div>
                <span className="text-muted-foreground">커버리지: </span>
                <span className="font-semibold">{stats.embedded_datasets} / {stats.total_datasets}</span>
                <Badge variant="outline" className="ml-2">{stats.coverage_pct}%</Badge>
              </div>
              {stats.embedded_entities && (
                <div>
                  <span className="text-muted-foreground">엔티티: </span>
                  <span className="font-semibold">
                    용어집 {stats.embedded_entities.glossary_term} · Agent {stats.embedded_entities.ai_agent} · API {stats.embedded_entities.api}
                  </span>
                </div>
              )}
              {stats.provider && (
                <div>
                  <span className="text-muted-foreground">제공자: </span>
                  <span className="font-semibold">{stats.provider} / {stats.model}</span>
                  <span className="text-muted-foreground ml-1">({stats.dimension}차원)</span>
                </div>
              )}
            </div>
            {stats.embedded_datasets < stats.total_datasets && stats.total_datasets > 0 && (
              <div className="mt-2">
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-emerald-500 rounded-full transition-all"
                    style={{ width: `${stats.coverage_pct}%` }}
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">임베딩 제공자</CardTitle>
          <CardDescription>
            카탈로그 데이터셋 시맨틱 검색에 사용할 임베딩 제공자를 설정합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Enable toggle */}
          <div className="flex items-center justify-between">
            <div>
              <Label>시맨틱 검색 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                활성화하면 임베딩 제공자가 초기화되어 시맨틱 검색(쿼리 임베딩)·백필에 사용됩니다.
              </p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>

          {/* Auto embed-on-write toggle */}
          <div className="flex items-center justify-between">
            <div>
              <Label>쓰기 시 자동 임베딩</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                데이터셋 생성/수정/동기화마다 자동으로 임베딩합니다. 끄면 아래 백필로만 생성됩니다
                (대량 동기화 시 모델 추론 부하 회피 — 기본 꺼짐).
              </p>
            </div>
            <Switch checked={autoOnWrite} onCheckedChange={setAutoOnWrite} disabled={!enabled} />
          </div>

          {/* Provider */}
          <div className="space-y-1.5">
            <Label>제공자</Label>
            <Select value={provider} onValueChange={handleProviderChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(PROVIDER_MODELS).map(([key, val]) => (
                  <SelectItem key={key} value={key}>{val.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Model */}
          <div className="space-y-1.5">
            <Label>모델</Label>
            <Select value={model} onValueChange={handleModelChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDER_MODELS[provider]?.models.map((m) => (
                  <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              차원: {dimension}. 직접 모델명을 입력할 수도 있습니다.
            </p>
          </div>

          {/* API Key (OpenAI only) */}
          {provider === "openai" && (
            <div className="space-y-1.5">
              <Label>API Key</Label>
              <div className="relative">
                <Input
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-..."
                  className="pr-10"
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"
                  onClick={() => setShowApiKey(!showApiKey)}
                >
                  {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
          )}

          {/* API URL (OpenAI/Ollama) */}
          {(provider === "openai" || provider === "ollama") && (
            <div className="space-y-1.5">
              <Label>API URL {provider === "ollama" ? "(Ollama 엔드포인트)" : "(선택적 재정의)"}</Label>
              <Input
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder={provider === "ollama" ? "http://localhost:11434" : "https://api.openai.com/v1"}
              />
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 pt-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
              저장
            </Button>
            <Button variant="outline" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
              연결 테스트
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Embedding Management */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">임베딩 관리</CardTitle>
          <CardDescription>
            시맨틱 검색용 데이터셋 임베딩을 관리합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={handleBackfill} disabled={backfilling || !enabled}>
              {backfilling ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
              {backfilling ? "백필 중..." : "전체 데이터셋 백필"}
            </Button>
            <Button variant="outline" onClick={handleClear} className="text-red-600 hover:text-red-700">
              <Trash2 className="h-4 w-4 mr-1" />
              모든 임베딩 삭제
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            백필은 아직 임베딩이 없는 모든 데이터셋에 대해 임베딩을 생성합니다.
            삭제는 모든 임베딩을 제거합니다(차원이 다른 제공자로 전환할 때 필요).
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
