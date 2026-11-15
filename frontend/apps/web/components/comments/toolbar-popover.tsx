"use client"

// 편집기 툴바의 선택 창(이모지·특수문자·글자 색·표 삽입)을 띄우는 공용 팝오버.
//
// 편집기 테두리 상자는 둥근 모서리 때문에 overflow-hidden 이라, 그 안에 absolute 로 띄우면 편집 영역이 작을 때
// 상자 밖으로 나가는 부분이 잘린다(DEF-072). 그래서 body 에 포털로 띄우고 버튼 위치를 기준으로 fixed 배치한다.
// - 아래 공간이 모자라면 위로 펼치고, 양쪽 다 모자라면 넓은 쪽에 붙여 높이를 줄이고 안에서 스크롤한다.
// - 가로는 화면 안으로 맞춘다. 스크롤·창 크기 변경 때 다시 맞춘다.
// - 바깥을 누르거나 Esc 를 누르면 닫는다(버튼·팝오버 안은 제외).

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

const GAP = 4 // 버튼과 팝오버 사이
const MARGIN = 8 // 화면 가장자리 여백

type Placement = { top: number; left: number; maxHeight?: number }

/** 버튼 위치·팝오버 크기·화면 크기로 팝오버 자리를 정한다(순수 함수 — 테스트 대상). */
export function placePopover(
  anchor: { top: number; bottom: number; left: number },
  panel: { width: number; height: number },
  viewport: { width: number; height: number },
): Placement {
  const below = viewport.height - anchor.bottom - GAP - MARGIN
  const above = anchor.top - GAP - MARGIN
  const left = Math.max(MARGIN, Math.min(anchor.left, viewport.width - panel.width - MARGIN))

  if (panel.height <= below) return { top: anchor.bottom + GAP, left }
  if (panel.height <= above) return { top: anchor.top - GAP - panel.height, left }
  // 어느 쪽에도 다 들어가지 않으면 넓은 쪽에 붙이고 높이를 줄인다
  if (below >= above) return { top: anchor.bottom + GAP, left, maxHeight: Math.max(below, 0) }
  const maxHeight = Math.max(above, 0)
  return { top: anchor.top - GAP - maxHeight, left, maxHeight }
}

export function ToolbarPopover({
  anchorRef,
  open,
  onClose,
  width,
  className = "",
  children,
}: {
  /** 팝오버를 여는 버튼(을 감싼 요소) */
  anchorRef: React.RefObject<HTMLElement | null>
  open: boolean
  onClose: () => void
  /** 고정 폭(px). 주지 않으면 내용 폭. */
  width?: number
  className?: string
  children: React.ReactNode
}) {
  const panelRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<Placement | null>(null)

  const update = useCallback(() => {
    const anchor = anchorRef.current
    const panel = panelRef.current
    if (!anchor || !panel) return
    // maxHeight 로 줄여 둔 상태에서 재면 원래 높이를 모르므로 scrollHeight 로 잰다
    const rect = anchor.getBoundingClientRect()
    setPos(
      placePopover(
        rect,
        { width: panel.offsetWidth, height: panel.scrollHeight },
        { width: window.innerWidth, height: window.innerHeight },
      ),
    )
  }, [anchorRef])

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    update()
  }, [open, update])

  useEffect(() => {
    if (!open) return
    // 내용이 바뀌어 높이가 달라질 때(탭 전환·검색 결과)도 다시 맞춘다
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => update()) : null
    if (ro && panelRef.current) ro.observe(panelRef.current.firstElementChild ?? panelRef.current)
    window.addEventListener("resize", update)
    window.addEventListener("scroll", update, true)
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (anchorRef.current?.contains(t) || panelRef.current?.contains(t)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      ro?.disconnect()
      window.removeEventListener("resize", update)
      window.removeEventListener("scroll", update, true)
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open, update, onClose, anchorRef])

  if (!open || typeof document === "undefined") return null
  return createPortal(
    <div
      ref={panelRef}
      className={"fixed z-50 overflow-y-auto rounded-md border border-input bg-white shadow-lg p-2 " + className}
      style={{
        top: pos?.top ?? 0,
        left: pos?.left ?? 0,
        width,
        maxHeight: pos?.maxHeight,
        // 자리를 정하기 전 첫 그림은 숨긴다(0,0 에서 깜빡이지 않게)
        visibility: pos ? "visible" : "hidden",
      }}
    >
      <div>{children}</div>
    </div>,
    document.body,
  )
}
