"use client"

// AI 에이전트 상세의 '스킬'(A2A AgentSkill, TODO-024)·'데이터 접근'(TODO-019) 카드와,
// 데이터셋·데이터 소스·MLflow 모델 상세에 붙는 "이 자원을 쓰는 에이전트" 패널(역방향 조회).

import { Tip } from "@/components/tip"
import { useEffect, useState } from "react"
import { Bot, Loader2, Plus, ShieldAlert, Trash2 } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge, type StatusTone } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { DetailSection } from "@/components/detail-section"
import { usePermissions } from "@/features/permissions/use-permissions"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Textarea } from "@workspace/ui/components/textarea"

import { errorMessage, notifyError } from "@/lib/notify"

import {
  addAIAgentDataAccess,
  addAIAgentSkill,
  deleteAIAgentDataAccess,
  deleteAIAgentSkill,
  fetchAgentResourceUsage,
  type AgentResourceUsage,
  type AIAgentDetail,
  type DataAccessMode,
} from "../api"
import { TagInput } from "@/components/tag-input"

// ---------------------------------------------------------------------------
// 표시 이름
// ---------------------------------------------------------------------------

export const RISK_TIER_LABEL: Record<string, string> = {
  minimal: "최소",
  limited: "제한",
  high: "고위험",
  prohibited: "금지",
}
/** AI 위험 등급 톤(TODO-107) — 최소 무채색 · 제한 정보 · 고위험 주의 · 금지 위험 */
export const RISK_TIER_TONE: Record<string, StatusTone> = {
  minimal: "neutral", limited: "info", high: "warning", prohibited: "danger",
}
export const IMPACT_RESULT_LABEL: Record<string, string> = {
  passed: "통과",
  conditional: "조건부 통과",
  failed: "미통과",
  pending: "진행 중",
}
export const AUTONOMY_LABEL: Record<string, string> = {
  advisory: "제안만",
  supervised: "사람 승인 후 실행",
  autonomous: "자율 실행",
}
export const IDENTITY_TYPE_LABEL: Record<string, string> = {
  service_account: "서비스 계정",
  oauth_client: "OAuth 클라이언트",
  workload: "워크로드 신원",
  user_delegated: "사용자 위임",
}
export const IDENTITY_STATE_LABEL: Record<string, string> = {
  ok: "사용자 목록에 있음(활성)",
  unknown: "사용자 목록에 없음",
  inactive: "비활성 계정",
}
export const ACCESS_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  read_write: "읽기·쓰기",
}
const VIA_LABEL: Record<string, string> = {
  data_access: "데이터 접근",
  datasource: "데이터 소스 전체 접근",
  base_model: "기반 모델",
  lineage: "리니지",
}

export function RiskTierBadge({ tier }: { tier: string | null | undefined }) {
  if (!tier) return <span className="text-muted-foreground">-</span>
  return (
    <StatusBadge tone={RISK_TIER_TONE[tier] ?? "neutral"} label={RISK_TIER_LABEL[tier] ?? tier} />
  )
}

export const agentHref = (name: string) => `/dashboard/ai-agents?agent=${encodeURIComponent(name)}`
export const datasetHref = (id: number) => `/dashboard/datasets/${id}`
export const modelHref = (name: string) => `/dashboard/models/${encodeURIComponent(name)}`


// ---------------------------------------------------------------------------
// 스킬 (A2A AgentSkill)
// ---------------------------------------------------------------------------

