"use client"

import { useCallback, useState } from "react"
import dynamic from "next/dynamic"
import { Check, Copy, Loader2 } from "lucide-react"
import { toast } from "sonner"
import type { EditorProps } from "@monaco-editor/react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"

type MonacoOptions = NonNullable<EditorProps["options"]>

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  ),
})

type CodeViewerProps = {
  /** 표시할 코드 문자열. */
  code: string
  /** Monaco language id. 예) "python", "yaml", "json", "shell", "sql", "plaintext". */
  language?: string
  /** 컨테이너 높이. ``number`` 면 px, ``string`` 이면 그대로 CSS 값으로 전달
   *  (예: ``"calc(100vh - 280px)"`` 로 viewport 바닥까지 채우기). */
  height?: number | string
  /** 우상단 복사 버튼 클릭 시 토스트에 표시되는 라벨(예: "requirements.txt"). */
  copyLabel?: string
  /** Monaco theme. 기본 "light". */
  theme?: "light" | "vs-dark" | "vs"
  /** 줄바꿈 정책. 기본 "off" (코드는 가로 스크롤). 파일 내용 뷰어는 "on" 권장. */
  wordWrap?: "on" | "off"
  /** 외곽 컨테이너에 1px 테두리를 표시할지 여부. 기본 false. */
  border?: boolean
  /** 추가/오버라이드 Monaco 옵션 (기본 readonly 옵션은 그대로 유지). */
  options?: MonacoOptions
  /** 외곽 컨테이너 추가 className. */
  className?: string
}

// 모든 readonly 코드 뷰어가 공유하는 기본 옵션.
// readonly 흐름에서 의미 없는 cursor / line highlight / overview ruler 등을 비활성.
const BASE_READONLY_OPTIONS: MonacoOptions = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  fontSize: 13,
  fontFamily: "D2Coding, Menlo, Consolas, monospace",
  lineNumbers: "on",
  renderLineHighlight: "none",
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  contextmenu: false,
  padding: { top: 8, bottom: 8 },
}

/**
 * 읽기 전용 Monaco 에디터 뷰어 + 우상단 hover 시 표시되는 복사 버튼.
 *
 * - 모든 readonly 코드 영역에서 동일한 폰트·옵션·UX 를 보장하기 위한 공용 래퍼.
 * - 복사 버튼은 hover/focus 시에만 나타나 코드 가독성을 방해하지 않는다.
 * - 차트 래퍼(`Highchart`)와 동일하게 props 의 옵션이 항상 기본값을 덮어쓴다.
 */
export function CodeViewer({
  code,
  language = "plaintext",
  height = 300,
  copyLabel = "코드",
  theme = "light",
  wordWrap = "off",
  border = false,
  options,
  className,
}: CodeViewerProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      toast.success(`${copyLabel} 내용을 클립보드에 복사했습니다.`)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("클립보드 복사에 실패했습니다.")
    }
  }, [code, copyLabel])

  const mergedOptions: MonacoOptions = {
    ...BASE_READONLY_OPTIONS,
    wordWrap,
    ...options,
  }

  return (
    <div
      className={cn(
        "group/code-viewer relative overflow-hidden rounded bg-background",
        border && "border",
        className,
      )}
      style={{ height }}
    >
      <MonacoEditor
        height="100%"
        language={language}
        value={code}
        theme={theme}
        options={mergedOptions}
      />
      {/*
        우상단 hover 페이드인 복사 버튼.
        - right-5 (20px) — Monaco 의 vertical scrollbar(약 14px) 와 hover widget 영역을 피한다.
          right-2 (8px) 에 두면 스크롤바와 겹쳐 클릭이 막힘.
        - z-10 으로 에디터 스크롤바보다 위.
        - copied 상태에서는 항상 표시 (사용자 피드백 유지).
      */}
      <Button
        type="button"
        variant="secondary"
        size="icon"
        onClick={handleCopy}
        title={copied ? "복사됨" : "복사"}
        aria-label={copied ? "복사됨" : "복사"}
        className={cn(
          "absolute top-1.5 right-5 z-10 h-7 w-7 shadow-sm",
          "opacity-0 group-hover/code-viewer:opacity-100 focus-visible:opacity-100 transition-opacity",
          copied && "opacity-100",
        )}
      >
        {copied
          ? <Check className="h-3.5 w-3.5" />
          : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </div>
  )
}
