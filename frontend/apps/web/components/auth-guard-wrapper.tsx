// Added for SSO AUTH - client-side wrapper for AuthGuard (needed in server component layout).
"use client"

import type { ReactNode } from "react"
import { AuthGuard } from "@/features/auth"

export function AuthGuardWrapper({ children }: { children: ReactNode }) {
  return <AuthGuard>{children}</AuthGuard>
}
