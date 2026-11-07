"use client"

// 목록 필터 줄 — 저장된 보기, 상태·부서·소유자·태그·위험도·관리 방식·책임자 상태·정보 부족, 정렬.
// 질의는 provider 의 applyQuery 로 적용한다(백엔드 GET /ai-agents 질의와 같은 이름).
// 설계: design/ai-agent-ownership-design.md §4

import { useCallback, useEffect, useRef, useState } from "react"
import { Bookmark, Save, Trash2, X } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Checkbox } from "@workspace/ui/components/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

import { useAuth } from "@/features/auth"
import {
  cleanQuery,
  deleteAgentView,
  fetchAgentViews,
  saveAgentView,
  type AgentListQuery,
  type AgentView,
} from "../api"
import { AGENT_STATUS_LABEL, MANAGED_BY_LABEL, OWNER_STATE_LABEL, RISK_LABEL } from "../data/schema"
import { useAIAgents } from "./ai-agents-provider"

const ALL = "__all__"
const SORT_LABEL: Record<string, string> = {
  updated: "최근 갱신 순",
  name: "이름 순",
  usage: "호출 많은 순",
  last_invoked: "최근 호출 순",
  completeness: "완성도 낮은 순",
  "-completeness": "완성도 높은 순",
  risk: "위험도 높은 순",
}

/** 입력이 멈추면(0.5초) 적용하는 짧은 입력 칸. */
function FilterInput({
  value,
  placeholder,
  onCommit,
  className,
}: {
  value: string
  placeholder: string
  onCommit: (v: string) => void
  className?: string
}) {
  const [text, setText] = useState(value)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => setText(value), [value])
  return (
    <Input
      value={text}
      placeholder={placeholder}
      onChange={(e) => {
        const v = e.target.value
        setText(v)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => onCommit(v.trim()), 500)
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.nativeEvent.isComposing) {
          if (timer.current) clearTimeout(timer.current)
          onCommit(text.trim())
        }
      }}
      className={className ?? "h-8 w-[140px] text-xs"}
    />
  )
}

