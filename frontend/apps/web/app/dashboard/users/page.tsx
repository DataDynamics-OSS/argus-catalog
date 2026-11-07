"use client"

import { Info } from "lucide-react"

import { Alert, AlertDescription } from "@workspace/ui/components/alert"
import { DashboardHeader } from "@/components/dashboard-header"
import { useAuth } from "@/features/auth"
import { UsersDialogs } from "@/features/users/components/users-dialogs"
import { UsersProvider } from "@/features/users/components/users-provider"
import { UsersTableWrapper } from "@/features/users/components/users-table-wrapper"

export default function UsersPage() {
  const { authType } = useAuth()
  return (
    <UsersProvider>
      <DashboardHeader title="사용자 관리" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        {authType === "keycloak" && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              Keycloak 인증 모드입니다. 계정 생성, 역할, 비밀번호, 이름·이메일·소속은 Keycloak 에서
              관리하며 로그인할 때마다 이 목록에 반영됩니다. 여기서는 활성/비활성(비활성이면 Keycloak
              로그인도 막힘), 전화번호 수정, 삭제만 할 수 있습니다. 삭제한 계정도 Keycloak 에 남아 있으면
              다음 로그인 때 다시 생기므로, 접근을 막으려면 비활성화하세요.
            </AlertDescription>
          </Alert>
        )}
        <UsersTableWrapper />
      </div>

      <UsersDialogs />
    </UsersProvider>
  )
}
