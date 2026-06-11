"use client"

import { useEffect, useState } from "react"
import { Star } from "lucide-react"

import { fetchAllFavorites, fetchApiCredentials, fetchApiDetail, type ApiCredential, type ApiDetail, type ApiFavorite } from "../api"
import { EndpointsTab } from "./apis-detail"

// API 1개 분량 — 상세를 불러와 즐겨찾기된 엔드포인트만 펼침(문서/호출) 가능하게 표시.
function FavoriteApiSection({ apiName, displayName, refs }: {
  apiName: string; displayName: string | null; refs: { method: string; path: string }[]
}) {
  const [api, setApi] = useState<ApiDetail | null>(null)
  const [credentials, setCredentials] = useState<ApiCredential[]>([])

  useEffect(() => {
    fetchApiDetail(apiName).then(setApi).catch(() => {})
    fetchApiCredentials(apiName).then(setCredentials).catch(() => setCredentials([]))
  }, [apiName])

  const filter = new Set(refs.map((r) => `${r.method} ${r.path}`))

  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold">{displayName || apiName}</span>
        <span className="text-xs text-muted-foreground">{refs.length}개</span>
      </div>
      {!api ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : (
        <EndpointsTab api={api} credentials={credentials} endpointFilter={filter} hideHeader />
      )}
    </div>
  )
}

export function ApisFavorites() {
  const [favorites, setFavorites] = useState<ApiFavorite[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchAllFavorites().then(setFavorites).catch(() => {}).finally(() => setLoading(false))
  }, [])

  // API 별 그룹화
  const groups = new Map<string, { api_name: string; api_display_name: string | null; refs: { method: string; path: string }[] }>()
  for (const f of favorites) {
    const key = f.api_name ?? String(f.api_id)
    if (!groups.has(key)) groups.set(key, { api_name: f.api_name ?? "", api_display_name: f.api_display_name, refs: [] })
    groups.get(key)!.refs.push({ method: f.method, path: f.path })
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        자주 사용하는 엔드포인트를 API별로 모아 보는 화면입니다. API 상세의 엔드포인트 행 오른쪽 ★ 아이콘으로 추가하며, 각 항목을 펼쳐 문서 확인·호출(Try-it)을 이 화면에서 바로 할 수 있습니다.
      </p>
      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : favorites.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-md border border-dashed py-16 text-center">
          <Star className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">즐겨찾기한 엔드포인트가 없습니다.</p>
          <p className="text-xs text-muted-foreground">API 상세 → 엔드포인트 행 오른쪽의 ★ 아이콘을 눌러 추가하세요.</p>
        </div>
      ) : (
        [...groups.values()].map((g) => (
          <FavoriteApiSection key={g.api_name} apiName={g.api_name} displayName={g.api_display_name} refs={g.refs} />
        ))
      )}
    </div>
  )
}
