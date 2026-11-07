"use client"

/**
 * 권한 관리 — 역할(Superuser/User) × 메뉴·기능 매트릭스 (admin 전용 메뉴).
 *
 * MenuPermissionsCard/FeaturePermissionsCard 패턴:
 * - 전체 교체 PUT: 작은 매트릭스라 '화면에 보이는 상태' 그대로 서버에 푸시
 * - Admin 열은 항상 체크·비활성 (저장 대상 아님 — 서버도 무시)
 * - open-by-default: 서버에 없는 key 는 전 역할 체크 상태로 초기화
 *   (단, 명시 허용형 기능(defaultDeny)은 미설정 = admin 전용이라 미체크로 초기화)
 * - 기능 매트릭스는 메뉴별 접이식 (행이 많아 한 번에 펼치면 부담)
 * - 관리 메뉴(adminOnly)는 항상 관리자 전용 — 체크 불가, 저장 대상 아님(서버도 무시)
 * - 강제 범위 배지: 메뉴 "서버 API 차단"(serverEnforced) / "화면만", 기능 "화면만"(uiOnly)
 * - 역할 열 머리글·그룹 행의 체크박스로 일괄 허용/해제 (일부만 체크면 중간 상태)
 * - 저장된 상태와 다른 칸 수를 "저장 안 된 변경 n건"으로 표시, 변경이 있으면 페이지 이탈 경고
 * - 역할별 미리보기: 저장 전 매트릭스 기준으로 Superuser/User 가 보게 될 메뉴·기능
 */

import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronRight, Eye, ShieldCheck } from "lucide-react"
import { toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"
import { AdminAuditButton } from "@/features/audit/admin-audit-sheet"
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
import {
  FIXED_ADMIN_KEYS,
  countChanges,
  draftToPayload,
  initDraft,
  previewRole,
  roleState,
  setRoleForKeys,
  type Matrix,
} from "@/features/permissions/matrix"

function Badge({ children, tone }: { children: React.ReactNode; tone: "ok" | "muted" | "warn" }) {
  const cls = tone === "ok"
    ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400"
    : tone === "warn" ? "border-amber-500/40 text-amber-700 dark:text-amber-400"
    : "border-border text-muted-foreground"
  return <span className={`ml-1.5 inline-flex items-center rounded border px-1 py-px align-middle text-[10px] font-normal ${cls}`}>{children}</span>
}

/** 전부/일부/없음 3상태 체크박스 (일부 = indeterminate) */
function TriCheckbox({ state, onChange, disabled, label }: {
  state: "all" | "some" | "none"
  onChange: (on: boolean) => void
  disabled?: boolean
  label: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === "some"
  }, [state])
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={state === "all"}
      disabled={disabled}
      aria-label={label}
      title={label}
      onChange={() => onChange(state !== "all")}
      className="h-4 w-4 accent-primary"
    />
  )
}

export default function PermissionsPage() {
  const [menuDraft, setMenuDraft] = useState<Matrix | null>(null)
  const [featureDraft, setFeatureDraft] = useState<Matrix | null>(null)
  const [menuDirty, setMenuDirty] = useState(0)
  const [featureDirty, setFeatureDirty] = useState(0)
  const dirty = menuDirty + featureDirty

  // 저장 안 된 변경이 있으면 새로 고침·탭 닫기·외부 이동 때 브라우저 확인 창을 띄운다
  useEffect(() => {
    if (dirty === 0) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [dirty])

  return (
    <>
      <DashboardHeader title="권한 관리" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex items-start gap-4">
        <p className="flex-1 text-sm text-muted-foreground -mb-1">
          역할별 메뉴 접근과 기능 사용을 설정합니다. Admin 은 항상 모든 권한을 가지며,
          설정하지 않은 항목은 모든 역할에 허용됩니다 (잠그고 싶은 항목만 해제하세요).
          단, 설명에 &ldquo;미설정 시 관리자만&rdquo;이라고 표시된 기능은 허용할 역할을 직접 체크해야 합니다.
          메뉴를 막으면 사이드바에서 숨기고 주소로 직접 들어와도 페이지를 열 수 없습니다.
          {" "}<b className="font-medium">서버 API 차단</b> 표시가 있는 메뉴는 API 직접 호출도 막고,
          {" "}<b className="font-medium">화면만</b> 표시는 여러 메뉴가 함께 쓰는 API 라서 화면에서만 막습니다.
          변경 내용은 사용자가 탭으로 돌아오거나 1분 안에 화면에 반영됩니다(서버 API 차단은 즉시).
          역할 열 머리글이나 그룹 행의 체크박스로 한꺼번에 허용·해제할 수 있고, 저장하기 전에 아래
          {" "}<b className="font-medium">역할별 미리보기</b>로 결과를 확인할 수 있습니다.
        </p>
        <AdminAuditButton category="permissions" />
        </div>
        <MatrixCard
          title="메뉴 권한"
          subtitle="역할별 사이드바 표시·페이지 접근 (관리 메뉴는 항상 관리자 전용)"
          endpoint="/api/v1/permissions/menus"
          rows={MENU_REGISTRY}
          onDraftChange={setMenuDraft}
          onDirtyChange={setMenuDirty}
          grouped={(rows) =>
            MENU_GROUP_ORDER.map((g) => ({
              group: g,
              items: (rows as MenuEntry[]).filter((m) => m.group === g),
            })).filter((g) => g.items.length > 0)
          }
          rowLabel={(m) => (
            <>
              <div className="font-medium">
                {(m as MenuEntry).label}
                {(m as MenuEntry).adminOnly ? <Badge tone="warn">관리자 전용(고정)</Badge>
                  : (m as MenuEntry).serverEnforced ? <Badge tone="ok">서버 API 차단</Badge>
                  : <Badge tone="muted">화면만</Badge>}
              </div>
              <div className="text-[11px] text-muted-foreground">{(m as MenuEntry).url}</div>
            </>
          )}
        />
        <MatrixCard
          title="기능 권한"
          subtitle="메뉴 내부의 버튼·탭·민감 데이터 단위 통제"
          endpoint="/api/v1/permissions/features"
          rows={FEATURE_REGISTRY}
          onDraftChange={setFeatureDraft}
          onDirtyChange={setFeatureDirty}
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
              <div className="font-medium">
                {(f as FeatureEntry).label}
                {(f as FeatureEntry).uiOnly && <Badge tone="muted">화면만</Badge>}
              </div>
              <div className="text-[11px] text-muted-foreground">{(f as FeatureEntry).description}</div>
            </>
          )}
        />
        <RolePreviewCard menuDraft={menuDraft} featureDraft={featureDraft} dirty={dirty} />
      </div>
    </>
  )
}

