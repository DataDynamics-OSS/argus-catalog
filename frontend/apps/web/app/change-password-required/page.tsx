// 최초 로그인 시 강제 비밀번호 변경 화면.
// LDAP 동기화 계정은 초기 비밀번호가 생년월일(YYYYMMDD)이라, 변경 전까지 이 화면에
// 머문다(대시보드 진입 차단). 변경 성공 시 백엔드가 게이트 해제된 새 토큰을 재발급하고,
// applyTokens 로 세션을 교체한 뒤 대시보드로 이동한다.
"use client"

import { useEffect, useState, type FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"
import { useAuth } from "@/features/auth"
import { changePassword } from "@/features/auth/api"

// 새 비밀번호 정책: 8자 이상, 소문자 1개 이상, 숫자 1개 이상(관리자 재설정 다이얼로그와 동일).
function validateNewPassword(pw: string): string | null {
  if (pw.length < 8) return "비밀번호는 8자 이상이어야 합니다."
  if (!/[a-z]/.test(pw)) return "비밀번호에 소문자를 1자 이상 포함해야 합니다."
  if (!/[0-9]/.test(pw)) return "비밀번호에 숫자를 1자 이상 포함해야 합니다."
  return null
}

export default function ChangePasswordRequiredPage() {
  const router = useRouter()
  const { user, accessToken, isAuthenticated, isLoading, applyTokens, logout } = useAuth()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  // 인증 안 됨 → 로그인. 이미 변경 완료(플래그 false)면 대시보드로.
  useEffect(() => {
    if (isLoading) return
    if (!isAuthenticated) {
      router.replace("/login")
    } else if (user && !user.must_change_password) {
      router.replace("/dashboard")
    }
  }, [isLoading, isAuthenticated, user, router])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError("")

    const policyError = validateNewPassword(newPassword)
    if (policyError) {
      setError(policyError)
      return
    }
    if (newPassword !== confirmPassword) {
      setError("새 비밀번호가 일치하지 않습니다.")
      return
    }
    if (newPassword === currentPassword) {
      setError("새 비밀번호는 현재 비밀번호와 달라야 합니다.")
      return
    }
    if (!accessToken) {
      setError("세션이 만료되었습니다. 다시 로그인해 주세요.")
      return
    }

    setLoading(true)
    try {
      const tokens = await changePassword(accessToken, currentPassword, newPassword)
      await applyTokens(tokens) // 게이트 해제된 새 토큰으로 교체
      router.replace("/dashboard")
    } catch (err) {
      setError(err instanceof Error ? err.message : "비밀번호 변경에 실패했습니다.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold">비밀번호 변경 필요</CardTitle>
          <CardDescription>
            최초 로그인입니다. 계속하려면 비밀번호를 변경해 주세요.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="current">현재 비밀번호</Label>
              <Input
                id="current"
                type="password"
                placeholder="현재 비밀번호(예: 생년월일 8자리)"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
                autoFocus
                autoComplete="current-password"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="new">새 비밀번호</Label>
              <Input
                id="new"
                type="password"
                placeholder="8자 이상, 소문자·숫자 포함"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                autoComplete="new-password"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="confirm">새 비밀번호 확인</Label>
              <Input
                id="confirm"
                type="password"
                placeholder="새 비밀번호를 다시 입력"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                autoComplete="new-password"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "변경 중..." : "비밀번호 변경"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              onClick={() => {
                void logout().then(() => router.replace("/login"))
              }}
            >
              로그아웃
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
