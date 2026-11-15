import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@workspace/ui/lib/utils"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        // 상태 의미 톤(TODO-107) — destructive 와 같은 옅은 칠. 성공 · 주의 · 진행 중에 쓴다.
        success:
          "bg-success/10 text-success dark:bg-success/20 [a]:hover:bg-success/20",
        warning:
          "bg-warning/10 text-warning dark:bg-warning/20 [a]:hover:bg-warning/20",
        info: "bg-info/10 text-info dark:bg-info/20 [a]:hover:bg-info/20",
        // 끝난 · 물러난 상태(퇴역 · 보관 · 삭제 · 무시) — 무채색 칠로 한 걸음 물러나 보이게.
        muted: "bg-muted text-muted-foreground [a]:hover:bg-muted/80",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
      },
      // 크기는 둘(TODO-109) — 촘촘한 곳(트리 · 표 셀 · 제목 옆 꼬리표)은 sm. className 으로 글자 · 높이를 덮어쓰지 않는다.
      size: {
        default: "",
        sm: "h-4 px-1.5 py-0 text-[11px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    />
  )
}

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>

export { Badge, badgeVariants, type BadgeVariant }
