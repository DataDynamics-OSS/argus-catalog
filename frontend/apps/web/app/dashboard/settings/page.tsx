"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import { DashboardHeader } from "@/components/dashboard-header"
import { AdminAuditButton } from "@/features/audit/admin-audit-sheet"
import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { OciModelRegistrySettings } from "@/features/settings/oci-model-registry-settings"
import { EmbeddingSettings } from "@/features/settings/embedding-settings"
import { LLMSettings } from "@/features/settings/llm-settings"
import { AssistantSettings } from "@/features/settings/assistant-settings"
import { ApiKeysSettings } from "@/features/settings/api-keys-settings"
import { AuthSettings } from "@/features/settings/auth-settings"
import { CorsSettings } from "@/features/settings/cors-settings"
import { CacheSettings } from "@/features/settings/cache-settings"
import { EmailSettings } from "@/features/settings/email-settings"
import { NotifySettings } from "@/features/settings/notify-settings"
import { ChangeMgmtSettings } from "@/features/settings/change-mgmt-settings"
import { CatalogSettings } from "@/features/settings/catalog-settings"
import { BrandingSettings } from "@/features/settings/branding-settings"
import { AiReadySettings } from "@/features/settings/ai-ready-settings"
import { RelationshipSettings } from "@/features/settings/relationship-settings"
import { MetadataSyncSettings } from "@/features/settings/metadata-sync-settings"
import { SettingSources } from "@/features/settings/setting-sources"

/**
 * category: 값 출처 패널에 쓸 설정 범주(catalog_configuration.category). 없으면 패널 없음.
 * feature: 이 탭을 여는 위임 기능(명시 허용형, TODO-123). 없으면 관리자만 — 인증 · CORS · 데이터셋 등록 ·
 * 쿼리 분석 · 변경관리. 서버 API 도 같은 기준이다.
 */
type SettingsTab = { value: string; label: string; render: () => React.ReactNode; category?: string; feature?: string }

// 탭이 12개라 한 줄에 다 놓으면 좁은 화면에서 잘린다 — 성격별로 묶어 왼쪽 세로 메뉴로 보여 준다.
// value 는 북마크·링크(?tab=) 호환을 위해 기존 값을 유지한다 (oci-model-registry → 오브젝트 스토리지).
const SETTINGS_GROUPS: { label: string; tabs: SettingsTab[] }[] = [
  {
    label: "인증·보안",
    tabs: [
      { value: "auth", label: "인증", render: () => <AuthSettings />, category: "auth" },
      { value: "api-keys", label: "API 키", render: () => <ApiKeysSettings />, feature: "settings.api-keys" },
      { value: "cors", label: "CORS", render: () => <CorsSettings />, category: "cors" },
      { value: "catalog", label: "데이터셋 등록", render: () => <CatalogSettings />, category: "catalog" },
    ],
  },
  {
    label: "데이터 관리",
    tabs: [
      { value: "ai-ready", label: "AI-Ready", render: () => <AiReadySettings />, category: "ai_ready", feature: "settings.ai-ready" },
    ],
  },
  {
    label: "AI",
    tabs: [
      { value: "embedding", label: "임베딩", render: () => <EmbeddingSettings />, category: "embedding", feature: "settings.embedding" },
      { value: "llm", label: "LLM / AI", render: () => <LLMSettings />, category: "llm", feature: "settings.llm" },
      { value: "assistant", label: "AI 어시스턴트", render: () => <AssistantSettings />, category: "assistant", feature: "settings.assistant" },
      { value: "relationships", label: "쿼리 분석", render: () => <RelationshipSettings /> },
    ],
  },
  {
    label: "연동",
    tabs: [
      { value: "oci-model-registry", label: "오브젝트 스토리지", render: () => <OciModelRegistrySettings />, category: "object_storage", feature: "settings.object-storage" },
      { value: "metadata-sync", label: "수집 서비스", render: () => <MetadataSyncSettings />, category: "metadata_sync", feature: "settings.metadata-sync" },
      { value: "email", label: "이메일", render: () => <EmailSettings />, category: "mail", feature: "settings.mail" },
      { value: "notify", label: "알림", render: () => <NotifySettings />, category: "notify", feature: "settings.notify" },
      { value: "change", label: "변경관리", render: () => <ChangeMgmtSettings />, category: "change" },
    ],
  },
  {
    label: "시스템",
    tabs: [
      { value: "branding", label: "사이드바 표시", render: () => <BrandingSettings />, category: "branding" },
      { value: "cache", label: "캐시", render: () => <CacheSettings />, feature: "settings.cache" },
    ],
  },
]

