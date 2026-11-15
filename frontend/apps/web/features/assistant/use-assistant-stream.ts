"use client"

/**
 * AI 어시스턴트 SSE 스트리밍 훅.
 *
 * POST /api/v1/ai/assistant/chat 을 fetch + ReadableStream 으로 소비한다
 * (EventSource 는 POST/Authorization 헤더 불가). 한 이벤트 = `data: {...}\n\n`.
 *
 * 이벤트: text_delta(점진 텍스트) / tool_call·tool_result(도구 실행) / usage(토큰·conversation_id) / done / error.
 * 서버(agent serve)는 LLM 의 진짜 토큰 스트리밍을 그대로 흘려보낸다 — 이 훅은
 * text_delta 를 누적(append)하므로 청크 크기와 무관하게 동작한다.
 *
 * - 답변마다 id·질문·소요 시간·토큰을 붙인다 — 다시 생성·평가(👍/👎)에 쓴다 (TODO-068)
 * - 대화는 sessionStorage 에 보관해 새로고침 뒤에도 이어진다(같은 탭 안에서만) (DEF-048)
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { authFetch } from "@/features/auth/auth-fetch"

export type StreamEvent =
  | { type: "text_delta"; data: { text: string } }
  | { type: "tool_call"; data: { id: string; name: string; args: Record<string, unknown> } }
  | { type: "tool_result"; data: { id: string; name: string; result: unknown } }
  | { type: "usage"; data: { tokens_in?: number; tokens_out?: number; conversation_id?: string } }
  | { type: "done"; data: Record<string, never> }
  | { type: "error"; data: { reason: string } }

export type ToolCall = {
  id: string
  name: string
  args: Record<string, unknown>
  result?: unknown   // tool_result 도착 전까지 undefined = "실행 중" 표시
}

export type Rating = "up" | "down" | null

export type AssistantTurn = {
  kind: "assistant"
  id: string
  question: string
  text: string
  tool_calls?: ToolCall[]
  startedAt: number
  elapsedMs?: number
  tokensIn?: number
  tokensOut?: number
  done?: boolean
  rating?: Rating
}

export type ChatTurn =
  | { kind: "user"; text: string }
  | AssistantTurn
  | { kind: "error"; id: string; question: string; text: string }

const STORAGE_KEY = "argus.assistant.session"

/** 답변 식별자 — crypto.randomUUID 는 보안 컨텍스트(https·localhost)에서만 있어 http 접속은 대체 값을 쓴다. */
function newId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID()
  } catch {
    // 아래 대체 값
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

type Saved = { turns: ChatTurn[]; conversationId: string | null }

function loadSaved(): Saved {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<Saved>
      if (Array.isArray(v.turns)) return { turns: v.turns, conversationId: v.conversationId ?? null }
    }
  } catch {
    // 보관본을 못 읽으면 빈 대화
  }
  return { turns: [], conversationId: null }
}

function save(s: Saved) {
  try {
    if (s.turns.length) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s))
    else window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // 용량 초과 등 — 새로고침 뒤 이어지지 않을 뿐
  }
}

