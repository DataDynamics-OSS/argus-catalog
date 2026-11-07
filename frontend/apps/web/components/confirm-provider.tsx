"use client"

/**
 * 브라우저 기본 `confirm()` / `prompt()` 대체 — AlertDialog 를 Promise 로 띄운다.
 *
 * 사용:
 *   const askConfirm = useConfirm()
 *   if (!(await askConfirm({ title: "규칙 삭제", description: "…", confirmText: "삭제", destructive: true }))) return
 *
 *   const askInput = usePrompt()
 *   const reason = await askInput({ title: "반려", label: "반려 사유", multiline: true })
 *   if (reason === null) return   // 취소
 *
 * `ConfirmProvider` 는 app/layout.tsx 에서 한 번만 감싼다.
 * 열고 닫는 상태를 직접 관리하는 화면은 기존 `ConfirmDialog` 를 그대로 써도 된다.
 */
import * as React from "react"

import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Textarea } from "@workspace/ui/components/textarea"

import { ConfirmDialog } from "@/components/confirm-dialog"

export type ConfirmOptions = {
  title: React.ReactNode
  description?: React.JSX.Element | string
  confirmText?: React.ReactNode
  cancelText?: string
  /** 삭제·폐기처럼 되돌릴 수 없는 동작이면 true (빨간 버튼) */
  destructive?: boolean
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

export type PromptOptions = ConfirmOptions & {
  /** 입력란 라벨 */
  label?: string
  placeholder?: string
  defaultValue?: string
  /** 여러 줄 입력(Textarea) */
  multiline?: boolean
  /** true 면 공백만 입력 시 확인 버튼 비활성 */
  required?: boolean
  maxLength?: number
}

/** 확인 시 입력값(trim 전 원문), 취소 시 null */
type PromptFn = (opts: PromptOptions) => Promise<string | null>

const ConfirmContext = React.createContext<ConfirmFn | null>(null)
const PromptContext = React.createContext<PromptFn | null>(null)

function PromptHost({
  opts,
  onClose,
}: {
  opts: PromptOptions | null
  onClose: (value: string | null) => void
}) {
  const [value, setValue] = React.useState("")
  React.useEffect(() => {
    if (opts) setValue(opts.defaultValue ?? "")
  }, [opts])
  const blocked = !!opts?.required && !value.trim()
  const inputId = "app-prompt-input"
  const submit = () => { if (!blocked) onClose(value) }

  return (
    <ConfirmDialog
      open={opts !== null}
      onOpenChange={(open) => { if (!open) onClose(null) }}
      title={opts?.title ?? ""}
      desc={<span className="whitespace-pre-line">{opts?.description ?? ""}</span>}
      confirmText={opts?.confirmText ?? "확인"}
      cancelBtnText={opts?.cancelText}
      destructive={opts?.destructive}
      disabled={blocked}
      handleConfirm={submit}
    >
      <div className="grid gap-2">
        {opts?.label ? <Label htmlFor={inputId}>{opts.label}{opts.required ? " *" : ""}</Label> : null}
        {opts?.multiline ? (
          <Textarea
            id={inputId}
            autoFocus
            rows={4}
            value={value}
            maxLength={opts.maxLength}
            placeholder={opts.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Ctrl/⌘+Enter 로 확인
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit() }
            }}
          />
        ) : (
          <Input
            id={inputId}
            autoFocus
            value={value}
            maxLength={opts?.maxLength}
            placeholder={opts?.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
            }}
          />
        )}
      </div>
    </ConfirmDialog>
  )
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = React.useState<ConfirmOptions | null>(null)
  const resolverRef = React.useRef<((v: boolean) => void) | null>(null)

  const confirm = React.useCallback<ConfirmFn>((o) => {
    // 이미 열린 확인창이 있으면 취소로 끝낸다 (중첩 방지)
    resolverRef.current?.(false)
    setOpts(o)
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve
    })
  }, [])

  const close = React.useCallback((result: boolean) => {
    resolverRef.current?.(result)
    resolverRef.current = null
    setOpts(null)
  }, [])

  // ── prompt ──
  const [promptOpts, setPromptOpts] = React.useState<PromptOptions | null>(null)
  const promptResolverRef = React.useRef<((v: string | null) => void) | null>(null)

  const prompt = React.useCallback<PromptFn>((o) => {
    promptResolverRef.current?.(null)
    setPromptOpts(o)
    return new Promise<string | null>((resolve) => {
      promptResolverRef.current = resolve
    })
  }, [])

  const closePrompt = React.useCallback((v: string | null) => {
    promptResolverRef.current?.(v)
    promptResolverRef.current = null
    setPromptOpts(null)
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      <PromptContext.Provider value={prompt}>
        {children}
        <PromptHost opts={promptOpts} onClose={closePrompt} />
        <ConfirmDialog
          open={opts !== null}
          onOpenChange={(open) => { if (!open) close(false) }}
          title={opts?.title ?? ""}
          desc={<span className="whitespace-pre-line">{opts?.description ?? ""}</span>}
          confirmText={opts?.confirmText ?? "확인"}
          cancelBtnText={opts?.cancelText}
          destructive={opts?.destructive}
          handleConfirm={() => close(true)}
        />
      </PromptContext.Provider>
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): ConfirmFn {
  const ctx = React.useContext(ConfirmContext)
  if (!ctx) throw new Error("useConfirm 은 ConfirmProvider 안에서만 쓸 수 있습니다.")
  return ctx
}

export function usePrompt(): PromptFn {
  const ctx = React.useContext(PromptContext)
  if (!ctx) throw new Error("usePrompt 는 ConfirmProvider 안에서만 쓸 수 있습니다.")
  return ctx
}
