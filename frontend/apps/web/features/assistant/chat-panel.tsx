"use client"

/**
 * 어시스턴트 chat 패널 — FloatingChat 안에 마운트.
 *
 * - user 메시지: 우측 정렬 primary 버블 / assistant: 좌측 markdown 본문
 * - 스트리밍 중 마지막 assistant 메시지 끝에 깜빡이는 caret(▍)
 * - 코드 블록은 언어 표시와 관계없이 밝은 테마(oneLight) + 테두리 상자
 * - 표는 본문과 같은 text-sm, 넓으면 표만 가로 스크롤
 * - 코드 블록 복사 버튼, 인라인 코드는 옅은 칩, 내부 링크는 Next Link·외부 링크는 새 탭(대화 유지)
 * - 자동 스크롤은 사용자가 맨 아래 근처에 있을 때만(위로 올려 읽는 동안은 위치 유지)
 * - 도구 결과는 카드·미니 그래프(tool-cards.tsx), 답변 속 데이터셋 이름은 상세 링크(도구 결과의 이름·id)
 * - 답변 아래 소요 시간·토큰, 마지막 답변 다시 생성, 👍/👎 평가
 * - 빈 상태에서는 카탈로그 관련 추천 질문을 제시
 */

import { Tip } from "@/components/tip"
import { createContext, isValidElement, useContext, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react"
import Link from "next/link"
import { Check, Copy, RefreshCw, Send, RotateCcw, Square, Sparkles, ThumbsDown, ThumbsUp, User as UserIcon } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism"

import { useAssistantStream, type AssistantTurn, type ChatTurn } from "./use-assistant-stream"
import { buildDatasetIndex, datasetHref, lookupDataset, type DatasetIndex } from "./dataset-index"
import { ToolCard } from "./tool-cards"
import { tryCopyText } from "@/lib/clipboard"

const SUGGESTIONS = [
  "고객 관련 테이블 뭐가 있어?",
  "rental 테이블 구조를 설명해줘",
  "점포별 월 대여 건수를 구하는 SQL 만들어줘",
  "staff 테이블 품질이 왜 WARN이야?",
  "rental 데이터는 어디서 오고 어디에 영향을 줘?",
]

/** 맨 아래에서 이만큼(px) 안이면 "맨 아래에 있음"으로 보고 새 글자를 따라 내려간다. */
const STICK_THRESHOLD = 80

/**
 * 스트리밍 커서(▍) — 마크다운 원문에 붙이면 코드 블록·표 안에 글자로 들어가므로, 마지막 문단·제목·목록 항목의
 * ::after 로만 그린다. 마지막 블록이 코드·표면 커서를 그리지 않는다(글자가 늘어나는 것으로 진행이 보임).
 */
const STREAM_CARET =
  "[&>:is(p,h1,h2,h3,h4,h5,h6):last-child]:after:content-['▍'] [&>:is(ul,ol):last-child>li:last-child]:after:content-['▍'] " +
  "[&>:is(p,h1,h2,h3,h4,h5,h6):last-child]:after:animate-pulse [&>:is(ul,ol):last-child>li:last-child]:after:animate-pulse"

/** 화면 낭독기에 알릴 마지막 답변 상태. */
function lastAnnouncement(turns: ChatTurn[]): string {
  const last = turns[turns.length - 1]
  if (!last || last.kind === "user") return ""
  return last.kind === "error" ? `오류: ${last.text}` : "답변을 받았습니다."
}

/** 답변 안 링크 — 카탈로그 내부 경로는 Next Link(패널·대화 유지), 그 밖은 새 탭. */
function MarkdownLink({ href, children }: { href?: string; children: ReactNode }) {
  if (href && href.startsWith("/") && !href.startsWith("//")) {
    return <Link href={href}>{children}</Link>
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  )
}

const DatasetIndexContext = createContext<DatasetIndex | null>(null)

/** 인라인 코드 — 데이터셋 이름이면 상세 링크. */
function InlineCode({ children }: { children: ReactNode }) {
  const index = useContext(DatasetIndexContext)
  const id = typeof children === "string" ? lookupDataset(index, children) : undefined
  const chip = <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] font-normal">{children}</code>
  if (id === undefined) return chip
  return (
    <Link href={datasetHref(id)} className="no-underline hover:underline" title="데이터셋 상세 보기">
      <code className="rounded bg-sky-50 px-1 py-0.5 font-mono text-[0.85em] font-normal text-sky-700 ring-1 ring-sky-200">{children}</code>
    </Link>
  )
}