/** 역할별 미리보기 — 저장 전 매트릭스 기준으로 한 역할이 보게 될 메뉴·기능 */
function RolePreviewCard({ menuDraft, featureDraft, dirty }: {
  menuDraft: Matrix | null
  featureDraft: Matrix | null
  dirty: number
}) {
  const [role, setRole] = useState<string>(MANAGED_ROLES[0])
  const p = previewRole(role, menuDraft, featureDraft, MENU_REGISTRY, FEATURE_REGISTRY)
  const menuLabel = (key: string) => MENU_REGISTRY.find((m) => m.key === key)?.label ?? key
  const chips = (items: { key: string; label: string }[], tone: "ok" | "muted" | "warn", empty: string) =>
    items.length === 0 ? <span className="text-xs text-muted-foreground">{empty}</span> : (
      <div className="flex flex-wrap gap-1">
        {items.map((it) => <Badge key={it.key} tone={tone}>{it.label}</Badge>)}
      </div>
    )
  return (
    <Card>
      <CardContent className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-1.5 font-semibold">
              <Eye className="h-4 w-4 text-muted-foreground" /> 역할별 미리보기
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              위 매트릭스{dirty > 0 ? "(저장 전 변경 포함)" : ""} 기준으로 이 역할의 사용자가 보게 될 메뉴와 쓸 수 있는 기능입니다.
              관리 메뉴는 항상 관리자 전용입니다.
            </p>
          </div>
          <div className="flex gap-1" role="group" aria-label="미리볼 역할">
            {MANAGED_ROLES.map((r) => (
              <Button key={r} size="sm" variant={role === r ? "default" : "outline"} onClick={() => setRole(r)}>
                {ROLE_LABELS[r]}
              </Button>
            ))}
          </div>
        </div>
        {!menuDraft || !featureDraft ? (
          <p className="text-sm text-muted-foreground">권한을 불러오는 중...</p>
        ) : (
          <div className="grid gap-4 text-sm md:grid-cols-2">
            <div className="space-y-2">
              <div className="font-medium">보이는 메뉴 ({p.visibleMenus.length})</div>
              {chips(p.visibleMenus, "ok", "없음")}
              <div className="pt-1 font-medium">막힌 메뉴 ({p.blockedMenus.length})</div>
              {chips(p.blockedMenus, "muted", "없음")}
            </div>
            <div className="space-y-2">
              <div className="font-medium">쓸 수 있는 기능 ({p.allowedFeatures.length})</div>
              {chips(p.allowedFeatures, "ok", "없음")}
              <div className="pt-1 font-medium">막힌 기능 ({p.blockedFeatures.length})</div>
              {chips(p.blockedFeatures, "muted", "없음")}
              {p.unreachableFeatures.length > 0 && (
                <>
                  <div className="pt-1 font-medium">허용했지만 메뉴가 막혀 쓸 수 없는 기능 ({p.unreachableFeatures.length})</div>
                  {chips(
                    p.unreachableFeatures.map((f) => ({ key: f.key, label: `${f.label} (${menuLabel(f.menuKey)})` })),
                    "warn",
                    "없음",
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function MatrixCard({
  title, subtitle, endpoint, rows, grouped, rowLabel, collapsible = false, onDraftChange, onDirtyChange,
}: {
  title: string
  subtitle: string
  endpoint: string
  rows: (MenuEntry | FeatureEntry)[]
  grouped: (rows: (MenuEntry | FeatureEntry)[]) => { group: string; items: (MenuEntry | FeatureEntry)[] }[]
  rowLabel: (row: MenuEntry | FeatureEntry) => React.ReactNode
  collapsible?: boolean
  onDraftChange?: (draft: Matrix | null) => void
  onDirtyChange?: (count: number) => void
}) {
  const [draft, setDraft] = useState<Matrix | null>(null)
  // 서버에 저장된 상태 — draft 와 비교해 저장 안 된 변경 수를 센다
  const [saved, setSaved] = useState<Matrix | null>(null)
  const dirty = countChanges(saved, draft)

  useEffect(() => { onDraftChange?.(draft) }, [draft, onDraftChange])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  const [saving, setSaving] = useState(false)
  // 접이식 카드는 첫 그룹만 자동 펼침 (전부 펼치면 부담)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const [initialized, setInitialized] = useState(false)

  const load = useCallback(async () => {
    try {
      const resp = await authFetch(endpoint)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const server: Record<string, string[]> = await resp.json()
      const loaded = initDraft(rows.map((r) => r.key), server)
      setDraft(loaded)
      setSaved(loaded)
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
        body: JSON.stringify(draftToPayload(draft, FIXED_ADMIN_KEYS)),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setSaved(draft)
      toast.success(`${title}을 저장했습니다.`)
    } catch (err) {
      console.error("Failed to save permissions", { endpoint, err })
      toast.error(`${title}을 저장하지 못했습니다.`)
    } finally {
      setSaving(false)
    }
  }

  const groups = grouped(rows)
  const allKeys = rows.map((r) => r.key)
  const bulk = (keys: string[], role: string, on: boolean) =>
    setDraft((prev) => (prev ? setRoleForKeys(prev, keys, role, on) : prev))

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
          <div className="flex items-center gap-2">
            {dirty > 0 && (
              <span className="rounded border border-amber-500/40 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400">
                저장 안 된 변경 {dirty}건
              </span>
            )}
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
            <Button variant="outline" size="sm" onClick={() => setDraft(saved)} disabled={!draft || saving || dirty === 0}>
              변경 취소
            </Button>
            <Button size="sm" onClick={save} disabled={!draft || saving || dirty === 0}>
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
                    <div className="flex items-center justify-center gap-1.5">
                      <TriCheckbox
                        state={roleState(draft, allKeys, r)}
                        disabled={!draft}
                        label={`${ROLE_LABELS[r]} 전체 허용/해제`}
                        onChange={(on) => bulk(allKeys, r, on)}
                      />
                      {ROLE_LABELS[r]}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const open = collapsible ? (openGroups[g.group] ?? false) : true
                return (
                  <Fragment key={g.group}>
                    {/* 그룹 머리행 — 항목보다 눈에 띄도록 진한 배경·왼쪽 강조선·굵은 본문색 글자 */}
                    <tr className="bg-muted/80">
                      <td colSpan={2}
                          className="border-t border-l-4 border-l-primary/70 px-3 py-2 text-[13px] font-semibold text-foreground">
                        {collapsible ? (
                          <button type="button" aria-expanded={open}
                            className="flex w-full items-center gap-1.5 text-left hover:text-primary"
                            onClick={() => setOpenGroups((o) => ({ ...o, [g.group]: !open }))}>
                            {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                            {g.group}
                            <span className="rounded-full border bg-background px-1.5 text-[11px] font-normal text-muted-foreground">
                              {g.items.length}
                            </span>
                          </button>
                        ) : g.group}
                      </td>
                      {MANAGED_ROLES.map((r) => {
                        const keys = g.items.map((it) => it.key)
                        const editable = keys.some((k) => !FIXED_ADMIN_KEYS.has(k))
                        return (
                          <td key={r} className="border-t px-3 py-2 text-center">
                            {editable && (
                              <TriCheckbox
                                state={roleState(draft, keys, r)}
                                disabled={!draft}
                                label={`${g.group} — ${ROLE_LABELS[r]} 일괄 허용/해제`}
                                onChange={(on) => bulk(keys, r, on)}
                              />
                            )}
                          </td>
                        )
                      })}
                    </tr>
                    {open && g.items.map((row) => (
                      <tr key={row.key} className="border-t border-border/60">
                        {/* 그룹 아래 항목은 들여써서 소속이 보이게 */}
                        <td className="py-2 pl-9 pr-3">{rowLabel(row)}</td>
                        {/* Admin — 항상 허용·비활성 */}
                        <td className="px-3 py-2 text-center">
                          <input type="checkbox" checked disabled className="h-4 w-4 accent-primary opacity-60" />
                        </td>
                        {MANAGED_ROLES.map((r) => (
                          <td key={r} className="px-3 py-2 text-center">
                            <input
                              type="checkbox"
                              checked={FIXED_ADMIN_KEYS.has(row.key) ? false : (draft?.[row.key]?.has(r) ?? false)}
                              disabled={!draft || FIXED_ADMIN_KEYS.has(row.key)}
                              title={FIXED_ADMIN_KEYS.has(row.key) ? "관리 메뉴는 항상 관리자 전용입니다" : undefined}
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
