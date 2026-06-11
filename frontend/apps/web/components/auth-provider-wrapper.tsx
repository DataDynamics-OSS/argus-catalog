// Added for SSO AUTH - client-side wrapper for AuthProvider (needed in server component layout).
"use client"

import type { ReactNode } from "react"
import { AuthProvider } from "@/features/auth"

export function AuthProviderWrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}