export function ChatPanel() {
  const { turns, streaming, send, regenerate, rate, abort, reset } = useAssistantStream()
  const datasetIndex = useMemo(() => buildDatasetIndex(turns), [turns])
  const [input, setInput] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // 사용자가 맨 아래 근처에 있는지 — 위로 올려 읽는 동안은 새 글자가 와도 끌어내리지 않는다
  const stickRef = useRef(true)
  // 마지막으로 본 scrollTop — 위로 줄었을 때만 "사용자가 올림" 으로 본다
  const lastTopRef = useRef(0)

  function scrollToBottom() {
    const el = scrollRef.current
    // 스트리밍 조각마다 오므로 smooth 대신 즉시 — smooth 는 애니메이션 중 위치 판정이 흔들린다
    if (!el || !stickRef.current) return
    el.scrollTo({ top: el.scrollHeight })
    lastTopRef.current = el.scrollTop
  }

  useEffect(scrollToBottom, [turns])

  // 그래프 카드처럼 그린 뒤에 높이가 늘어나는 내용도 따라간다(DEF-086)
  useEffect(() => {
    const content = contentRef.current
    if (!content || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(() => scrollToBottom())
    ro.observe(content)
    return () => ro.disconnect()
  }, [])

  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    const top = el.scrollTop
    if (el.scrollHeight - top - el.clientHeight < STICK_THRESHOLD) stickRef.current = true
    // 내용이 늘어 맨 아래와 멀어진 것은 위로 올린 것이 아니다 — scrollTop 이 줄었을 때만 따라가기를 끈다
    else if (top < lastTopRef.current - 1) stickRef.current = false
    lastTopRef.current = top
  }

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
    // 새 질문을 보내면 다시 맨 아래를 따라간다
    stickRef.current = true
    void send(v)
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-background">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        role="region"
        aria-label="대화 내용"
        className="flex-1 min-h-0 overflow-auto px-3 py-3"
      >
        <div ref={contentRef} className="space-y-3">
          {turns.length === 0 && <EmptyState onPick={(s) => setInput(s)} />}
          <DatasetIndexContext.Provider value={datasetIndex}>
            {turns.map((t, i) => (
              <Turn
                key={t.kind === "user" ? `u-${i}` : t.id || `t-${i}`}
                turn={t}
                streaming={streaming && i === turns.length - 1}
                isLast={i === turns.length - 1}
                busy={streaming}
                onRegenerate={regenerate}
                onRate={rate}
              />
            ))}
          </DatasetIndexContext.Provider>
        </div>
      </div>
      {/* 화면 낭독기 알림 — 스트리밍 중 글자마다 읽지 않도록 답변이 끝난 뒤 한 번만 */}
      <div aria-live="polite" className="sr-only">{streaming ? "" : lastAnnouncement(turns)}</div>

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
          aria-label="AI 어시스턴트에게 보낼 메시지"
          placeholder="메시지를 입력 (Shift+Enter 줄바꿈)"
          className="flex-1 resize-none overflow-y-auto rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
          disabled={streaming}
        />
        <div className="flex flex-row gap-1">
          {streaming ? (
            <Tip label="답변 중단"><button
              type="button"
              onClick={abort}
             
              aria-label="답변 중단"
              className="h-8 w-8 inline-flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted"
            >
              <Square className="h-3.5 w-3.5" />
            </button></Tip>
          ) : (
            <Tip label="메시지 전송"><button
              type="button"
              onClick={submit}
             
              aria-label="메시지 전송"
              disabled={!input.trim()}
              className="h-8 w-8 inline-flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
            </button></Tip>
          )}
          <Tip label="새 대화 시작"><button
            type="button"
            onClick={reset}
           
            aria-label="새 대화 시작"
            disabled={streaming}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button></Tip>
        </div>
      </div>
    </div>
  )
}

