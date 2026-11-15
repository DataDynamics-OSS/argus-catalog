"use client"

/**
 * 우하단 floating AI 어시스턴트 — 모든 dashboard 페이지에서 노출.
 *
 * 게이팅: GET /api/v1/ai/status — 설정 > AI 어시스턴트에서 활성화
 * (assistant_enabled=true)했고 기능 권한(ai.assistant)이 있을 때만 버튼을 표시.
 * 패널 토글 / ESC 닫기 / 채팅은 ChatPanel 에 위임.
 * 크기 조절: 패널은 우하단에 고정되므로 왼쪽 위 모서리·왼쪽 변·위쪽 변을 끌어 바꾼다(더블클릭은 기본 크기).
 * 크기는 이 브라우저에만 기억한다(localStorage — 실패해도 기본 크기로 동작). 헤더의 최대화 버튼으로 화면 대부분을 덮는다.
 * 닫기는 숨기기만 한다 — 패널(ChatPanel)을 언마운트하면 대화와 진행 중인 답변이 사라지므로 한 번 연 뒤에는 계속 마운트해 둔다(DEF-048).
 */

import { Tip } from "@/components/tip"
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react"
import { Maximize2, MessageCircle, Minimize2, X, Sparkles } from "lucide-react"

import { authFetch } from "@/features/auth/auth-fetch"
import { usePermissions } from "@/features/permissions/use-permissions"
import { ChatPanel } from "./chat-panel"

type AiStatus = { enabled: boolean; provider: string | null; model: string | null; assistant_enabled?: boolean }

type Size = { w: number; h: number }
type Edge = "corner" | "left" | "top"

const DEFAULT_SIZE: Size = { w: 480, h: 720 }
const MIN_SIZE: Size = { w: 360, h: 420 }
const SIZE_KEY = "argus.assistant.panelSize"
const KEY_STEP = 20

/** 화면 안에 들어오게 — 좌우 여백 2rem, 위아래는 버튼·여백 6rem(패널 max-w·max-h 클래스와 같은 기준). */
function clampSize(size: Size): Size {
  const maxW = Math.max(MIN_SIZE.w, window.innerWidth - 32)
  const maxH = Math.max(MIN_SIZE.h, window.innerHeight - 96)
  return {
    w: Math.round(Math.min(Math.max(size.w, MIN_SIZE.w), maxW)),
    h: Math.round(Math.min(Math.max(size.h, MIN_SIZE.h), maxH)),
  }
}

function loadSize(): Size {
  try {
    const raw = window.localStorage.getItem(SIZE_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<Size>
      if (typeof v.w === "number" && typeof v.h === "number") return clampSize({ w: v.w, h: v.h })
    }
  } catch {
    // 저장소를 쓸 수 없으면 기본 크기
  }
  return DEFAULT_SIZE
}

function saveSize(size: Size) {
  try {
    window.localStorage.setItem(SIZE_KEY, JSON.stringify(size))
  } catch {
    // 기억하지 못할 뿐 동작에는 영향 없음
  }
}

