import type { MenuConfig } from "@/types/menu"

export async function getMenu(): Promise<MenuConfig> {
  const data = await import("@/data/menu.json")
  return data.default as MenuConfig
}
