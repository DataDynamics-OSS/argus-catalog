"use client"

/**
 * 권한 관리 — 역할(Superuser/User) × 메뉴·기능 매트릭스 (admin 전용 메뉴).
 *
 * MenuPermissionsCard/FeaturePermissionsCard 패턴:
 * - 전체 교체 PUT: 작은 매트릭스라 '화면에 보이는 상태' 그대로 서버에 푸시
 * - Admin 열은 항상 체크·비활성 (저장 대상 아님 — 서버도 무시)
 * - open-by-default: 서버에 없는 key 는 전 역할 체크 상태로 초기화
 * - 기능 매트릭스는 메뉴별 접이식 (행이 많아 한 번에 펼치면 부담)
 */

import { Fragment, useCallback, useEffect, useState } from "react"
import { ChevronDown, ChevronRight, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"
import { authFetch } from "@/features/auth/auth-fetch"
import {
  FEATURE_REGISTRY,
  MANAGED_ROLES,
  MENU_GROUP_ORDER,
  MENU_REGISTRY,
  ROLE_LABELS,
  type FeatureEntry,
  type MenuEntry,
} from "@/lib/permission-registry"

type Matrix = Record<string, Set<string>>

/** 서버 맵 → draft 초기화. 서버에 없는 key = 전 역할 허용(open-by-default). */
function initDraft(keys: string[], server: Record<string, string[]>): Matrix {
  const next: Matrix = {}
  for (const k of keys) {
    if (server[k] === undefined) {
      next[k] = new Set(MANAGED_ROLES)
    } else {
      // "__none__" 센티널(admin 전용)은 빈 집합으로
      next[k] = new Set(server[k].filter((r) => (MANAGED_ROLES as readonly string[]).includes(r)))
    }
  }
  return next
}

function draftToPayload(draft: Matrix): Record<string, string[]> {
  const payload: Record<string, string[]> = {}
  for (const [k, roles] of Object.entries(draft)) payload[k] = Array.from(roles)
  return payload
}

export default function PermissionsPage() {
  return (
    <>
      <DashboardHeader title="권한 관리" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <p className="text-sm text-muted-foreground -mb-1">
          역할별 메뉴 표시와 기능 사용을 설정합니다. Admin 은 항상 모든 권한을 가지며,
          설정하지 않은 항목은 모든 역할에 허용됩니다 (잠그고 싶은 항목만 해제하세요).
        </p>
        <MatrixCard
          title="메뉴 권한"
          subtitle="역할별 사이드바 메뉴 표시 여부"
          endpoint="/api/v1/permissions/menus"
          rows={MENU_REGISTRY}
          grouped={(rows) =>
            MENU_GROUP_ORDER.map((g) => ({
              group: g,
              items: (rows as MenuEntry[]).filter((m) => m.group === g),
            })).filter((g) => g.items.length > 0)
          }
          rowLabel={(m) => (
            <>
              <div className="font-medium">{(m as MenuEntry).label}</div>
              <div className="text-[11px] text-muted-foreground">{(m as MenuEntry).url}</div>
            </>
          )}
        />
        <MatrixCard
          title="기능 권한"
          subtitle="메뉴 내부의 버튼·탭·민감 데이터 단위 통제"
          endpoint="/api/v1/permissions/features"
          rows={FEATURE_REGISTRY}
          collapsible
          grouped={(rows) => {
            const menus = Array.from(new Set((rows as FeatureEntry[]).map((f) => f.menuKey)))
            return menus.map((mk) => ({
              group: MENU_REGISTRY.find((m) => m.key === mk)?.label ?? mk,
              items: (rows as FeatureEntry[]).filter((f) => f.menuKey === mk),
            }))
          }}
          rowLabel={(f) => (
            <>
              <div className="font-medium">{(f as FeatureEntry).label}</div>
              <div className="text-[11px] text-muted-foreground">{(f as FeatureEntry).description}</div>
            </>
          )}
        />
      </div>
    </>
  )
}

function MatrixCard({
  title, subtitle, endpoint, rows, grouped, rowLabel, collapsible = false,
}: {
  title: string
  subtitle: string
  endpoint: string
  rows: (MenuEntry | FeatureEntry)[]
  grouped: (rows: (MenuEntry | FeatureEntry)[]) => { group: string; items: (MenuEntry | FeatureEntry)[] }[]
  rowLabel: (row: MenuEntry | FeatureEntry) => React.ReactNode
  collapsible?: boolean
}) {
  const [draft, setDraft] = useState<Matrix | null>(null)
  const [saving, setSaving] = useState(false)
  // 접이식 카드는 첫 그룹만 자동 펼침 (전부 펼치면 부담)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const [initialized, setInitialized] = useState(false)

  const load = useCallback(async () => {
    try {
      const resp = await authFetch(endpoint)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const server: Record<string, string[]> = await resp.json()
      setDraft(initDraft(rows.map((r) => r.key), server))
      if (collapsible && !initialized) {
        const first = grouped(rows)[0]?.group
        if (first) setOpenGroups({ [first]: true })
        setInitialized(true)
      }
    } catch (err) {
      console.error("Failed to load permissions", { endpoint, err })
      toast.error("권한을 불러오지 못했습니다 — 관리자 권한이 필요합니다")
    }
    // rows 는 정적 레지스트리라 의존성에서 제외해도 안전
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint])

  useEffect(() => { void load() }, [load])

  const toggle = (key: string, role: string) => {
    setDraft((prev) => {
      if (!prev) return prev
      const cur = new Set(prev[key] ?? [])
      if (cur.has(role)) cur.delete(role)
      else cur.add(role)
      return { ...prev, [key]: cur }
    })
  }

  const save = async () => {
    if (!draft) return
    setSaving(true)
    try {
      const resp = await authFetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draftToPayload(draft)),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      toast.success(`${title} 저장됨`)
    } catch (err) {
      console.error("Failed to save permissions", { endpoint, err })
      toast.error("저장 실패")
    } finally {
      setSaving(false)
    }
  }

  const groups = grouped(rows)

  return (
    <Card>
      <CardContent className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="flex items-center gap-1.5 font-semibold">
              <ShieldCheck className="h-4 w-4 text-muted-foreground" /> {title}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
          </div>
          <div className="flex gap-2">
            {collapsible && (
              <Button
                variant="ghost" size="sm"
                onClick={() => {
                  const all = grouped(rows)
                  const anyClosed = all.some((g) => !openGroups[g.group])
                  setOpenGroups(Object.fromEntries(all.map((g) => [g.group, anyClosed])))
                }}
              >
                전체 {groups.some((g) => !openGroups[g.group]) ? "펼치기" : "접기"}
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={load} disabled={!draft || saving}>
              변경 취소
            </Button>
            <Button size="sm" onClick={save} disabled={!draft || saving}>
              {saving ? "저장 중..." : "저장"}
            </Button>
          </div>
        </div>

        <div className="overflow-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/60 text-xs">
              <tr>
                <th className="px-3 py-2 text-left font-medium">항목</th>
                <th className="px-3 py-2 text-center font-medium">{ROLE_LABELS["argus-admin"]}</th>
                {MANAGED_ROLES.map((r) => (
                  <th key={r} className="whitespace-nowrap px-3 py-2 text-center font-medium">
                    {ROLE_LABELS[r]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const open = collapsible ? (openGroups[g.group] ?? false) : true
                return (
                  <Fragment key={g.group}>
                    <tr className="bg-muted/30">
                      <td colSpan={2 + MANAGED_ROLES.length}
                          className="border-t px-3 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                        {collapsible ? (
                          <button type="button" className="inline-flex items-center gap-1 hover:text-foreground"
                            onClick={() => setOpenGroups((o) => ({ ...o, [g.group]: !open }))}>
                            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                            {g.group} ({g.items.length})
                          </button>
                        ) : g.group}
                      </td>
                    </tr>
                    {open && g.items.map((row) => (
                      <tr key={row.key} className="border-t border-border/60">
                        <td className="px-3 py-2">{rowLabel(row)}</td>
                        {/* Admin — 항상 허용·비활성 */}
                        <td className="px-3 py-2 text-center">
                          <input type="checkbox" checked disabled className="h-4 w-4 accent-primary opacity-60" />
                        </td>
                        {MANAGED_ROLES.map((r) => (
                          <td key={r} className="px-3 py-2 text-center">
                            <input
                              type="checkbox"
                              checked={draft?.[row.key]?.has(r) ?? false}
                              disabled={!draft}
                              onChange={() => toggle(row.key, r)}
                              className="h-4 w-4 accent-primary"
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}
