"use client"

/**
 * 사용 에이전트 탭 — 에이전트별 리소스 범위 편집 (Phase 3).
 *
 * 인스턴스 승인 범위 위에 이 에이전트에게만 추가로 적용할 규칙이다(좁히기만). 저장하면 정책 번들
 * agents.<이름>.resource_scopes 가 바뀌어 ETag 가 달라지고, 서버가 다음 갱신 때 받아 강제(enforce) 모드에서 집행한다.
 */

import { useEffect, useState } from "react"
import { Loader2, Plus, Save } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import { saveMcpAgentScopes } from "../../api"
import type { McpDeploymentAgent, McpScopeInput } from "../../data/schema"
import { OriginBadge } from "../mcp-badges"
import { ScopeRow } from "./policy-panel"

export function AgentScopeDialog({ name, agent, onOpenChange, onSaved }: {
  name: string
  agent: McpDeploymentAgent | null
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const [scopes, setScopes] = useState<McpScopeInput[]>([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setScopes((agent?.resource_scopes ?? []).map((s) => ({
      resource_type: s.resource_type, action: s.action, effect: s.effect, patterns: s.patterns, match: s.match,
      description: s.description,
    })))
  }, [agent])

  const save = async () => {
    if (!agent) return
    const valid = scopes.filter((s) => s.resource_type.trim() && s.patterns.length)
    if (valid.length !== scopes.length) {
      toast.error("리소스 유형과 패턴이 비어 있는 규칙이 있습니다")
      return
    }
    setSaving(true)
    try {
      await saveMcpAgentScopes(name, agent.link_id, valid.map((s) => ({ ...s, resource_type: s.resource_type.trim() })))
      toast.success("에이전트 리소스 범위를 저장했습니다")
      onSaved()
      onOpenChange(false)
    } catch (e) {
      notifyError(e, "에이전트 리소스 범위를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={agent !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            에이전트 리소스 범위 — {agent?.agent_display_name || agent?.agent_name} <OriginBadge origin="argus" />
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          배포의 승인 리소스 범위 위에 이 에이전트에게만 추가로 적용되는 규칙입니다. 서버는 인스턴스 범위와 에이전트 범위를
          <b> 둘 다</b> 통과해야 허용합니다(protect &gt; deny &gt; allow 목록). allow 규칙을 두면 그 목록 밖은 거부됩니다.
          저장하면 정책 번들이 바뀌어 서버가 다음 갱신 때 받아 강제(enforce) 모드에서 집행합니다. policy_version 은 바뀌지 않습니다.
        </p>
        <div className="max-h-[50vh] space-y-2 overflow-y-auto">
          {scopes.length === 0 && (
            <p className="py-4 text-center text-sm text-muted-foreground">규칙이 없습니다 — 인스턴스 범위만 적용됩니다.</p>
          )}
          {scopes.map((s, i) => (
            <ScopeRow
              key={i}
              value={s}
              onChange={(v) => setScopes(scopes.map((x, j) => (j === i ? v : x)))}
              onRemove={() => setScopes(scopes.filter((_, j) => j !== i))}
            />
          ))}
        </div>
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setScopes([...scopes, { resource_type: "", action: "any", effect: "allow", patterns: [] }])}
          >
            <Plus className="mr-1 h-4 w-4" /> 규칙 추가
          </Button>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>취소</Button>
          <Button onClick={save} disabled={saving}>
            {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />} 저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
