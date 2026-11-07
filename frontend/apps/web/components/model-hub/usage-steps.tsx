"use client"

import { useState } from "react"
import { Check, Copy } from "lucide-react"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism"
import { toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"

/** 복사 버튼이 붙은 가벼운 코드 블록 (높이는 내용에 맞춘다). */
export function CodeSnippet({ code, language = "bash" }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("클립보드에 복사하지 못했습니다.")
    }
  }
  return (
    <div className="group relative rounded-md border bg-muted/30">
      <Button
        variant="ghost" size="icon-sm"
        className="absolute right-1.5 top-1.5 h-7 w-7 opacity-60 group-hover:opacity-100"
        onClick={copy} aria-label="코드 복사" title="복사"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
      <SyntaxHighlighter
        language={language}
        style={oneLight}
        customStyle={{
          margin: 0, padding: "12px 44px 12px 14px", background: "transparent",
          fontSize: "13px", fontFamily: "D2Coding, Menlo, Consolas, monospace",
        }}
        codeTagProps={{ style: { fontFamily: "inherit" } }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

export type UsageStep = { title: string; description?: React.ReactNode; code?: string; language?: string }

/** 번호 매긴 단계형 사용법 — 각 단계는 짧은 설명과 복사 가능한 코드 한 덩어리. */
export function UsageSteps({ steps }: { steps: UsageStep[] }) {
  return (
    <ol className="space-y-5">
      {steps.map((s, i) => (
        <li key={s.title} className="grid grid-cols-[1.75rem_1fr] gap-x-3">
          <span className="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
            {i + 1}
          </span>
          <div className="min-w-0 space-y-2">
            <div>
              <h3 className="text-sm font-semibold">{s.title}</h3>
              {s.description && <p className="mt-0.5 text-sm text-muted-foreground">{s.description}</p>}
            </div>
            {s.code && <CodeSnippet code={s.code} language={s.language} />}
          </div>
        </li>
      ))}
    </ol>
  )
}
