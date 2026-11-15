"use client"

/**
 * 권한 관리 — 역할(Superuser/User · 사용자 정의 역할) × 메뉴·기능 매트릭스 (admin 전용 메뉴).
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
 * - 역할별 미리보기: 저장 전 매트릭스 기준으로 각 역할이 보게 될 메뉴·기능
 * - 사용자 정의 역할(TODO-124): 역할 카드에서 추가 · 수정 · 삭제, 권한 표에 열로 더해진다
 * - 메뉴 보기 전용(TODO-124): 체크한 역할은 메뉴를 보기만 한다(편집 기능은 서버 · 화면 모두 막힘)
 * - 저장 충돌(TODO-124): 불러올 때 받은 ETag 를 If-Match 로 보내 그 사이 다른 관리자가 저장했으면 409 → 다시 불러온다
 */

import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronRight, Eye, ShieldCheck } from "lucide-react"
import { toast } from "@/lib/notify"

import { StatusBadge } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"
import { AdminAuditButton } from "@/features/audit/admin-audit-sheet"
import { authFetch } from "@/features/auth/auth-fetch"
import { RolesCard, type PermissionRole } from "@/features/permissions/roles-card"
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

/** 권한 표시(TODO-113) — 공용 `StatusBadge` 작은 크기. 예전엔 이름이 같은 로컬 Badge(네모 · 10px · 팔레트 색)였다. */
const PERM_TONE = { ok: "success", warn: "warning", muted: "neutral" } as const

