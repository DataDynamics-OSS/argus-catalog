"use client"

/**
 * Resizable Image extension for TipTap.
 *
 * ``@tiptap/extension-image`` 의 기본 Image 를 확장해:
 *  1) ``width`` (string) 속성 추가 — 예: "30%", "200px".
 *  2) React NodeView 로 렌더 — 선택 시 우상단에 25 / 50 / 75 / 100% 버튼,
 *     우하단에 corner 드래그 핸들 (포인터 드래그로 폭 비율 조절).
 *
 * 본문 HTML 에는 ``<img src="..." style="width:30%">`` 같이 width 가 inline
 * style 로 저장돼 read-only viewer 에서도 동일 비율로 보인다.
 */

import { useCallback, useRef } from "react"
import { mergeAttributes } from "@tiptap/core"
import { Image as TiptapImage } from "@tiptap/extension-image"
import { ReactNodeViewRenderer, NodeViewWrapper } from "@tiptap/react"
import type { NodeViewProps } from "@tiptap/react"

const PRESETS: Array<{ label: string; value: string }> = [
  { label: "25%", value: "25%" },
  { label: "50%", value: "50%" },
  { label: "75%", value: "75%" },
  { label: "100%", value: "100%" },
  { label: "원본", value: "" }, // 빈 값 = 속성 제거 → 원본 크기.
]

// 테두리 색 — 사용자 명시.
const BORDER_COLOR = "#CCCCCC"

