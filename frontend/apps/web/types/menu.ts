export interface MenuItem {
  id: string
  title: string
  url: string
  icon: string
  adminOnly?: boolean
  /** 관리자 전용(adminOnly) 메뉴를 이 기능(명시 허용형) 중 하나라도 받은 역할에도 연다 — 플러그인 메뉴용(TODO-123). */
  grantedBy?: string[]
}

export interface MenuGroup {
  id: string
  label: string
  items: MenuItem[]
}

export interface MenuConfig {
  groups: MenuGroup[]
}
