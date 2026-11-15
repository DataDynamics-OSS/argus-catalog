"use client"

// 데이터셋 상세 › 메타데이터 탭의 "분류 체계" 항목 — 매핑된 분류를 보여 주고, 그 자리에서 분류를 붙이거나 뗀다(TODO-075).
// 예전에는 분류 체계 메뉴에서 데이터셋을 다시 검색해야만 매핑할 수 있었다.
// 권한은 데이터셋 분류 매핑 taxonomies.map(서버가 강제, TODO-138) — 데이터셋 편집 권한 · 분류 정의 편집(taxonomies.edit)과 다르다.

import { Tip } from "@/components/tip"
import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@workspace/ui/components/command"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { notifyError } from "@/lib/notify"
import {
  type DatasetCategoryRef,
  type Taxonomy,
  type TreeCategory,
  addDatasetCategory,
  fetchTaxonomies,
  fetchTaxonomyTree,
  removeDatasetCategory,
} from "@/features/taxonomy/api"
import { datasetListHref } from "../links"

type FlatCategory = { id: number; name: string; path: string; depth: number }

function flatten(nodes: TreeCategory[], prefix: string[], depth: number, out: FlatCategory[]): FlatCategory[] {
  for (const n of nodes) {
    const path = [...prefix, n.name]
    out.push({ id: n.id, name: n.name, path: path.join(" > "), depth })
    flatten(n.children ?? [], path, depth + 1, out)
  }
  return out
}

export function DatasetCategoryField({
  datasetId,
  categories,
  canEdit,
  onChanged,
}: {
  datasetId: number
  categories: DatasetCategoryRef[]
  canEdit: boolean
  /** 매핑을 바꾼 뒤 목록을 다시 읽게 한다 */
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [taxonomies, setTaxonomies] = useState<Taxonomy[]>([])
  const [taxonomyId, setTaxonomyId] = useState<string>("")
  const [tree, setTree] = useState<TreeCategory[]>([])
  const [busy, setBusy] = useState(false)

  // 열 때마다 분류 체계를 다시 읽는다 — 다른 탭에서 분류를 만든 뒤 돌아와도 보이게
  useEffect(() => {
    if (!open) return
    fetchTaxonomies()
      .then((list) => {
        setTaxonomies(list)
        setTaxonomyId((cur) => (cur && list.some((t) => String(t.id) === cur) ? cur : list[0] ? String(list[0].id) : ""))
      })
      .catch((err) => notifyError(err, "분류 체계를 불러오지 못했습니다."))
  }, [open])

  useEffect(() => {
    if (!open || !taxonomyId) return setTree([])
    fetchTaxonomyTree(Number(taxonomyId))
      .then((t) => setTree(t.categories ?? []))
      .catch((err) => notifyError(err, "분류를 불러오지 못했습니다."))
  }, [open, taxonomyId])

  const mapped = useMemo(() => new Set(categories.map((c) => c.category_id)), [categories])
  const options = useMemo(() => flatten(tree, [], 0, []), [tree])

  const add = async (categoryId: number) => {
    setBusy(true)
    try {
      await addDatasetCategory(datasetId, categoryId)
      setOpen(false)
      onChanged()
    } catch (err) {
      notifyError(err, "분류를 붙이지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  const remove = async (categoryId: number) => {
    setBusy(true)
    try {
      await removeDatasetCategory(datasetId, categoryId)
      onChanged()
    } catch (err) {
      notifyError(err, "분류를 떼지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {categories.length > 0 ? (
          categories.map((c) => (
            <Badge key={c.category_id} variant="secondary" className="gap-1">
              <Link
                href={datasetListHref({ category: String(c.category_id), taxonomy: String(c.taxonomy_id) })}
                className="hover:underline"
                title="이 분류의 데이터셋 보기"
              >
                {c.path}
              </Link>
              {canEdit && (
                <Tip label="분류 떼기"><button
                  className="ml-0.5 hover:opacity-70"
                  onClick={() => remove(c.category_id)}
                  disabled={busy}
                  aria-label="분류 떼기"
                >
                  <X className="h-3 w-3" />
                </button></Tip>
              )}
            </Badge>
          ))
        ) : (
          <span className="text-xs text-muted-foreground">없음</span>
        )}
      </div>
      {canEdit && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button size="sm" variant="outline" className="h-7 w-[45px] px-0" disabled={busy}>
              추가
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-[360px] p-0" align="start">
            <div className="border-b p-2">
              <Select value={taxonomyId} onValueChange={setTaxonomyId}>
                <SelectTrigger size="sm" className="h-8 w-full text-xs">
                  <SelectValue placeholder="분류 체계 선택" />
                </SelectTrigger>
                <SelectContent>
                  {taxonomies.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)} className="text-xs">
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Command>
              <CommandInput placeholder="분류 검색…" />
              <CommandList>
                <CommandEmpty>
                  {taxonomies.length === 0 ? "분류 체계가 없습니다" : "분류가 없습니다"}
                </CommandEmpty>
                <CommandGroup>
                  {options.map((o) => {
                    const already = mapped.has(o.id)
                    return (
                      <CommandItem
                        key={o.id}
                        value={o.path}
                        disabled={already || busy}
                        onSelect={() => !already && add(o.id)}
                        title={o.path}
                      >
                        <span style={{ paddingLeft: `${o.depth * 12}px` }} className="truncate">
                          {o.name}
                        </span>
                        {already && <span className="ml-auto text-[11px]">붙어 있음</span>}
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              </CommandList>
            </Command>
            <div className="border-t px-3 py-2 text-xs">
              원하는 분류가 없으면{" "}
              <a href="/dashboard/taxonomies" target="_blank" rel="noreferrer" className="text-primary hover:underline">
                분류 체계 화면에서 만들기 ↗
              </a>
            </div>
          </PopoverContent>
        </Popover>
      )}
    </div>
  )
}
