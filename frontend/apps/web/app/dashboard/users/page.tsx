"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { UsersDialogs } from "@/features/users/components/users-dialogs"
import { UsersProvider } from "@/features/users/components/users-provider"
import { UsersTableWrapper } from "@/features/users/components/users-table-wrapper"

export default function UsersPage() {
  return (
    <UsersProvider>
      <DashboardHeader title="사용자 관리" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <UsersTableWrapper />
      </div>

      <UsersDialogs />
    </UsersProvider>
  )
}
