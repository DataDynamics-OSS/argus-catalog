"use client"

import { Tip } from "@/components/tip"
import type { ReactNode } from "react"
import Link from "next/link"
import { MoreHorizontal } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@workspace/ui/components/dropdown-menu"
import { cn } from "@workspace/ui/lib/utils"

import { interactiveBoxClass } from "@/components/card-hover"

/**
 * 목록 카드 보기 공용 틀 — 데이터셋 · AI 에이전트 · MCP 서버가 같은 모양을 쓴다(TODO-114).
 *
 * - 격자: 너비에 맞춰 열 수가 바뀌고(카드 최소 300px) 한 줄의 카드 높이가 같다(`EntityCardGrid`).
 * - 머리: 경로(작은 회색) · 이름(논리명은 `text-base`, 영문 식별자만 있으면 `text-sm`) · 식별자(고정폭 회색) · ⋯ 메뉴.
 * - 본문: 배지 줄, 설명 두 줄(`EntityCardText`, 비면 "설명 없음"), 그 밖의 줄은 본문 색.
 * - 바닥: 위 테두리 아래 `text-xs` 한 줄(`footer`).
 * - 카드 어디를 눌러도 열린다(이름 링크/버튼의 ::after 가 카드를 덮는다). ⋯ 와 `EntityCardRaised` 로 감싼 것만 그 위에 있다.
 *   ⋯ 는 마우스를 올렸을 때(터치 기기는 늘) 보인다.
 * 회색(`text-muted-foreground`)은 경로 · 식별자 · 빈 값에만 쓴다 — 회색 글씨가 많으면 산만하다.
 */
export function EntityCardGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr))]">
      {children}
    </div>
  )
}

const OVERLAY =
  "text-left outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:after:ring-2 focus-visible:after:ring-ring"

export function EntityCard({
  crumb,
  title,
  prominent,
  identifier,
  href,
  onOpen,
  menuLabel,
  menu,
  children,
  footer,
}: {
  /** 머리 위 경로 — "데이터 소스 › 스키마", "부서 › 분류" 등 */
  crumb?: string | null
  title: string
  /** 논리명(한글 이름)이면 true — 한 단계 큰 글씨 */
  prominent?: boolean
  /** 이름 아래 식별자(물리명 · 등록 이름) — 고정폭 회색 */
  identifier?: string | null
  /** 상세 주소(링크) 또는 같은 화면에서 여는 함수 중 하나. 둘 다 없으면 열 곳이 없는 카드 — 이름은 글자, 마우스 강조 없음 */
  href?: string
  onOpen?: () => void
  menuLabel?: string
  /** ⋯ 메뉴 항목(`DropdownMenuItem` 들). 없으면 ⋯ 를 그리지 않는다 */
  menu?: ReactNode
  children?: ReactNode
  footer?: ReactNode
}) {
  return (
    <article
      className={cn(
        "group relative flex h-full flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground",
        (href || onOpen) && interactiveBoxClass,
      )}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {crumb && (
            <p className="mb-1 truncate text-xs leading-4" title={crumb}>{crumb}</p>
          )}
          <h3 className={cn("truncate font-semibold", prominent ? "text-base leading-6" : "text-sm leading-5")}>
            {href ? (
              <Link href={href} className={OVERLAY} title={title}>{title}</Link>
            ) : onOpen ? (
              <button type="button" onClick={onOpen} className={OVERLAY} title={title}>{title}</button>
            ) : (
              <span title={title}>{title}</span>
            )}
          </h3>
          {identifier && (
            <p className="truncate font-mono text-xs leading-4" title={identifier}>{identifier}</p>
          )}
        </div>
        {menu && (
          <DropdownMenu>
            <Tip label={menuLabel ?? `${title} 메뉴`}><DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="relative z-10 -mr-1 -mt-1 h-7 w-7 p-0 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100"
                aria-label={menuLabel ?? `${title} 메뉴`}
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger></Tip>
            <DropdownMenuContent align="end">{menu}</DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {children}

      {footer && (
        <div className="mt-auto flex items-center gap-3 border-t pt-3 text-xs">{footer}</div>
      )}
    </article>
  )
}

/** 설명 두 줄 — 비면 회색 "설명 없음". 줄 높이를 늘 두 줄로 잡아 카드끼리 아래 줄이 맞는다 */
export function EntityCardText({ text }: { text: string | null | undefined }) {
  return (
    <p className={cn("line-clamp-2 min-h-10 text-sm leading-5", !text && "text-muted-foreground")} title={text ?? undefined}>
      {text || "설명 없음"}
    </p>
  )
}

/** 카드를 덮는 링크 위로 올려야 하는 것(툴팁 · 링크) */
export function EntityCardRaised({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("relative z-10", className)}>{children}</span>
}