export function AgentSkillsCard({
  agent,
  canEdit,
  onChanged,
}: {
  agent: AIAgentDetail
  canEdit: boolean
  onChanged: () => Promise<void>
}) {
  const modes = (v: string[] | null) => (v && v.length > 0 ? v.join(", ") : "기본값")
  return (
    <DetailSection
      title={<>스킬 ({agent.skills.length})</>}
      actions={canEdit && <AddSkillDialog agentName={agent.name} onAdded={onChanged} />}
      description={<>호출하는 쪽에 알리는 &lsquo;이 에이전트가 할 수 있는 일&rsquo;입니다(A2A 카드의 skills). 에이전트가 안에서 부르는
          도구(도구·통합 탭)와 다릅니다. 스킬이 없으면 카드는 위 &lsquo;수행 기능&rsquo; 목록으로 스킬을 만듭니다.</>}
    >
        <div className="rounded-md border"><Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">ID</TableHead>
              <TableHead>이름·설명</TableHead>
              <TableHead>예시 요청</TableHead>
              <TableHead className="w-48">입력 → 출력</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {agent.skills.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  등록된 스킬 없음
                </TableCell>
              </TableRow>
            ) : (
              agent.skills.map((sk) => (
                <TableRow key={sk.id}>
                  <TableCell className="font-mono text-xs break-all">{sk.skill_key}</TableCell>
                  <TableCell>
                    <div className="font-medium">{sk.name}</div>
                    {sk.description && <div className="text-xs">{sk.description}</div>}
                    {(sk.tags?.length ?? 0) > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {sk.tags!.map((t) => (
                          <Badge key={t} variant="secondary">{t}</Badge>
                        ))}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {(sk.examples?.length ?? 0) > 0 ? (
                      <ul className="space-y-0.5">
                        {sk.examples!.map((e) => <li key={e}>· {e}</li>)}
                      </ul>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {modes(sk.input_modes)} → {modes(sk.output_modes)}
                  </TableCell>
                  <TableCell>
                    {canEdit && (
                      <Tip label="스킬 삭제"><Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="스킬 삭제"
                        onClick={async () => {
                          try {
                            await deleteAIAgentSkill(agent.name, sk.id)
                            await onChanged()
                          } catch (e) {
                            notifyError(e, "스킬을 삭제하지 못했습니다.")
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button></Tip>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table></div>
    </DetailSection>
  )
}

function AddSkillDialog({ agentName, onAdded }: { agentName: string; onAdded: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState("")
  const [name, setName] = useState("")
  const [desc, setDesc] = useState("")
  const [tags, setTags] = useState<string[]>([])
  const [examples, setExamples] = useState("")
  const [inModes, setInModes] = useState<string[]>([])
  const [outModes, setOutModes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const reset = () => {
    setKey(""); setName(""); setDesc(""); setTags([]); setExamples(""); setInModes([]); setOutModes([]); setErr(null)
  }

  const submit = async () => {
    setErr(null)
    setBusy(true)
    try {
      await addAIAgentSkill(agentName, {
        skill_key: key.trim() || undefined,
        name: name.trim(),
        description: desc.trim() || null,
        tags,
        // 예시 요청은 쉼표가 들어갈 수 있어 줄 단위로 받는다
        examples: examples.split("\n").map((t) => t.trim()).filter(Boolean),
        input_modes: inModes,
        output_modes: outModes,
      })
      reset()
      setOpen(false)
      await onAdded()
    } catch (e) {
      setErr(errorMessage(e) ?? "스킬을 등록하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) reset() }}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline"><Plus className="mr-1 h-4 w-4" /> 스킬 추가</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>스킬 추가</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>이름</Label>
              <Input placeholder="예: 환불 가능 여부 판단" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>ID (선택)</Label>
              <Input placeholder="비우면 이름에서 만듦" value={key} onChange={(e) => setKey(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>설명</Label>
            <Input placeholder="호출하는 쪽이 읽을 설명" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>태그</Label>
            <TagInput placeholder="예: 환불 입력 후 Enter" value={tags} onChange={setTags} />
          </div>
          <div className="space-y-1.5">
            <Label>예시 요청 (한 줄에 하나)</Label>
            <Textarea
              placeholder={"이 주문 환불되나요?\n지난주 결제 취소해 주세요"}
              value={examples}
              onChange={(e) => setExamples(e.target.value)}
              className="min-h-20 text-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>입력 형식 (MIME, 선택)</Label>
              <TagInput mono placeholder="비우면 에이전트 기본값" value={inModes} onChange={setInModes} />
            </div>
            <div className="space-y-1.5">
              <Label>출력 형식 (MIME, 선택)</Label>
              <TagInput mono placeholder="예: application/json" value={outModes} onChange={setOutModes} />
            </div>
          </div>
          {err && <p className="text-sm text-destructive">{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>취소</Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// 데이터 접근
// ---------------------------------------------------------------------------

export function AgentDataAccessCard({
  agent,
  canEdit,
  onChanged,
}: {
  agent: AIAgentDetail
  canEdit: boolean
  onChanged: () => Promise<void>
}) {
  const [targetType, setTargetType] = useState<"dataset" | "datasource">("dataset")
  const [ref, setRef] = useState("")
  const [access, setAccess] = useState<DataAccessMode>("read")
  const [busy, setBusy] = useState(false)

  const add = async () => {
    setBusy(true)
    try {
      await addAIAgentDataAccess(agent.name, {
        ...(targetType === "dataset" ? { dataset_urn: ref.trim() } : { datasource_name: ref.trim() }),
        access,
      })
      setRef("")
      await onChanged()
    } catch (e) {
      notifyError(e, "데이터 접근을 추가하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <DetailSection
      title={<>데이터 접근 ({agent.data_access.length})</>}
      description={<>에이전트가 읽거나 쓰는 카탈로그 데이터입니다. 데이터셋·데이터 소스 상세에 &lsquo;이 자원을 쓰는 에이전트&rsquo;로
          표시되고, 정책 번들(집행 탭)에 실려 실행 계층이 이 밖의 접근을 막는 기준이 됩니다.</>}
    >
        <div className="rounded-md border"><Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">대상 유형</TableHead>
              <TableHead>대상</TableHead>
              <TableHead className="w-24">접근</TableHead>
              <TableHead className="w-24">개인정보</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {agent.data_access.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  등록된 데이터 접근 없음
                </TableCell>
              </TableRow>
            ) : (
              agent.data_access.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <Badge variant="secondary">{d.target_type === "dataset" ? "데이터셋" : "데이터 소스"}</Badge>
                  </TableCell>
                  <TableCell>
                    {d.target_type === "dataset" && d.dataset_id ? (
                      <a href={datasetHref(d.dataset_id)} className="font-medium text-primary hover:underline">
                        {d.target_name ?? d.target_urn}
                      </a>
                    ) : (
                      <span className="font-medium">{d.target_name ?? "-"}</span>
                    )}
                    <div className="font-mono text-xs break-all">
                      {d.target_urn}
                      {d.datasource_name && ` · ${d.datasource_name}`}
                    </div>
                    {d.description && <div className="text-xs">{d.description}</div>}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{ACCESS_LABEL[d.access] ?? d.access}</Badge>
                  </TableCell>
                  <TableCell>
                    {d.contains_pii ? (
                      <StatusBadge tone="warning" icon={ShieldAlert}>포함</StatusBadge>
                    ) : d.contains_pii === false ? (
                      <span className="text-xs text-muted-foreground">없음</span>
                    ) : (
                      <span className="text-xs text-muted-foreground" title="데이터 소스 전체 접근은 판단하지 않습니다">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {canEdit && (
                      <Tip label="데이터 접근 삭제"><Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="데이터 접근 삭제"
                        onClick={async () => {
                          try {
                            await deleteAIAgentDataAccess(agent.name, d.id)
                            await onChanged()
                          } catch (e) {
                            notifyError(e, "데이터 접근을 삭제하지 못했습니다.")
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button></Tip>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table></div>
        {canEdit && (
          <div className="flex items-end gap-2 pt-2">
            <Select value={targetType} onValueChange={(v) => setTargetType(v as "dataset" | "datasource")}>
              <SelectTrigger size="sm" className="w-[120px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="dataset">데이터셋</SelectItem>
                <SelectItem value="datasource">데이터 소스</SelectItem>
              </SelectContent>
            </Select>
            <Input
              placeholder={targetType === "dataset" ? "데이터셋 URN" : "데이터 소스 이름 또는 식별 ID"}
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              className="h-8"
            />
            <Select value={access} onValueChange={(v) => setAccess(v as DataAccessMode)}>
              <SelectTrigger size="sm" className="w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(ACCESS_LABEL).map(([v, l]) => (
                  <SelectItem key={v} value={v}>{l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Tip label="데이터 접근 추가"><Button size="sm" disabled={busy || !ref.trim()} onClick={add} aria-label="데이터 접근 추가">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            </Button></Tip>
          </div>
        )}
    </DetailSection>
  )
}

// ---------------------------------------------------------------------------
// 역방향 — 이 자원을 쓰는 에이전트 (데이터셋·데이터 소스·MLflow 모델 상세)
// ---------------------------------------------------------------------------

export function AgentUsagePanel({
  target,
}: {
  target: { dataset_id: number } | { datasource_id: number } | { model_id: number }
}) {
  const [rows, setRows] = useState<AgentResourceUsage[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = JSON.stringify(target)
  // AI Agent 메뉴가 막힌 역할은 /ai-agents/usage 가 403 이다 — 부르지 않고 패널을 숨긴다 (DEF-160)
  const { loaded: permsLoaded, isMenuAllowed } = usePermissions()
  const allowed = permsLoaded && isMenuAllowed("ai-agents")

  useEffect(() => {
    if (!allowed) return
    let active = true
    setRows(null)
    setError(null)
    fetchAgentResourceUsage(JSON.parse(key))
      .then((r) => active && setRows(r))
      .catch((e) => active && setError(errorMessage(e) ?? "이 자원을 쓰는 에이전트를 불러오지 못했습니다."))
    return () => {
      active = false
    }
  }, [key, allowed])

  if (!allowed) return null

  return (
    <DetailSection
      title={<>이 자원을 쓰는 AI 에이전트{rows && ` (${rows.length})`}</>}
      icon={<Bot />}
      description="AI 에이전트의 데이터 접근·기반 모델·리니지에 이 자원이 등록된 경우입니다."
    >
        {error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : rows === null ? (
          <p className="text-sm text-muted-foreground">불러오는 중…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">이 자원을 쓰는 에이전트가 없습니다.</p>
        ) : (
          <div className="rounded-md border"><Table>
            <TableHeader>
              <TableRow>
                <TableHead>에이전트</TableHead>
                <TableHead className="w-40">연결</TableHead>
                <TableHead className="w-28">관계</TableHead>
                <TableHead className="w-48">소유자</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((u, i) => (
                <TableRow key={`${u.agent_id}-${u.via}-${i}`}>
                  <TableCell>
                    <a href={agentHref(u.agent_name)} className="font-medium text-primary hover:underline">
                      {u.display_name || u.agent_name}
                    </a>
                    <div className="font-mono text-xs">{u.agent_name} · {u.status}</div>
                    {u.description && <div className="text-xs">{u.description}</div>}
                  </TableCell>
                  <TableCell className="text-xs">{VIA_LABEL[u.via] ?? u.via}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{ACCESS_LABEL[u.relation] ?? u.relation}</Badge>
                  </TableCell>
                  <TableCell className="text-xs">{u.owner_email ?? "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table></div>
        )}
    </DetailSection>
  )
}