function PermBadge({ children, tone }: { children: React.ReactNode; tone: keyof typeof PERM_TONE }) {
  return <StatusBadge tone={PERM_TONE[tone]} size="sm" className="ml-1.5 align-middle font-normal">{children}</StatusBadge>
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

/** 관리 메뉴를 여는 위임 기능 이름 — 하나면 그 이름, 여럿이면 "설정 항목 위임 n개 중 하나". */
function grantedByLabel(keys: readonly string[]): string {
  if (keys.length === 1) return FEATURE_REGISTRY.find((f) => f.key === keys[0])?.label ?? keys[0]!
  return `위임 ${keys.length}개 중 하나`
}

/** 보기 전용을 둘 수 있는 메뉴 — 관리 메뉴(항상 관리자 전용)를 뺀 메뉴 */
const VIEWABLE_MENUS = MENU_REGISTRY.filter((m) => !m.adminOnly)

export default function PermissionsPage() {
  const [menuDraft, setMenuDraft] = useState<Matrix | null>(null)
  const [viewDraft, setViewDraft] = useState<Matrix | null>(null)
  const [featureDraft, setFeatureDraft] = useState<Matrix | null>(null)
  const [menuDirty, setMenuDirty] = useState(0)
  const [viewDirty, setViewDirty] = useState(0)
  const [featureDirty, setFeatureDirty] = useState(0)
  const dirty = menuDirty + viewDirty + featureDirty

  // 역할 — 권한 표의 열(기본 두 역할 + 사용자 정의 역할). 역할을 바꾸면 표도 다시 불러온다(새 역할은 권한 행을 복사해 생긴다)
  const [roles, setRoles] = useState<PermissionRole[] | null>(null)
  const [columns, setColumns] = useState<readonly string[]>(MANAGED_ROLES)
  const [reloadKey, setReloadKey] = useState(0)
  const loadRoles = useCallback(async () => {
    try {
      const res = await authFetch("/api/v1/permissions/roles")
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const d: { roles: PermissionRole[]; columns: string[] } = await res.json()
      setRoles(d.roles)
      setColumns(d.columns)
    } catch (err) {
      console.error("Failed to load roles", err)
      toast.error("역할 목록을 불러오지 못했습니다.")
    }
  }, [])
  useEffect(() => { void loadRoles() }, [loadRoles])
  const roleLabel = useCallback(
    (r: string) => ROLE_LABELS[r] ?? roles?.find((x) => x.role_id === r)?.name ?? r,
    [roles],
  )
  const onRolesChanged = useCallback(() => {
    void loadRoles()
    setReloadKey((k) => k + 1)
  }, [loadRoles])

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
        <p className="flex-1 text-sm -mb-1">
          역할별 메뉴 접근과 기능 사용을 설정합니다. Admin 은 항상 모든 권한을 가지며,
          설정하지 않은 항목은 모든 역할에 허용됩니다 (잠그고 싶은 항목만 해제하세요).
          단, 설명에 &ldquo;미설정 시 관리자만&rdquo;이라고 표시된 기능은 허용할 역할을 직접 체크해야 합니다.
          메뉴를 막으면 사이드바에서 숨기고 주소로 직접 들어와도 페이지를 열 수 없습니다.
          {" "}<b className="font-medium">서버 API 차단</b> 표시가 있는 메뉴는 API 직접 호출도 막고,
          {" "}<b className="font-medium">편집 서버 차단</b> 표시는 조회 API 는 데이터셋 화면과 함께 써서 열어 두되 편집은 서버에서도 막으며,
          {" "}<b className="font-medium">화면만</b> 표시는 여러 메뉴가 함께 쓰는 API 라서 화면에서만 막습니다.
          메뉴를 <b className="font-medium">보기 전용</b>으로 두면 그 메뉴의 등록 · 수정 · 삭제 같은 편집 기능을 쓸 수 없습니다.
          변경 내용은 사용자가 탭으로 돌아오거나 1분 안에 화면에 반영됩니다(서버 API 차단은 즉시).
          역할 열 머리글이나 그룹 행의 체크박스로 한꺼번에 허용·해제할 수 있고, 저장하기 전에 아래
          {" "}<b className="font-medium">역할별 미리보기</b>로 결과를 확인할 수 있습니다.
        </p>
        <AdminAuditButton category="permissions" />
        </div>
        <RolesCard roles={roles} onChanged={onRolesChanged} />
        <MatrixCard
          title="메뉴 권한"
          subtitle="역할별 사이드바 표시·페이지 접근 (관리 메뉴는 항상 관리자 전용)"
          endpoint="/api/v1/permissions/menus"
          rows={MENU_REGISTRY}
          roles={columns}
          roleLabel={roleLabel}
          reloadKey={reloadKey}
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
                {(m as MenuEntry).adminOnly && (m as MenuEntry).grantedBy?.length
                  ? <PermBadge tone="warn">관리자 + {grantedByLabel((m as MenuEntry).grantedBy!)} 권한</PermBadge>
                  : (m as MenuEntry).adminOnly ? <PermBadge tone="warn">관리자 전용(고정)</PermBadge>
                  : (m as MenuEntry).serverEnforced ? <PermBadge tone="ok">서버 API 차단</PermBadge>
                  : (m as MenuEntry).serverWrite ? <PermBadge tone="ok">편집 서버 차단</PermBadge>
                  : <PermBadge tone="muted">화면만</PermBadge>}
              </div>
              <div className="text-[11px]">{(m as MenuEntry).url}</div>
            </>
          )}
        />
        <MatrixCard
          title="메뉴 보기 전용"
          subtitle="체크한 역할은 메뉴를 보기만 합니다 — 그 메뉴의 등록 · 수정 · 삭제 같은 편집 기능을 쓸 수 없습니다 (메뉴가 열린 역할에만 뜻이 있습니다)"
          endpoint="/api/v1/permissions/menu-views"
          rows={VIEWABLE_MENUS}
          roles={columns}
          roleLabel={roleLabel}
          reloadKey={reloadKey}
          emptyByDefault
          onDraftChange={setViewDraft}
          onDirtyChange={setViewDirty}
          collapsible
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
                {(m as MenuEntry).serverEnforced ? <PermBadge tone="ok">쓰기 API 차단</PermBadge> : null}
              </div>
              <div className="text-[11px]">{(m as MenuEntry).url}</div>
            </>
          )}
        />
        <MatrixCard
          title="기능 권한"
          subtitle="메뉴 내부의 버튼·탭·민감 데이터 단위 통제"
          endpoint="/api/v1/permissions/features"
          rows={FEATURE_REGISTRY}
          roles={columns}
          roleLabel={roleLabel}
          reloadKey={reloadKey}
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
                {(f as FeatureEntry).uiOnly && <PermBadge tone="muted">화면만</PermBadge>}
                {(f as FeatureEntry).view && <PermBadge tone="muted">조회 — 보기 전용에서도 사용</PermBadge>}
              </div>
              <div className="text-[11px]">{(f as FeatureEntry).description}</div>
            </>
          )}
        />
        <RolePreviewCard menuDraft={menuDraft} viewDraft={viewDraft} featureDraft={featureDraft} dirty={dirty}
          roles={columns} roleLabel={roleLabel} />
      </div>
    </>
  )
}

