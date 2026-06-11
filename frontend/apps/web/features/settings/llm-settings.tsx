"use client"

import { useCallback, useEffect, useState } from "react"
import { Bot, Check, Eye, EyeOff, Loader2, Play, Save, Sparkles, X } from "lucide-react"

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
  bulkGenerate, fetchAIStats, fetchLLMConfig, testLLM, updateLLMConfig,
  type AIStats,
} from "./api"

const PROVIDER_MODELS: Record<string, { label: string; models: { id: string; label: string }[] }> = {
  openai: {
    label: "OpenAI API",
    models: [
      { id: "gpt-4o-mini", label: "gpt-4o-mini (fast, low cost)" },
      { id: "gpt-4o", label: "gpt-4o (high quality)" },
      { id: "gpt-4.1-mini", label: "gpt-4.1-mini (latest)" },
    ],
  },
  ollama: {
    label: "Ollama (Local LLM)",
    models: [
      { id: "qwen2.5:7b", label: "qwen2.5:7b (Korean recommended)" },
      { id: "qwen2.5:14b", label: "qwen2.5:14b (Korean high quality)" },
      { id: "llama3.1:8b", label: "llama3.1:8b (English)" },
      { id: "gemma2:9b", label: "gemma2:9b (Multilingual)" },
    ],
  },
  anthropic: {
    label: "Anthropic (Claude)",
    models: [
      { id: "claude-sonnet-4-20250514", label: "Claude Sonnet 4 (balanced)" },
      { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (fast)" },
    ],
  },
}

const LANGUAGES = [
  { id: "ko", label: "한국어" },
  { id: "en", label: "English" },
  { id: "ja", label: "日本語" },
  { id: "zh", label: "中文" },
]

export function LLMSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const [enabled, setEnabled] = useState(false)
  const [provider, setProvider] = useState("ollama")
  const [model, setModel] = useState("qwen2.5:7b")
  const [apiKey, setApiKey] = useState("")
  const [apiUrl, setApiUrl] = useState("")
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState(1024)
  const [autoGenerateOnSync, setAutoGenerateOnSync] = useState(false)
  const [language, setLanguage] = useState("ko")
  const [showApiKey, setShowApiKey] = useState(false)

  const [stats, setStats] = useState<AIStats | null>(null)

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const [cfg, st] = await Promise.all([
        fetchLLMConfig(),
        fetchAIStats().catch(() => null),
      ])
      setEnabled(cfg.enabled)
      setProvider(cfg.provider)
      setModel(cfg.model)
      setApiKey(cfg.api_key)
      setApiUrl(cfg.api_url)
      setTemperature(cfg.temperature)
      setMaxTokens(cfg.max_tokens)
      setAutoGenerateOnSync(cfg.auto_generate_on_sync)
      setLanguage(cfg.language)
      setStats(st)
    } catch {
      setMessage({ type: "error", text: "LLM 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleProviderChange = (val: string) => {
    setProvider(val)
    const models = PROVIDER_MODELS[val]?.models
    if (models?.length) setModel(models[0]!.id)
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateLLMConfig({
        enabled, provider, model, api_key: apiKey, api_url: apiUrl,
        temperature, max_tokens: maxTokens,
        auto_generate_on_sync: autoGenerateOnSync, language,
      })
      setMessage({ type: "success", text: "LLM 설정을 저장했습니다." })
      const st = await fetchAIStats().catch(() => null)
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
      const result = await testLLM({
        enabled, provider, model, api_key: apiKey, api_url: apiUrl,
        temperature, max_tokens: maxTokens,
        auto_generate_on_sync: autoGenerateOnSync, language,
      })
      setMessage({ type: result.success ? "success" : "error", text: result.message })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "연결 테스트에 실패했습니다." })
    } finally {
      setTesting(false)
    }
  }

  const handleBulkGenerate = async (types: string[]) => {
    setGenerating(true)
    setMessage(null)
    try {
      const result = await bulkGenerate({
        generation_types: types,
        apply: true,
        empty_only: true,
      })
      setMessage({
        type: "success",
        text: `일괄 생성 완료: ${result.processed}건 처리, ${result.errors}건 오류 (총 ${result.total}건)`,
      })
      const st = await fetchAIStats().catch(() => null)
      setStats(st)
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "일괄 생성에 실패했습니다." })
    } finally {
      setGenerating(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const coverage = stats?.description_coverage

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

      {/* AI Coverage Stats */}
      {stats && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Bot className="h-4 w-4" /> AI 메타데이터 상태
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-6 text-sm flex-wrap">
              {coverage && (
                <div>
                  <span className="text-muted-foreground">설명 커버리지: </span>
                  <span className="font-semibold">{coverage.described_datasets} / {coverage.total_datasets}</span>
                  <Badge variant="outline" className="ml-2">{coverage.coverage_pct}%</Badge>
                </div>
              )}
              {stats.provider && (
                <div>
                  <span className="text-muted-foreground">제공자: </span>
                  <span className="font-semibold">{stats.provider} / {stats.model}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-6 text-sm flex-wrap">
              <div>
                <span className="text-muted-foreground">생성 건수: </span>
                <span className="font-semibold">{stats.total_generations}</span>
                <span className="text-muted-foreground ml-2">({stats.applied_count}건 적용, {stats.pending_count}건 대기)</span>
              </div>
              <div>
                <span className="text-muted-foreground">토큰: </span>
                <span className="font-semibold">{(stats.total_prompt_tokens + stats.total_completion_tokens).toLocaleString()}</span>
              </div>
            </div>
            {coverage && coverage.described_datasets < coverage.total_datasets && coverage.total_datasets > 0 && (
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald-500 rounded-full transition-all"
                  style={{ width: `${coverage.coverage_pct}%` }}
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">LLM 제공자</CardTitle>
          <CardDescription>
            AI 기반 메타데이터 생성(설명, 태그, PII 감지)에 사용할 LLM 제공자를 설정합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Enable toggle */}
          <div className="flex items-center justify-between">
            <div>
              <Label>AI 메타데이터 생성 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                활성화하면 AI 가 설명을 생성하고 태그를 제안하며 PII 를 감지할 수 있습니다.
              </p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
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
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDER_MODELS[provider]?.models.map((m) => (
                  <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* API Key (OpenAI/Anthropic) */}
          {(provider === "openai" || provider === "anthropic") && (
            <div className="space-y-1.5">
              <Label>API Key</Label>
              <div className="relative">
                <Input
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={provider === "openai" ? "sk-..." : "sk-ant-..."}
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

          {/* API URL */}
          <div className="space-y-1.5">
            <Label>API URL {provider === "ollama" ? "(Ollama 엔드포인트)" : "(선택적 재정의)"}</Label>
            <Input
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder={
                provider === "ollama" ? "http://localhost:11434" :
                provider === "anthropic" ? "https://api.anthropic.com" :
                "https://api.openai.com/v1"
              }
            />
          </div>

          {/* Language */}
          <div className="space-y-1.5">
            <Label>생성 언어</Label>
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGES.map((l) => (
                  <SelectItem key={l.id} value={l.id}>{l.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Temperature */}
          <div className="space-y-1.5">
            <Label>Temperature ({temperature})</Label>
            <Input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
              className="h-2"
            />
            <p className="text-xs text-muted-foreground">
              낮을수록 사실 중심, 높을수록 창의적. 메타데이터에는 0.3 권장.
            </p>
          </div>

          {/* Max Tokens */}
          <div className="space-y-1.5">
            <Label>최대 토큰 수</Label>
            <Input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(parseInt(e.target.value) || 1024)}
              min={256}
              max={8192}
            />
          </div>

          {/* Auto-generate on sync */}
          <div className="flex items-center justify-between">
            <div>
              <Label>동기화 시 자동 생성</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                메타데이터 동기화 후 새로 추가된 데이터셋에 대해 자동으로 설명을 생성합니다.
              </p>
            </div>
            <Switch checked={autoGenerateOnSync} onCheckedChange={setAutoGenerateOnSync} />
          </div>

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

      {/* Bulk Generation */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">AI 일괄 생성</CardTitle>
          <CardDescription>
            설명이 비어 있는 데이터셋에 대해 메타데이터를 일괄 생성합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="outline"
              onClick={() => handleBulkGenerate(["description"])}
              disabled={generating || !enabled}
            >
              {generating ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Sparkles className="h-4 w-4 mr-1" />}
              설명 생성
            </Button>
            <Button
              variant="outline"
              onClick={() => handleBulkGenerate(["description", "columns", "tags", "pii"])}
              disabled={generating || !enabled}
            >
              {generating ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Sparkles className="h-4 w-4 mr-1" />}
              전체 생성 (설명 + 컬럼 + 태그 + PII)
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            설명이 비어 있는 데이터셋만 처리되며, 결과는 즉시 적용됩니다.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