function tabFromUrl(tabs: SettingsTab[], fallback: string): string {
  if (typeof window === "undefined") return fallback
  const t = new URLSearchParams(window.location.search).get("tab")
  return t && tabs.some((x) => x.value === t) ? t : fallback
}

export default function SettingsPage() {
  // 관리자는 전부, 그 밖에는 위임받은 항목의 탭만(TODO-123)
  const { user } = useAuth()
  const { canUseFeature, loaded } = usePermissions()
  const isAdmin = !!user?.is_admin
  const groups = useMemo(
    () => SETTINGS_GROUPS
      .map((g) => ({ ...g, tabs: g.tabs.filter((t) => isAdmin || (!!t.feature && canUseFeature(t.feature))) }))
      .filter((g) => g.tabs.length > 0),
    [isAdmin, canUseFeature],
  )
  const allTabs = useMemo(() => groups.flatMap((g) => g.tabs), [groups])
  const defaultTab = allTabs[0]?.value ?? ""
  const [tab, setTab] = useState("")
  // 한 번 연 탭은 계속 마운트해 둔다 — 다른 탭에 갔다 와도 저장 전 입력이 사라지지 않는다.
  // (처음부터 전부 마운트하지 않는 건 탭마다 설정 조회가 나가기 때문)
  const [visited, setVisited] = useState<Set<string>>(() => new Set())

  const select = useCallback((value: string) => {
    setTab(value)
    setVisited((prev) => (prev.has(value) ? prev : new Set(prev).add(value)))
  }, [])

  useEffect(() => {
    // 권한 정보를 받기 전에는 탭을 고르지 않는다 — 위임 사용자가 관리자 탭을 잠깐 여는 일을 막는다
    if (!user || (!isAdmin && !loaded) || !defaultTab) return
    select(tabFromUrl(allTabs, defaultTab))
  }, [select, user, isAdmin, loaded, allTabs, defaultTab])

  const onTabChange = (value: string) => {
    select(value)
    const url = new URL(window.location.href)
    url.searchParams.set("tab", value)
    window.history.replaceState(null, "", url)
  }

  return (
    <>
      <DashboardHeader title="설정" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm">
            저장하지 않은 입력은 다른 항목으로 이동해도 유지되지만, 페이지를 떠나면 사라집니다.
          </p>
          <AdminAuditButton category="settings" />
        </div>
        <Tabs
          value={tab}
          onValueChange={onTabChange}
          orientation="vertical"
          className="flex-col items-stretch gap-4 lg:flex-row lg:items-start"
        >
          <TabsList
            variant="line"
            aria-label="설정 항목"
            className="w-full shrink-0 items-stretch gap-0.5 lg:sticky lg:top-4 lg:w-48"
          >
            {groups.map((g, i) => (
              <div
                key={g.label}
                className={`flex flex-col gap-0.5 ${i > 0 ? "mt-2 border-t pt-3" : ""}`}
                role="presentation"
              >
                {/* 상위(그룹) — 하위 항목보다 크고 진하게, 하위는 들여쓰고 왼쪽 선으로 묶는다 */}
                <div className="px-2 pb-1 text-sm font-bold text-foreground">{g.label}</div>
                <div className="ml-2 flex flex-col gap-0.5 border-l pl-2">
                  {g.tabs.map((t) => (
                    <TabsTrigger
                      key={t.value}
                      value={t.value}
                      className="h-8 flex-none text-muted-foreground data-active:bg-muted data-active:text-foreground"
                    >
                      {t.label}
                    </TabsTrigger>
                  ))}
                </div>
              </div>
            ))}
          </TabsList>
          <div className="min-w-0 flex-1">
            {allTabs.filter((t) => visited.has(t.value)).map((t) => (
              <TabsContent key={t.value} value={t.value} forceMount className="data-[state=inactive]:hidden">
                {t.render()}
                {t.category && <SettingSources category={t.category} />}
              </TabsContent>
            ))}
          </div>
        </Tabs>
      </div>
    </>
  )
}
