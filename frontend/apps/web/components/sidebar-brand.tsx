"use client"

import Link from "next/link"

import { SidebarMenuButton } from "@workspace/ui/components/sidebar"
import { Logo } from "@/components/logo"
import { brandLink } from "@/features/branding/branding"
import { useBranding } from "@/features/branding/use-branding"
import { DEFAULT_BRANDING, fetchBrandingConfig } from "@/features/settings/api"

/** 사이드바 위 브랜드 — 제품명 · 회사명 문구와 링크는 설정 > 사이드바 표시(TODO-145). */
export function SidebarBrand() {
  const brand = useBranding(fetchBrandingConfig)

  const { href, external } = brandLink(brand.link_url)
  const body = (
    <>
      <div className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground group-data-[collapsible=icon]:flex">
        <Logo className="size-5" />
      </div>
      {/* 자매 제품(RAG Studio 등) 헤더와 같은 2줄 구성 — 브랜드 글꼴(Roboto Condensed) 제품명 + 회사명 */}
      <div className="flex min-w-0 flex-col group-data-[collapsible=icon]:hidden">
        <span className="font-display truncate text-xl font-semibold leading-none tracking-wide text-sidebar-accent-foreground">
          {brand.title || DEFAULT_BRANDING.title}
        </span>
        {brand.subtitle && (
          <span className="mt-1 truncate text-xs text-sidebar-foreground/60">{brand.subtitle}</span>
        )}
      </div>
    </>
  )

  return (
    <SidebarMenuButton size="lg" asChild className="pr-6 group-data-[collapsible=icon]:pr-0">
      {external ? (
        <a href={href} target="_blank" rel="noopener noreferrer">{body}</a>
      ) : (
        <Link href={href}>{body}</Link>
      )}
    </SidebarMenuButton>
  )
}
