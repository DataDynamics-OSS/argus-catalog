/**
 * 클립보드 복사.
 *
 * `navigator.clipboard` 는 보안 컨텍스트(https · localhost)에서만 있다. 사내 http 주소로 연 화면에서도
 * 복사가 되도록 숨긴 textarea + execCommand("copy") 로 대신한다.
 * 화면 코드는 navigator.clipboard 를 직접 쓰지 않고 이 함수를 쓴다 (lib/notify.test.ts 가 정적 검사).
 */

/** 복사하고 성공 여부를 돌려준다. */
export async function tryCopyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 권한 거부 등 — 아래 대체 경로로
  }
  if (typeof document === "undefined") return false
  const ta = document.createElement("textarea")
  ta.value = text
  ta.setAttribute("readonly", "")
  ta.style.position = "fixed"
  ta.style.opacity = "0"
  document.body.appendChild(ta)
  ta.select()
  try {
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    document.body.removeChild(ta)
  }
}

/** 복사하고, 못 하면 예외를 던진다 — `navigator.clipboard.writeText` 와 같은 모양. */
export async function copyText(text: string): Promise<void> {
  if (!(await tryCopyText(text))) throw new Error("클립보드에 복사하지 못했습니다. 브라우저 권한을 확인하세요.")
}