export function FloatingChat() {
  const [open, setOpen] = useState(false)
  // 한 번이라도 열었는지 — 연 뒤로는 닫아도 패널을 마운트해 둔다(DEF-048)
  const [mounted, setMounted] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [size, setSize] = useState<Size>(DEFAULT_SIZE)
  const [resizing, setResizing] = useState(false)
  const drag = useRef<{ edge: Edge; x: number; y: number; size: Size } | null>(null)
  const { canUseFeature } = usePermissions()

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

  // 저장된 크기는 패널을 열 때 읽는다(SSR 에는 window 가 없음)
  useEffect(() => {
    if (open) setSize(loadSize())
  }, [open])

  function onResizeStart(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    // 어느 손잡이인지는 data-edge 로 받는다
    const edge = (e.currentTarget.dataset.edge ?? "corner") as Edge
    drag.current = { edge, x: e.clientX, y: e.clientY, size }
    setResizing(true)
  }

  function onResizeMove(e: ReactPointerEvent<HTMLDivElement>) {
    const d = drag.current
    if (!d) return
    // 우하단 고정이라 왼쪽·위로 끌수록 커진다
    setSize(clampSize({
      w: d.edge === "top" ? d.size.w : d.size.w + (d.x - e.clientX),
      h: d.edge === "left" ? d.size.h : d.size.h + (d.y - e.clientY),
    }))
  }

  function onResizeEnd(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drag.current) return
    drag.current = null
    setResizing(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    setSize((s) => {
      saveSize(s)
      return s
    })
  }

  function resetSize() {
    const s = clampSize(DEFAULT_SIZE)
    setSize(s)
    saveSize(s)
  }

  // 키보드: 모서리 손잡이에 포커스한 뒤 방향키(←↑ 크게, →↓ 작게), Home 은 기본 크기
  function onResizeKey(e: ReactKeyboardEvent<HTMLDivElement>) {
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [KEY_STEP, 0], ArrowRight: [-KEY_STEP, 0], ArrowUp: [0, KEY_STEP], ArrowDown: [0, -KEY_STEP],
    }
    if (e.key === "Home") {
      e.preventDefault()
      resetSize()
      return
    }
    const d = delta[e.key]
    if (!d) return
    e.preventDefault()
    const next = clampSize({ w: size.w + d[0], h: size.h + d[1] })
    setSize(next)
    saveSize(next)
  }

  // AI 어시스턴트 비활성 또는 기능 권한(ai.assistant) 차단 시 비표시
  if (!status?.assistant_enabled || !canUseFeature("ai.assistant")) return null

  return (
    <>
      {mounted && (
        <div
          role="dialog"
          aria-label="AI 어시스턴트"
          // 닫혀 있으면 보이지 않게만 — visibility:hidden 이라 크기는 유지되고(미니 그래프 배치), 탭 이동·낭독 대상에서는 빠진다
          aria-hidden={!open}
          style={maximized ? { width: "calc(100vw - 2rem)", height: "calc(100vh - 6rem)" } : { width: size.w, height: size.h }}
          className={`fixed bottom-20 right-4 z-50 max-w-[calc(100vw-2rem)] max-h-[calc(100vh-6rem)] rounded-xl border border-border bg-card shadow-2xl flex flex-col overflow-hidden
            motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-95 motion-safe:slide-in-from-bottom-2 motion-safe:duration-200
            ${resizing ? "select-none" : ""} ${open ? "" : "invisible pointer-events-none"}`}
        >
          {/* 크기 조절 손잡이 — 왼쪽 변·위쪽 변·왼쪽 위 모서리. 최대화 중에는 숨긴다 */}
          {!maximized && (<>
          <div
            aria-hidden
            className="absolute left-0 top-3 bottom-0 w-1.5 z-10 cursor-ew-resize touch-none hover:bg-primary/20"
            data-edge="left"
            onPointerDown={onResizeStart}
            onPointerMove={onResizeMove}
            onPointerUp={onResizeEnd}
            onPointerCancel={onResizeEnd}
            onDoubleClick={resetSize}
          />
          <div
            aria-hidden
            className="absolute top-0 left-3 right-0 h-1.5 z-10 cursor-ns-resize touch-none hover:bg-primary/20"
            data-edge="top"
            onPointerDown={onResizeStart}
            onPointerMove={onResizeMove}
            onPointerUp={onResizeEnd}
            onPointerCancel={onResizeEnd}
            onDoubleClick={resetSize}
          />
          <div
            role="separator"
            tabIndex={0}
            aria-label="창 크기 조절 (방향키, Home 은 기본 크기)"
            aria-valuenow={size.w}
            title="끌어서 크기 조절 · 더블클릭하면 기본 크기"
            className="absolute left-0 top-0 h-3 w-3 z-20 cursor-nwse-resize touch-none rounded-tl-xl hover:bg-primary/30 focus-visible:bg-primary/30 focus-visible:outline-none"
            onKeyDown={onResizeKey}
            data-edge="corner"
            onPointerDown={onResizeStart}
            onPointerMove={onResizeMove}
            onPointerUp={onResizeEnd}
            onPointerCancel={onResizeEnd}
            onDoubleClick={resetSize}
          />
          </>)}
          <div className="flex items-center justify-between px-3 h-12 border-b border-border bg-gradient-to-r from-sky-50 to-violet-50 dark:from-sky-950/30 dark:to-violet-950/30">
            <div className="flex items-center gap-2 min-w-0">
              <div className="h-7 w-7 shrink-0 rounded-lg bg-gradient-to-br from-sky-400 to-violet-500 flex items-center justify-center text-white shadow-sm">
                <Sparkles className="h-3.5 w-3.5" />
              </div>
              <span className="text-sm font-semibold leading-tight truncate">AI 어시스턴트</span>
            </div>
            <div className="flex items-center gap-0.5">
              <Tip label={maximized ? "원래 크기로" : "최대화"}><button
                type="button"
                onClick={() => setMaximized((v) => !v)}
                className="h-7 w-7 inline-flex items-center justify-center rounded-md hover:bg-muted/60"
                aria-label={maximized ? "원래 크기로" : "최대화"}
                aria-pressed={maximized}
               
              >
                {maximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
              </button></Tip>
              <Tip label="AI 어시스턴트 닫기"><button
                type="button"
                onClick={() => setOpen(false)}
                className="h-7 w-7 inline-flex items-center justify-center rounded-md hover:bg-muted/60"
                aria-label="AI 어시스턴트 닫기"
               
              >
                <X className="h-4 w-4" />
              </button></Tip>
            </div>
          </div>
          <ChatPanel />
        </div>
      )}

      <Tip label={open ? "AI 어시스턴트 닫기" : "AI 어시스턴트 열기"}><button
        type="button"
        onClick={() => {
          setMounted(true)
          setOpen((v) => !v)
        }}
       
        aria-label={open ? "AI 어시스턴트 닫기" : "AI 어시스턴트 열기"}
        aria-expanded={open}
        className="fixed bottom-4 right-4 z-50 h-12 w-12 inline-flex items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-violet-600 text-white shadow-lg hover:shadow-xl hover:scale-105 transition-all"
      >
        {open ? <X className="h-5 w-5" /> : <MessageCircle className="h-5 w-5" />}
      </button></Tip>
    </>
  )
}
