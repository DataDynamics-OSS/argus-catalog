import type { ReactNode } from "react"

/**
 * 입력란 바로 아래에 보이는 검증 오류 문구.
 * 입력값 검증 오류는 toast 가 아니라 이 컴포넌트로 해당 입력란 옆에 보여 준다(lib/notify.ts 규칙).
 * 입력란에는 `aria-invalid={!!error}` 를 함께 단다 — 테두리가 빨갛게 바뀐다.
 */
export function FieldError({ children, id, className }: { children?: ReactNode; id?: string; className?: string }) {
  if (!children) return null
  return (
    <p id={id} role="alert" className={`text-xs text-destructive ${className ?? ""}`.trim()}>
      {children}
    </p>
  )
}
