import type { ReactNode } from "react"

import { cn } from "@workspace/ui/lib/utils"

/**
 * 상세 화면 탭 본문의 한 묶음 — 제목(왼쪽) · 작업 단추(오른쪽) · 내용.
 *
 * 탭 본문을 카드로 감싸지 않고 이 묶음을 쌓는다. 박스는 한 화면에 한 겹만 — 내용 자체가 영역인 것(코드, 큰 표,
 * 그래프)이나 같은 모양이 반복되는 것(지표 타일, 목록 행)만 테두리를 가진다.
 */
export function DetailSection({
  title,
  icon,
  description,
  actions,
  children,
  className,
}: {
  title?: ReactNode
  icon?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
}) {
  const hasHead = title !== undefined || actions !== undefined
  return (
    <section className={cn("space-y-3", className)}>
      {hasHead && (
        <div className="flex min-h-8 flex-wrap items-center justify-between gap-2">
          {title !== undefined ? (
            <h3 className="flex items-center gap-2 text-base font-semibold [&_svg]:size-4 [&_svg]:text-muted-foreground">
              {icon}
              {title}
            </h3>
          ) : <span />}
          {actions !== undefined && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {description !== undefined && <p className="-mt-1 text-sm">{description}</p>}
      {children}
    </section>
  )
}

/** 밑줄형 탭 — 탭 목록 아래 선이 헤더와 본문의 경계가 된다(데이터셋 상세). */
export const LINE_TABS_LIST = "h-10 w-full justify-start gap-0 overflow-x-auto overflow-y-hidden border-b p-0"
export const LINE_TABS_TRIGGER = "flex-none px-3 group-data-horizontal/tabs:after:bottom-[-2px]"

/** 탭 목록 안에 둔 일반 단추(더보기 메뉴)를 밑줄형 탭처럼 — ``data-active`` 면 밑줄. */
export const LINE_TABS_BUTTON =
  "relative inline-flex h-[calc(100%-1px)] items-center justify-center gap-1.5 px-3 text-sm font-medium whitespace-nowrap " +
  "text-foreground/60 transition-colors hover:text-foreground data-active:text-foreground focus-visible:outline-1 " +
  "focus-visible:outline-ring after:absolute after:inset-x-0 after:bottom-[-2px] after:h-0.5 after:bg-foreground " +
  "after:opacity-0 after:transition-opacity data-active:after:opacity-100"
