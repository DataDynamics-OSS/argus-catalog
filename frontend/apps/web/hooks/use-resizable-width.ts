"use client"

import { useEffect, useRef, useState } from "react"

type Options = {
  /** 처음 열 때(저장값이 없을 때) 폭(px). */
  defaultWidth: number
  min: number
  max: number
}

/**
 * 좌우 분할 화면의 왼쪽 패널 폭 — separator 를 드래그해 조절하고, 마지막 폭을 이 브라우저의
 * localStorage(`storageKey`)에 기억해 다음에 열 때 그대로 쓴다.
 *
 * 반환한 `startResize` 를 separator 의 `onMouseDown` 에 연결한다. 저장값은 [min, max] 로 맞추고,
 * 숫자가 아니거나 저장소를 쓸 수 없으면(비공개 창 등) `defaultWidth` 로 연다.
 * 저장값은 마운트 후에 읽는다 — 서버 렌더와 첫 클라이언트 렌더를 같게 해 hydration 불일치를 막는다.
 */
export function useResizableWidth(storageKey: string, { defaultWidth, min, max }: Options) {
  const [width, setWidth] = useState(defaultWidth)
  const dragRef = useRef<{ startX: number; startW: number; last: number } | null>(null)

  const clamp = (w: number) => Math.min(max, Math.max(min, Math.round(w)))

  useEffect(() => {
    try {
      const n = Number(window.localStorage.getItem(storageKey))
      if (Number.isFinite(n) && n > 0) setWidth(Math.min(max, Math.max(min, Math.round(n))))
    } catch {
      /* localStorage 를 쓸 수 없는 환경 — 기본 폭 */
    }
  }, [storageKey, min, max])

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: width, last: width }
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return
      const next = clamp(dragRef.current.startW + (ev.clientX - dragRef.current.startX))
      dragRef.current.last = next
      setWidth(next)
    }
    const onUp = () => {
      if (dragRef.current) {
        try {
          window.localStorage.setItem(storageKey, String(dragRef.current.last))
        } catch {
          /* localStorage 비허용/가득참 환경은 무시 */
        }
      }
      dragRef.current = null
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
      document.body.style.userSelect = ""
      document.body.style.cursor = ""
    }
    document.body.style.userSelect = "none"
    document.body.style.cursor = "col-resize"
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }

  return { width, startResize }
}
