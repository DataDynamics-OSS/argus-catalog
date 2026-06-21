import type { MenuConfig, MenuGroup } from "@/types/menu"
import { pluginMenus } from "@/plugins/registry"

// 베이스 메뉴(menu.json)에 플러그인 메뉴를 병합한다.
// 규칙: 플러그인 그룹 id 가 베이스에 있으면 그 그룹 끝에 아이템을 덧붙이고,
// 없으면 'admin'(관리) 그룹 앞에 새 그룹으로 삽입한다 — 관리 그룹은 항상 마지막 유지.
function mergeMenus(base: MenuGroup[], extra: MenuGroup[]): MenuGroup[] {
  if (extra.length === 0) return base

  const merged = base.map((g) => ({ ...g, items: [...g.items] }))
  const byId = new Map(merged.map((g) => [g.id, g]))
  const adminIdx = merged.findIndex((g) => g.id === "admin")
  const insertAt = adminIdx === -1 ? merged.length : adminIdx

  let offset = 0
  for (const group of extra) {
    const target = byId.get(group.id)
    if (target) {
      target.items.push(...group.items)
    } else {
      const copy = { ...group, items: [...group.items] }
      merged.splice(insertAt + offset, 0, copy)
      byId.set(copy.id, copy)
      offset++
    }
  }
  return merged
}

export async function getMenu(): Promise<MenuConfig> {
  const data = await import("@/data/menu.json")
  const base = (data.default as MenuConfig).groups
  return { groups: mergeMenus(base, pluginMenus) }
}