function SmallSelect({
  value,
  onChange,
  placeholder,
  options,
  width = "w-[130px]",
}: {
  value: string | undefined
  onChange: (v: string | undefined) => void
  placeholder: string
  options: [string, string][]
  width?: string
}) {
  return (
    <Select value={value ?? ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger size="sm" className={`${width} text-xs`}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{placeholder}</SelectItem>
        {options.map(([v, label]) => (
          <SelectItem key={v} value={v}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function AIAgentsFilters() {
  const { user } = useAuth()
  const { query, applyQuery, activeViewId } = useAIAgents()
  const [views, setViews] = useState<AgentView[]>([])
  const [saveOpen, setSaveOpen] = useState(false)

  const loadViews = useCallback(() => {
    fetchAgentViews().then(setViews).catch(() => setViews([]))
  }, [])
  useEffect(() => loadViews(), [loadViews, user?.sub])

  const set = (patch: Partial<AgentListQuery>) => applyQuery({ ...query, ...patch })
  const active = views.find((v) => v.id === activeViewId) ?? null
  const hasFilter = Object.keys(cleanQuery({ ...query, search: undefined })).length > 0
  const groups: [string, AgentView[]][] = [
    ["기본 보기", views.filter((v) => v.kind === "builtin")],
    ["내 보기", views.filter((v) => v.kind !== "builtin" && v.editable && v.kind === "personal")],
    ["공유 보기", views.filter((v) => v.kind === "shared")],
  ]

  async function removeActive() {
    if (!active) return
    try {
      await deleteAgentView(active.id)
      toast.success(`'${active.name}' 보기를 삭제했습니다.`)
      applyQuery(query, null)
      loadViews()
    } catch (e) {
      toast.error(errorMessage(e) ?? String(e))
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Bookmark className="h-4 w-4 text-muted-foreground" />
        <Select
          value={activeViewId ?? ALL}
          onValueChange={(id) => {
            if (id === ALL) return applyQuery({}, null)
            const v = views.find((x) => x.id === id)
            if (v) applyQuery(v.query, v.id)
          }}
        >
          <SelectTrigger size="sm" className="w-[200px] text-xs">
            <SelectValue placeholder="보기 선택" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>전체(조건 없음)</SelectItem>
            {groups.map(([label, items]) =>
              items.length ? (
                <SelectGroup key={label}>
                  <SelectSeparator />
                  <SelectLabel>{label}</SelectLabel>
                  {items.map((v) => (
                    <SelectItem key={v.id} value={v.id}>
                      {v.name}
                      {v.kind === "shared" && v.owner_name ? ` · ${v.owner_name}` : ""}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ) : null,
            )}
          </SelectContent>
        </Select>
        {user && (
          <Button size="sm" variant="ghost" className="h-8 text-xs" disabled={!hasFilter} onClick={() => setSaveOpen(true)}>
            <Save className="mr-1 h-3.5 w-3.5" /> {active?.editable ? "보기 고치기" : "현재 조건 저장"}
          </Button>
        )}
        {active?.editable && (
          <Button size="sm" variant="ghost" className="h-8 text-xs text-destructive" onClick={removeActive}>
            <Trash2 className="mr-1 h-3.5 w-3.5" /> 보기 삭제
          </Button>
        )}
        {(hasFilter || query.search) && (
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => applyQuery({}, null)}>
            <X className="mr-1 h-3.5 w-3.5" /> 조건 초기화
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SmallSelect
          value={query.status}
          onChange={(v) => set({ status: v })}
          placeholder="전체 상태"
          options={Object.entries(AGENT_STATUS_LABEL)}
        />
        <FilterInput value={query.department ?? ""} placeholder="부서(쉼표로 여러 개)" onCommit={(v) => set({ department: v })} />
        <div className="flex items-center gap-1">
          <FilterInput
            value={query.owner === "me" ? "" : (query.owner ?? "")}
            placeholder="소유자 이메일"
            onCommit={(v) => set({ owner: v })}
          />
          {user && (
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              <Checkbox checked={query.owner === "me"} onCheckedChange={(c) => set({ owner: c ? "me" : undefined })} />
              내 것
            </label>
          )}
        </div>
        <FilterInput value={query.tag ?? ""} placeholder="태그(쉼표, 모두 포함)" onCommit={(v) => set({ tag: v })} />
        <SmallSelect
          value={query.risk}
          onChange={(v) => set({ risk: v as AgentListQuery["risk"] })}
          placeholder="위험도 전체"
          options={Object.entries(RISK_LABEL).map(([k, l]) => [k, `${l} 이상`])}
        />
        <SmallSelect
          value={query.managed_by}
          onChange={(v) => set({ managed_by: v })}
          placeholder="관리 방식 전체"
          options={Object.entries(MANAGED_BY_LABEL)}
        />
        <SmallSelect
          value={query.owner_state}
          onChange={(v) => set({ owner_state: v })}
          placeholder="책임자 전체"
          width="w-[150px]"
          options={[["attention", "확인 필요(전부)"], ...Object.entries(OWNER_STATE_LABEL)]}
        />
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <Checkbox checked={!!query.incomplete} onCheckedChange={(c) => set({ incomplete: !!c })} />
          정보 부족만
        </label>
        <SmallSelect
          value={query.sort}
          onChange={(v) => set({ sort: v })}
          placeholder="최근 갱신 순"
          width="w-[140px]"
          options={Object.entries(SORT_LABEL).filter(([k]) => k !== "updated")}
        />
      </div>

      <SaveViewDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        editing={active?.editable ? active : null}
        query={query}
        onSaved={(v) => {
          loadViews()
          applyQuery(v.query, v.id)
        }}
      />
    </div>
  )
}

function SaveViewDialog({
  open,
  onOpenChange,
  editing,
  query,
  onSaved,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  editing: AgentView | null
  query: AgentListQuery
  onSaved: (v: AgentView) => void
}) {
  const [name, setName] = useState("")
  const [shared, setShared] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(editing?.name ?? "")
    setShared(editing?.kind === "shared")
  }, [open, editing])

  const summary = Object.entries(cleanQuery(query))
    .map(([k, v]) => `${k}=${v}`)
    .join(", ")

  async function save() {
    setBusy(true)
    try {
      const v = await saveAgentView({ name: name.trim(), shared, query }, editing?.id)
      toast.success(`'${v.name}' 보기를 저장했습니다.`)
      onOpenChange(false)
      onSaved(v)
    } catch (e) {
      toast.error(errorMessage(e) ?? String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{editing ? "보기 고치기" : "현재 조건을 보기로 저장"}</DialogTitle>
          <DialogDescription>지금 목록의 필터·정렬을 이름을 붙여 저장합니다. 검색어도 함께 저장됩니다.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="view-name">이름</Label>
            <Input id="view-name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={shared} onCheckedChange={(c) => setShared(!!c)} />
            모두에게 공유(만든 사람과 관리자만 고칠 수 있음)
          </label>
          <p className="break-all rounded-md bg-muted/50 px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
            {summary || "조건 없음"}
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            취소
          </Button>
          <Button disabled={!name.trim() || busy} onClick={save}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
