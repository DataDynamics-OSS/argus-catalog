"use client"

/**
 * AI 어시스턴트 SSE 스트리밍 훅.
 *
 * POST /api/v1/ai/assistant/chat 을 fetch + ReadableStream 으로 소비한다
 * (EventSource 는 POST/Authorization 헤더 불가). 한 이벤트 = `data: {...}\n\n`.
 *
 * 이벤트: text_delta(점진 텍스트) / usage(토큰·conversation_id) / done / error.
 * 서버(agent serve)는 LLM 의 진짜 토큰 스트리밍을 그대로 흘려보낸다 — 이 훅은
 * text_delta 를 누적(append)하므로 청크 크기와 무관하게 동작한다.
 */

import { useCallback, useRef, useState } from "react"
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

export type ChatTurn =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; tool_calls?: ToolCall[] }
  | { kind: "error"; text: string }

export function useAssistantStream() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const send = useCallback(
    async (message: string) => {
      if (!message.trim() || streaming) return
      const ac = new AbortController()
      abortRef.current = ac
      setStreaming(true)
      setTurns((t) => [...t, { kind: "user", text: message }, { kind: "assistant", text: "", tool_calls: [] }])

      try {
        const resp = await authFetch("/api/v1/ai/assistant/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, conversation_id: conversationId }),
          signal: ac.signal,
        })
        if (!resp.ok || !resp.body) {
          const err = await resp.text().catch(() => "스트림 시작 실패")
          setTurns((t) => replaceLast(t, { kind: "error", text: err.slice(0, 400) }))
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
        if ((e as Error)?.name !== "AbortError") {
          setTurns((t) => replaceLast(t, { kind: "error", text: String((e as Error)?.message ?? e) }))
        }
      } finally {
        setStreaming(false)
        abortRef.current = null
      }
    },
    [conversationId, streaming],
  )

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

  return { turns, streaming, send, abort, reset }
}

function replaceLast(turns: ChatTurn[], replacement: ChatTurn): ChatTurn[] {
  if (!turns.length) return [replacement]
  return [...turns.slice(0, -1), replacement]
}

function applyEvent(prev: ChatTurn[], ev: StreamEvent): ChatTurn[] {
  if (!prev.length) return prev
  const last = prev[prev.length - 1]!
  if (ev.type === "text_delta" && last.kind === "assistant") {
    return [...prev.slice(0, -1), { ...last, text: last.text + ev.data.text }]
  }
  if (ev.type === "tool_call" && last.kind === "assistant") {
    // 도구 호출 시작 — "실행 중" 카드로 표시 (result 도착 시 완료 전환)
    return [...prev.slice(0, -1), {
      ...last,
      tool_calls: [...(last.tool_calls ?? []), { id: ev.data.id, name: ev.data.name, args: ev.data.args }],
    }]
  }
  if (ev.type === "tool_result" && last.kind === "assistant") {
    const calls = (last.tool_calls ?? []).map((c) =>
      c.id === ev.data.id ? { ...c, result: ev.data.result } : c)
    return [...prev.slice(0, -1), { ...last, tool_calls: calls }]
  }
  if (ev.type === "error") {
    return [...prev.slice(0, -1), { kind: "error", text: ev.data.reason }]
  }
  return prev
}
