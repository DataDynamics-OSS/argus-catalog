"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { AlertTriangle, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Search, Star, X } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import {
  type ApiCredential, type ApiDetail, type ApiFavorite,
  addApiFavorite, fetchAllFavorites, fetchApiCredentials, fetchApiDetail, removeApiFavorite,
} from "../api"
import { API_STATUS_LABEL, API_STATUS_VARIANTS, protocolLabel } from "./apis-constants"
import { MethodBadge, TabEmpty, TabError, TabLoading } from "./apis-detail-shared"
import { EndpointsTab } from "./endpoints-tab"

type FavSort = "name" | "recent"
type Group = {
  apiName: string
  displayName: string | null
  status: string | null
  protocol: string | null
  items: ApiFavorite[]
  /** 그룹 안에서 가장 최근에 추가한 시각 — 최근 추가순 정렬용 */
  latest: string
}

const favKey = (method: string, path: string) => `${method} ${path}`
// API 가 이 수 이하이면 처음부터 모두 펼친다(많으면 접어 두고 펼칠 때 상세를 불러온다).
const AUTO_EXPAND_MAX = 5

function matches(f: ApiFavorite, q: string) {
  if (!q) return true
  return [f.api_name, f.api_display_name, f.method, f.path, f.summary].some((v) => v?.toLowerCase().includes(q))
}

// ---------------------------------------------------------------------------
// API 1개 분량 본문 — 펼칠 때 처음 상세·자격증명을 불러와 즐겨찾기 엔드포인트만 표시(문서/호출)
// ---------------------------------------------------------------------------
function FavoriteApiBody({ apiName, keys, onToggleFavorite }: {
  apiName: string
  /** 표시할(검색에 맞는) 즐겨찾기 "METHOD path" */
  keys: Set<string>
  onToggleFavorite: (method: string, path: string) => void
}) {
  const [api, setApi] = useState<ApiDetail | null>(null)
  const [credentials, setCredentials] = useState<ApiCredential[]>([])
  const [error, setError] = useState<string | null>(null)
  const [seq, setSeq] = useState(0)

  useEffect(() => {
    let active = true
    Promise.all([
      fetchApiDetail(apiName),
      // 자격증명 권한이 없으면(403) 빈 목록 — 호출은 수동 헤더로 가능
      fetchApiCredentials(apiName).catch((): ApiCredential[] => []),
    ])
      .then(([d, c]) => { if (active) { setApi(d); setCredentials(c) } })
      .catch((e) => { if (active) setError(errorMessage(e) ?? "API 정보를 불러오지 못했습니다.") })
    return () => { active = false }
  }, [apiName, seq])

  if (error) return <TabError message={error} onRetry={() => { setError(null); setSeq((n) => n + 1) }} />
  if (!api) return <TabLoading />
  return <EndpointsTab api={api} credentials={credentials} endpointFilter={keys} hideHeader favoriteKeys={keys} onToggleFavorite={onToggleFavorite} />
}

