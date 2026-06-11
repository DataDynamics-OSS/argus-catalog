"use client"

import { Fragment, useCallback, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import {
  Activity,
  ArrowLeft,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Copy,
  History,
  Layers,
  Loader2,
  Network,
  Pencil,
  Plug,
  Plus,
  Save,
  Share2,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from "lucide-react"
import { toast } from "sonner"
import type { EditorProps, OnMount } from "@monaco-editor/react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
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
import { Switch } from "@workspace/ui/components/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Separator } from "@workspace/ui/components/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@workspace/ui/components/tooltip"
import { cn } from "@workspace/ui/lib/utils"

import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"

import {
  addAIAgentEval,
  addAIAgentLineage,
  addAIAgentMcpServer,
  addAIAgentTool,
  addAIAgentVersion,
  deleteAIAgentLineage,
  deleteAIAgentMcpServer,
  deleteAIAgentTool,
  fetchAIAgentCard,
  fetchAIAgentDetail,
  fetchAIAgentEvals,
  fetchAIAgentHookEvents,
  fetchAIAgentMetering,
  fetchAIAgentPolicy,
  fetchAIAgentStatusHistory,
  updateAIAgent,
  type AIAgentCard,
  type AIAgentDetail,
  type AIAgentEval,
  type AIAgentHookEvent,
  type AIAgentStatusHistory,
  type AIAgentMetering,
  type AIAgentPolicyBundle,
} from "../api"
import { AGENT_STATUS_VARIANTS } from "../data/schema"
import { useAIAgents } from "./ai-agents-provider"

function StringList({ items }: { items: string[] | null }) {
  if (!items || items.length === 0) return <span className="text-muted-foreground">-</span>
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((it) => (
        <Badge key={it} variant="secondary">
          {it}
        </Badge>
      ))}
    </div>
  )
}

// 소유자 표시 — 데이터셋 상세와 동일하게 이름 + hover 툴팁(소속/소속부서/Username/Email).
// owner_email 로 조회한 사용자(user)가 있으면 이름을 보여주고, 없으면 이메일을 그대로 표시.
function AgentOwnerValue({ email, user }: { email: string | null; user: User | null }) {
  if (!email) return <span className="text-muted-foreground">-</span>
  // 데이터셋 상세와 동일한 "이름 (username)" 표기. 사용자 미조회 시 이메일 표시.
  const fullName = user ? `${user.lastName ?? ""}${user.firstName ?? ""}`.trim() : ""
  const displayName = user
    ? fullName
      ? `${fullName} (${user.username})`
      : user.username
    : email
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help font-medium underline decoration-dotted decoration-muted-foreground/50 underline-offset-2">
            {displayName}
          </span>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-sm whitespace-pre-wrap">
          <div className="space-y-0.5">
            <div>소속: {user?.organization || "—"}</div>
            <div>소속부서: {user?.department || "—"}</div>
            <div>Username: {user?.username || "—"}</div>
            <div>Email: {user?.email || email}</div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// SSR 비활성 + 로딩 표시(동적 import). code-viewer 와 동일 패턴.
const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => (
    <div className="flex h-10 items-center justify-center">
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
    </div>
  ),
})

// 읽기 전용 JSON 뷰어 — 스크롤바 없이 내용 높이에 맞춰 표시, JSON 구문 강조.
const JSON_VIEWER_OPTIONS: NonNullable<EditorProps["options"]> = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  // 스크롤바·줄바꿈 — 가로/세로 스크롤바를 숨기고 wordWrap 으로 가로 넘침 방지.
  scrollbar: { vertical: "hidden", horizontal: "hidden", handleMouseWheel: false, alwaysConsumeMouseWheel: false },
  wordWrap: "on",
  lineNumbers: "off",
  folding: false,
  fontSize: 13,
  fontFamily: "D2Coding, Menlo, Consolas, monospace",
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  contextmenu: false,
  guides: { indentation: false },
  automaticLayout: true,
  padding: { top: 6, bottom: 6 },
}

function JsonViewer({ value }: { value: unknown }) {
  const [height, setHeight] = useState(40)
  const [copied, setCopied] = useState(false)
  if (value === null || value === undefined)
    return <span className="text-sm text-muted-foreground">없음</span>

  const json = JSON.stringify(value, null, 2)
  // 내용 높이에 맞춰 컨테이너/에디터 높이를 동기화 → 세로 스크롤바 제거.
  const handleMount: OnMount = (editor) => {
    const update = () => setHeight(Math.max(editor.getContentHeight(), 24))
    update()
    editor.onDidContentSizeChange(update)
  }
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(json)
      setCopied(true)
      toast.success("JSON 을 클립보드에 복사했습니다.")
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("클립보드 복사에 실패했습니다.")
    }
  }

  return (
    <div className="relative overflow-hidden rounded bg-muted/30" style={{ height }}>
      <MonacoEditor
        language="json"
        value={json}
        theme="light"
        height={height}
        options={JSON_VIEWER_OPTIONS}
        onMount={handleMount}
      />
      {/* 클립보드 복사 버튼 — 항상 노출(우상단). */}
      <Button
        type="button"
        variant="secondary"
        size="icon"
        onClick={handleCopy}
        title={copied ? "복사됨" : "복사"}
        aria-label={copied ? "복사됨" : "복사"}
        className="absolute top-1 right-1 z-10 h-7 w-7 shadow-sm"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value ?? "-"}</p>
    </div>
  )
}

// 공용 메타 표 — 그룹 헤더 + 항목/설정값/설명 3컬럼. (개요·기능·I/O·A2A·집행 공통)
// 정보 출처 구분: 사용자 입력 vs 자동 수집(텔레메트리·파생·생성).
// 공간 절약을 위해 텍스트 배지 대신 색상 원(circle)으로 표시 — 파랑=사용자 입력, 초록=자동 수집.
// 의미는 hover 시 title 로 확인.
type Source = "input" | "auto"
function SourceBadge({ source, className }: { source?: Source; className?: string }) {
  if (!source) return null
  const isAuto = source === "auto"
  const label = isAuto ? "자동 수집" : "사용자 입력"
  return (
    <span
      title={label}
      aria-label={label}
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full align-middle",
        isAuto ? "bg-emerald-500" : "bg-sky-500",
        className,
      )}
    />
  )
}

// 선택형 편집 컨트롤 — Select 프리셋 + 맨 위 "미지정", 맨 아래 "사용자 입력"(직접 입력).
const COMBO_UNSET = "__unset__"
const COMBO_CUSTOM = "__custom__"

// 필드별 프리셋 옵션
const OPT_CATEGORY = [
  "고객 지원", "데이터 분석", "개발 지원", "문서 처리", "검색", "추천",
  "콘텐츠 생성", "요약", "번역", "리서치", "자동화/워크플로",
  "보안/거버넌스", "영업/마케팅", "HR", "재무/회계",
]
const OPT_PROVIDER = [
  "OpenAI", "Anthropic", "Google", "Meta", "Mistral", "Cohere",
  "Alibaba Cloud", "DeepSeek", "xAI", "Azure OpenAI", "AWS Bedrock",
  "Google Vertex AI", "Groq", "Together AI", "Fireworks AI",
  "Hugging Face", "NVIDIA", "Databricks", "Ollama",
]
const OPT_MODEL = [
  "gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-7-sonnet", "claude-3-5-haiku",
  "gemini-1.5-pro", "llama-3.1-70b",
  // Qwen 계열
  "qwen2.5-max", "qwen2.5-72b-instruct", "qwen2.5-32b-instruct", "qwen2.5-14b-instruct",
  "qwen2.5-7b-instruct", "qwen2.5-coder-32b-instruct", "qwen2.5-vl-72b-instruct",
  "qwq-32b", "qwen2-72b-instruct",
  // DeepSeek 계열
  "deepseek-v3", "deepseek-r1", "deepseek-r1-distill-qwen-32b", "deepseek-r1-distill-llama-70b",
  "deepseek-chat", "deepseek-reasoner", "deepseek-coder-v2",
  // Mistral 계열
  "mistral-large-2", "mistral-small-3", "mistral-nemo", "mixtral-8x22b", "mixtral-8x7b",
  "ministral-8b", "ministral-3b", "codestral", "pixtral-large",
]
const OPT_FRAMEWORK = [
  "LangChain", "LangGraph", "LlamaIndex", "CrewAI", "AutoGen", "Semantic Kernel",
  "Haystack", "DSPy", "OpenAI Agents SDK", "Google ADK", "Pydantic AI",
  "Agno", "Strands Agents", "AutoGPT", "n8n",
]
// 벤더 중립 실행 전략 taxonomy (프레임워크별 실제 명칭은 다를 수 있음)
const OPT_EXEC = [
  "single_pass",
  "react",
  "plan_and_execute",
  "reflection",
  "tree_search",
  "router",
  "multi_agent",
]
// 벤더 중립 메모리 taxonomy (프레임워크별 실제 명칭은 다를 수 있음)
const OPT_MEMORY = [
  "none",
  "short_term_buffer",
  "sliding_window",
  "summary",
  "long_term_vector",
  "entity",
  "hybrid",
]
// 벤더 중립 통신 프로토콜 taxonomy
const OPT_PROTOCOL = ["https", "http", "http2", "grpc", "websocket", "sse", "amqp", "mqtt", "tcp"]
// 벤더 중립 호출 방식 taxonomy (게이트웨이/프레임워크별 실제 명칭은 다를 수 있음)
const OPT_INVOCATION = [
  "rest",
  "grpc",
  "graphql",
  "websocket",
  "webhook",
  "message_queue",
  "sdk",
  "cli",
  "scheduled",
]
// 벤더 중립 인증 방식 taxonomy (게이트웨이/프레임워크별 실제 명칭은 다를 수 있음)
const OPT_AUTH = [
  "none",
  "api_key",
  "bearer_token",
  "oauth2",
  "jwt",
  "basic",
  "hmac",
  "mtls",
]
const OPT_DEPARTMENT = ["데이터 플랫폼팀", "고객경험팀", "개발생산성팀", "기획팀"]
// 거버넌스 프리셋
const OPT_PII = ["none", "masking", "redaction", "pseudonymization", "anonymization", "tokenization", "encryption"]
const OPT_RESIDENCY = ["한국", "미국", "유럽연합(EU)", "일본", "중국", "동남아시아", "글로벌"]
const OPT_DLP = [
  "resident-id-masking", "card-number-masking", "email-masking", "phone-masking",
  "secret-scan", "credential-masking", "pii-column-masking", "row-level-security",
  "schema-only-no-data",
]

