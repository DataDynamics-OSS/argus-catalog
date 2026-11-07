import {
  Bell,
  BookOpen,
  Bot,
  Box,
  ClipboardCheck,
  Database,
  FolderOpen,
  FolderTree,
  Globe,
  HelpCircle,
  Inbox,
  Shield,
  ShieldCheck,
  LayoutDashboard,
  Network,
  Plug,
  Server,
  Settings,
  Star,
  Tags,
  Users,
  Webhook,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { pluginIcons } from "@/plugins/registry"

const iconMap: Record<string, LucideIcon> = {
  Bell,
  BookOpen,
  Bot,
  Box,
  ClipboardCheck,
  Database,
  FolderOpen,
  FolderTree,
  Globe,
  Inbox,
  LayoutDashboard,
  Network,
  Plug,
  Server,
  Settings,
  Shield,
  ShieldCheck,
  Star,
  Tags,
  Users,
  Webhook,
}

export function getIcon(name: string): LucideIcon {
  // 코어 아이콘 → 플러그인 제공 아이콘 → 기본(HelpCircle) 순으로 조회.
  return iconMap[name] ?? pluginIcons[name] ?? HelpCircle
}
