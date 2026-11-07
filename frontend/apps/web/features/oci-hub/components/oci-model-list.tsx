"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import {
  BookOpen, Box, ChevronDown, Code2, Grid3X3, Import, List, Loader2, Plus, RefreshCw, Search, X,
} from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { Input } from "@workspace/ui/components/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { useAuth } from "@/features/auth"

import {
  fetchOciFacets, fetchOciModels, OCI_STATUS_LABELS,
  type OciFacets, type OciModelSummary,
} from "@/features/oci-hub/api"

import {
  ALL, formatSize, Pager, SourceBadge, StatusBadge, timeAgo, versionLabel,
} from "./oci-common"
import { OciGuideDialog } from "./oci-guides"
import { OciImportDialog } from "./oci-import-dialog"
import { OciModelDialog } from "./oci-model-dialog"
import { errorMessage } from "@/lib/notify"
import { usePermissions } from "@/features/permissions/use-permissions"

const PAGE_SIZE = 12

export function ociDetailHref(name: string): string {
  return `/dashboard/oci-hub/${encodeURIComponent(name)}`
}

function ModelCard({ model }: { model: OciModelSummary }) {
  const kind = [model.task, model.framework].filter(Boolean).join(" · ")
  const tags = model.tags ?? []
  return (
    <Link href={ociDetailHref(model.name)} className="block">
      <Card className="h-full gap-0 py-0 transition-colors hover:border-primary/40 hover:bg-muted/30">
        <CardContent className="flex h-full flex-col gap-2 px-4 pt-3.5 pb-3">
          <div className="flex items-start gap-2">
            <Box className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <h3 className="truncate font-semibold leading-tight" title={model.display_name || model.name}>
                {model.display_name || model.name}
              </h3>
              <p className="truncate text-xs text-muted-foreground">{kind || model.name}</p>
            </div>
            <StatusBadge status={model.status} />
          </div>
          <p className="min-h-[2.5rem] text-sm text-muted-foreground line-clamp-2">
            {model.description || <span className="italic text-muted-foreground/60">설명이 없습니다.</span>}
          </p>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {tags.slice(0, 3).map((t) => (
                <Badge key={t.id} variant="outline" className="text-[11px]" style={{ borderColor: t.color, color: t.color }}>
                  {t.name}
                </Badge>
              ))}
              {tags.length > 3 && <span className="text-[11px] text-muted-foreground">+{tags.length - 3}</span>}
            </div>
          )}
          <div className="mt-auto flex items-center gap-2 border-t pt-2 text-xs text-muted-foreground">
            <SourceBadge sourceType={model.source_type} />
            <span className="truncate">
              {model.version_count > 0
                ? `v${model.version_count} · ${formatSize(model.total_size)} · 다운로드 ${model.download_count.toLocaleString()}회`
                : "버전 없음"}
            </span>
            <span className="ml-auto shrink-0">{timeAgo(model.updated_at)}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}

