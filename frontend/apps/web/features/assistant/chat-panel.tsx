"use client"

/**
 * 어시스턴트 chat 패널 — FloatingChat 안에 마운트.
 *
 * - user 메시지: 우측 정렬 primary 버블 / assistant: 좌측 markdown 본문
 * - 스트리밍 중 마지막 assistant 메시지 끝에 깜빡이는 caret(▍)
 * - 빈 상태에서는 카탈로그 관련 추천 질문을 제시
 */

import { useEffect, useRef, useState } from "react"
import { Send, RotateCcw, Square, Sparkles, User as UserIcon, Wrench } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"

import { useAssistantStream, type ChatTurn, type ToolCall } from "./use-assistant-stream"

const SUGGESTIONS = [
  "고객 관련 테이블 뭐가 있어?",
  "rental 테이블 구조를 설명해줘",
  "점포별 월 대여 건수를 구하는 SQL 만들어줘",
  "staff 테이블 품질이 왜 WARN이야?",
  "rental 데이터는 어디서 오고 어디에 영향을 줘?",
]

export function ChatPanel() {
  const { turns, streaming, send, abort, reset } = useAssistantStream()
  const [input, setInput] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [turns])

  // 입력창 자동 높이 — 한 줄로 시작해 내용에 따라 확장(최대치까지). input 변화마다 갱신.
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [input])

  function submit() {
    const v = input.trim()
    if (!v || streaming) return
    setInput("")
    void send(v)
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-background">
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto px-3 py-3 space-y-3">
        {turns.length === 0 && <EmptyState onPick={(s) => setInput(s)} />}
        {turns.map((t, i) => (
          <Turn key={i} turn={t} streaming={streaming && i === turns.length - 1} />
        ))}
      </div>

      <div className="border-t border-border bg-muted/30 px-2 py-2 flex items-end gap-1.5">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          rows={1}
          placeholder="메시지를 입력 (Shift+Enter 줄바꿈)"
          className="flex-1 resize-none overflow-y-auto rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
          disabled={streaming}
        />
        <div className="flex flex-row gap-1">
          {streaming ? (
            <button
              type="button"
              onClick={abort}
              title="중단"
              className="h-8 w-8 inline-flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              title="전송"
              disabled={!input.trim()}
              className="h-8 w-8 inline-flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={reset}
            title="새 대화"
            disabled={streaming}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}

function Turn({ turn, streaming }: { turn: ChatTurn; streaming: boolean }) {
  if (turn.kind === "user") {
    return (
      <div className="flex justify-end gap-2 motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-1 motion-safe:duration-200">
        <div className="rounded-2xl rounded-br-sm bg-primary text-primary-foreground px-3 py-1.5 text-sm max-w-[85%] whitespace-pre-wrap break-words">
          {turn.text}
        </div>
        <Avatar role="user" />
      </div>
    )
  }

  if (turn.kind === "error") {
    return (
      <div className="flex gap-2 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
        <Avatar role="assistant" />
        <div className="rounded-md border border-rose-300 dark:border-rose-700 px-3 py-2 text-xs text-red-600 dark:text-red-400">
          {turn.text}
        </div>
      </div>
    )
  }

  const hasText = !!turn.text
  const toolCalls = turn.kind === "assistant" ? (turn.tool_calls ?? []) : []
  return (
    <div className="flex gap-2 motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-1 motion-safe:duration-200">
      <Avatar role="assistant" />
      <div className="flex-1 min-w-0 space-y-1.5">
        {toolCalls.map((tc) => (
          <ToolCard key={tc.id} call={tc} />
        ))}
        {hasText && (
          <div className="rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-sm prose prose-sm dark:prose-invert max-w-none break-words
            prose-p:my-1 prose-headings:my-1.5 prose-li:my-0
            prose-table:text-[11px] prose-table:my-1 prose-th:py-1 prose-td:py-0.5
            prose-code:before:content-none prose-code:after:content-none
            prose-pre:my-1.5 prose-pre:p-0 prose-pre:bg-transparent">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkBreaks]}
              components={{
                // 펜스 코드블록(```lang)만 syntax highlight(oneDark) — 기본 크기 유지.
                // 인라인 코드(`...`)는 백틱/회색칩 없이, 모노스페이스 + 본문 기본 크기로 표시.
                code({ className, children }) {
                  const match = /language-(\w+)/.exec(className || "")
                  const codeStr = String(children).replace(/\n$/, "")
                  if (match) {
                    return (
                      <SyntaxHighlighter
                        style={oneDark}
                        language={match[1]}
                        PreTag="div"
                        customStyle={{ margin: 0, borderRadius: "0.5rem", fontSize: "0.875rem", fontFamily: "D2Coding, monospace" }}
                      >
                        {codeStr}
                      </SyntaxHighlighter>
                    )
                  }
                  return <span className="font-mono">{children}</span>
                },
              }}
            >
              {turn.text + (streaming ? "▍" : "")}
            </ReactMarkdown>
          </div>
        )}
        {!hasText && toolCalls.length === 0 && streaming && (
          <div className="rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-xs text-muted-foreground inline-flex items-center gap-1">
            <DotsLoading />
            <span>생각 중</span>
          </div>
        )}
      </div>
    </div>
  )
}

/** 도구 이름 → 사용자에게 보여줄 한글 라벨. */
const TOOL_LABELS: Record<string, string> = {
  search_datasets: "카탈로그 검색",
  get_dataset_detail: "스키마 조회",
  get_erd: "ER 관계 조회",
  get_quality: "품질 조회",
  get_lineage: "리니지 조회",
  get_glossary_term: "용어집 조회",
  get_standard_compliance: "표준 준수 조회",
  get_quality_rule_recommendations: "품질 규칙 추천",
  validate_sql: "SQL 검증",
}

/** 도구 실행 카드 — 어떤 도구를 어떤 인자로 호출했는지 한 줄 상태(실행 중/완료)로만 표시. */
function ToolCard({ call }: { call: ToolCall }) {
  const label = TOOL_LABELS[call.name] ?? call.name
  const running = call.result === undefined
  const argText = Object.values(call.args ?? {}).filter((v) => typeof v === "string").join(", ")

  return (
    <div className={`flex items-center gap-1.5 rounded-lg border-l-2 bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground ${
      running ? "border-sky-400" : "border-emerald-400"
    }`}>
      <Wrench className="h-3 w-3 shrink-0" />
      <span className="font-medium">{label}</span>
      {argText && <span className="truncate">— {argText.slice(0, 60)}</span>}
      {running && <span className="ml-auto inline-flex items-center gap-1 text-sky-600">실행 중<DotsLoading /></span>}
    </div>
  )
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  if (role === "user") {
    return (
      <div className="h-7 w-7 shrink-0 rounded-full bg-muted flex items-center justify-center text-muted-foreground border border-border">
        <UserIcon className="h-3.5 w-3.5" />
      </div>
    )
  }
  return (
    <div className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-sky-400 to-violet-500 flex items-center justify-center text-white shadow-sm">
      <Sparkles className="h-3.5 w-3.5" />
    </div>
  )
}

function DotsLoading() {
  return (
    <span className="inline-flex gap-0.5 items-center">
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce [animation-delay:-0.3s]" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce [animation-delay:-0.15s]" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce" />
    </span>
  )
}

function EmptyState({ onPick }: { onPick: (s: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-2 py-6 space-y-3">
      <div className="h-12 w-12 rounded-2xl bg-gradient-to-br from-sky-400 to-violet-500 flex items-center justify-center text-white shadow-md">
        <Sparkles className="h-6 w-6" />
      </div>
      <div className="space-y-1">
        <div className="text-base font-semibold">무엇을 도와드릴까요?</div>
        <div className="text-sm text-muted-foreground max-w-[280px]">
          데이터 카탈로그 사용법·데이터 관리에 대해 자연어로 질문하세요.
        </div>
      </div>
      <div className="grid gap-1.5 w-full pt-1">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="text-left text-sm rounded-lg border border-border bg-background px-3 py-2 hover:bg-muted hover:border-foreground/20 transition-colors"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
