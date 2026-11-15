"use client"

import { Copy, ExternalLink, Link2 } from "lucide-react"

import { DropdownMenuItem } from "@workspace/ui/components/dropdown-menu"
import { copyText } from "@/lib/clipboard"
import { notifyError, toast } from "@/lib/notify"

/** 앱 안 경로를 공유할 수 있는 전체 주소로. */
export function absoluteUrl(path: string): string {
  return typeof window === "undefined" ? path : new URL(path, window.location.origin).toString()
}

// what 은 조사까지 포함해 넘긴다(예: "링크를", "URN을")
async function copyWithToast(text: string, what: string) {
  try {
    await copyText(text)
    toast.success(`${what} 복사했습니다.`)
  } catch (e) {
    notifyError(e, "클립보드에 복사하지 못했습니다.")
  }
}

/**
 * 목록 행 · 카드 ⋯ 메뉴의 공통 항목 — 새 탭에서 열기 · 링크 복사 · (있으면) URN 복사.
 * 행을 누르면 상세가 열리므로 "상세 보기" 는 메뉴에 두지 않는다.
 */
export function LinkMenuItems({ href, urn }: { href: string; urn?: string | null }) {
  const url = () => absoluteUrl(href)
  return (
    <>
      {/* noopener 를 쓰지 않는다 — 로그인 토큰이 sessionStorage(탭 단위)에 있어, opener 관계로 열어야
          브라우저가 새 탭에 sessionStorage 를 복사해 다시 로그인하지 않는다(같은 사이트 주소라 opener 노출 위험 없음). */}
      <DropdownMenuItem onSelect={() => window.open(url(), "_blank")}>
        <ExternalLink /> 새 탭에서 열기
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => void copyWithToast(url(), "링크를")}>
        <Link2 /> 링크 복사
      </DropdownMenuItem>
      {urn && (
        <DropdownMenuItem onSelect={() => void copyWithToast(urn, "URN을")}>
          <Copy /> URN 복사
        </DropdownMenuItem>
      )}
    </>
  )
}