type TurnProps = {
  turn: ChatTurn
  streaming: boolean
  isLast: boolean
  busy: boolean
  onRegenerate: () => void
  onRate: (answerId: string, rating: "up" | "down") => Promise<boolean>
}

function Turn({ turn, streaming, isLast, busy, onRegenerate, onRate }: TurnProps) {
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
        <div className="min-w-0 space-y-1">
          <div role="alert" className="rounded-md border border-rose-300 dark:border-rose-700 px-3 py-2 text-sm text-red-600 dark:text-red-400">
            {turn.text}
          </div>
          {isLast && !busy && turn.question && (
            <button type="button" onClick={onRegenerate}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
              <RefreshCw className="h-3 w-3" />다시 시도
            </button>
          )}
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
          <div className={`rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-sm prose prose-sm dark:prose-invert max-w-none break-words
            prose-p:my-1 prose-headings:my-1.5 prose-li:my-0
            prose-h1:text-base prose-h2:text-base prose-h3:text-sm prose-h4:text-sm
            prose-table:text-sm prose-table:my-0 prose-th:py-1 prose-td:py-0.5 prose-th:whitespace-nowrap
            prose-code:before:content-none prose-code:after:content-none
            prose-pre:my-1.5 prose-pre:p-0 prose-pre:bg-transparent ${streaming ? STREAM_CARET : ""}`}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkBreaks]}
              components={{
                // 펜스 코드 블록은 <pre> 에서 처리한다 — react-markdown 10 은 code 에 inline 여부를 주지 않아,
                // 언어 표시 없는 ``` 블록을 code 에서 구분하면 인라인 코드로 오인한다.
                pre({ children }) {
                  return <CodeBlock code={children} />
                },
                // 표는 가로 스크롤 상자로 감싼다 — 칸이 많은 표가 말풍선·패널 밖으로 넘치지 않게
                table({ children }) {
                  return (
                    <div className="my-1 overflow-x-auto">
                      <table>{children}</table>
                    </div>
                  )
                },
                // 인라인 코드(`...`)는 백틱 없이 옅은 칩 — 테이블·컬럼 이름이 본문과 구분되게
                code({ children }) {
                  return <InlineCode>{children}</InlineCode>
                },
                a({ href, children }) {
                  return <MarkdownLink href={href}>{children}</MarkdownLink>
                },
              }}
            >
              {turn.text}
            </ReactMarkdown>
          </div>
        )}
        {!hasText && toolCalls.length === 0 && streaming && (
          <div className="rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-sm text-muted-foreground inline-flex items-center gap-1">
            <DotsLoading />
            <span>생각 중</span>
          </div>
        )}
        {!streaming && (turn.elapsedMs !== undefined || hasText) && (
          <AnswerMeta turn={turn} canRegenerate={isLast && !busy} onRegenerate={onRegenerate} onRate={onRate} />
        )}
      </div>
    </div>
  )
}

