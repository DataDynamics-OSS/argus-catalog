/**
 * 권한 관리 화면의 매트릭스 계산 — 화면 컴포넌트와 분리한 순수 함수 (단위 테스트 대상).
 *
 * Matrix = 항목 key → 허용 역할 집합 (Superuser/User 만. Admin 은 항상 허용이라 담지 않는다).
 */

import {
  DEFAULT_DENY_FEATURE_KEYS,
  MANAGED_ROLES,
  MENU_REGISTRY,
  type FeatureEntry,
  type MenuEntry,
} from "@/lib/permission-registry"

export type Matrix = Record<string, Set<string>>

/** 관리자 전용 고정 행(관리 메뉴) — 체크 불가·저장 제외 */
export const FIXED_ADMIN_KEYS: ReadonlySet<string> = new Set(
  MENU_REGISTRY.filter((m) => m.adminOnly).map((m) => m.key),
)

/** 서버 맵 → draft 초기화. 서버에 없는 key = 전 역할 허용(open-by-default), 명시 허용형은 미허용. */
export function initDraft(keys: string[], server: Record<string, string[]>): Matrix {
  const next: Matrix = {}
  for (const k of keys) {
    if (server[k] === undefined) {
      next[k] = DEFAULT_DENY_FEATURE_KEYS.has(k) ? new Set() : new Set(MANAGED_ROLES)
    } else {
      // "__none__" 센티널(admin 전용)은 빈 집합으로
      next[k] = new Set(server[k].filter((r) => (MANAGED_ROLES as readonly string[]).includes(r)))
    }
  }
  return next
}

export function draftToPayload(draft: Matrix, fixed: ReadonlySet<string> = FIXED_ADMIN_KEYS): Record<string, string[]> {
  const payload: Record<string, string[]> = {}
  for (const [k, roles] of Object.entries(draft)) if (!fixed.has(k)) payload[k] = Array.from(roles)
  return payload
}

/** 저장된 상태와 다른 칸(항목×역할) 수. 고정 행은 세지 않는다. */
export function countChanges(saved: Matrix | null, draft: Matrix | null, fixed: ReadonlySet<string> = FIXED_ADMIN_KEYS): number {
  if (!saved || !draft) return 0
  let n = 0
  for (const k of Object.keys(draft)) {
    if (fixed.has(k)) continue
    for (const r of MANAGED_ROLES) if ((saved[k]?.has(r) ?? false) !== (draft[k]?.has(r) ?? false)) n++
  }
  return n
}

/** 주어진 항목들에서 한 역할의 체크 상태 — 전부/일부/없음. 고정 행은 제외. */
export function roleState(draft: Matrix | null, keys: string[], role: string, fixed: ReadonlySet<string> = FIXED_ADMIN_KEYS): "all" | "some" | "none" {
  const editable = keys.filter((k) => !fixed.has(k))
  if (!draft || editable.length === 0) return "none"
  const on = editable.filter((k) => draft[k]?.has(role)).length
  return on === 0 ? "none" : on === editable.length ? "all" : "some"
}

/** 주어진 항목들에서 한 역할을 일괄 허용(on)·해제. 고정 행은 건드리지 않는다. */
export function setRoleForKeys(draft: Matrix, keys: string[], role: string, on: boolean, fixed: ReadonlySet<string> = FIXED_ADMIN_KEYS): Matrix {
  const next: Matrix = { ...draft }
  for (const k of keys) {
    if (fixed.has(k)) continue
    const cur = new Set(next[k] ?? [])
    if (on) cur.add(role)
    else cur.delete(role)
    next[k] = cur
  }
  return next
}

export type RolePreview = {
  visibleMenus: MenuEntry[]
  blockedMenus: MenuEntry[]
  /** 허용된 기능 (메뉴도 열려 있어 실제로 쓸 수 있는 것) */
  allowedFeatures: FeatureEntry[]
  /** 기능 권한에서 막힌 기능 */
  blockedFeatures: FeatureEntry[]
  /** 기능은 허용이지만 소속 메뉴가 막혀 쓸 수 없는 기능 */
  unreachableFeatures: FeatureEntry[]
}

/** 현재 매트릭스(저장 전 포함) 기준으로 한 역할이 보게 될 메뉴·기능. */
export function previewRole(
  role: string,
  menuDraft: Matrix | null,
  featureDraft: Matrix | null,
  menus: MenuEntry[],
  features: FeatureEntry[],
): RolePreview {
  const menuOpen = (m: MenuEntry) => !m.adminOnly && (menuDraft?.[m.key]?.has(role) ?? false)
  const visibleMenus = menus.filter(menuOpen)
  const blockedMenus = menus.filter((m) => !menuOpen(m))
  const openKeys = new Set(visibleMenus.map((m) => m.key))
  const allowedFeatures: FeatureEntry[] = []
  const blockedFeatures: FeatureEntry[] = []
  const unreachableFeatures: FeatureEntry[] = []
  for (const f of features) {
    if (!(featureDraft?.[f.key]?.has(role) ?? false)) blockedFeatures.push(f)
    // "dashboard" 소속 기능(AI 어시스턴트 플로팅 챗)은 모든 화면에 떠 있어 메뉴 차단과 무관하다
    else if (f.menuKey !== "dashboard" && !openKeys.has(f.menuKey)) unreachableFeatures.push(f)
    else allowedFeatures.push(f)
  }
  return { visibleMenus, blockedMenus, allowedFeatures, blockedFeatures, unreachableFeatures }
}