const userDisplayName = (u: User) =>
  `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

// 빈 값은 "-" 로 표시
const dash = (v: unknown) => (v === null || v === undefined || v === "" ? "-" : String(v))

// 에이전트 상태 코드 → 한글 라벨 / 표시 순서
const STATUS_LABEL: Record<string, string> = {
  draft: "초안", staging: "스테이징", active: "활성",
  blocked: "차단", deprecated: "사용 중단", retired: "폐기",
}
const STATUS_ORDER = ["draft", "staging", "active", "blocked", "deprecated", "retired"]

// 프리셋 선택 + "사용자 입력" 시 우측 Input 노출.
function ComboInput({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  options: string[]
  placeholder?: string
}) {
  const [custom, setCustom] = useState(false)
  const isPreset = value !== "" && options.includes(value)
  const showInput = custom || (value !== "" && !isPreset)
  const selectValue = showInput ? COMBO_CUSTOM : value === "" ? COMBO_UNSET : value
  return (
    // w-full + min-w-0 flex 로 자식이 부모 폭 안에서 줄어들게 해 footprint 를 일정하게 유지.
    <div className="flex w-full items-center gap-2">
      <Select
        value={selectValue}
        onValueChange={(v) => {
          if (v === COMBO_CUSTOM) setCustom(true)
          else if (v === COMBO_UNSET) { setCustom(false); onChange("") }
          else { setCustom(false); onChange(v) }
        }}
      >
        <SelectTrigger className={cn("h-8 text-sm", showInput ? "min-w-0 flex-1" : "w-full")}>
          <SelectValue placeholder="미지정" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>{o}</SelectItem>
          ))}
          <SelectItem value={COMBO_CUSTOM}>사용자 입력</SelectItem>
        </SelectContent>
      </Select>
      {showInput && (
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder ?? "직접 입력"}
          className="h-8 min-w-0 flex-1 text-sm"
        />
      )}
    </div>
  )
}

// 소유자 전용 — 사용자 목록에서 선택(선택 시 부서 자동 입력). 맨 아래 "사용자 입력"으로 이메일 직접 입력.
function OwnerCombo({
  users,
  value,
  onSelectUser,
  onCustomEmail,
}: {
  users: User[]
  value: string
  onSelectUser: (u: User) => void
  onCustomEmail: (email: string) => void
}) {
  const [custom, setCustom] = useState(false)
  const matched = users.find((u) => u.email === value)
  const showInput = custom || (value !== "" && !matched)
  const selectValue = showInput ? COMBO_CUSTOM : value === "" ? COMBO_UNSET : value
  return (
    <div className="flex items-center gap-2">
      <Select
        value={selectValue}
        onValueChange={(v) => {
          if (v === COMBO_CUSTOM) setCustom(true)
          else if (v === COMBO_UNSET) { setCustom(false); onCustomEmail("") }
          else {
            setCustom(false)
            const u = users.find((x) => x.email === v)
            if (u) onSelectUser(u)
          }
        }}
      >
        <SelectTrigger className={cn("h-8 text-sm", showInput ? "w-[180px] shrink-0" : "w-full")}>
          <SelectValue placeholder="미지정" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
          {users.map((u) => (
            <SelectItem key={u.id} value={u.email}>
              {userDisplayName(u)} ({u.username})
            </SelectItem>
          ))}
          <SelectItem value={COMBO_CUSTOM}>사용자 입력</SelectItem>
        </SelectContent>
      </Select>
      {showInput && (
        <Input
          value={value}
          onChange={(e) => onCustomEmail(e.target.value)}
          placeholder="이메일 직접 입력"
          className="h-8 flex-1 text-sm"
        />
      )}
    </div>
  )
}

// 태그 배지 입력 — 입력 후 Enter→배지 추가, X→삭제. 저장 포맷은 콤마 구분 문자열.
function TagsInput({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const [text, setText] = useState("")
  const tags = value.split(",").map((t) => t.trim()).filter(Boolean)
  const commit = (raw: string) => {
    const incoming = raw.split(",").map((t) => t.trim()).filter(Boolean)
    if (incoming.length === 0) return
    const next = [...tags]
    for (const t of incoming) if (!next.includes(t)) next.push(t)
    onChange(next.join(", "))
    setText("")
  }
  const remove = (t: string) => onChange(tags.filter((x) => x !== t).join(", "))
  return (
    <div className="flex w-full flex-wrap items-center gap-1">
      {tags.map((t) => (
        <Badge key={t} variant="secondary" className="gap-1 text-xs">
          {t}
          <button type="button" className="hover:opacity-70" onClick={() => remove(t)} aria-label={`${t} 제거`}>
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
      <Input
        className="h-8 min-w-[100px] flex-1 text-sm"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(text) }
          else if (e.key === "Backspace" && text === "" && tags.length > 0) remove(tags[tags.length - 1]!)
        }}
        onBlur={() => { if (text.trim()) commit(text) }}
        placeholder="입력 후 Enter"
      />
    </div>
  )
}

// 지원 언어 목록 — 한국어/영어/일본어 우선, 이후 사용 빈도순.
const LANGUAGES = [
  "한국어", "영어", "일본어", "중국어", "독일어", "프랑스어", "스페인어",
  "포르투갈어", "이탈리아어", "러시아어", "아랍어", "힌디어",
  "베트남어", "태국어", "인도네시아어",
]

// 언어 다중 선택 — 배지 토글. 저장 포맷은 콤마 구분 문자열.
function LangMultiSelect({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const selected = value.split(",").map((s) => s.trim()).filter(Boolean)
  const toggle = (lang: string) => {
    const next = selected.includes(lang) ? selected.filter((l) => l !== lang) : [...selected, lang]
    onChange(next.join(", "))
  }
  return (
    <div className="flex flex-wrap gap-1">
      {LANGUAGES.map((lang) => {
        const on = selected.includes(lang)
        return (
          <button type="button" key={lang} onClick={() => toggle(lang)} aria-pressed={on}>
            <Badge
              variant={on ? "default" : "outline"}
              className={cn("cursor-pointer text-xs", !on && "text-muted-foreground hover:bg-muted")}
            >
              {lang}
            </Badge>
          </button>
        )
      })}
    </div>
  )
}

// 프리셋 토글 + 사용자 정의 추가가 가능한 다중 선택(예: DLP 정책). 저장 포맷은 콤마 구분 문자열.
function ChipMultiSelect({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string
  onChange: (next: string) => void
  options: string[]
  placeholder?: string
}) {
  const [text, setText] = useState("")
  const selected = value.split(",").map((s) => s.trim()).filter(Boolean)
  const customs = selected.filter((s) => !options.includes(s))
  const toggle = (o: string) =>
    onChange((selected.includes(o) ? selected.filter((x) => x !== o) : [...selected, o]).join(", "))
  const remove = (o: string) => onChange(selected.filter((x) => x !== o).join(", "))
  const addCustom = (raw: string) => {
    const incoming = raw.split(",").map((t) => t.trim()).filter(Boolean)
    if (incoming.length === 0) return
    const next = [...selected]
    for (const t of incoming) if (!next.includes(t)) next.push(t)
    onChange(next.join(", "))
    setText("")
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1">
        {options.map((o) => {
          const on = selected.includes(o)
          return (
            <button type="button" key={o} onClick={() => toggle(o)} aria-pressed={on}>
              <Badge variant={on ? "default" : "outline"} className={cn("cursor-pointer text-xs", !on && "text-muted-foreground hover:bg-muted")}>
                {o}
              </Badge>
            </button>
          )
        })}
      </div>
      {customs.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {customs.map((c) => (
            <Badge key={c} variant="secondary" className="gap-1 text-xs">
              {c}
              <button type="button" className="hover:opacity-70" onClick={() => remove(c)} aria-label={`${c} 제거`}>
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <Input
        className="h-8 text-sm"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustom(text) } }}
        onBlur={() => { if (text.trim()) addCustom(text) }}
        placeholder={placeholder ?? "사용자 정의 추가 후 Enter"}
      />
    </div>
  )
}

// 개요 탭 편집 draft — 사용자 입력 항목만 포함(숫자는 문자열로 보관, 저장 시 변환).
type OverviewDraft = {
  description: string
  version: string
  category: string
  department: string
  owner_email: string
  tags: string
  base_model: string
  model_provider: string
  framework: string
  execution_policy: string
  memory_type: string
  max_steps: string
  endpoint: string
  protocol: string
  invocation_method: string
  auth_method: string
  is_multi_agent: boolean
  streaming: boolean
}
const EMPTY_DRAFT: OverviewDraft = {
  description: "", version: "", category: "", department: "", owner_email: "", tags: "",
  base_model: "", model_provider: "", framework: "", execution_policy: "",
  memory_type: "", max_steps: "", endpoint: "", protocol: "",
  invocation_method: "", auth_method: "", is_multi_agent: false, streaming: false,
}

// 기능·I/O 탭 편집 draft — 목록은 콤마 문자열, 스키마는 JSON 텍스트로 보관.
type CapDraft = {
  capabilities: string
  supported_languages: string
  use_cases: string
  limitations: string
  input_schema: string
  output_schema: string
}
const EMPTY_CAP: CapDraft = {
  capabilities: "", supported_languages: "", use_cases: "", limitations: "",
  input_schema: "", output_schema: "",
}

// 거버넌스 탭 편집 draft.
type GovDraft = {
  pii_handling: string
  data_residency: string
  budget_limit: string
  hitl_required: boolean
  audit_log_ref: string
  dlp_policies: string // 콤마 구분
  guardrails: string // JSON 텍스트
  hitl_config: string // JSON 텍스트
}
const EMPTY_GOV: GovDraft = {
  pii_handling: "", data_residency: "", budget_limit: "", hitl_required: false,
  audit_log_ref: "", dlp_policies: "", guardrails: "", hitl_config: "",
}

// 도구 위험도 배지 — low/medium/high/critical 색상 구분.
const RISK_META: Record<string, { label: string; cls: string }> = {
  low: { label: "낮음", cls: "border-emerald-300 text-emerald-700 dark:text-emerald-400" },
  medium: { label: "보통", cls: "border-amber-300 text-amber-700 dark:text-amber-400" },
  high: { label: "높음", cls: "border-orange-400 text-orange-700 dark:text-orange-400" },
  critical: { label: "치명", cls: "border-red-500 text-red-700 dark:text-red-400" },
}
const RISK_OPTIONS = ["low", "medium", "high", "critical"]
function RiskBadge({ risk }: { risk: string | null }) {
  if (!risk) return <span className="text-muted-foreground">-</span>
  const m = RISK_META[risk] ?? { label: risk, cls: "" }
  return <Badge variant="outline" className={cn("text-xs", m.cls)}>{m.label}</Badge>
}

type MetaTableRow = [React.ReactNode, React.ReactNode, React.ReactNode, Source?]
function MetaTable({ groups }: { groups: { title: string; rows: MetaTableRow[] }[] }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full table-fixed border-collapse text-sm">
        <colgroup>
          <col className="w-[160px]" />
          <col className="w-[34%]" />
          <col />
        </colgroup>
        <thead>
          <tr className="bg-muted/40">
            <th className="border px-3 py-2 text-left font-bold">항목</th>
            <th className="border px-3 py-2 text-left font-bold">설정값</th>
            <th className="border px-3 py-2 text-left font-bold">설명</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g, gi) => (
            <Fragment key={gi}>
              <tr>
                <th colSpan={3} className="border bg-muted px-3 py-2 text-left text-sm font-semibold">
                  {g.title}
                </th>
              </tr>
              {g.rows.map(([label, value, desc, source], ri) => (
                <tr key={ri}>
                  <th className="border bg-muted/50 px-3 py-2 text-left align-middle font-medium text-foreground">
                    <span className="inline-flex flex-wrap items-center gap-1">
                      {label}
                      <SourceBadge source={source} />
                    </span>
                  </th>
                  <td className="border px-3 py-2 align-middle break-words">
                    {value === null || value === undefined || value === "" ? "-" : value}
                  </td>
                  <td className="border px-3 py-2 align-top text-foreground">{desc}</td>
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function AIAgentsDetail({ agentName }: { agentName: string }) {
  const { setSelectedAgentName, setDeleteTargetName, setOpen } = useAIAgents()
  const [agent, setAgent] = useState<AIAgentDetail | null>(null)
  const [evals, setEvals] = useState<AIAgentEval[]>([])
  const [metering, setMetering] = useState<AIAgentMetering | null>(null)
  const [card, setCard] = useState<AIAgentCard | null>(null)
  const [policy, setPolicy] = useState<AIAgentPolicyBundle | null>(null)
  const [hookEvents, setHookEvents] = useState<AIAgentHookEvent[]>([])
  const [statusHistory, setStatusHistory] = useState<AIAgentStatusHistory[]>([])
  const [error, setError] = useState<string | null>(null)
  // 활성 탭(제어형) — 카드 탭의 "개요에서 편집" 바로가기용
  const [activeTab, setActiveTab] = useState("overview")
  // owner_email 로 사용자 레코드를 조회해 소유자 이름·소속·부서를 표시한다.
  const [ownerUser, setOwnerUser] = useState<User | null>(null)
  // 개요 탭 편집 모드 / 저장 중 / 편집 draft
  const [editingOverview, setEditingOverview] = useState(false)
  const [savingOverview, setSavingOverview] = useState(false)
  const [draft, setDraft] = useState<OverviewDraft>(EMPTY_DRAFT)
  // 소유자 선택용 사용자 목록(편집 진입 시 1회 로드)
  const [pickerUsers, setPickerUsers] = useState<User[]>([])
  // 기능·I/O 탭 편집 모드 / 저장 중 / draft
  const [editingCap, setEditingCap] = useState(false)
  const [savingCap, setSavingCap] = useState(false)
  const [capDraft, setCapDraft] = useState<CapDraft>(EMPTY_CAP)
  // 도구 표에서 파라미터 스키마를 펼친 도구 id 집합
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set())
  // RAG 설정 / 네트워크 편집
  const [editingRag, setEditingRag] = useState(false)
  const [savingRag, setSavingRag] = useState(false)
  const [ragText, setRagText] = useState("")
  const [networkText, setNetworkText] = useState("")
  // 거버넌스 탭 편집
  const [editingGov, setEditingGov] = useState(false)
  const [savingGov, setSavingGov] = useState(false)
  const [govDraft, setGovDraft] = useState<GovDraft>(EMPTY_GOV)

  const reloadDetail = useCallback(async () => {
    setAgent(await fetchAIAgentDetail(agentName))
  }, [agentName])

  const reloadEvals = useCallback(async () => {
    setEvals(await fetchAIAgentEvals(agentName))
  }, [agentName])

  const reloadStatusHistory = useCallback(async () => {
    setStatusHistory(await fetchAIAgentStatusHistory(agentName))
  }, [agentName])

  // 헤더 상태 배지 드롭다운 — 즉시 PATCH + 재조회 + 이력 갱신.
  const changeStatus = async (next: string) => {
    if (!agent || next === agent.status) return
    try {
      await updateAIAgent(agent.name, { status: next })
      await reloadDetail()
      await reloadStatusHistory()
      toast.success(`상태를 '${STATUS_LABEL[next] ?? next}'(으)로 변경했습니다.`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "상태 변경에 실패했습니다.")
    }
  }

  // 개요 편집 시작: 현재 에이전트 값으로 draft 초기화.
  const startEditOverview = () => {
    if (!agent) return
    setDraft({
      description: agent.description ?? "",
      version: agent.version ?? "",
      category: agent.category ?? "",
      department: agent.department ?? "",
      owner_email: agent.owner_email ?? "",
      tags: (agent.tags ?? []).join(", "),
      base_model: agent.base_model ?? "",
      model_provider: agent.model_provider ?? "",
      framework: agent.framework ?? "",
      execution_policy: agent.execution_policy ?? "",
      memory_type: agent.memory_type ?? "",
      max_steps: agent.max_steps == null ? "" : String(agent.max_steps),
      endpoint: agent.endpoint ?? "",
      protocol: agent.protocol ?? "",
      invocation_method: agent.invocation_method ?? "",
      auth_method: agent.auth_method ?? "",
      is_multi_agent: agent.is_multi_agent ?? false,
      streaming: agent.streaming ?? false,
    })
    setEditingOverview(true)
    // 소유자 선택 콤보용 사용자 목록 로드
    fetchUsers({ pageSize: 0 })
      .then((r) => setPickerUsers(r.items))
      .catch(() => setPickerUsers([]))
  }

  const setD = (k: keyof OverviewDraft, v: string | boolean) =>
    setDraft((d) => ({ ...d, [k]: v }))

  // 빈 문자열 → null, 그 외 trim. 숫자/태그는 개별 변환.
  const handleSaveOverview = async () => {
    if (!agent) return
    const s = (v: string) => (v.trim() === "" ? null : v.trim())
    setSavingOverview(true)
    try {
      await updateAIAgent(agent.name, {
        description: s(draft.description),
        version: draft.version.trim() || agent.version,
        category: s(draft.category),
        department: s(draft.department),
        owner_email: s(draft.owner_email),
        tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean),
        base_model: s(draft.base_model),
        model_provider: s(draft.model_provider),
        framework: s(draft.framework),
        execution_policy: s(draft.execution_policy),
        memory_type: s(draft.memory_type),
        max_steps: draft.max_steps.trim() === "" ? null : Number(draft.max_steps),
        endpoint: s(draft.endpoint),
        protocol: s(draft.protocol),
        invocation_method: s(draft.invocation_method),
        auth_method: s(draft.auth_method),
        is_multi_agent: draft.is_multi_agent,
        streaming: draft.streaming,
      })
      await reloadDetail()
      setEditingOverview(false)
      toast.success("개요를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
    } finally {
      setSavingOverview(false)
    }
  }

  // 기능·I/O 편집 시작: 목록은 콤마 문자열, 스키마는 JSON 텍스트로 초기화.
  const startEditCap = () => {
    if (!agent) return
    const j = (v: unknown) => (v == null ? "" : JSON.stringify(v, null, 2))
    setCapDraft({
      capabilities: (agent.capabilities ?? []).join(", "),
      supported_languages: (agent.supported_languages ?? []).join(", "),
      use_cases: (agent.use_cases ?? []).join(", "),
      limitations: (agent.limitations ?? []).join(", "),
      input_schema: j(agent.input_schema),
      output_schema: j(agent.output_schema),
    })
    setEditingCap(true)
  }
  const setCap = (k: keyof CapDraft, v: string) => setCapDraft((d) => ({ ...d, [k]: v }))

  const handleSaveCap = async () => {
    if (!agent) return
    const toList = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean)
    let inputSchema: Record<string, unknown> | null = null
    let outputSchema: Record<string, unknown> | null = null
    try {
      inputSchema = capDraft.input_schema.trim() === "" ? null : JSON.parse(capDraft.input_schema)
      outputSchema = capDraft.output_schema.trim() === "" ? null : JSON.parse(capDraft.output_schema)
    } catch {
      toast.error("입력/출력 스키마가 올바른 JSON 형식이 아닙니다.")
      return
    }
    setSavingCap(true)
    try {
      await updateAIAgent(agent.name, {
        capabilities: toList(capDraft.capabilities),
        supported_languages: toList(capDraft.supported_languages),
        use_cases: toList(capDraft.use_cases),
        limitations: toList(capDraft.limitations),
        input_schema: inputSchema,
        output_schema: outputSchema,
      })
      await reloadDetail()
      setEditingCap(false)
      toast.success("기능·I/O를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
    } finally {
      setSavingCap(false)
    }
  }

  // RAG 설정 / 네트워크 편집
  const startEditRag = () => {
    if (!agent) return
    setRagText(agent.rag_config == null ? "" : JSON.stringify(agent.rag_config, null, 2))
    setNetworkText((agent.network_allowlist ?? []).join(", "))
    setEditingRag(true)
  }
  const handleSaveRag = async () => {
    if (!agent) return
    let ragConfig: Record<string, unknown> | null = null
    try {
      ragConfig = ragText.trim() === "" ? null : JSON.parse(ragText)
    } catch {
      toast.error("RAG 설정이 올바른 JSON 형식이 아닙니다.")
      return
    }
    setSavingRag(true)
    try {
      await updateAIAgent(agent.name, {
        rag_config: ragConfig,
        network_allowlist: networkText.split(",").map((t) => t.trim()).filter(Boolean),
      })
      await reloadDetail()
      setEditingRag(false)
      toast.success("RAG 설정/네트워크를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
    } finally {
      setSavingRag(false)
    }
  }

  // 거버넌스 편집
  const startEditGov = () => {
    if (!agent) return
    const j = (v: unknown) => (v == null ? "" : JSON.stringify(v, null, 2))
    setGovDraft({
      pii_handling: agent.pii_handling ?? "",
      data_residency: agent.data_residency ?? "",
      budget_limit: agent.budget_limit == null ? "" : String(agent.budget_limit),
      hitl_required: agent.hitl_required ?? false,
      audit_log_ref: agent.audit_log_ref ?? "",
      dlp_policies: (agent.dlp_policies ?? []).join(", "),
      guardrails: j(agent.guardrails),
      hitl_config: j(agent.hitl_config),
    })
    setEditingGov(true)
  }
  const setGov = (k: keyof GovDraft, v: string | boolean) => setGovDraft((d) => ({ ...d, [k]: v }))
  const handleSaveGov = async () => {
    if (!agent) return
    const s = (v: string) => (v.trim() === "" ? null : v.trim())
    let guardrails: Record<string, unknown> | null = null
    let hitlConfig: Record<string, unknown> | null = null
    try {
      guardrails = govDraft.guardrails.trim() === "" ? null : JSON.parse(govDraft.guardrails)
      hitlConfig = govDraft.hitl_config.trim() === "" ? null : JSON.parse(govDraft.hitl_config)
    } catch {
      toast.error("가드레일/HITL 설정이 올바른 JSON 형식이 아닙니다.")
      return
    }
    setSavingGov(true)
    try {
      await updateAIAgent(agent.name, {
        pii_handling: s(govDraft.pii_handling),
        data_residency: s(govDraft.data_residency),
        budget_limit: govDraft.budget_limit.trim() === "" ? null : Number(govDraft.budget_limit),
        hitl_required: govDraft.hitl_required,
        audit_log_ref: s(govDraft.audit_log_ref),
        dlp_policies: govDraft.dlp_policies.split(",").map((t) => t.trim()).filter(Boolean),
        guardrails,
        hitl_config: hitlConfig,
      })
      await reloadDetail()
      setEditingGov(false)
      toast.success("거버넌스를 저장했습니다.")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장에 실패했습니다.")
    } finally {
      setSavingGov(false)
    }
  }

  useEffect(() => {
    let active = true
    Promise.all([
      fetchAIAgentDetail(agentName),
      fetchAIAgentEvals(agentName),
      fetchAIAgentMetering(agentName),
      fetchAIAgentCard(agentName),
      fetchAIAgentPolicy(agentName),
      fetchAIAgentHookEvents(agentName),
      fetchAIAgentStatusHistory(agentName),
    ])
      .then(([d, e, m, c, p, h, sh]) => {
        if (!active) return
        setAgent(d)
        setEvals(e)
        setMetering(m)
        setCard(c)
        setPolicy(p)
        setHookEvents(h)
        setStatusHistory(sh)
      })
      .catch((err) => active && setError(err instanceof Error ? err.message : "조회 실패"))
    return () => {
      active = false
    }
  }, [agentName])

  // owner_email → 사용자 레코드 조회(소속/부서/이름). 이메일이 정확히 일치하는 사용자만 사용.
  useEffect(() => {
    const email = agent?.owner_email?.trim()
    if (!email) {
      setOwnerUser(null)
      return
    }
    let active = true
    fetchUsers({ search: email, pageSize: 0 })
      .then((r) => {
        if (active) setOwnerUser(r.items.find((u) => u.email === email) ?? null)
      })
      .catch(() => active && setOwnerUser(null))
    return () => {
      active = false
    }
  }, [agent?.owner_email])

  if (error) {
    return (
      <div className="flex flex-col gap-4">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit"
          onClick={() => setSelectedAgentName(null)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> AI Agent 목록으로
        </Button>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  if (!agent) {
    return <p className="p-4 text-muted-foreground">불러오는 중...</p>
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 w-fit"
            onClick={() => setSelectedAgentName(null)}
          >
            <ArrowLeft className="mr-1 h-4 w-4" /> AI Agent 목록으로
          </Button>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold">{agent.display_name || agent.name}</h2>
            <Select value={agent.status} onValueChange={changeStatus}>
              <SelectTrigger
                className={cn(
                  "h-auto w-auto gap-1 rounded-md border-0 px-2 py-1 text-xs font-medium shadow-none focus:ring-0 focus:ring-offset-0",
                  AGENT_STATUS_VARIANTS[agent.status] ?? AGENT_STATUS_VARIANTS.draft,
                )}
                title="상태 변경"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_ORDER.map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABEL[s] ?? s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Badge variant="outline" className="text-xs font-medium">
              {agent.version}
            </Badge>
            {agent.reputation_score != null && (
              <Badge variant="secondary">평판 {agent.reputation_score}</Badge>
            )}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{agent.urn}</p>
          {agent.description && (
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{agent.description}</p>
          )}
        </div>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => {
            setDeleteTargetName(agent.name)
            setOpen("delete")
          }}
        >
          <Trash2 className="mr-1 h-4 w-4" /> 삭제
        </Button>
      </div>

      <Separator />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="overview" className="gap-1.5">
            <BookOpen className="h-4 w-4" />
            개요
          </TabsTrigger>
          <TabsTrigger value="capability" className="gap-1.5">
            <Layers className="h-4 w-4" />
            기능·I/O
          </TabsTrigger>
          <TabsTrigger value="tools" className="gap-1.5">
            <Wrench className="h-4 w-4" />
            도구·통합
          </TabsTrigger>
          <TabsTrigger value="governance" className="gap-1.5">
            <ShieldCheck className="h-4 w-4" />
            거버넌스
          </TabsTrigger>
          <TabsTrigger value="observability" className="gap-1.5">
            <Activity className="h-4 w-4" />
            관측·비용
          </TabsTrigger>
          <TabsTrigger value="eval" className="gap-1.5">
            <ClipboardCheck className="h-4 w-4" />
            평가
          </TabsTrigger>
          <TabsTrigger value="lineage" className="gap-1.5">
            <Share2 className="h-4 w-4" />
            리니지
          </TabsTrigger>
          <TabsTrigger value="versions" className="gap-1.5">
            <History className="h-4 w-4" />
            버전
          </TabsTrigger>
          <TabsTrigger value="card" className="gap-1.5">
            <Network className="h-4 w-4" />
            에이전트 카드(A2A)
          </TabsTrigger>
          <TabsTrigger value="enforcement" className="gap-1.5">
            <Plug className="h-4 w-4" />
            집행(연동)
          </TabsTrigger>
        </TabsList>

        {/* 정보 출처 범례 — 표/항목의 색상 원 의미 */}
        <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-sky-500" /> 사용자 입력
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" /> 자동 수집
          </span>
        </div>

        {/* 개요 — 4개 박스를 단일 표로 통합 (그룹 헤더 + key/value 행) */}
        <TabsContent value="overview" className="mt-4">
          <div className="mb-4 flex items-start justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              에이전트의 식별·소유, 모델·아키텍처, 인터페이스, 관측 요약 등 기본 메타데이터를 정리한 화면입니다.
              {editingOverview && " 사용자 입력(파랑) 항목만 편집할 수 있습니다."}
            </p>
            <div className="flex shrink-0 gap-2">
              {editingOverview ? (
                <>
                  <Button size="sm" variant="outline" disabled={savingOverview} onClick={() => setEditingOverview(false)}>
                    취소
                  </Button>
                  <Button size="sm" disabled={savingOverview} onClick={handleSaveOverview}>
                    {savingOverview ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
                    저장
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="outline" onClick={startEditOverview}>
                  <Pencil className="mr-1 h-4 w-4" /> 편집
                </Button>
              )}
            </div>
          </div>
          <div className="overflow-hidden rounded-md border">
                <table className="w-full table-fixed border-collapse text-sm">
                  <colgroup>
                    <col className="w-[160px]" />
                    <col className="w-[34%]" />
                    <col />
                  </colgroup>
                  <thead>
                    <tr className="bg-muted/40">
                      <th className="border px-3 py-2 text-left font-bold">항목</th>
                      <th className="border px-3 py-2 text-left font-bold">설정값</th>
                      <th className="border px-3 py-2 text-left font-bold">설명</th>
                    </tr>
                  </thead>
                  <tbody>
                    {([
                      {
                        title: "식별 / 소유",
                        rows: [
                          { label: "이름", display: agent.name, desc: "에이전트의 고유 식별 이름(시스템 내 유일)", source: "input" },
                          { label: "요약설명", display: agent.description, desc: "에이전트의 한 줄 요약 설명", source: "input", field: "description" },
                          { label: "버전", display: agent.version, desc: "에이전트 버전 (예: 1.2.0). 에이전트 카드에도 반영", source: "input", field: "version" },
                          { label: "카테고리", display: agent.category, desc: "용도별 분류(고객 지원·데이터 분석 등)", source: "input", field: "category", combo: OPT_CATEGORY },
                          { label: "소속", display: ownerUser?.organization ?? null, desc: "소유자(사용자) 프로필에서 자동 연동되는 조직/회사", source: "auto" },
                          { label: "부서", display: agent.department, desc: "에이전트를 운영·소유하는 부서/팀(소유자 선택 시 자동 입력)", source: "input", field: "department", combo: OPT_DEPARTMENT },
                          { label: "소유자", display: <AgentOwnerValue email={agent.owner_email} user={ownerUser} />, desc: "데이터 책임자(이메일로 등록). 이름(username)에 마우스를 올리면 소속·소속부서·Username·Email 표시", source: "input", field: "owner_email" },
                          { label: "태그", display: <StringList items={agent.tags} />, desc: "검색·분류용 라벨(쉼표로 구분)", source: "input", field: "tags" },
                        ],
                      },
                      {
                        title: "모델 / 아키텍처",
                        rows: [
                          { label: "기저 모델", display: agent.base_model, desc: "에이전트가 사용하는 기반 LLM 모델", source: "input", field: "base_model", combo: OPT_MODEL },
                          { label: "모델 프로바이더", display: agent.model_provider, desc: "모델 공급자(OpenAI·Anthropic 등)", source: "input", field: "model_provider", combo: OPT_PROVIDER },
                          { label: "프레임워크", display: agent.framework, desc: "구현에 사용된 에이전트 프레임워크(LangChain·LangGraph 등)", source: "input", field: "framework", combo: OPT_FRAMEWORK },
                          { label: "실행 정책", display: agent.execution_policy, source: "input", field: "execution_policy", combo: OPT_EXEC, desc: (
                            <div>
                              <div>작업 수행 추론·실행 전략 (벤더 중립 분류)</div>
                              <ul className="mt-1 space-y-0.5">
                                <li>· single_pass(단일 패스): 반복 없이 한 번의 호출로 응답 생성</li>
                                <li>· react(추론-행동 반복): 추론(Reasoning)과 도구 사용(Acting)을 번갈아 반복</li>
                                <li>· plan_and_execute(계획-실행): 전체 계획을 먼저 수립한 뒤 단계별로 실행</li>
                                <li>· reflection(자기 반성): 결과를 스스로 평가·재시도하며 개선</li>
                                <li>· tree_search(탐색 기반): 여러 경로를 탐색·평가해 최적 경로 선택(ToT 등)</li>
                                <li>· router(라우팅): 입력을 분류해 적절한 도구/하위 흐름으로 분기</li>
                                <li>· multi_agent(다중 에이전트): 여러 하위 에이전트가 역할을 분담해 협업</li>
                              </ul>
                              <div className="mt-1 text-muted-foreground">※ 프레임워크별 실제 명칭은 다를 수 있음(LangChain·LangGraph·CrewAI 등)</div>
                            </div>
                          ) },
                          { label: "메모리", display: agent.memory_type, source: "input", field: "memory_type", combo: OPT_MEMORY, desc: (
                            <div>
                              <div>실행 중 대화·상태 보존 방식 (벤더 중립 분류)</div>
                              <ul className="mt-1 space-y-0.5">
                                <li>· none(무상태): 상태를 저장하지 않고 매 호출 독립 처리</li>
                                <li>· short_term_buffer(단기 버퍼): 최근 대화 원문을 그대로 유지(세션 내 단기 기억)</li>
                                <li>· sliding_window(슬라이딩 윈도우): 최근 N턴/토큰까지만 유지하고 오래된 것은 폐기</li>
                                <li>· summary(요약 압축): 이전 대화를 요약해 압축 보관</li>
                                <li>· long_term_vector(장기 벡터): 임베딩 벡터 저장소에서 관련 맥락을 검색해 장기 기억 활용</li>
                                <li>· entity(엔티티): 핵심 엔티티·사실 단위로 추출·보관</li>
                                <li>· hybrid(혼합): 단기 버퍼 + 장기 검색을 결합</li>
                              </ul>
                              <div className="mt-1 text-muted-foreground">※ 프레임워크별 실제 명칭은 다를 수 있음(LangChain·LlamaIndex·CrewAI 등)</div>
                            </div>
                          ) },
                          { label: "멀티 에이전트", display: agent.is_multi_agent ? "예" : "아니오", desc: "여러 하위 에이전트가 협업하는 구조인지 여부", source: "input", field: "is_multi_agent", kind: "bool" },
                          { label: "최대 단계", display: agent.max_steps, desc: "한 번의 실행에서 허용되는 최대 추론·도구 호출 단계 수", source: "input", field: "max_steps", kind: "num" },
                        ],
                      },
                      {
                        title: "인터페이스",
                        rows: [
                          { label: "엔드포인트", display: agent.endpoint, desc: "에이전트를 호출하는 URL", source: "input", field: "endpoint" },
                          { label: "스트리밍", display: agent.streaming ? "예" : "아니오", desc: "스트리밍 응답 지원 여부(에이전트 카드에 반영)", source: "input", field: "streaming", kind: "bool" },
                          { label: "프로토콜", display: agent.protocol, source: "input", field: "protocol", combo: OPT_PROTOCOL, desc: (
                            <div>
                              <div>에이전트 호출에 사용하는 통신 프로토콜</div>
                              <ul className="mt-1 space-y-0.5">
                                <li>· https: TLS로 암호화된 HTTP</li>
                                <li>· http: 평문 HTTP</li>
                                <li>· http2: 멀티플렉싱 지원 HTTP/2</li>
                                <li>· grpc: HTTP/2 기반 RPC 프로토콜</li>
                                <li>· websocket: 양방향 지속 연결</li>
                                <li>· sse: 서버→클라이언트 단방향 스트리밍(Server-Sent Events)</li>
                                <li>· amqp: 메시지 브로커 프로토콜(RabbitMQ 등)</li>
                                <li>· mqtt: 경량 pub/sub 메시징</li>
                                <li>· tcp: 원시 TCP 소켓</li>
                              </ul>
                            </div>
                          ) },
                          { label: "호출 방식", display: agent.invocation_method, source: "input", field: "invocation_method", combo: OPT_INVOCATION, desc: (
                            <div>
                              <div>에이전트를 호출하는 인터페이스 유형 (벤더 중립 분류)</div>
                              <ul className="mt-1 space-y-0.5">
                                <li>· rest(REST/HTTP): 동기 HTTP 요청/응답</li>
                                <li>· grpc(gRPC): HTTP/2 기반 고성능 RPC</li>
                                <li>· graphql(GraphQL): 단일 엔드포인트 쿼리 기반 호출</li>
                                <li>· websocket(WebSocket): 지속 연결 기반 양방향 스트리밍</li>
                                <li>· webhook(웹훅): 이벤트 발생 시 등록된 URL로 콜백</li>
                                <li>· message_queue(메시지 큐): 비동기 큐(Kafka·SQS·RabbitMQ 등)를 통한 호출</li>
                                <li>· sdk(SDK/라이브러리): 클라이언트 라이브러리로 인프로세스 호출</li>
                                <li>· cli(CLI): 커맨드라인에서 실행</li>
                                <li>· scheduled(예약 실행): 배치/크론 등 스케줄 기반 실행</li>
                              </ul>
                              <div className="mt-1 text-muted-foreground">※ 게이트웨이/프레임워크별 실제 명칭은 다를 수 있음</div>
                            </div>
                          ) },
                          { label: "인증", display: agent.auth_method, source: "input", field: "auth_method", combo: OPT_AUTH, desc: (
                            <div>
                              <div>호출 시 사용하는 인증 방식 (벤더 중립 분류)</div>
                              <ul className="mt-1 space-y-0.5">
                                <li>· none(없음): 인증 없이 호출 허용</li>
                                <li>· api_key(API 키): 사전 발급된 키를 헤더 등으로 전달</li>
                                <li>· bearer_token(Bearer 토큰): Authorization: Bearer &lt;token&gt; 방식</li>
                                <li>· oauth2(OAuth 2.0): 인가 서버를 통한 토큰 발급·위임 인증</li>
                                <li>· jwt(JWT): 서명된 JSON 웹 토큰 검증</li>
                                <li>· basic(Basic 인증): 사용자명/비밀번호 base64 전달</li>
                                <li>· hmac(HMAC 서명): 공유 비밀키로 요청 본문을 서명·검증</li>
                                <li>· mtls(상호 TLS): 클라이언트·서버 양방향 인증서 검증</li>
                              </ul>
                              <div className="mt-1 text-muted-foreground">※ 게이트웨이/프레임워크별 실제 명칭은 다를 수 있음</div>
                            </div>
                          ) },
                        ],
                      },
                      {
                        title: "관측 요약",
                        rows: [
                          { label: "누적 호출", display: agent.usage_count, desc: "지금까지의 총 호출 횟수", source: "auto" },
                          { label: "지연 p50 (ms)", display: agent.latency_p50, desc: "응답 지연 시간의 중앙값(50퍼센타일)", source: "auto" },
                          { label: "지연 p95 (ms)", display: agent.latency_p95, desc: "응답 지연 시간 상위 5% 수준(95퍼센타일)", source: "auto" },
                          { label: "오류율", display: agent.error_rate, desc: "전체 호출 대비 오류가 발생한 비율", source: "auto" },
                          { label: "평균 토큰", display: agent.avg_token_usage, desc: "호출당 평균 토큰 사용량", source: "auto" },
                          { label: "평판 점수", display: agent.reputation_score, desc: "사용·평가 기반으로 산정된 신뢰 점수", source: "auto" },
                        ],
                      },
                    ] as { title: string; rows: { label: string; display: React.ReactNode; desc: React.ReactNode; source?: Source; field?: keyof OverviewDraft; kind?: "text" | "num" | "bool"; combo?: string[] }[] }[]).map((g) => (
                      <Fragment key={g.title}>
                        <tr>
                          <th colSpan={3} className="border bg-muted px-3 py-2 text-left text-sm font-semibold">
                            {g.title}
                          </th>
                        </tr>
                        {/* 관측 요약은 (편집 중이 아니고) 호출 텔레메트리가 없으면 안내 문구를 표시 */}
                        {g.title === "관측 요약" &&
                        [agent.usage_count, agent.latency_p50, agent.latency_p95, agent.error_rate, agent.avg_token_usage, agent.reputation_score]
                          .every((v) => v === null || v === undefined) ? (
                          <tr>
                            <td colSpan={3} className="border px-3 py-3 text-sm text-muted-foreground">
                              아직 수집된 호출 텔레메트리가 없습니다. 에이전트 런타임이 호출 지표를 전송하면
                              누적 호출·지연(p50/p95)·오류율·평균 토큰·평판 점수가 자동으로 표시됩니다.
                            </td>
                          </tr>
                        ) : (
                          g.rows.map((r) => (
                            <tr key={r.label}>
                              <th className="border bg-muted/50 px-3 py-2 text-left align-middle font-medium text-foreground">
                                <span className="inline-flex flex-wrap items-center gap-1">
                                  {r.label}
                                  <SourceBadge source={r.source} />
                                </span>
                              </th>
                              <td className="border px-3 py-2 align-middle break-words">
                                {editingOverview && r.field ? (
                                  r.field === "tags" ? (
                                    <TagsInput value={draft.tags} onChange={(v) => setD("tags", v)} />
                                  ) : r.field === "owner_email" ? (
                                    <OwnerCombo
                                      users={pickerUsers}
                                      value={draft.owner_email}
                                      onSelectUser={(u) =>
                                        setDraft((d) => ({ ...d, owner_email: u.email, department: u.department ?? d.department }))
                                      }
                                      onCustomEmail={(email) => setD("owner_email", email)}
                                    />
                                  ) : r.combo ? (
                                    <ComboInput
                                      value={draft[r.field] as string}
                                      onChange={(v) => setD(r.field!, v)}
                                      options={r.combo}
                                    />
                                  ) : r.kind === "bool" ? (
                                    <Select value={draft[r.field] ? "yes" : "no"} onValueChange={(v) => setD(r.field!, v === "yes")}>
                                      <SelectTrigger className="h-8 w-full text-sm"><SelectValue /></SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="yes">예</SelectItem>
                                        <SelectItem value="no">아니오</SelectItem>
                                      </SelectContent>
                                    </Select>
                                  ) : (
                                    <Input
                                      type={r.kind === "num" ? "number" : "text"}
                                      value={draft[r.field] as string}
                                      onChange={(e) => setD(r.field!, e.target.value)}
                                      className="h-8 w-full text-sm"
                                    />
                                  )
                                ) : r.display === null || r.display === undefined || r.display === "" ? (
                                  "-"
                                ) : (
                                  r.display
                                )}
                              </td>
                              <td className="border px-3 py-2 align-top text-foreground">
                                {r.desc}
                              </td>
                            </tr>
                          ))
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
        </TabsContent>

        {/* 기능·I/O — 개요 탭과 동일한 표 형식(항목/설정값/설명) */}
        <TabsContent value="capability" className="mt-4">
          <div className="mb-4 flex items-start justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              에이전트가 제공하는 기능·지원 언어·활용 사례·제약과 입출력 데이터 계약(스키마)을 정의합니다.
              {editingCap && " 목록은 Enter로 배지 추가, 스키마는 JSON으로 입력합니다."}
            </p>
            <div className="flex shrink-0 gap-2">
              {editingCap ? (
                <>
                  <Button size="sm" variant="outline" disabled={savingCap} onClick={() => setEditingCap(false)}>
                    취소
                  </Button>
                  <Button size="sm" disabled={savingCap} onClick={handleSaveCap}>
                    {savingCap ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
                    저장
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="outline" onClick={startEditCap}>
                  <Pencil className="mr-1 h-4 w-4" /> 편집
                </Button>
              )}
            </div>
          </div>
          <div className="overflow-hidden rounded-md border">
                <table className="w-full table-fixed border-collapse text-sm">
                  <colgroup>
                    <col className="w-[160px]" />
                    <col className="w-[34%]" />
                    <col />
                  </colgroup>
                  <thead>
                    <tr className="bg-muted/40">
                      <th className="border px-3 py-2 text-left font-bold">항목</th>
                      <th className="border px-3 py-2 text-left font-bold">설정값</th>
                      <th className="border px-3 py-2 text-left font-bold">설명</th>
                    </tr>
                  </thead>
                  <tbody>
                    {([
                      {
                        title: "기능",
                        rows: [
                          { label: "수행 기능", display: <StringList items={agent.capabilities} />, desc: "에이전트가 수행할 수 있는 핵심 기능을 짧은 식별자(태그) 목록으로 정의(자유 문자열, snake_case 권장). 예: nl2sql, faq_search, summarization, code_review", field: "capabilities", editor: "tags" },
                          { label: "지원 언어", display: <StringList items={agent.supported_languages} />, desc: "처리·응답이 가능한 자연어 언어(다중 선택). 예: 한국어, 영어, 일본어", field: "supported_languages", editor: "lang" },
                          { label: "활용 사례", display: <StringList items={agent.use_cases} />, desc: "주요 활용 사례·적용 시나리오. 예: 1차 고객 응대, 회의록 요약, 쿼리 초안 생성", field: "use_cases", editor: "tags" },
                          { label: "제약 사항", display: <StringList items={agent.limitations} />, desc: "알려진 제약·한계 사항. 예: 실시간 데이터 미지원, 한국어 외 언어 정확도 저하, 입력 최대 8K 토큰, 외부 네트워크 호출 불가", field: "limitations", editor: "tags" },
                        ],
                      },
                      {
                        title: "입출력 계약",
                        rows: [
                          { label: "입력 스키마", display: <JsonViewer value={agent.input_schema} />, desc: "에이전트 호출 시 전달하는 입력 데이터 구조 정의(JSON Schema). 예: { query: string(필수), top_k: number(기본 5) }", field: "input_schema", editor: "json" },
                          { label: "출력 스키마", display: <JsonViewer value={agent.output_schema} />, desc: "에이전트가 반환하는 출력 데이터 구조 정의(JSON Schema). 예: { answer: string, sources: string[], confidence: number }", field: "output_schema", editor: "json" },
                        ],
                      },
                    ] as { title: string; rows: { label: string; display: React.ReactNode; desc: React.ReactNode; field: keyof CapDraft; editor: "tags" | "json" | "lang" }[] }[]).map((g) => (
                      <Fragment key={g.title}>
                        <tr>
                          <th colSpan={3} className="border bg-muted px-3 py-2 text-left text-sm font-semibold">
                            {g.title}
                          </th>
                        </tr>
                        {g.rows.map((r) => (
                          <tr key={r.label}>
                            <th className="border bg-muted/50 px-3 py-2 text-left align-middle font-medium text-foreground">
                              <span className="inline-flex flex-wrap items-center gap-1">
                                {r.label}
                                <SourceBadge source="input" />
                              </span>
                            </th>
                            <td className="border px-3 py-2 align-middle break-words">
                              {editingCap ? (
                                r.editor === "tags" ? (
                                  <TagsInput value={capDraft[r.field]} onChange={(v) => setCap(r.field, v)} />
                                ) : r.editor === "lang" ? (
                                  <LangMultiSelect value={capDraft[r.field]} onChange={(v) => setCap(r.field, v)} />
                                ) : (
                                  <Textarea
                                    value={capDraft[r.field]}
                                    onChange={(e) => setCap(r.field, e.target.value)}
                                    placeholder='JSON 형식 (예: {"query": {"type": "string"}})'
                                    className="min-h-32 w-full font-mono text-xs"
                                  />
                                )
                              ) : (
                                r.display
                              )}
                            </td>
                            <td className="border px-3 py-2 align-top text-foreground">
                              {r.desc}
                            </td>
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
        </TabsContent>

        {/* 도구·통합 (관리 가능) */}
        <TabsContent value="tools" className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            에이전트가 사용하는 도구(Tool)와 연동된 MCP 서버 등 외부 통합 구성을 관리합니다.
          </p>
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">도구 ({agent.tools.length}) <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
                <AddToolDialog agentName={agent.name} onAdded={reloadDetail} />
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>이름</TableHead>
                    <TableHead className="w-20">위험도</TableHead>
                    <TableHead className="w-16">승인</TableHead>
                    <TableHead className="w-24">파라미터</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agent.tools.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-muted-foreground">
                        등록된 도구 없음
                      </TableCell>
                    </TableRow>
                  ) : (
                    agent.tools.map((t) => {
                      const props = (t.tool_schema as { properties?: Record<string, unknown> } | null)?.properties
                      const paramCount = props ? Object.keys(props).length : 0
                      const hasSchema = t.tool_schema != null
                      const open = expandedTools.has(t.id)
                      return (
                        <Fragment key={t.id}>
                          <TableRow>
                            <TableCell className="align-top">
                              {hasSchema && (
                                <button
                                  type="button"
                                  className="text-muted-foreground hover:text-foreground"
                                  onClick={() =>
                                    setExpandedTools((s) => {
                                      const n = new Set(s)
                                      if (n.has(t.id)) n.delete(t.id)
                                      else n.add(t.id)
                                      return n
                                    })
                                  }
                                  aria-label="파라미터 펼치기"
                                >
                                  {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                </button>
                              )}
                            </TableCell>
                            <TableCell className="align-top">
                              <div className="font-medium">{t.name}</div>
                              {t.description && <div className="text-xs text-muted-foreground">{t.description}</div>}
                            </TableCell>
                            <TableCell className="align-top"><RiskBadge risk={t.risk} /></TableCell>
                            <TableCell className="align-top">
                              {t.requires_approval ? (
                                <Badge variant="outline" className="border-amber-300 text-xs text-amber-700 dark:text-amber-400">필요</Badge>
                              ) : (
                                <span className="text-muted-foreground">-</span>
                              )}
                            </TableCell>
                            <TableCell className="align-top text-muted-foreground">
                              {hasSchema ? `${paramCount}개 항목` : "-"}
                            </TableCell>
                            <TableCell className="align-top">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={async () => {
                                  await deleteAIAgentTool(agent.name, t.id)
                                  await reloadDetail()
                                }}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </TableCell>
                          </TableRow>
                          {open && hasSchema && (
                            <TableRow>
                              <TableCell />
                              <TableCell colSpan={5} className="bg-muted/30">
                                <p className="mb-1 text-xs text-muted-foreground">파라미터 스키마 (JSON)</p>
                                <JsonViewer value={t.tool_schema} />
                              </TableCell>
                            </TableRow>
                          )}
                        </Fragment>
                      )
                    })
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">MCP 서버 ({agent.mcp_servers.length}) <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>이름</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>인증</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agent.mcp_servers.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="font-medium">{m.name}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground break-all">{m.url || "-"}</TableCell>
                      <TableCell>
                        {m.auth_method ? <Badge variant="outline">{m.auth_method}</Badge> : "-"}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={async () => {
                            await deleteAIAgentMcpServer(agent.name, m.id)
                            await reloadDetail()
                          }}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                  <AddMcpRow agentName={agent.name} onAdded={reloadDetail} />
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">RAG 설정 / 네트워크 <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
                <div className="flex gap-2">
                  {editingRag ? (
                    <>
                      <Button size="sm" variant="outline" disabled={savingRag} onClick={() => setEditingRag(false)}>취소</Button>
                      <Button size="sm" disabled={savingRag} onClick={handleSaveRag}>
                        {savingRag ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
                        저장
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" variant="outline" onClick={startEditRag}>
                      <Pencil className="mr-1 h-4 w-4" /> 편집
                    </Button>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <p className="mb-1 text-sm text-muted-foreground">RAG 설정 (rag_config)</p>
                {editingRag ? (
                  <Textarea
                    value={ragText}
                    onChange={(e) => setRagText(e.target.value)}
                    placeholder='JSON 형식 (예: {"vector_store":"pgvector","top_k":5})'
                    className="min-h-32 w-full font-mono text-xs"
                  />
                ) : (
                  <JsonViewer value={agent.rag_config} />
                )}
              </div>
              <div>
                <p className="mb-1 text-sm text-muted-foreground">네트워크 허용목록 (network_allowlist)</p>
                {editingRag ? (
                  <TagsInput value={networkText} onChange={setNetworkText} />
                ) : (
                  <StringList items={agent.network_allowlist} />
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 거버넌스 */}
        <TabsContent value="governance" className="mt-4">
          <div className="mb-4 flex items-start justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              개인정보(PII)·데이터 레지던시·예산·HITL 등 보안·거버넌스 정책과 가드레일·DLP 설정입니다.
            </p>
            <div className="flex shrink-0 gap-2">
              {editingGov ? (
                <>
                  <Button size="sm" variant="outline" disabled={savingGov} onClick={() => setEditingGov(false)}>취소</Button>
                  <Button size="sm" disabled={savingGov} onClick={handleSaveGov}>
                    {savingGov ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
                    저장
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="outline" onClick={startEditGov}>
                  <Pencil className="mr-1 h-4 w-4" /> 편집
                </Button>
              )}
            </div>
          </div>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup>
                <col className="w-[160px]" />
                <col className="w-[34%]" />
                <col />
              </colgroup>
              <thead>
                <tr className="bg-muted/40">
                  <th className="border px-3 py-2 text-left font-bold">항목</th>
                  <th className="border px-3 py-2 text-left font-bold">설정값</th>
                  <th className="border px-3 py-2 text-left font-bold">설명</th>
                </tr>
              </thead>
              <tbody>
                {([
                  {
                    title: "보안 / 정책",
                    rows: [
                      { label: "PII 처리", display: dash(agent.pii_handling), field: "pii_handling", editor: "combo", options: OPT_PII, desc: (
                        <div>
                          <div>개인정보(PII) 처리 방식</div>
                          <ul className="mt-1 space-y-0.5">
                            <li>* none(없음): 개인정보 보호 조치 없음</li>
                            <li>* masking(마스킹): 일부를 가림(예: 010-****-1234)</li>
                            <li>* redaction(편집·삭제): 민감 부분을 제거하거나 가림 처리</li>
                            <li>* pseudonymization(가명처리): 식별자를 가명으로 치환(키 분리 보관 시 복원 가능)</li>
                            <li>* anonymization(익명처리): 식별 불가능하게 비가역 변환(복원 불가)</li>
                            <li>* tokenization(토큰화): 민감값을 무의미 토큰으로 치환, 매핑은 안전 저장</li>
                            <li>* encryption(암호화): 저장·전송 시 암호화</li>
                          </ul>
                        </div>
                      ) },
                      { label: "데이터 위치", display: dash(agent.data_residency), desc: "데이터를 저장·처리하는 지역(레지던시). 예: 한국, 미국, 유럽연합(EU)", field: "data_residency", editor: "combo", options: OPT_RESIDENCY },
                      { label: "예산 한도", display: agent.budget_limit == null ? "-" : `${agent.budget_limit}만원`, desc: "에이전트 호출 비용 상한 (만원 단위, 숫자만)", field: "budget_limit", editor: "num" },
                      { label: "HITL 필요", display: agent.hitl_required ? "예" : "아니오", desc: "사람 개입(Human-in-the-loop) 필요 여부", field: "hitl_required", editor: "bool" },
                      { label: "감사 로그", display: dash(agent.audit_log_ref), desc: "감사 로그 저장 위치/참조", field: "audit_log_ref", editor: "text" },
                      { label: "DLP 정책", display: <StringList items={agent.dlp_policies} />, desc: "적용된 데이터 유출 방지(DLP) 정책 목록(프리셋 선택 또는 직접 추가)", field: "dlp_policies", editor: "chips" },
                    ],
                  },
                  {
                    title: "가드레일 / HITL 설정",
                    rows: [
                      { label: "가드레일(guardrails)", display: <JsonViewer value={agent.guardrails} />, desc: "입출력 필터·금칙어·안전장치 등 가드레일 설정(JSON)", field: "guardrails", editor: "json" },
                      { label: "HITL 설정(hitl_config)", display: <JsonViewer value={agent.hitl_config} />, desc: "사람 개입 트리거·승인 흐름 설정(JSON)", field: "hitl_config", editor: "json" },
                    ],
                  },
                ] as { title: string; rows: { label: string; display: React.ReactNode; desc: React.ReactNode; field: keyof GovDraft; editor: "combo" | "num" | "bool" | "text" | "chips" | "json"; options?: string[] }[] }[]).map((g) => (
                  <Fragment key={g.title}>
                    <tr>
                      <th colSpan={3} className="border bg-muted px-3 py-2 text-left text-sm font-semibold">{g.title}</th>
                    </tr>
                    {g.rows.map((r) => (
                      <tr key={r.label}>
                        <th className="border bg-muted/50 px-3 py-2 text-left align-middle font-medium text-foreground">
                          <span className="inline-flex flex-wrap items-center gap-1">
                            {r.label}
                            <SourceBadge source="input" />
                          </span>
                        </th>
                        <td className="border px-3 py-2 align-middle break-words">
                          {editingGov ? (
                            r.editor === "combo" ? (
                              <ComboInput value={govDraft[r.field] as string} onChange={(v) => setGov(r.field, v)} options={r.options ?? []} />
                            ) : r.editor === "chips" ? (
                              <ChipMultiSelect value={govDraft[r.field] as string} onChange={(v) => setGov(r.field, v)} options={OPT_DLP} />
                            ) : r.editor === "bool" ? (
                              <Select value={govDraft.hitl_required ? "yes" : "no"} onValueChange={(v) => setGov("hitl_required", v === "yes")}>
                                <SelectTrigger className="h-8 w-full text-sm"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="yes">예</SelectItem>
                                  <SelectItem value="no">아니오</SelectItem>
                                </SelectContent>
                              </Select>
                            ) : r.editor === "json" ? (
                              <Textarea value={govDraft[r.field] as string} onChange={(e) => setGov(r.field, e.target.value)} placeholder='JSON 형식' className="min-h-28 w-full font-mono text-xs" />
                            ) : r.editor === "num" ? (
                              <div className="flex items-center gap-1">
                                <Input
                                  inputMode="numeric"
                                  value={govDraft[r.field] as string}
                                  onChange={(e) => setGov(r.field, e.target.value.replace(/[^0-9]/g, ""))}
                                  placeholder="숫자"
                                  className="h-8 w-full text-sm"
                                />
                                <span className="shrink-0 text-sm text-muted-foreground">만원</span>
                              </div>
                            ) : (
                              <Input value={govDraft[r.field] as string} onChange={(e) => setGov(r.field, e.target.value)} className="h-8 w-full text-sm" />
                            )
                          ) : r.display === null || r.display === undefined || r.display === "" ? (
                            "-"
                          ) : (
                            r.display
                          )}
                        </td>
                        <td className="border px-3 py-2 align-top text-foreground">{r.desc}</td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>

        {/* 관측·비용 (미터링) */}
        <TabsContent value="observability" className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            실제 호출 텔레메트리로 집계한 사용량·지연·오류율·토큰·비용 등 운영 지표입니다.
          </p>
          {!metering ? (
            <p className="text-sm text-muted-foreground">미터링 데이터를 불러오는 중...</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Metric label="총 호출" value={metering.total_invocations} />
                <Metric
                  label="성공률"
                  value={`${(metering.success_rate * 100).toFixed(1)}%`}
                />
                <Metric label="오류율" value={`${(metering.error_rate * 100).toFixed(1)}%`} />
                <Metric label="평판 점수" value={metering.reputation_score ?? "-"} />
                <Metric label="지연 p50 (ms)" value={metering.latency_p50 ?? "-"} />
                <Metric label="지연 p95 (ms)" value={metering.latency_p95 ?? "-"} />
                <Metric
                  label="총 토큰"
                  value={metering.total_input_tokens + metering.total_output_tokens}
                />
                <Metric label="총 비용" value={metering.total_cost} />
              </div>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">소비자별 호출 ({metering.by_consumer.length}) <SourceBadge source="auto" className="ml-1 align-middle" /></CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>소비자</TableHead>
                        <TableHead className="text-right">호출 수</TableHead>
                        <TableHead className="text-right">비율</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {metering.by_consumer.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={3} className="text-center text-muted-foreground">
                            데이터 없음
                          </TableCell>
                        </TableRow>
                      ) : (
                        metering.by_consumer.map((c) => (
                          <TableRow key={c.name}>
                            <TableCell>{c.name}</TableCell>
                            <TableCell className="text-right font-medium">{c.count.toLocaleString()}</TableCell>
                            <TableCell className="text-right text-muted-foreground">
                              {metering.total_invocations > 0
                                ? `${((c.count / metering.total_invocations) * 100).toFixed(1)}%`
                                : "-"}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </>
          )}
        </TabsContent>

        {/* 평가 */}
        <TabsContent value="eval" className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            에이전트 품질을 검증하는 평가(Eval) 실행 결과와 점수입니다.
          </p>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">평가 결과 ({evals.length}) <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>유형</TableHead>
                    <TableHead>지표</TableHead>
                    <TableHead>값</TableHead>
                    <TableHead>통과</TableHead>
                    <TableHead>일시</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {evals.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground">
                        평가 결과 없음
                      </TableCell>
                    </TableRow>
                  ) : (
                    evals.map((e) => (
                      <TableRow key={e.id}>
                        <TableCell>
                          <Badge variant="outline">{e.eval_type}</Badge>
                        </TableCell>
                        <TableCell>{e.metric_key}</TableCell>
                        <TableCell className="font-mono">{e.metric_value}</TableCell>
                        <TableCell>
                          {e.passed == null ? "-" : e.passed ? "✅" : "❌"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(e.evaluated_at).toLocaleString()}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              <AddEvalForm
                agentName={agent.name}
                onAdded={async () => {
                  await reloadEvals()
                  await reloadDetail()
                }}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* 리니지 (관리 가능) */}
        <TabsContent value="lineage" className="mt-4">
          <p className="mb-4 text-sm text-muted-foreground">
            에이전트가 의존하거나 생성하는 데이터·모델·에이전트 간 연결 관계(리니지)입니다.
          </p>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">의존 관계 ({agent.lineage.length}) <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>관계</TableHead>
                    <TableHead>대상 유형</TableHead>
                    <TableHead>대상</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agent.lineage.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={4} className="text-center text-muted-foreground">
                        의존 관계 없음
                      </TableCell>
                    </TableRow>
                  ) : (
                    agent.lineage.map((l) => (
                      <TableRow key={l.id}>
                        <TableCell>
                          <Badge variant="outline">{l.relation}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{l.target_type}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs break-all">{l.target_ref}</TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={async () => {
                              await deleteAIAgentLineage(agent.name, l.id)
                              await reloadDetail()
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              <AddLineageForm agentName={agent.name} onAdded={reloadDetail} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* 버전 (관리 가능) */}
        <TabsContent value="versions" className="mt-4">
          <p className="mb-4 text-sm text-muted-foreground">
            에이전트의 버전 이력과 각 버전의 변경 내용입니다.
          </p>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">버전 이력 ({agent.versions.length}) <SourceBadge source="input" className="ml-1 align-middle" /></CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-28">버전</TableHead>
                    <TableHead>변경 내용</TableHead>
                    <TableHead className="w-48">등록 일시</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agent.versions.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground">
                        버전 이력 없음
                      </TableCell>
                    </TableRow>
                  ) : (
                    agent.versions.map((v) => (
                      <TableRow key={v.id}>
                        <TableCell>
                          <Badge variant="outline">{v.version}</Badge>
                        </TableCell>
                        <TableCell className="whitespace-pre-wrap">{v.changelog || "-"}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(v.created_at).toLocaleString()}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              <AddVersionForm agentName={agent.name} onAdded={reloadDetail} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">상태 변경 이력 ({statusHistory.length}) <SourceBadge source="auto" className="ml-1 align-middle" /></CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>변경</TableHead>
                    <TableHead className="w-48">일시</TableHead>
                    <TableHead>변경자</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {statusHistory.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground">
                        상태 변경 이력 없음
                      </TableCell>
                    </TableRow>
                  ) : (
                    statusHistory.map((h) => (
                      <TableRow key={h.id}>
                        <TableCell>
                          <span className="inline-flex items-center gap-1.5">
                            {h.from_status ? (
                              <Badge variant="outline" className="text-xs">{STATUS_LABEL[h.from_status] ?? h.from_status}</Badge>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                            <span className="text-muted-foreground">→</span>
                            <Badge variant="secondary" className="text-xs">{STATUS_LABEL[h.to_status] ?? h.to_status}</Badge>
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(h.changed_at).toLocaleString()}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{h.changed_by || "-"}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 카드 (A2A) */}
        <TabsContent value="card" className="mt-4">
          <div className="mb-4 flex items-start justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              에이전트 카드(Agent Card)는 A2A(Agent-to-Agent) 프로토콜에서 에이전트가 자신을 다른
              에이전트·시스템에 소개하기 위해 게시하는 표준 메타데이터입니다. 사람의 명함처럼 이름·버전·
              접근 URL·프로토콜·인증·제공자·노출 스킬을 담아, 다른 에이전트가 이를 읽고 자동으로 발견·연동할 수 있게 합니다.
              아래 값은 개요·도구 탭의 정보를 그대로 가져와 자동 구성한 읽기 전용 미리보기입니다.
            </p>
            <Button size="sm" variant="outline" className="shrink-0" onClick={() => setActiveTab("overview")}>
              <Pencil className="mr-1 h-4 w-4" /> 개요에서 편집
            </Button>
          </div>
          {!card ? (
            <p className="text-sm text-muted-foreground">에이전트 카드를 불러오는 중...</p>
          ) : (
            <MetaTable
              groups={[
                    {
                      title: "카드 미리보기 (읽기 전용)",
                      rows: [
                        ["이름", card.name, "에이전트 식별 이름(식별자)", "input"],
                        ["버전", card.version, "에이전트 버전 — 개요에서 편집", "input"],
                        ["URL", card.url, "에이전트 접근 URL — 개요 > 엔드포인트", "input"],
                        ["프로토콜", card.protocol, "통신 프로토콜 — 개요에서 편집", "input"],
                        ["인증", card.auth_method, "호출 인증 방식 — 개요에서 편집", "input"],
                        ["제공자", `${ownerUser?.organization || "-"} / ${agent.department || "-"} / ${ownerUser ? `${userDisplayName(ownerUser)} (${ownerUser.username})` : (agent.owner_email || "-")}`, "소속 / 부서 / 소유자 — 개요에서 편집", "input"],
                        ["스트리밍", card.streaming ? "예" : "아니오", "스트리밍 응답 지원 여부 — 개요 > 인터페이스", "input"],
                        ["스킬", <StringList key="skills" items={card.skills.map((sk) => sk.name)} />, "외부에 노출하는 스킬 — 도구·통합 탭의 도구(tools)에서 구성됨", "input"],
                      ],
                    },
                    {
                      title: "원본 (A2A 교환용)",
                      rows: [
                        ["Raw JSON", <JsonViewer key="card-json" value={card} />, "에이전트 간 상호 검색·협업 교환에 사용하는 카드 원본(JSON). 위 값들로 자동 조립됨", "auto"],
                      ],
                    },
                  ]}
            />
          )}
        </TabsContent>

        {/* 집행(연동) — 외부 Execution Plane 연동 인터페이스 (읽기 전용) */}
        <TabsContent value="enforcement" className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            외부 실행 런타임(Execution Plane)이 연동하는 계약입니다. 카탈로그는 정책을 내려주고
            (pull) 집행 결과를 수신(push)만 하며, 실제 격리 실행·인라인 훅·egress 강제는 외부
            런타임이 수행합니다.
          </p>
          <MetaTable
            groups={[
                  {
                    title: "정책 번들 (런타임이 pull — GET /policy)",
                    rows: [
                      ["정책 버전", policy?.policy_version ?? null, "현재 적용 중인 정책 번들의 버전(생성 시각 기준)", "auto"],
                      ["정책 번들(JSON)", <JsonViewer key="policy-json" value={policy} />, "거버넌스 설정에서 자동 생성되어 런타임이 내려받는 전체 정책 번들 원본", "auto"],
                    ],
                  },
                ]}
          />
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">
                집행 훅 이벤트 (런타임이 push) — POST /hook-events ({hookEvents.length}) <SourceBadge source="auto" className="ml-1 align-middle" />
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>단계</TableHead>
                      <TableHead>결정</TableHead>
                      <TableHead>정책</TableHead>
                      <TableHead>대상</TableHead>
                      <TableHead>사유</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {hookEvents.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={5} className="text-center text-muted-foreground">
                          수신된 집행 이벤트 없음
                        </TableCell>
                      </TableRow>
                    ) : (
                      hookEvents.map((h) => (
                        <TableRow key={h.id}>
                          <TableCell>
                            <Badge variant="secondary">{h.stage}</Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">{h.decision}</Badge>
                          </TableCell>
                          <TableCell className="font-mono text-xs">{h.policy_ref || "-"}</TableCell>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            {h.target || "-"}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {h.reason || "-"}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline add forms
// ---------------------------------------------------------------------------

// 도구 추가 다이얼로그 — 이름·설명·위험도·승인 필요·파라미터 스키마(JSON).
function AddToolDialog({ agentName, onAdded }: { agentName: string; onAdded: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [desc, setDesc] = useState("")
  const [risk, setRisk] = useState("") // "" = 미지정
  const [approval, setApproval] = useState(false)
  const [schemaText, setSchemaText] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const reset = () => {
    setName(""); setDesc(""); setRisk(""); setApproval(false); setSchemaText(""); setErr(null)
  }

  const submit = async () => {
    setErr(null)
    let schema: Record<string, unknown> | null = null
    if (schemaText.trim() !== "") {
      try {
        schema = JSON.parse(schemaText)
      } catch {
        setErr("파라미터 스키마가 올바른 JSON 형식이 아닙니다.")
        return
      }
    }
    setBusy(true)
    try {
      await addAIAgentTool(agentName, {
        name: name.trim(),
        description: desc.trim() || undefined,
        risk: risk || null,
        requires_approval: approval,
        tool_schema: schema,
      })
      reset()
      setOpen(false)
      await onAdded()
    } catch (e) {
      setErr(e instanceof Error ? e.message : "도구 등록 실패")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) reset() }}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline"><Plus className="mr-1 h-4 w-4" /> 도구 추가</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>도구 추가</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>이름</Label>
            <Input placeholder="예: run_sql" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>설명</Label>
            <Input placeholder="도구 용도" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="flex items-end gap-4">
            <div className="flex-1 space-y-1.5">
              <Label>위험도</Label>
              <Select
                value={risk || COMBO_UNSET}
                onValueChange={(v) => {
                  const next = v === COMBO_UNSET ? "" : v
                  setRisk(next)
                  // high/critical 은 승인 필요를 자동 권장
                  if (next === "high" || next === "critical") setApproval(true)
                }}
              >
                <SelectTrigger><SelectValue placeholder="미지정" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={COMBO_UNSET} className="text-muted-foreground">미지정</SelectItem>
                  {RISK_OPTIONS.map((r) => (
                    <SelectItem key={r} value={r}>{RISK_META[r]?.label ?? r} ({r})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2 pb-2">
              <Switch id="tool-approval" checked={approval} onCheckedChange={setApproval} />
              <Label htmlFor="tool-approval">승인 필요</Label>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>파라미터 스키마 (JSON, 선택)</Label>
            <Textarea
              placeholder='{"type":"object","properties":{"q":{"type":"string"}}}'
              value={schemaText}
              onChange={(e) => setSchemaText(e.target.value)}
              className="min-h-28 font-mono text-xs"
            />
          </div>
          {err && <p className="text-sm text-destructive">{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>취소</Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Plus className="mr-1 h-4 w-4" />}
            추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// MCP 추가 입력 — 표의 이름/URL/인증 컬럼에 정렬되도록 TableRow 로 렌더.
function AddMcpRow({ agentName, onAdded }: { agentName: string; onAdded: () => Promise<void> }) {
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [auth, setAuth] = useState("")
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    try {
      await addAIAgentMcpServer(agentName, {
        name: name.trim(),
        url: url.trim() || undefined,
        auth_method: auth.trim() || undefined,
      })
      setName(""); setUrl(""); setAuth("")
      await onAdded()
    } finally {
      setBusy(false)
    }
  }
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell className="align-middle">
        <Input placeholder="MCP 이름" value={name} onChange={(e) => setName(e.target.value)} className="h-8" />
      </TableCell>
      <TableCell className="align-middle">
        <Input placeholder="URL" value={url} onChange={(e) => setUrl(e.target.value)} className="h-8" />
      </TableCell>
      <TableCell className="align-middle">
        <ComboInput value={auth} onChange={setAuth} options={OPT_AUTH} placeholder="인증 직접 입력" />
      </TableCell>
      <TableCell className="align-middle">
        <Button size="icon" className="h-7 w-7" disabled={busy || !name.trim()} onClick={submit}>
          <Plus className="h-4 w-4" />
        </Button>
      </TableCell>
    </TableRow>
  )
}

function AddLineageForm({
  agentName,
  onAdded,
}: {
  agentName: string
  onAdded: () => Promise<void>
}) {
  const [relation, setRelation] = useState("depends_on")
  const [targetType, setTargetType] = useState("agent")
  const [targetRef, setTargetRef] = useState("")
  const [busy, setBusy] = useState(false)
  return (
    <div className="flex items-end gap-2 pt-2">
      <Select value={relation} onValueChange={setRelation}>
        <SelectTrigger size="sm" className="w-[130px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="depends_on">depends_on</SelectItem>
          <SelectItem value="consumed_by">consumed_by</SelectItem>
          <SelectItem value="related">related</SelectItem>
        </SelectContent>
      </Select>
      <Select value={targetType} onValueChange={setTargetType}>
        <SelectTrigger size="sm" className="w-[110px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="agent">agent</SelectItem>
          <SelectItem value="model">model</SelectItem>
          <SelectItem value="dataset">dataset</SelectItem>
        </SelectContent>
      </Select>
      <Input
        placeholder="대상 name/URN"
        value={targetRef}
        onChange={(e) => setTargetRef(e.target.value)}
        className="h-8"
      />
      <Button
        size="sm"
        disabled={busy || !targetRef.trim()}
        onClick={async () => {
          setBusy(true)
          try {
            await addAIAgentLineage(agentName, {
              relation,
              target_type: targetType,
              target_ref: targetRef.trim(),
            })
            setTargetRef("")
            await onAdded()
          } finally {
            setBusy(false)
          }
        }}
      >
        <Plus className="h-4 w-4" />
      </Button>
    </div>
  )
}

function AddVersionForm({
  agentName,
  onAdded,
}: {
  agentName: string
  onAdded: () => Promise<void>
}) {
  const [version, setVersion] = useState("")
  const [changelog, setChangelog] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  return (
    <div className="flex flex-col gap-2 pt-2">
      <div className="flex items-end gap-2">
        <Input
          placeholder="버전 (예: 1.3.0)"
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          className="h-8 w-40"
        />
        <Input
          placeholder="변경 사유"
          value={changelog}
          onChange={(e) => setChangelog(e.target.value)}
          className="h-8"
        />
        <Button
          size="sm"
          disabled={busy || !version.trim()}
          onClick={async () => {
            setBusy(true)
            setErr(null)
            try {
              await addAIAgentVersion(agentName, {
                version: version.trim(),
                changelog: changelog.trim() || undefined,
              })
              setVersion("")
              setChangelog("")
              await onAdded()
            } catch (e) {
              setErr(e instanceof Error ? e.message : "버전 생성 실패")
            } finally {
              setBusy(false)
            }
          }}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
    </div>
  )
}

function AddEvalForm({ agentName, onAdded }: { agentName: string; onAdded: () => Promise<void> }) {
  const [evalType, setEvalType] = useState("accuracy")
  const [metricKey, setMetricKey] = useState("")
  const [metricValue, setMetricValue] = useState("")
  const [busy, setBusy] = useState(false)
  return (
    <div className="flex items-end gap-2 pt-3">
      <Select value={evalType} onValueChange={setEvalType}>
        <SelectTrigger size="sm" className="w-[150px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="accuracy">accuracy</SelectItem>
          <SelectItem value="task_success">task_success</SelectItem>
          <SelectItem value="hallucination">hallucination</SelectItem>
          <SelectItem value="safety">safety</SelectItem>
          <SelectItem value="user_rating">user_rating</SelectItem>
        </SelectContent>
      </Select>
      <Input
        placeholder="지표 키"
        value={metricKey}
        onChange={(e) => setMetricKey(e.target.value)}
        className="h-8"
      />
      <Input
        placeholder="값 (0~1)"
        type="number"
        step="0.01"
        value={metricValue}
        onChange={(e) => setMetricValue(e.target.value)}
        className="h-8 w-28"
      />
      <Button
        size="sm"
        disabled={busy || !metricKey.trim() || metricValue === ""}
        onClick={async () => {
          setBusy(true)
          try {
            await addAIAgentEval(agentName, {
              eval_type: evalType,
              metric_key: metricKey.trim(),
              metric_value: Number(metricValue),
            })
            setMetricKey("")
            setMetricValue("")
            await onAdded()
          } finally {
            setBusy(false)
          }
        }}
      >
        <Plus className="mr-1 h-4 w-4" /> 평가 추가
      </Button>
    </div>
  )
}
