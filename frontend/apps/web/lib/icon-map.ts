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
  Server,
  Settings,
  Star,
  Tags,
  Users,
  Webhook,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

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
  return iconMap[name] ?? HelpCircle
}