// ---------------------------------------------------------------------------
// 즐겨찾기 화면 — API 별로 묶어 문서 확인·호출(Try-it)
// ---------------------------------------------------------------------------
export function ApisFavorites() {
  const [favorites, setFavorites] = useState<ApiFavorite[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [seq, setSeq] = useState(0)
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState<FavSort>("name")
  // 사용자가 직접 접거나 펼친 API — 없으면 기본값(API 수에 따라)
  const [openOverride, setOpenOverride] = useState<Map<string, boolean>>(new Map())
  // 한 번 펼친 API 는 접어도 본문을 유지(다시 펼칠 때 재조회·입력 초기화 방지)
  const [mounted, setMounted] = useState<Set<string>>(new Set())

  useEffect(() => {
    let active = true
    fetchAllFavorites()
      .then((fs) => { if (active) setFavorites(fs) })
      .catch((e) => { if (active) setError(errorMessage(e) ?? "즐겨찾기를 불러오지 못했습니다.") })
    return () => { active = false }
  }, [seq])

  // 즐겨찾기 해제 — 화면에서 바로 빼고, 실수였으면 "되돌리기"로 다시 추가
  const unfavorite = useCallback(async (f: ApiFavorite) => {
    const apiName = f.api_name ?? ""
    try {
      await removeApiFavorite(apiName, f.method, f.path)
    } catch (e) {
      notifyError(e, "즐겨찾기를 해제하지 못했습니다.")
      return
    }
    setFavorites((list) => list?.filter((x) => x.id !== f.id) ?? list)
    toast.success(`즐겨찾기를 해제했습니다: ${f.method} ${f.path}`, {
      action: {
        label: "되돌리기",
        onClick: () => {
          addApiFavorite(apiName, f.method, f.path)
            .then(() => setFavorites((list) => (list && !list.some((x) => x.id === f.id) ? [...list, f] : list)))
            .catch((e) => notifyError(e, "즐겨찾기를 다시 추가하지 못했습니다."))
        },
      },
    })
  }, [])

  if (error) {
    return <TabError message={error} onRetry={() => { setError(null); setSeq((n) => n + 1) }} />
  }
  if (!favorites) return <TabLoading />

  const intro = (
    <p className="text-sm text-muted-foreground">
      자주 사용하는 엔드포인트를 API별로 모아 보는 화면입니다. API 상세의 엔드포인트 행 오른쪽 ★ 아이콘으로 추가하며, 각 항목을 펼쳐 문서 확인·호출(Try-it)을 이 화면에서 바로 할 수 있습니다.
    </p>
  )
  if (favorites.length === 0) {
    return (
      <div className="space-y-6">
        {intro}
        <div className="flex flex-col items-center gap-2 rounded-md border border-dashed py-16 text-center">
          <Star className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">즐겨찾기한 엔드포인트가 없습니다.</p>
          <p className="text-xs text-muted-foreground">API 상세 → 엔드포인트 행 오른쪽의 ★ 아이콘을 눌러 추가하세요.</p>
          <Link href="/dashboard/apis" className="text-xs text-primary hover:underline">API 목록으로 이동</Link>
        </div>
      </div>
    )
  }

  // API 별 그룹 — 검색은 API 이름·표시명·메서드·경로·설명
  const q = search.trim().toLowerCase()
  const byApi = new Map<string, Group>()
  for (const f of favorites) {
    const name = f.api_name ?? String(f.api_id)
    let g = byApi.get(name)
    if (!g) {
      g = { apiName: name, displayName: f.api_display_name, status: f.api_status, protocol: f.api_protocol, items: [], latest: f.created_at }
      byApi.set(name, g)
    }
    g.items.push(f)
    if (f.created_at > g.latest) g.latest = f.created_at
  }
  const allGroups = [...byApi.values()]
  const groups = allGroups
    .map((g) => ({ ...g, items: g.items.filter((f) => matches(f, q)) }))
    .filter((g) => g.items.length > 0)
    .sort((a, b) => sort === "recent"
      ? b.latest.localeCompare(a.latest)
      : (a.displayName || a.apiName).localeCompare(b.displayName || b.apiName, "ko"))
  const missingTotal = favorites.filter((f) => f.endpoint_missing).length
  const shownCount = groups.reduce((n, g) => n + g.items.length, 0)

  const defaultOpen = allGroups.length <= AUTO_EXPAND_MAX
  const isOpen = (name: string) => openOverride.get(name) ?? defaultOpen
  const setOpen = (name: string, open: boolean) => {
    setOpenOverride((m) => new Map(m).set(name, open))
    if (open) setMounted((s) => (s.has(name) ? s : new Set(s).add(name)))
  }
  const setAll = (open: boolean) => {
    setOpenOverride(new Map(groups.map((g) => [g.apiName, open])))
    if (open) setMounted((s) => new Set([...s, ...groups.map((g) => g.apiName)]))
  }
  const anyOpen = groups.some((g) => isOpen(g.apiName))

  return (
    <div className="space-y-4">
      {intro}

      <div className="flex flex-wrap items-center gap-2">
        <p className="mr-auto text-sm">
          엔드포인트 <b>{favorites.length}</b>개 · API <b>{allGroups.length}</b>개
          {missingTotal > 0 && <span className="ml-2 text-amber-600">· 현재 목록에 없는 엔드포인트 {missingTotal}개</span>}
          {q && <span className="ml-2 text-muted-foreground">(검색 결과 {shownCount}개)</span>}
        </p>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="즐겨찾기 검색"
            placeholder="API·메서드·경로·설명 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 w-[260px] pr-7 pl-8 text-sm"
          />
          {search && (
            <button type="button" aria-label="검색어 지우기" onClick={() => setSearch("")} className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <Select value={sort} onValueChange={(v) => setSort(v as FavSort)}>
          <SelectTrigger className="h-8 w-[130px] text-sm" aria-label="정렬"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="name">API 이름순</SelectItem>
            <SelectItem value="recent">최근 추가순</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" variant="outline" className="h-8" onClick={() => setAll(!anyOpen)}>
          {anyOpen ? <><ChevronsDownUp className="mr-1 h-4 w-4" /> 모두 접기</> : <><ChevronsUpDown className="mr-1 h-4 w-4" /> 모두 펼치기</>}
        </Button>
      </div>

      {groups.length === 0 ? (
        <TabEmpty>검색 조건에 맞는 즐겨찾기가 없습니다.</TabEmpty>
      ) : groups.map((g) => {
        const open = isOpen(g.apiName)
        const present = g.items.filter((f) => !f.endpoint_missing)
        const missing = g.items.filter((f) => f.endpoint_missing)
        const keys = new Set(present.map((f) => favKey(f.method, f.path)))
        const byKey = new Map(present.map((f) => [favKey(f.method, f.path), f]))
        const showBody = open || mounted.has(g.apiName) || (defaultOpen && !openOverride.has(g.apiName))
        return (
          <section key={g.apiName} className="rounded-md border">
            <div className="flex flex-wrap items-center gap-2 px-3 py-2">
              <button
                type="button"
                aria-expanded={open}
                aria-label={open ? "접기" : "펼치기"}
                onClick={() => setOpen(g.apiName, !open)}
                className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted"
              >
                {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              <Link href={`/dashboard/apis?api=${encodeURIComponent(g.apiName)}`} className="text-sm font-semibold hover:underline" title="API 상세 열기">
                {g.displayName || g.apiName}
              </Link>
              {g.displayName && <span className="font-mono text-xs text-muted-foreground">{g.apiName}</span>}
              {g.status && (
                <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", API_STATUS_VARIANTS[g.status] ?? API_STATUS_VARIANTS.draft)}>
                  {API_STATUS_LABEL[g.status] ?? g.status}
                </span>
              )}
              {g.protocol && <span className="rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground">{protocolLabel(g.protocol)}</span>}
              <span className="text-xs text-muted-foreground">{g.items.length}개</span>
              {missing.length > 0 && <span className="text-xs text-amber-600">(없는 엔드포인트 {missing.length})</span>}
            </div>
            {showBody && (
              <div className={cn("space-y-3 border-t px-3 py-3", !open && "hidden")}>
                {(g.status === "deprecated" || g.status === "retired") && (
                  <p className="flex items-center gap-1 text-xs text-amber-700">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {g.status === "retired" ? "폐기된 API 입니다." : "사용 중단된 API 입니다. 대체 API 를 확인하세요."}
                  </p>
                )}
                {missing.length > 0 && (
                  <div className="rounded-md border border-amber-300 bg-amber-50/50 p-2">
                    <p className="mb-1 text-xs font-medium text-amber-800">현재 엔드포인트 목록에 없는 즐겨찾기 — 새 스펙에서 제거되었거나 경로가 바뀌었습니다.</p>
                    <ul className="space-y-1">
                      {missing.map((f) => (
                        <li key={f.id} className="flex items-center gap-2 text-sm">
                          <MethodBadge method={f.method} />
                          <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground line-through" title={f.path}>{f.path}</span>
                          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => void unfavorite(f)}>
                            <X className="mr-1 h-3.5 w-3.5" /> 해제
                          </Button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {present.length > 0 && (
                  <FavoriteApiBody
                    apiName={g.apiName}
                    keys={keys}
                    onToggleFavorite={(method, path) => {
                      const f = byKey.get(favKey(method, path))
                      if (f) void unfavorite(f)
                    }}
                  />
                )}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
