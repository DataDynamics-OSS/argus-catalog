// Added for SSO AUTH - login page with username/password form.
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

export default function LoginPage() {
  const router = useRouter()
  const { login, isAuthenticated, user } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  // If already authenticated, redirect (강제 변경 대상은 변경 화면으로)
  useEffect(() => {
    if (isAuthenticated) {
      router.replace(
        user?.must_change_password ? "/change-password-required" : "/dashboard"
      )
    }
  }, [isAuthenticated, user, router])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError("")
    setLoading(true)
    try {
      const userInfo = await login(username, password)
      // 강제 비밀번호 변경 대상이면 변경 화면으로, 아니면 대시보드로
      router.replace(
        userInfo.must_change_password ? "/change-password-required" : "/dashboard"
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold">Argus Catalog</CardTitle>
          <CardDescription>Sign in to your account</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                type="text"
                placeholder="Enter your username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
                autoComplete="username"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </CardContent>
        {/* Copyright — 오픈소스(Apache 2.0) 관행에 맞춰 "All rights reserved" 는 쓰지 않는다
            (라이선스가 부여하는 광범위한 권리와 상충하는 인상을 주므로). */}
        {/* -mb-6: Card 자체의 하단 패딩(py-6)을 상쇄해 푸터가 카드 바닥에 붙도록 */}
        <div className="border-t border-border px-6 py-3 -mb-6">
          <p className="text-center text-xs text-muted-foreground">
            Copyright © {new Date().getFullYear()} Data Dynamics Inc.
          </p>
        </div>
      </Card>
    </div>
  )
}