function ResizableImageView(props: NodeViewProps) {
  const { node, updateAttributes, selected, editor } = props
  const widthAttr: string = (node.attrs.width as string | undefined) ?? ""
  const borderOn: boolean = !!node.attrs.border
  const wrapperRef = useRef<HTMLSpanElement | null>(null)
  const editable = editor?.isEditable ?? false

  /** corner 드래그 — 새 폭 = (마우스 X - 컨테이너 left) / 컨테이너 width × 100 %. */
  const onCornerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!editable) return
      e.preventDefault()
      e.stopPropagation()
      const wrap = wrapperRef.current
      // 폭 기준은 editor 의 prose 영역(가장 가까운 block 조상) — paragraph.
      // wrapper 자체는 inline-block 이라 width 가 img 따라 변해서 기준으로 부적합.
      const ref = wrap?.closest("p, div") as HTMLElement | null
      const parent = ref ?? wrap?.parentElement ?? null
      if (!wrap || !parent) return
      const parentRect = parent.getBoundingClientRect()
      ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
      const onMove = (ev: PointerEvent) => {
        const x = Math.min(
          Math.max(ev.clientX - parentRect.left, 30),
          parentRect.width,
        )
        const pct = Math.max(10, Math.min(100, Math.round((x / parentRect.width) * 100)))
        updateAttributes({ width: `${pct}%` })
      }
      const onUp = (ev: PointerEvent) => {
        ;(e.target as HTMLElement).releasePointerCapture(ev.pointerId)
        window.removeEventListener("pointermove", onMove)
        window.removeEventListener("pointerup", onUp)
      }
      window.addEventListener("pointermove", onMove)
      window.addEventListener("pointerup", onUp)
    },
    [editable, updateAttributes],
  )

  const setPreset = useCallback(
    (v: string) => {
      // 빈 값이면 width attr 제거 — 원본 크기 복원.
      updateAttributes({ width: v || null })
    },
    [updateAttributes],
  )

  // NodeViewWrapper 는 단순 inline-block wrapper — img 자체에 width 적용해
  // parent 와 child 의 width auto 가 circular 해져 폭이 0 으로 collapse 되는
  // 문제 회피.
  return (
    <NodeViewWrapper
      ref={wrapperRef as never}
      as="span"
      className="tiptap-image-wrap"
      style={{
        position: "relative",
        display: "inline-block",
        // selection 시 dashed outline 으로 활성 표시.
        outline: selected && editable ? "2px dashed rgb(59 130 246 / 0.6)" : undefined,
        outlineOffset: "2px",
        borderRadius: 4,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={(node.attrs.src as string) ?? ""}
        alt={(node.attrs.alt as string) ?? ""}
        title={(node.attrs.title as string) ?? ""}
        draggable={!editable}
        style={{
          display: "block",
          width: widthAttr || undefined,
          maxWidth: "100%",
          height: "auto",
          borderRadius: 4,
          border: borderOn ? `1px solid ${BORDER_COLOR}` : undefined,
        }}
      />
      {editable && selected && (
        <>
          {/* 우상단 preset 메뉴 */}
          <div
            contentEditable={false}
            style={{
              position: "absolute",
              top: -28,
              right: 0,
              display: "flex",
              gap: 2,
              padding: 2,
              background: "white",
              border: "1px solid rgb(226 232 240)",
              borderRadius: 4,
              boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
              fontSize: 11,
              zIndex: 10,
            }}
          >
            {PRESETS.map((p) => {
              const active = (widthAttr || "") === p.value
              return (
                <button
                  key={p.label}
                  type="button"
                  onMouseDown={(ev) => ev.preventDefault()}
                  onClick={() => setPreset(p.value)}
                  style={{
                    padding: "2px 6px",
                    borderRadius: 3,
                    background: active ? "rgb(226 232 240)" : "transparent",
                    color: "rgb(15 23 42)",
                    cursor: "pointer",
                  }}
                  title={`폭 ${p.label}`}
                >
                  {p.label}
                </button>
              )
            })}
            <span
              style={{
                width: 1,
                margin: "0 2px",
                background: "rgb(226 232 240)",
              }}
            />
            <button
              type="button"
              onMouseDown={(ev) => ev.preventDefault()}
              onClick={() => updateAttributes({ border: !borderOn })}
              style={{
                padding: "2px 6px",
                borderRadius: 3,
                background: borderOn ? "rgb(226 232 240)" : "transparent",
                color: "rgb(15 23 42)",
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
              title={`테두리 ${borderOn ? "끄기" : "켜기"} (${BORDER_COLOR})`}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  border: `1px solid ${BORDER_COLOR}`,
                  background: "white",
                }}
              />
              테두리
            </button>
          </div>
          {/* 우하단 corner 핸들 — pointer 드래그로 폭 조절 */}
          <span
            contentEditable={false}
            onPointerDown={onCornerPointerDown}
            style={{
              position: "absolute",
              right: -6,
              bottom: -6,
              width: 12,
              height: 12,
              background: "rgb(59 130 246)",
              border: "2px solid white",
              borderRadius: "50%",
              cursor: "nwse-resize",
              boxShadow: "0 1px 2px rgba(0,0,0,0.2)",
              zIndex: 10,
              touchAction: "none",
            }}
            title="드래그하여 폭 조절"
          />
        </>
      )}
    </NodeViewWrapper>
  )
}

/**
 * tiptap Image 확장 + width / border 속성 + React NodeView.
 * - 본문 저장 시 ``<img style="width:30%; border:1px solid #CCC">`` 형태로 직렬화.
 */
export const ResizableImage = TiptapImage.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        // 기존 본문(width 없음) 호환 — style.width 가 있으면 가져옴.
        parseHTML: (element: HTMLElement) =>
          element.style.width || element.getAttribute("width") || null,
        renderHTML: () => ({}), // style 은 아래 combined renderHTML 에서.
      },
      border: {
        default: false,
        // 기존 본문 호환 — style 에 border 가 있으면 true.
        parseHTML: (element: HTMLElement) =>
          !!element.style.border && element.style.border !== "none",
        renderHTML: () => ({}),
      },
    }
  },

  // src/alt/title 같은 부모 attrs 는 mergeAttributes 로 보존 — 직접 spread 시
  // tiptap 의 extension config(HTMLAttributes 의 class 등) 와 누락 위험.
  // width/border 는 합산해 inline style 한 줄로 출력.
  renderHTML({ HTMLAttributes, node }) {
    const w = (node?.attrs?.width as string | null) ?? null
    const b = !!node?.attrs?.border
    const styleParts: string[] = []
    if (w) styleParts.push(`width: ${w}`)
    if (b) styleParts.push(`border: 1px solid ${BORDER_COLOR}`)
    const finalAttrs = mergeAttributes(
      this.options.HTMLAttributes,
      HTMLAttributes,
    ) as Record<string, unknown>
    if (styleParts.length > 0) {
      const existing = (finalAttrs.style as string | undefined) ?? ""
      finalAttrs.style = [existing, styleParts.join("; ")]
        .filter(Boolean)
        .join("; ")
    }
    return ["img", finalAttrs]
  },

  addNodeView() {
    return ReactNodeViewRenderer(ResizableImageView)
  },
})
