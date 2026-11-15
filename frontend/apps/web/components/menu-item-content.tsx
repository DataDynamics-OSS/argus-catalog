import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

/**
 * 메뉴 항목(DropdownMenuItem · ContextMenuItem) 안의 공통 모양 — 아이콘 + 라벨 + (선택) 회색 설명줄.
 *
 * 메뉴 규칙 (lib/button-labels.test.ts 가 일부 검사)
 * - 순서: 열기·보기 → 추가 → 편집·이름 변경 → 복사·내려받기 → 속성 → (구분선) → 삭제. 삭제는 맨 아래 묶음, `variant="destructive"`.
 * - 라벨은 동사만("삭제" · "편집"). 추가할 대상이 여럿일 때만 "시스템 추가" 처럼 대상을 붙인다.
 * - 괄호 설명 · 쓸 수 없는 이유는 라벨에 넣지 않고 `hint`(둘째 줄)로.
 * - 한 메뉴 안에서는 모든 항목에 아이콘을 단다. 동작 아이콘은 버튼과 같다(삭제 Trash2, 편집 Pencil, 복사 Copy …).
 */
export function MenuItemContent({ icon: Icon, label, hint }: { icon?: LucideIcon; label: ReactNode; hint?: ReactNode }) {
  if (!hint) {
    return (
      <>
        {Icon && <Icon />}
        {label}
      </>
    )
  }
  return (
    <span className="flex items-start gap-2">
      {Icon && <Icon className="mt-0.5 size-4 shrink-0" />}
      <span className="flex flex-col gap-0.5">
        <span>{label}</span>
        <span className="text-xs">{hint}</span>
      </span>
    </span>
  )
}