/** 역할별 미리보기 — 저장 전 매트릭스 기준으로 한 역할이 보게 될 메뉴·기능 */
function RolePreviewCard({ menuDraft, viewDraft, featureDraft, dirty, roles, roleLabel }: {
  menuDraft: Matrix | null
  viewDraft: Matrix | null
  featureDraft: Matrix | null
  dirty: number
  roles: readonly string[]
  roleLabel: (role: string) => string
}) {
  const [picked, setRole] = useState<string>(MANAGED_ROLES[0])
  // 고른 역할이 지워졌으면 첫 역할로
  const role = roles.includes(picked) ? picked : (roles[0] ?? MANAGED_ROLES[0])
  const p = previewRole(role, menuDraft, featureDraft, MENU_REGISTRY, FEATURE_REGISTRY, viewDraft)
  const menuLabel = (key: string) => MENU_REGISTRY.find((m) => m.key === key)?.label ?? key
  const chips = (items: { key: string; label: string }[], tone: "ok" | "muted" | "warn", empty: string) =>
    items.length === 0 ? <span className="text-xs text-muted-foreground">{empty}</span> : (
      <div className="flex flex-wrap gap-1">
        {items.map((it) => <PermBadge key={it.key} tone={tone}>{it.label}</PermBadge>)}
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
            <p className="mt-0.5 text-xs">
              위 매트릭스{dirty > 0 ? "(저장 전 변경 포함)" : ""} 기준으로 이 역할의 사용자가 보게 될 메뉴와 쓸 수 있는 기능입니다.
              관리 메뉴는 항상 관리자 전용입니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="미리볼 역할">
            {roles.map((r) => (
              <Button key={r} size="sm" variant={role === r ? "default" : "outline"} onClick={() => setRole(r)}>
                {roleLabel(r)}
              </Button>
            ))}
          </div>
        </div>
        {!menuDraft || !featureDraft ? (
          <p className="text-sm text-muted-foreground">권한을 불러오는 중…</p>
        ) : (
          <div className="grid gap-4 text-sm md:grid-cols-2">
            <div className="space-y-2">
              <div className="font-medium">보이는 메뉴 ({p.visibleMenus.length})</div>
              {chips(p.visibleMenus, "ok", "없음")}
              {p.readonlyMenus.length > 0 && (
                <>
                  <div className="pt-1 font-medium">그중 보기 전용 ({p.readonlyMenus.length})</div>
                  {chips(p.readonlyMenus, "warn", "없음")}
                </>
              )}
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
              {p.viewBlockedFeatures.length > 0 && (
                <>
                  <div className="pt-1 font-medium">허용했지만 메뉴가 보기 전용이라 쓸 수 없는 기능 ({p.viewBlockedFeatures.length})</div>
                  {chips(
                    p.viewBlockedFeatures.map((f) => ({ key: f.key, label: `${f.label} (${menuLabel(f.menuKey)})` })),
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
  roles, roleLabel, reloadKey, emptyByDefault = false,
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
  /** 역할 열 — 기본 두 역할 + 사용자 정의 역할 */
  roles: readonly string[]
  roleLabel: (role: string) => string
  /** 바뀌면 다시 불러온다(역할 추가 · 삭제 뒤) */
  reloadKey: number
  /** 보기 전용 표 — 미설정 = 아무 역할도 체크 안 됨, Admin 열은 체크 없음(관리자는 늘 편집 가능) */
  emptyByDefault?: boolean
}) {
  const [draft, setDraft] = useState<Matrix | null>(null)
  // 서버에 저장된 상태 — draft 와 비교해 저장 안 된 변경 수를 센다
  const [saved, setSaved] = useState<Matrix | null>(null)
  // 불러올 때 받은 버전 — 저장할 때 If-Match 로 보내 그 사이 다른 관리자가 저장했는지 서버가 확인한다
  const etag = useRef<string | null>(null)
  const dirty = countChanges(saved, draft, FIXED_ADMIN_KEYS, roles)

  useEffect(() => { onDraftChange?.(draft) }, [draft, onDraftChange])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  const [saving, setSaving] = useState(false)
  // 접이식 카드는 첫 그룹만 자동 펼침 (전부 펼치면 부담)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const [initialized, setInitialized] = useState(false)

  // 역할 열 — 내용이 같으면 다시 부르지 않도록 문자열로 비교한다
  const rolesKey = roles.join(",")
  const load = useCallback(async () => {
    try {
      const resp = await authFetch(endpoint)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      etag.current = resp.headers.get("ETag")
      const server: Record<string, string[]> = await resp.json()
      const loaded = initDraft(rows.map((r) => r.key), server, roles, emptyByDefault)
      setDraft(loaded)
      setSaved(loaded)
      if (collapsible && !initialized) {
        const first = grouped(rows)[0]?.group
        if (first) setOpenGroups({ [first]: true })
        setInitialized(true)
      }
    } catch (err) {
      console.error("Failed to load permissions", { endpoint, err })
      toast.error("권한을 불러오지 못했습니다. 관리자 권한이 필요합니다.")
    }
    // rows 는 정적 레지스트리라 의존성에서 제외해도 안전. roles 는 rolesKey 로 본다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, rolesKey, emptyByDefault])

  useEffect(() => { void load() }, [load, reloadKey])

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
        headers: {
          "Content-Type": "application/json",
          ...(etag.current ? { "If-Match": etag.current } : {}),
        },
        body: JSON.stringify(draftToPayload(draft, FIXED_ADMIN_KEYS)),
      })
      if (resp.status === 409) {
        // 다른 관리자가 먼저 저장 — 덮어쓰지 않고 최신 상태를 다시 불러온다(내 변경은 다시 적용해야 한다)
        toast.error(`다른 관리자가 먼저 ${title}을 저장했습니다. 최신 상태를 다시 불러왔으니 변경을 다시 적용하세요.`)
        await load()
        return
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      etag.current = resp.headers.get("ETag")
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
            <p className="mt-0.5 text-xs">{subtitle}</p>
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
              {saving ? "저장 중…" : "저장"}
            </Button>
          </div>
        </div>

        <div className="overflow-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/60 text-xs">
              <tr>
                <th className="px-3 py-2 text-left font-medium">항목</th>
                <th className="px-3 py-2 text-center font-medium">{ROLE_LABELS["argus-admin"]}</th>
                {roles.map((r) => (
                  <th key={r} className="whitespace-nowrap px-3 py-2 text-center font-medium">
                    <div className="flex items-center justify-center gap-1.5">
                      <TriCheckbox
                        state={roleState(draft, allKeys, r)}
                        disabled={!draft}
                        label={`${roleLabel(r)} 전체 허용/해제`}
                        onChange={(on) => bulk(allKeys, r, on)}
                      />
                      {roleLabel(r)}
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
                            <span className="rounded-full border bg-background px-1.5 text-[11px] font-normal">
                              {g.items.length}
                            </span>
                          </button>
                        ) : g.group}
                      </td>
                      {roles.map((r) => {
                        const keys = g.items.map((it) => it.key)
                        const editable = keys.some((k) => !FIXED_ADMIN_KEYS.has(k))
                        return (
                          <td key={r} className="border-t px-3 py-2 text-center">
                            {editable && (
                              <TriCheckbox
                                state={roleState(draft, keys, r)}
                                disabled={!draft}
                                label={`${g.group} — ${roleLabel(r)} 일괄 허용/해제`}
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
                          <input type="checkbox" checked={!emptyByDefault} disabled className="h-4 w-4 accent-primary opacity-60"
                            aria-label={emptyByDefault ? "관리자는 늘 편집할 수 있습니다" : "관리자는 늘 허용됩니다"} />
                        </td>
                        {roles.map((r) => (
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
