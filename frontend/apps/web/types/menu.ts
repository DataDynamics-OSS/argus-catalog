export interface MenuItem {
  id: string
  title: string
  url: string
  icon: string
  adminOnly?: boolean
}

export interface MenuGroup {
  id: string
  label: string
  items: MenuItem[]
}

export interface MenuConfig {
  groups: MenuGroup[]
}
