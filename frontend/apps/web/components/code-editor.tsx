"use client"

import { useCallback, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import { Loader2 } from "lucide-react"
import type { EditorProps, OnMount } from "@monaco-editor/react"

import { cn } from "@workspace/ui/lib/utils"
import { MONACO_FONT_SIZE } from "@/lib/monaco"

type MonacoOptions = NonNullable<EditorProps["options"]>

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => (
    <div className="flex h-full min-h-16 items-center justify-center">
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
    </div>
  ),
})

type CodeEditorProps = {
  value: string
  onChange: (value: string) => void
  /** Monaco language id — "json" · "yaml" · "xml" · "plaintext" … */
  language?: string
  /** 비어 있을 때 회색으로 보이는 예시(여러 줄 가능). */
  placeholder?: string
  /** `<Label htmlFor>` 와 연결할 id — 편집 영역(textarea)에 붙는다. */
  id?: string
  /** 스크린 리더용 이름. 라벨과 연결하지 않을 때 쓴다. */
  "aria-label"?: string
  /** 오류 표시(빨간 테두리) — Textarea 의 aria-invalid 와 같다. */
  invalid?: boolean
  readOnly?: boolean
  /** 내용에 맞춰 늘어나는 높이의 최소 · 최대(px). 최대를 넘으면 안에서 스크롤. */
  minHeight?: number
  maxHeight?: number
  options?: MonacoOptions
  className?: string
}

const LINE_HEIGHT = 20
const PADDING = 6

/**
 * 편집용 Monaco 에디터 — JSON · YAML · 설정 파일 · 원문 붙여 넣기처럼 고정폭 글꼴로 적는 입력란에 쓴다.
 *
 * 고정폭(font-mono) Textarea 대신 이것을 쓴다(lib/button-labels.test.ts 가 검사).
 * 글꼴 크기는 다른 입력란과 같은 `text-sm`(MONACO_FONT_SIZE), 높이는 내용에 맞춰 min~max 사이로 늘어난다.
 * 읽기 전용 표시는 CodeViewer 를 쓴다.
 */
export function CodeEditor({
  value,
  onChange,
  language = "plaintext",
  placeholder,
  id,
  "aria-label": ariaLabel,
  invalid,
  readOnly,
  minHeight = 120,
  maxHeight = 400,
  options,
  className,
}: CodeEditorProps) {
  const [contentHeight, setContentHeight] = useState(0)
  const [textarea, setTextarea] = useState<HTMLTextAreaElement | null>(null)
  const height = Math.min(maxHeight, Math.max(minHeight, contentHeight))

  const handleMount = useCallback<OnMount>((editor) => {
    const fit = () => setContentHeight(editor.getContentHeight())
    fit()
    editor.onDidContentSizeChange(fit)
    setTextarea(editor.getDomNode()?.querySelector("textarea") ?? null)
  }, [])

  // <Label htmlFor> 가 편집 영역을 가리키고, 오류 상태를 보조 기술에 알리도록 편집 영역(textarea)에 붙인다
  useEffect(() => {
    if (!textarea) return
    if (id) textarea.setAttribute("id", id)
    if (invalid) textarea.setAttribute("aria-invalid", "true")
    else textarea.removeAttribute("aria-invalid")
  }, [textarea, id, invalid])

  return (
    <div
      data-slot="code-editor"
      aria-invalid={invalid || undefined}
      className={cn(
        "w-full overflow-hidden rounded-md border border-input bg-transparent shadow-xs transition-[color,box-shadow]",
        "focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
        // 여러 줄 예시(placeholder)의 들여쓰기를 살린다
        "[&_.editorPlaceholder]:whitespace-pre",
        invalid && "border-destructive ring-destructive/20",
        readOnly && "bg-muted/40",
        className,
      )}
      style={{ height }}
    >
      <MonacoEditor
        value={value}
        onChange={(v) => onChange(v ?? "")}
        language={language}
        theme="light"
        height="100%"
        onMount={handleMount}
        options={{
          fontSize: MONACO_FONT_SIZE,
          lineHeight: LINE_HEIGHT,
          fontFamily: "D2Coding, Menlo, Consolas, monospace",
          placeholder,
          ariaLabel,
          readOnly,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: "on",
          tabSize: 2,
          lineNumbersMinChars: 3,
          glyphMargin: false,
          folding: false,
          renderLineHighlight: "none",
          overviewRulerLanes: 0,
          overviewRulerBorder: false,
          hideCursorInOverviewRuler: true,
          scrollbar: { alwaysConsumeMouseWheel: false, verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
          padding: { top: PADDING, bottom: PADDING },
          fixedOverflowWidgets: true,
          ...options,
          // 줄 번호는 모든 Monaco 에서 켠다(TODO-126) — options 로도 끄지 않는다
          lineNumbers: "on",
        }}
      />
    </div>
  )
}