/** 답변 아래 한 줄 — 소요 시간·토큰, 다시 생성(마지막 답변만), 👍/👎. */
function AnswerMeta({ turn, canRegenerate, onRegenerate, onRate }: {
  turn: AssistantTurn
  canRegenerate: boolean
  onRegenerate: () => void
  onRate: (answerId: string, rating: "up" | "down") => Promise<boolean>
}) {
  const [failed, setFailed] = useState(false)
  const parts: string[] = []
  if (turn.elapsedMs !== undefined) parts.push(`${(turn.elapsedMs / 1000).toFixed(1)}초`)
  if (turn.tokensIn != null || turn.tokensOut != null) {
    parts.push(`토큰 ${(turn.tokensIn ?? 0).toLocaleString()} → ${(turn.tokensOut ?? 0).toLocaleString()}`)
  }
  if (!turn.done && turn.elapsedMs !== undefined) parts.push("중단됨")

  async function rate(r: "up" | "down") {
    setFailed(!(await onRate(turn.id, r)))
  }

  const btn = "inline-flex h-6 items-center gap-1 rounded-md px-1.5 hover:bg-muted hover:text-foreground"
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs">
      {parts.length > 0 && <span className="mr-1" title="입력 토큰 → 출력 토큰">{parts.join(" · ")}</span>}
      <span className="ml-auto inline-flex items-center gap-0.5">
        {failed && <span className="mr-1 text-rose-600">평가를 저장하지 못했습니다</span>}
        {canRegenerate && (
          <Tip label="답변 다시 생성"><button type="button" onClick={onRegenerate} className={btn} aria-label="답변 다시 생성">
            <RefreshCw className="h-3 w-3" />
          </button></Tip>
        )}
        {turn.done && (
          <>
            <Tip label="좋은 답변"><button type="button" onClick={() => rate("up")} aria-pressed={turn.rating === "up"} aria-label="좋은 답변"
              className={`${btn} ${turn.rating === "up" ? "text-emerald-600" : ""}`}>
              <ThumbsUp className="h-3 w-3" fill={turn.rating === "up" ? "currentColor" : "none"} />
            </button></Tip>
            <Tip label="아쉬운 답변"><button type="button" onClick={() => rate("down")} aria-pressed={turn.rating === "down"} aria-label="아쉬운 답변"
              className={`${btn} ${turn.rating === "down" ? "text-rose-600" : ""}`}>
              <ThumbsDown className="h-3 w-3" fill={turn.rating === "down" ? "currentColor" : "none"} />
            </button></Tip>
          </>
        )}
      </span>
    </div>
  )
}

/** 펜스 코드 블록 — 밝은 테마(oneLight) + 테두리. ``` 에 언어가 없으면 강조 없이 같은 상자로 표시. */
function CodeBlock({ code }: { code: ReactNode }) {
  // <pre> 의 자식은 code 렌더러 요소 하나 — 그 props 에 원문과 language-* 클래스가 있다
  const el = isValidElement(code) ? (code as ReactElement<{ className?: string; children?: ReactNode }>) : null
  const language = /language-(\w+)/.exec(el?.props.className || "")?.[1] ?? "text"
  const text = String(el?.props.children ?? "").replace(/\n$/, "")
  return (
    <div className="group relative">
      <CopyButton text={text} />
      <SyntaxHighlighter
        style={oneLight}
        language={language}
        PreTag="div"
        customStyle={{
          margin: 0,
          padding: "0.75rem",
          borderRadius: "0.5rem",
          border: "1px solid var(--border)",
          fontSize: "0.875rem",
          fontFamily: "D2Coding, monospace",
        }}
        // 테마의 code 글꼴(Fira Code …)이 상자의 D2Coding 을 덮지 않게
        codeTagProps={{ style: { ...oneLight['code[class*="language-"]'], fontFamily: "inherit" } }}
      >
        {text}
      </SyntaxHighlighter>
    </div>
  )
}

/** 코드 블록 오른쪽 위 복사 버튼 — 마우스를 올리거나 포커스하면 보이고, 복사하면 잠깐 "복사됨". */
function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])

  async function onCopy() {
    const ok = await tryCopyText(text)
    setState(ok ? "copied" : "failed")
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setState("idle"), 1500)
  }

  const label = state === "copied" ? "복사됨" : state === "failed" ? "복사 실패" : "복사"
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={state === "idle" ? "코드 복사" : label}
      className={`absolute right-1.5 top-1.5 z-10 inline-flex items-center gap-1 rounded-md border border-border bg-background/90 px-1.5 py-0.5 text-xs text-muted-foreground shadow-sm transition-opacity hover:text-foreground focus-visible:opacity-100
        ${state === "idle" ? "opacity-0 group-hover:opacity-100" : "opacity-100"}`}
    >
      {state === "copied" ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
      {label}
    </button>
  )
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  if (role === "user") {
    return (
      <div className="h-7 w-7 shrink-0 rounded-full bg-muted flex items-center justify-center border border-border">
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
        <div className="text-sm max-w-[280px]">
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
