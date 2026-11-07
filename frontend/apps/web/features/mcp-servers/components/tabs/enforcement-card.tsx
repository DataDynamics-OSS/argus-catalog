"use client"

/**
 * 승인 정책 탭 — 정책 집행(enforce) 상태 카드 (Phase 3).
 *
 * 서버(Forge mcp-common catalog_policy)가 정책 번들을 pull 해 도구 호출 직전에 집행한다.
 * 이 카드는 번들 적용 상태(마지막 pull·버전·인스턴스)와 번들 미리보기, 서버 설정 예시를 보여 준다.
 */

import { useState } from "react"
import { AlertTriangle, Braces, CheckCircle2, Copy, FileCode2, Loader2, ShieldAlert } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { cn } from "@workspace/ui/lib/utils"

import { fetchMcpPolicyBundle } from "../../api"
import { PULL_STATE_LABEL, type McpDeploymentPolicy, type McpPolicyBundle } from "../../data/schema"
import { OriginBadge, fmtDate } from "../mcp-badges"
import { JsonBlock } from "../mcp-server-detail"

const STATE_TONE: Record<string, string> = {
  applied: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  outdated: "border-amber-300 text-amber-700 dark:text-amber-300",
  stale: "border-red-300 text-red-700 dark:text-red-400",
  never: "border-red-300 text-red-700 dark:text-red-400",
}

export function PullStateBadge({ state }: { state: string | null }) {
  if (!state) return null
  const Icon = state === "applied" ? CheckCircle2 : AlertTriangle
  return (
    <Badge variant="outline" className={cn("gap-1 text-[11px]", STATE_TONE[state])}>
      <Icon className="h-3 w-3" />
      {PULL_STATE_LABEL[state] ?? state}
    </Badge>
  )
}

function forgeSnippet(name: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "https://catalog.example.com"
  return [
    "# MCP 서버 config.yaml (Argus Forge mcp-common)",
    "catalog_policy:",
    "  enabled: true",
    `  url: ${origin}/api/v1`,
    `  deployment: ${name}`,
    "  token_env: ARGUS_CATALOG_TOKEN   # 감사 이벤트 탭에서 발급한 서비스 토큰(mcpi_…)",
    "  refresh_seconds: 60",
    "  fail_mode: open                  # closed: 번들을 못 받으면 변경 도구 거부",
    "",
    "# 서버 실행 환경",
    "export ARGUS_CATALOG_TOKEN=mcpi_…",
  ].join("\n")
}

// 번들 조회(policy-bundle)는 서비스 토큰이 없으면 등록자·관리자만 가능 — 그 밖의 사용자에게는 버튼을 숨긴다.
export function EnforcementCard({ name, policy, mode, canEdit = true }: {
  name: string
  policy: McpDeploymentPolicy
  mode: string
  canEdit?: boolean
}) {
  const [bundle, setBundle] = useState<McpPolicyBundle | null>(null)
  const [bundleOpen, setBundleOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [snippetOpen, setSnippetOpen] = useState(false)
  const enforce = policy.policy_mode === "enforce"
  const switching = mode !== policy.policy_mode

  const openBundle = async () => {
    setBundleOpen(true)
    setLoading(true)
    try {
      setBundle(await fetchMcpPolicyBundle(name))
    } catch (e) {
      notifyError(e, "정책 번들을 불러오지 못했습니다.")
      setBundleOpen(false)
    } finally {
      setLoading(false)
    }
  }

  const snippet = forgeSnippet(name)

  return (
    <Card className={cn(enforce && policy.pull_state !== "applied" && "border-amber-300 dark:border-amber-800")}>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-sm">
            정책 집행 <OriginBadge origin="argus" />
            {enforce ? <PullStateBadge state={policy.pull_state} /> : <Badge variant="outline" className="text-[11px]">관측만</Badge>}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {enforce
              ? "서버가 정책 번들을 주기적으로 받아 도구 호출 직전에 차단 도구·꺼진 그룹·사람 승인 도구·승인 리소스 범위·에이전트별 허용 범위를 집행합니다. 로컬 설정보다 넓힐 수는 없습니다."
              : "관측 모드에서는 서버가 번들을 받아도 집행하지 않습니다. 강제(enforce)로 바꾸면 서버가 다음 갱신(기본 60초) 때부터 집행합니다."}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          {canEdit && (
          <Button size="sm" variant="outline" onClick={openBundle}>
            <Braces className="mr-1 h-4 w-4" /> 번들 미리보기
          </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setSnippetOpen(true)}>
            <FileCode2 className="mr-1 h-4 w-4" /> 서버 설정
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!policy.has_service_token && (
          <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
            <ShieldAlert className="h-3.5 w-3.5" />
            서비스 토큰이 없습니다 — 강제 모드로 바꾸려면 감사 이벤트 탭에서 먼저 발급하세요(감사 수집·정책 pull 공용).
          </p>
        )}
        {switching && mode === "enforce" && (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            저장하면 강제 모드가 됩니다. 서버의 <code>catalog_policy</code> 설정이 켜져 있어야 집행됩니다.
          </p>
        )}
        <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">마지막 pull</dt>
          <dd>{policy.policy_pulled_at ? fmtDate(policy.policy_pulled_at) : "-"}</dd>
          <dt className="text-muted-foreground">받아간 번들</dt>
          <dd className="font-mono">{policy.policy_pulled_version ?? "-"}</dd>
          <dt className="text-muted-foreground">현재 번들</dt>
          <dd className="font-mono">
            {policy.policy_version ?? "-"}@{(policy.bundle_etag ?? "").replaceAll('"', "").slice(0, 8)}
          </dd>
          <dt className="text-muted-foreground">서버 인스턴스</dt>
          <dd className="font-mono">{policy.policy_pull_instance ?? "-"}</dd>
          <dt className="text-muted-foreground">연결 에이전트</dt>
          <dd>{policy.agent_count}개 (에이전트별 허용 그룹·도구·리소스 범위 포함)</dd>
        </dl>
      </CardContent>

      <Dialog open={bundleOpen} onOpenChange={setBundleOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>정책 번들 — {name}</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            서버가 <code>GET /api/v1/mcp-deployments/{name}/policy-bundle</code> 로 받는 문서입니다(미리보기는 pull 기록을 남기지 않음).
          </p>
          {loading || !bundle ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : (
            <div className="max-h-[60vh] overflow-auto"><JsonBlock value={bundle} /></div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={snippetOpen} onOpenChange={setSnippetOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>서버 설정 — 카탈로그 정책 pull</DialogTitle>
          </DialogHeader>
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{snippet}</pre>
          <p className="text-xs text-muted-foreground">
            카탈로그에 닿지 못하면 마지막 번들(<code>run/catalog-policy.json</code>)로 동작합니다. 에이전트별 제한은 세션 토큰의
            <code> agent</code> 클레임 또는 <code>X-Argus-Agent</code> 헤더로 적용됩니다.
          </p>
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(snippet).then(() => toast.success("복사했습니다"))}>
              <Copy className="mr-1 h-4 w-4" /> 복사
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
