"use client"

/**
 * 읽기 전용 Monaco 코드 뷰어 — 글꼴 text-sm(14px, `MONACO_FONT_SIZE`), 줄 번호, 우상단 복사 버튼.
 *
 * 기본은 스크롤바 없이 내용 높이에 맞춰 전부 보여 준다(긴 줄은 줄바꿈). `maxHeight` 를 주면 그 높이를 넘을 때만 내부 스크롤,
 * `scroll` 은 뷰포트 하단까지 채우는 큰 문서용. 처음엔 API 상세(apis-detail-shared.tsx)에만 있었고 MCP 서버 상세도 함께 쓴다.
 * 높이를 정해 두고 스크롤하는 뷰어는 `components/code-viewer.tsx`.
 */
import { Tip } from "@/components/tip"
import { useEffect, useRef, useState } from "react"
import dynamic from "next/dynamic"
import type { EditorProps, OnMount } from "@monaco-editor/react"
import { Check, Copy, Loader2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"

import { copyText } from "@/lib/clipboard"
import { MONACO_FONT_SIZE } from "@/lib/monaco"
import { toast } from "@/lib/notify"

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="flex h-10 items-center justify-center"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>,
})

// 읽기 전용 JSON 뷰어 — 스크롤바·테두리 없이 내용 높이에 맞춰 표시, 클립보드 복사 버튼.
const JSON_VIEWER_OPTIONS: NonNullable<EditorProps["options"]> = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  scrollbar: { vertical: "hidden", horizontal: "hidden", handleMouseWheel: false, alwaysConsumeMouseWheel: false },
  wordWrap: "on",
  lineNumbers: "on",
  folding: false,
  fontSize: MONACO_FONT_SIZE,
  fontFamily: "D2Coding, Menlo, Consolas, monospace",
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  contextmenu: false,
  guides: { indentation: false },
  automaticLayout: true,
  padding: { top: 6, bottom: 6 },
}

// 읽기 전용 코드 뷰어. 기본은 스크롤바·테두리 없이 내용 높이에 맞춰(bottom fit) 표시.
// scroll 모드: 뷰포트 하단까지 채우고(내부 스크롤바)(원본 스펙처럼 큰 문서용). 줄 번호는 늘 켠다(TODO-126). 공통으로 클립보드 복사 버튼.
// maxHeight: 내용 높이에 맞추되 이 값(px)을 넘으면 고정하고 내부 스크롤바 표시(호출 응답처럼 길이를 모르는 본문용).
export function FitCodeViewer({ text, language = "json", emptyLabel = "없음", scroll = false, bottomGap = 24, maxHeight, copyable = true }: {
  text: string | null | undefined; language?: string; emptyLabel?: string; scroll?: boolean; bottomGap?: number; maxHeight?: number
  /** 우상단 복사 버튼(기본 표시). 바깥에 복사 단추가 따로 있으면 false */
  copyable?: boolean
}) {
  const [height, setHeight] = useState(40)
  const [copied, setCopied] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [autoH, setAutoH] = useState(400)
  // scroll 모드: 에디터 상단 위치를 측정해 뷰포트 하단까지 정확히 채운다(페이지 스크롤 방지, 내부 스크롤만).
  useEffect(() => {
    if (!scroll) return
    const calc = () => {
      const el = wrapRef.current
      if (!el) return
      setAutoH(Math.max(240, window.innerHeight - el.getBoundingClientRect().top - bottomGap))
    }
    calc()
    window.addEventListener("resize", calc)
    return () => window.removeEventListener("resize", calc)
  }, [scroll, bottomGap])
  if (text === null || text === undefined || text === "") return <span className="text-sm text-black">{emptyLabel}</span>

  const options: NonNullable<EditorProps["options"]> = {
    ...JSON_VIEWER_OPTIONS,
    // scroll 모드(원본): 접기 활성화 — depth 4 이상은 자동으로 접고, 접기 컨트롤 항상 노출.
    folding: scroll ? true : JSON_VIEWER_OPTIONS.folding,
    showFoldingControls: scroll ? "always" : undefined,
    scrollbar: scroll
      ? { vertical: "auto", horizontal: "auto", handleMouseWheel: true, alwaysConsumeMouseWheel: true }
      : maxHeight
        // 상·하단 끝에 닿으면 페이지 스크롤로 넘김(alwaysConsumeMouseWheel=false)
        ? { vertical: "auto", horizontal: "hidden", handleMouseWheel: true, alwaysConsumeMouseWheel: false }
        : JSON_VIEWER_OPTIONS.scrollbar,
  }
  const handleMount: OnMount = (editor) => {
    if (scroll) {
      // 접기 범위 계산 후 depth 4 이상 자동 접기.
      setTimeout(() => editor.getAction("editor.foldLevel4")?.run(), 80)
      return  // 컨테이너 높이(뷰포트 기준)를 채움 — 내용에 맞춰 늘리지 않음
    }
    const update = () => setHeight(Math.min(Math.max(editor.getContentHeight(), 24), maxHeight ?? Infinity))
    update()
    editor.onDidContentSizeChange(update)
  }
  const handleCopy = async () => {
    try {
      await copyText(text)
      setCopied(true)
      toast.success("클립보드에 복사했습니다.")
      setTimeout(() => setCopied(false), 2000)
    } catch { toast.error("클립보드에 복사하지 못했습니다.") }
  }

  return (
    <div ref={wrapRef} className={cn("relative overflow-hidden rounded bg-muted/30", scroll && "border")} style={{ height: scroll ? autoH : height }}>
      <MonacoEditor language={language} value={text} theme="light" height="100%" options={options} onMount={handleMount} />
      {copyable && <Tip label={copied ? "복사됨" : "복사"}><Button
        type="button"
        variant="secondary"
        size="icon"
        onClick={handleCopy}
       
        aria-label={copied ? "복사됨" : "복사"}
        className="absolute top-1 right-1 z-10 h-7 w-7 shadow-sm"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </Button></Tip>}
    </div>
  )
}

export function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-sm text-black">없음</span>
  return <FitCodeViewer text={JSON.stringify(value, null, 2)} />
}
