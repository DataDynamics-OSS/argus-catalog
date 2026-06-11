"use client"

import { useCallback, useEffect, useState } from "react"
import {
  Check, Database, GitBranch, Loader2, MessageCircle, Play, Save,
  Search, Shield, Sparkles, Table2, X,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Switch } from "@workspace/ui/components/switch"

import {
  fetchAssistantConfig, testAssistant, updateAssistantConfig,
} from "./api"

// 어시스턴트가 제공하는 기능 — 설명 카드에 노출.
const CAPABILITIES: { icon: React.ElementType; title: string; desc: string }[] = [
  { icon: Search, title: "데이터셋 검색", desc: "자연어로 테이블·데이터셋을 시맨틱 검색 — \"고객 테이블 뭐 있어?\"" },
  { icon: Table2, title: "스키마·상세 조회", desc: "컬럼 타입·PK·PII·태그·행 수 등 데이터셋 상세를 근거로 설명" },
  { icon: GitBranch, title: "ERD·리니지", desc: "FK 조인 경로와 데이터 출처/영향 범위를 추적 — SQL 조인 근거" },
  { icon: Shield, title: "품질·표준", desc: "품질 점수·실패 규칙, 표준 용어 준수율을 조회해 답변" },
  { icon: Database, title: "SQL 작성·검증", desc: "스키마·조인 근거로 SELECT 문을 작성하고 자가 검증 (실행은 안 함)" },
]

export function AssistantSettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const [enabled, setEnabled] = useState(false)
  const [agentUrl, setAgentUrl] = useState("")

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const cfg = await fetchAssistantConfig()
      setEnabled(cfg.enabled)
      setAgentUrl(cfg.agent_url)
    } catch {
      setMessage({ type: "error", text: "어시스턴트 설정을 불러오지 못했습니다." })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await updateAssistantConfig({ enabled, agent_url: agentUrl })
      setMessage({ type: "success", text: "AI 어시스턴트 설정을 저장했습니다." })
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
      const result = await testAssistant({ enabled, agent_url: agentUrl })
      setMessage({ type: result.success ? "success" : "error", text: result.message })
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "연결 테스트에 실패했습니다." })
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

      {/* 기능 설명 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> AI 어시스턴트란?
          </CardTitle>
          <CardDescription>
            화면 우하단 플로팅 챗으로 동작하는 대화형 도우미입니다. LLM 이 카탈로그
            도구를 직접 호출해 <strong>실데이터 근거</strong>로 답합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {CAPABILITIES.map((c) => (
              <div key={c.title} className="flex items-start gap-2.5">
                <div className="mt-0.5 h-7 w-7 shrink-0 rounded-md bg-muted flex items-center justify-center">
                  <c.icon className="h-4 w-4 text-muted-foreground" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium leading-tight">{c.title}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{c.desc}</p>
                </div>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground border-t pt-3">
            tool-use 답변은 별도의 <strong>에이전트 서버(agent/ serve 모드)</strong>가 필요합니다.
            아래에서 활성화하고 에이전트 URL 을 지정하세요. 비활성화하면 도구 없이
            동작하는 <strong>내장 단순 대화</strong>로 폴백합니다.
          </p>
        </CardContent>
      </Card>

      {/* 설정 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <MessageCircle className="h-4 w-4" /> 에이전트 연결
          </CardTitle>
          <CardDescription>
            AI 어시스턴트 에이전트(agent/ serve)의 주소를 지정합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Enable toggle */}
          <div className="flex items-center justify-between">
            <div>
              <Label>에이전트 기반 어시스턴트 활성화</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                켜면 채팅이 아래 에이전트로 프록시되어 도구 기반 답변을 제공합니다.
              </p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>

          {/* Agent URL */}
          <div className="space-y-1.5">
            <Label>에이전트 URL</Label>
            <Input
              value={agentUrl}
              onChange={(e) => setAgentUrl(e.target.value)}
              placeholder="http://localhost:8930"
            />
            <p className="text-xs text-muted-foreground">
              agent serve 모드 서버 주소. 예) <code>http://localhost:8930</code> —
              에이전트 기동: <code>argus-agent serve --port 8930</code>
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 pt-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
              저장
            </Button>
            <Button variant="outline" onClick={handleTest} disabled={testing || !agentUrl.trim()}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
              연결 테스트
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">
            ※ 플로팅 챗 버튼은 <strong>LLM / AI</strong> 탭에서 LLM 제공자가 활성화돼
            있어야 표시됩니다.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
