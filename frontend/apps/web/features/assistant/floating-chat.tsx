"use client"

/**
 * 우하단 floating AI 어시스턴트 — 모든 dashboard 페이지에서 노출.
 *
 * 게이팅: GET /api/v1/ai/status — 설정 > AI 어시스턴트에서 활성화
 * (assistant_enabled=true)했고 기능 권한(ai.assistant)이 있을 때만 버튼을 표시.
 * 패널 토글 / ESC 닫기 / 채팅은 ChatPanel 에 위임.
 */

import { useEffect, useState } from "react"
import { MessageCircle, X, Sparkles } from "lucide-react"

import { authFetch } from "@/features/auth/auth-fetch"
import { usePermissions } from "@/features/permissions/use-permissions"
import { ChatPanel } from "./chat-panel"

type AiStatus = { enabled: boolean; provider: string | null; model: string | null; assistant_enabled?: boolean }

export function FloatingChat() {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const { isFeatureAllowed } = usePermissions()

  useEffect(() => {
    let cancelled = false
    authFetch("/api/v1/ai/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: AiStatus | null) => { if (!cancelled) setStatus(d) })
      .catch(() => { if (!cancelled) setStatus(null) })
    return () => { cancelled = true }
  }, [])

  // ESC 로 닫기
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open])

  // AI 어시스턴트 비활성 또는 기능 권한(ai.assistant) 차단 시 비표시
  if (!status?.assistant_enabled || !isFeatureAllowed("ai.assistant")) return null

  return (
    <>
      {open && (
        <div
          className="fixed bottom-20 right-4 z-50 w-[480px] max-w-[calc(100vw-2rem)] h-[720px] max-h-[calc(100vh-6rem)] rounded-xl border border-border bg-card shadow-2xl flex flex-col overflow-hidden
            motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-95 motion-safe:slide-in-from-bottom-2 motion-safe:duration-200"
        >
          <div className="flex items-center justify-between px-3 h-12 border-b border-border bg-gradient-to-r from-sky-50 to-violet-50 dark:from-sky-950/30 dark:to-violet-950/30">
            <div className="flex items-center gap-2 min-w-0">
              <div className="h-7 w-7 shrink-0 rounded-lg bg-gradient-to-br from-sky-400 to-violet-500 flex items-center justify-center text-white shadow-sm">
                <Sparkles className="h-3.5 w-3.5" />
              </div>
              <span className="text-sm font-semibold leading-tight truncate">AI 어시스턴트</span>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="h-7 w-7 inline-flex items-center justify-center rounded-md hover:bg-muted/60"
              aria-label="close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <ChatPanel />
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="AI 어시스턴트"
        className="fixed bottom-4 right-4 z-50 h-12 w-12 inline-flex items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-violet-600 text-white shadow-lg hover:shadow-xl hover:scale-105 transition-all"
      >
        {open ? <X className="h-5 w-5" /> : <MessageCircle className="h-5 w-5" />}
      </button>
    </>
  )
}
