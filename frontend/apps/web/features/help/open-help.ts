// 도움말 창 열기 — 이름 붙인 별도 창 하나를 다시 쓴다.

import { toast } from "@/lib/notify"

/** 도움말 창 이름. 같은 이름으로 열면 브라우저가 기존 창을 재사용한다. */
export const HELP_WINDOW_NAME = "argus-help"

/** 화면 오른쪽에 붙는 창 크기·위치 */
export function helpWindowFeatures(screen: { availWidth: number; availHeight: number; availLeft?: number; availTop?: number }): string {
  const width = Math.min(960, Math.max(480, Math.round(screen.availWidth * 0.5)))
  const height = Math.round(screen.availHeight * 0.9)
  const left = (screen.availLeft ?? 0) + Math.max(0, screen.availWidth - width)
  const top = (screen.availTop ?? 0) + Math.round((screen.availHeight - height) / 2)
  return `popup,width=${width},height=${height},left=${left},top=${top}`
}

/**
 * 도움말을 별도 창으로 연다. 이미 열린 도움말 창이 있으면 그 창의 주소를 바꾸고 앞으로 가져온다.
 * 팝업이 막히면 안내 toast 에 "새 탭으로 열기" 를 준다(버튼 클릭은 사용자 동작이라 막히지 않는다).
 */
export function openHelp(url: string, title?: string): void {
  // 같은 출처 창이라 noopener 를 쓰지 않는다 — 쓰면 window.open 이 null 을 돌려줘 재사용·포커스를 못 한다.
  const win = window.open(url, HELP_WINDOW_NAME, helpWindowFeatures(window.screen as Screen & { availLeft?: number; availTop?: number }))
  if (win) {
    win.focus()
    return
  }
  toast.warning("도움말 창이 차단되었습니다.", {
    description: `브라우저가 팝업을 막았습니다. 이 사이트의 팝업을 허용하거나 새 탭으로 여세요.${title ? ` (${title})` : ""}`,
    action: { label: "새 탭으로 열기", onClick: () => window.open(url, "_blank") },
  })
}
