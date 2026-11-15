// Added for SSO AUTH - Route guard that redirects unauthenticated users to /login.
"use client"

import { useEffect, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { loginPath } from "@/lib/navigation"
import { useAuth } from "./auth-context"

export function AuthGuard({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading, user } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (isLoading) return
    if (!isAuthenticated) {
      // 로그인 뒤 원래 화면으로 돌아오도록 현재 경로를 next 로 넘긴다(DEF-063)
      const { pathname, search } = window.location
      router.replace(loginPath(pathname + search))
    } else if (user?.must_change_password) {
      // 강제 변경 대상은 비밀번호를 바꾸기 전까지 보호 화면 밖으로 나갈 수 없다
      router.replace("/change-password-required")
    }
  }, [isLoading, isAuthenticated, user, router])

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    )
  }

  if (!isAuthenticated || user?.must_change_password) {
    return null
  }

  return <>{children}</>
}
