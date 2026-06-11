"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import {
  Briefcase,
  Building2,
  ChevronUp,
  LogOut,
  Mail,
  Pencil,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  User,
  User2,
} from "lucide-react"

import { Avatar, AvatarFallback, AvatarImage } from "@workspace/ui/components/avatar"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@workspace/ui/components/sidebar"
import { Separator } from "@workspace/ui/components/separator"
import { AvatarPicker } from "@/components/avatar-picker"
import { useAuth } from "@/features/auth"
import { UsersActionDialog } from "@/features/users/components/users-action-dialog"
import type { User as UserType } from "@/features/users/data/schema"
import { findPreset, type AvatarPreset } from "@/lib/avatar"
import { toast } from "sonner"

export function SidebarUser() {
  const { user, logout, setAvatar, authType } = useAuth()
  const router = useRouter()
  const [profileOpen, setProfileOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false)

  if (!user) return null

  const selectedPreset = findPreset(user.avatar_preset_id)

  async function handleAvatarSelect(preset: AvatarPreset) {
    try {
      await setAvatar(preset.id)
    } catch {
      toast.error("아바타를 저장하지 못했습니다.")
    }
  }

  // 한글 이름은 성+이름 붙임(김병곤), 영문 이름은 first last 순서 + 공백(Admin User).
  const hasLatinName = /[A-Za-z]/.test(`${user.first_name ?? ""}${user.last_name ?? ""}`)
  const displayName = (
    hasLatinName
      ? `${user.first_name ?? ""} ${user.last_name ?? ""}`.trim()
      : `${user.last_name ?? ""}${user.first_name ?? ""}`.trim()
  ) || user.username

  const RoleIcon = user.is_admin
    ? ShieldCheck
    : user.is_superuser
      ? ShieldAlert
      : User
  const roleName =
    user.role === "admin"
      ? "관리자"
      : user.role === "superuser"
        ? "슈퍼유저"
        : "사용자"

  // Build a User object for UsersActionDialog (edit mode)
  const currentUserAsRow: UserType = {
    id: user.sub,
    firstName: user.first_name,
    lastName: user.last_name,
    username: user.username,
    email: user.email,
    organization: user.organization ?? "",
    department: user.department ?? "",
    phoneNumber: user.phone_number ?? "",
    status: "active" as const,
    role: (user.roles?.[0] || user.realm_roles?.find(r => r.startsWith("argus-")) || "argus-user") as "argus-admin" | "argus-superuser" | "argus-user",
    createdAt: new Date(),
    updatedAt: new Date(),
  }

  async function handleLogout() {
    await logout()
    router.replace("/login")
  }

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size="lg"
                className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
              >
                <Avatar className="h-8 w-8 rounded-lg">
                  {selectedPreset && (
                    <AvatarImage
                      src={selectedPreset.dataUri}
                      alt={selectedPreset.label}
                      className="rounded-lg"
                    />
                  )}
                  <AvatarFallback className="rounded-lg">
                    <RoleIcon className="size-4" />
                  </AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">{displayName}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    @{user.username}
                  </span>
                </div>
                <ChevronUp className="ml-auto size-4" />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-[--radix-dropdown-menu-trigger-width] min-w-56 rounded-lg"
              side="bottom"
              align="end"
              sideOffset={4}
            >
              <DropdownMenuItem onSelect={() => setProfileOpen(true)}>
                <User2 className="mr-2 size-4" />
                프로필
              </DropdownMenuItem>
              {authType === "local" && (
                <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
                  <Settings className="mr-2 size-4" />
                  계정 설정
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={handleLogout}>
                <LogOut className="mr-2 size-4" />
                로그아웃
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      {/* Profile Dialog (read-only) */}
      <Dialog open={profileOpen} onOpenChange={setProfileOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>프로필</DialogTitle>
          </DialogHeader>

          <div className="flex flex-col items-center gap-4 py-2">
            <button
              type="button"
              onClick={() => setAvatarPickerOpen(true)}
              className="group relative rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              aria-label="아바타 변경"
            >
              <Avatar className="h-16 w-16 rounded-full">
                {selectedPreset && (
                  <AvatarImage
                    src={selectedPreset.dataUri}
                    alt={selectedPreset.label}
                  />
                )}
                <AvatarFallback className="rounded-full">
                  <RoleIcon className="size-7" />
                </AvatarFallback>
              </Avatar>
              <span className="absolute inset-0 flex items-center justify-center gap-1 rounded-full bg-black/55 text-xs font-medium text-white opacity-0 transition group-hover:opacity-100">
                <Pencil className="size-3.5" />
                변경
              </span>
            </button>
            <div className="text-center">
              <p className="text-lg font-semibold">{displayName}</p>
              <p className="text-sm text-muted-foreground">@{user.username}</p>
            </div>
          </div>

          <Separator />

          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm pt-2">
            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <User className="size-3.5" />
              이름
            </dt>
            <dd className="font-medium">{displayName}</dd>

            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <User2 className="size-3.5" />
              사용자명
            </dt>
            <dd className="font-medium">@{user.username}</dd>

            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <Mail className="size-3.5" />
              이메일
            </dt>
            <dd className="font-medium">{user.email}</dd>

            {user.organization && (
              <>
                <dt className="flex items-center gap-1.5 text-muted-foreground">
                  <Building2 className="size-3.5" />
                  소속
                </dt>
                <dd className="font-medium">{user.organization}</dd>
              </>
            )}

            {user.department && (
              <>
                <dt className="flex items-center gap-1.5 text-muted-foreground">
                  <Briefcase className="size-3.5" />
                  소속 부서
                </dt>
                <dd className="font-medium">{user.department}</dd>
              </>
            )}

            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <Shield className="size-3.5" />
              역할
            </dt>
            <dd className="font-medium">{roleName}</dd>
          </dl>
        </DialogContent>
      </Dialog>

      {/* Account Settings — reuse UsersActionDialog in edit mode, without Role */}
      <UsersActionDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        currentRow={currentUserAsRow}
        hideRole
        onSaved={() => window.location.reload()}
      />

      <AvatarPicker
        open={avatarPickerOpen}
        onOpenChange={setAvatarPickerOpen}
        selectedId={user.avatar_preset_id}
        onSelect={handleAvatarSelect}
      />
    </>
  )
}