export function useAssistantStream() {
  // 이 훅은 ai/status 확인 뒤 클라이언트에서만 마운트된다(FloatingChat) — 첫 렌더에 보관본을 읽어도 된다
  const [initial] = useState<Saved>(() => (typeof window === "undefined" ? { turns: [], conversationId: null } : loadSaved()))
  const [turns, setTurns] = useState<ChatTurn[]>(initial.turns)
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(initial.conversationId)
  const abortRef = useRef<AbortController | null>(null)

  // 답변이 끝날 때마다 보관 — 스트리밍 중 조각마다 쓰지 않는다
  useEffect(() => {
    if (!streaming) save({ turns, conversationId })
  }, [turns, conversationId, streaming])

  const run = useCallback(
    // replace: 마지막 답변(또는 오류) 자리에 새 답변을 받는다. dropServerAnswer: 서버 이력의 마지막 질문·답변도 지운다
    async (message: string, replace: boolean, dropServerAnswer = false) => {
      if (!message.trim() || streaming) return
      const ac = new AbortController()
      abortRef.current = ac
      setStreaming(true)
      const answer: AssistantTurn = {
        kind: "assistant", id: newId(), question: message, text: "", tool_calls: [], startedAt: Date.now(),
      }
      setTurns((t) => replace
        // 다시 생성 — 마지막 답변(또는 오류)을 새 답변 자리로 바꾼다. 질문은 그대로 둔다
        ? [...(t.length && t[t.length - 1]!.kind !== "user" ? t.slice(0, -1) : t), answer]
        : [...t, { kind: "user", text: message }, answer])

      const fail = (text: string) =>
        setTurns((t) => replaceLast(t, { kind: "error", id: answer.id, question: message, text }))
      try {
        const resp = await authFetch("/api/v1/ai/assistant/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, conversation_id: conversationId, ...(dropServerAnswer ? { regenerate: true } : {}) }),
          signal: ac.signal,
        })
        if (!resp.ok || !resp.body) {
          const err = await resp.text().catch(() => "응답 스트림을 시작하지 못했습니다.")
          fail(err.slice(0, 400))
          return
        }

        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buf = ""
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          let idx: number
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const block = buf.slice(0, idx)
            buf = buf.slice(idx + 2)
            const line = block.split("\n").find((l) => l.startsWith("data: "))
            if (!line) continue
            try {
              const ev: StreamEvent = JSON.parse(line.slice(6))
              setTurns((prev) => applyEvent(prev, ev))
              if (ev.type === "usage" && ev.data.conversation_id) {
                setConversationId(ev.data.conversation_id)
              }
            } catch {
              // malformed line 무시
            }
          }
        }
      } catch (e) {
        if ((e as Error)?.name !== "AbortError") fail(String((e as Error)?.message ?? e))
      } finally {
        // done 이벤트 없이 끝났어도(중단·끊김) 걸린 시간은 남긴다
        setTurns((prev) => finishLast(prev))
        setStreaming(false)
        abortRef.current = null
      }
    },
    [conversationId, streaming],
  )

  const send = useCallback((message: string) => run(message, false), [run])

  /**
   * 마지막 답변(또는 오류)을 같은 질문으로 다시 받는다.
   * 서버 이력에는 끝까지 받은 답변(done)만 남으므로, 그때만 서버에 이전 답변을 지우라고 한다(regenerate).
   * 오류·중단으로 끝난 답변을 다시 시도할 때 보내면 그 앞의 정상 질문·답변이 지워진다(DEF-076).
   */
  const regenerate = useCallback(() => {
    const last = turns[turns.length - 1]
    if (!last || last.kind === "user") return
    void run(last.question, true, dropsServerAnswer(last))
  }, [run, turns])

  /** 답변 평가 — 화면을 먼저 바꾸고, 저장에 실패하면 되돌린다. 같은 평가를 다시 누르면 취소. */
  const rate = useCallback(async (answerId: string, rating: "up" | "down") => {
    const target = turns.find((t): t is AssistantTurn => t.kind === "assistant" && t.id === answerId)
    if (!target) return false
    const prev = target.rating ?? null
    const next: Rating = prev === rating ? null : rating
    const setRating = (r: Rating) =>
      setTurns((ts) => ts.map((t) => (t.kind === "assistant" && t.id === answerId ? { ...t, rating: r } : t)))
    setRating(next)
    try {
      const resp = await authFetch("/api/v1/ai/assistant/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answer_id: answerId, conversation_id: conversationId, rating: next,
          question: target.question, answer: target.text,
        }),
      })
      if (!resp.ok) throw new Error(String(resp.status))
      return true
    } catch {
      setRating(prev)
      return false
    }
  }, [turns, conversationId])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setTurns([])
    setStreaming(false)
    setConversationId(null)
  }, [])

  return { turns, streaming, send, regenerate, rate, abort, reset }
}

function replaceLast(turns: ChatTurn[], replacement: ChatTurn): ChatTurn[] {
  if (!turns.length) return [replacement]
  return [...turns.slice(0, -1), replacement]
}

/** 다시 생성할 때 서버 이력의 마지막 질문·답변을 지워도 되는가 — 끝까지 받은 답변(done)만 서버 이력에 있다(DEF-076). */
export function dropsServerAnswer(last: ChatTurn | undefined): boolean {
  return last?.kind === "assistant" && last.done === true
}

function finishLast(turns: ChatTurn[]): ChatTurn[] {
  const last = turns[turns.length - 1]
  if (!last || last.kind !== "assistant" || last.elapsedMs !== undefined) return turns
  return replaceLast(turns, { ...last, elapsedMs: Date.now() - last.startedAt })
}

export function applyEvent(prev: ChatTurn[], ev: StreamEvent): ChatTurn[] {
  if (!prev.length) return prev
  const last = prev[prev.length - 1]!
  if (ev.type === "text_delta" && last.kind === "assistant") {
    return replaceLast(prev, { ...last, text: last.text + ev.data.text })
  }
  if (ev.type === "tool_call" && last.kind === "assistant") {
    // 도구 호출 시작 — "실행 중" 카드로 표시 (result 도착 시 완료 전환)
    return replaceLast(prev, {
      ...last,
      tool_calls: [...(last.tool_calls ?? []), { id: ev.data.id, name: ev.data.name, args: ev.data.args }],
    })
  }
  if (ev.type === "tool_result" && last.kind === "assistant") {
    const calls = (last.tool_calls ?? []).map((c) =>
      c.id === ev.data.id ? { ...c, result: ev.data.result } : c)
    return replaceLast(prev, { ...last, tool_calls: calls })
  }
  if (ev.type === "usage" && last.kind === "assistant") {
    return replaceLast(prev, { ...last, tokensIn: ev.data.tokens_in, tokensOut: ev.data.tokens_out })
  }
  if (ev.type === "done" && last.kind === "assistant") {
    return replaceLast(prev, { ...last, done: true, elapsedMs: Date.now() - last.startedAt })
  }
  if (ev.type === "error") {
    const question = last.kind === "user" ? last.text : last.question
    const id = last.kind === "user" ? "" : last.id
    return replaceLast(prev, { kind: "error", id, question, text: ev.data.reason })
  }
  return prev
}