function FacetSelect({
  label, value, options, onChange, format,
}: {
  label: string
  value: string
  options: string[]
  onChange: (v: string) => void
  format?: (v: string) => string
}) {
  return (
    <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? "" : v)}>
      <SelectTrigger className="h-9 w-36" aria-label={label}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{label}: 전체</SelectItem>
        {options.map((o) => (
          <SelectItem key={o} value={o}>{format ? format(o) : o}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * OCI 모델 목록 — 검색·필터·페이지·보기 방식을 주소(?q=&task=&framework=&status=&page=&view=)에
 * 남겨 상세에서 돌아오거나 링크를 공유해도 같은 화면이 열린다.
 */
export function OciModelList() {
  const { user } = useAuth()
  const { canUseFeature } = usePermissions()
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const q = params.get("q") ?? ""
  const task = params.get("task") ?? ""
  const framework = params.get("framework") ?? ""
  const status = params.get("status") ?? ""
  const page = Math.max(1, Number(params.get("page")) || 1)
  const view = params.get("view") === "list" ? "list" : "card"

  const [searchInput, setSearchInput] = useState(q)
  const [models, setModels] = useState<OciModelSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [facets, setFacets] = useState<OciFacets>({ tasks: [], frameworks: [], languages: [], statuses: [] })
  const [reloadKey, setReloadKey] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [guide, setGuide] = useState<"import" | "use" | null>(null)

  const setParams = useCallback((patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString())
    next.set("tab", "models")
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    // 조건이 바뀌면 1페이지로
    if (!("page" in patch)) next.delete("page")
    router.replace(`${pathname}?${next}`, { scroll: false })
  }, [params, pathname, router])

  // 주소의 검색어가 바뀌면(뒤로 가기 등) 입력창도 맞춘다.
  useEffect(() => { setSearchInput(q) }, [q])

  // 입력 후 300ms 멈추면 검색 반영
  useEffect(() => {
    if (searchInput.trim() === q) return
    const id = window.setTimeout(() => setParams({ q: searchInput.trim() || null }), 300)
    return () => window.clearTimeout(id)
  }, [searchInput, q, setParams])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchOciModels({
      search: q || undefined, task: task || undefined, framework: framework || undefined,
      status: status || undefined, page, pageSize: PAGE_SIZE,
    })
      .then((d) => {
        if (cancelled) return
        setModels(d.items)
        setTotal(d.total)
      })
      .catch((e) => { if (!cancelled) setError(errorMessage(e) ?? "목록을 불러오지 못했습니다.") })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [q, task, framework, status, page, reloadKey])

  useEffect(() => {
    fetchOciFacets().then(setFacets).catch(() => { /* 필터 후보 없이도 목록은 동작 */ })
  }, [reloadKey])

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])
  const totalPages = Math.ceil(total / PAGE_SIZE)
  const filtered = !!(q || task || framework || status)

  // 마지막 페이지에서 항목이 사라졌을 때(삭제 등) 범위 밖 페이지 보정
  useEffect(() => {
    if (!loading && totalPages > 0 && page > totalPages) setParams({ page: String(totalPages) })
  }, [loading, page, totalPages, setParams])

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-56">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="이름·표시 이름·설명 검색"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") setParams({ q: searchInput.trim() || null }) }}
            className="pl-8 h-9"
            aria-label="모델 검색"
          />
        </div>
        <FacetSelect label="태스크" value={task} options={facets.tasks} onChange={(v) => setParams({ task: v || null })} />
        <FacetSelect label="프레임워크" value={framework} options={facets.frameworks} onChange={(v) => setParams({ framework: v || null })} />
        <FacetSelect
          label="상태" value={status} options={facets.statuses}
          onChange={(v) => setParams({ status: v || null })}
          format={(s) => OCI_STATUS_LABELS[s] ?? s}
        />
        {filtered && (
          <Button
            variant="ghost" size="sm"
            onClick={() => { setSearchInput(""); setParams({ q: null, task: null, framework: null, status: null }) }}
          >
            <X className="h-3.5 w-3.5 mr-1" />초기화
          </Button>
        )}
        <div className="flex items-center gap-2 ml-auto">
          <Button variant="ghost" size="icon-sm" onClick={reload} aria-label="새로고침" title="새로고침">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <div className="flex items-center border rounded-md">
            <Button
              variant={view === "card" ? "secondary" : "ghost"} size="icon-sm"
              onClick={() => setParams({ view: null, page: params.get("page") })} aria-label="카드 보기"
            >
              <Grid3X3 className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant={view === "list" ? "secondary" : "ghost"} size="icon-sm"
              onClick={() => setParams({ view: "list", page: params.get("page") })} aria-label="목록 보기"
            >
              <List className="h-3.5 w-3.5" />
            </Button>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <BookOpen className="h-4 w-4 mr-1.5" />가이드<ChevronDown className="h-3.5 w-3.5 ml-1" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setGuide("import")}>
                <Import className="h-4 w-4" />모델 올리기 (push·가져오기)
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setGuide("use")}>
                <Code2 className="h-4 w-4" />모델 내려받아 쓰기
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {user && (user.is_admin || canUseFeature("oci.register")) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm">
                  <Plus className="h-4 w-4 mr-1.5" />모델 추가<ChevronDown className="h-3.5 w-3.5 ml-1" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuItem onSelect={() => setCreateOpen(true)} className="flex-col items-start gap-0.5">
                  <span className="flex items-center gap-2 font-medium"><Plus className="h-4 w-4" />직접 등록</span>
                  <span className="pl-6 text-xs text-muted-foreground">빈 모델을 만들고 CLI·SDK 로 파일 push</span>
                </DropdownMenuItem>
                {user.is_admin && (
                  <DropdownMenuItem onSelect={() => setImportOpen(true)} className="flex-col items-start gap-0.5">
                    <span className="flex items-center gap-2 font-medium"><Import className="h-4 w-4" />HuggingFace 에서 가져오기</span>
                    <span className="pl-6 text-xs text-muted-foreground">서버가 백그라운드로 내려받음 (관리자)</span>
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      {!loading && !error && (
        <p className="text-sm text-muted-foreground">
          {filtered ? `검색 결과 ${total.toLocaleString()}개` : `전체 ${total.toLocaleString()}개`}
        </p>
      )}

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />모델을 불러오는 중…
        </div>
      ) : error ? (
        <div className="text-center py-12 space-y-3">
          <p className="text-sm text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={reload}>다시 시도</Button>
        </div>
      ) : models.length === 0 ? (
        <div className="text-center py-12 space-y-3 text-muted-foreground">
          {filtered ? (
            <>
              <p>조건에 맞는 모델이 없습니다.</p>
              <Button
                variant="outline" size="sm"
                onClick={() => { setSearchInput(""); setParams({ q: null, task: null, framework: null, status: null }) }}
              >
                조건 초기화
              </Button>
            </>
          ) : (
            <>
              <p>아직 모델이 없습니다.</p>
              <div className="flex justify-center gap-2">
                {user?.is_admin && (
                  <Button size="sm" onClick={() => setImportOpen(true)}>
                    <Import className="h-4 w-4 mr-1.5" />HuggingFace 에서 가져오기
                  </Button>
                )}
                {user && (
                  <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
                    <Plus className="h-4 w-4 mr-1.5" />모델 등록
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => setGuide("import")}>
                  <BookOpen className="h-4 w-4 mr-1.5" />CLI·SDK 로 push 하는 방법
                </Button>
              </div>
            </>
          )}
        </div>
      ) : view === "card" ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {models.map((m) => <ModelCard key={m.id} model={m} />)}
        </div>
      ) : (
        <div className="border rounded-md overflow-auto">
          <table className="w-full text-sm table-fixed">
            <thead className="bg-muted/60 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left font-medium w-[320px]">이름</th>
                <th className="px-3 py-2 text-left font-medium">설명</th>
                <th className="px-3 py-2 text-center font-medium w-[140px]">태스크</th>
                <th className="px-3 py-2 text-center font-medium w-[120px]">프레임워크</th>
                <th className="px-3 py-2 text-center font-medium w-20">상태</th>
                <th className="px-3 py-2 text-center font-medium w-24">크기</th>
                <th className="px-3 py-2 text-center font-medium w-16">버전</th>
                <th className="px-3 py-2 text-center font-medium w-24">출처</th>
                <th className="px-3 py-2 text-center font-medium w-24">수정</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {models.map((m) => (
                <tr
                  key={m.id}
                  className="hover:bg-muted/30 cursor-pointer"
                  onClick={() => router.push(ociDetailHref(m.name))}
                >
                  <td className="px-3 py-2 align-top">
                    <Link
                      href={ociDetailHref(m.name)}
                      className="flex items-center gap-2 hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Box className="h-4 w-4 text-primary shrink-0" />
                      <span className="font-medium truncate" title={m.display_name || m.name}>{m.display_name || m.name}</span>
                    </Link>
                  </td>
                  <td className="px-3 py-2 align-top text-muted-foreground whitespace-normal break-words">{m.description || "-"}</td>
                  <td className="px-3 py-2 align-top text-center">{m.task || "-"}</td>
                  <td className="px-3 py-2 align-top text-center">{m.framework || "-"}</td>
                  <td className="px-3 py-2 align-top text-center"><StatusBadge status={m.status} /></td>
                  <td className="px-3 py-2 align-top text-center">{formatSize(m.total_size)}</td>
                  <td className="px-3 py-2 align-top text-center">{versionLabel(m.version_count)}</td>
                  <td className="px-3 py-2 align-top text-center"><SourceBadge sourceType={m.source_type} /></td>
                  <td className="px-3 py-2 align-top text-center text-muted-foreground">{timeAgo(m.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pager page={page} totalPages={totalPages} onPage={(p) => setParams({ page: p > 1 ? String(p) : null })} />

      <OciModelDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        model={null}
        onSaved={(m) => router.push(ociDetailHref(m.name))}
      />
      <OciImportDialog open={importOpen} onOpenChange={setImportOpen} onChanged={reload} />
      <OciGuideDialog kind="import" open={guide === "import"} onOpenChange={(o) => setGuide(o ? "import" : null)} />
      <OciGuideDialog kind="use" open={guide === "use"} onOpenChange={(o) => setGuide(o ? "use" : null)} />
    </div>
  )
}
