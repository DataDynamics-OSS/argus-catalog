"use client"

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useMemo, useState } from "react"
import { X } from "lucide-react"
import {
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"

import { cn } from "@workspace/ui/lib/utils"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Badge } from "@workspace/ui/components/badge"
import { DataTablePagination, DataTableToolbar } from "@/components/data-table"
import { fetchDatasources } from "../api"
import { type DatasetSummary, type Datasource, type GlossaryTerm, type Tag } from "../data/schema"
import { EMPTY_LIST_STATE } from "../links"
import { fetchTags } from "@/features/tags/api"
import { fetchGlossaryTerms } from "@/features/glossary/api"
import { fetchTaxonomyTree, type TreeCategory } from "@/features/taxonomy/api"
import {
  fetchTopology,
  type Topology,
  type TopologyOrganization,
} from "@/features/topology/api"
import { isDatasourceTypeImplemented, MANUAL_DATASOURCE_TYPE } from "@/features/datasources/datasource-configs"
import { DatasetsCardView } from "./datasets-card-view"
import { useCanDeleteDataset } from "../use-can-delete-dataset"
import { ViewModeToggle } from "./view-mode-toggle"
import { datasetsColumns as columns } from "./datasets-columns"
import { DatasetsPrimaryButtons } from "./datasets-primary-buttons"
import { type DatasetsFilters, useDatasets } from "./datasets-provider"

// 태그 필터용 숨은 열 — 툴바의 다중 선택 필터가 열 단위로 값을 들고 있어서 둔다(표에는 보이지 않음).
const TAG_FILTER_COLUMN: ColumnDef<DatasetSummary> = {
  id: "tags",
  enableHiding: false,
  enableSorting: false,
  header: () => null,
  cell: () => null,
}
const tableColumns = [...columns, TAG_FILTER_COLUMN]

/** 주소의 조건 → 툴바 입력값(검색어·다중 선택 필터). */
function toColumnFilters(f: DatasetsFilters): ColumnFiltersState {
  const out: ColumnFiltersState = []
  if (f.q) out.push({ id: "name", value: f.q })
  if (f.datasource.length) out.push({ id: "datasource_name", value: f.datasource })
  if (f.origin.length) out.push({ id: "origin", value: f.origin })
  if (f.status.length) out.push({ id: "status", value: f.status })
  if (f.tag.length) out.push({ id: "tags", value: f.tag })
  return out
}

/** 분류 트리에서 id 로 "체계 > 상위 > 분류" 경로를 찾는다. */
function findCategoryPath(nodes: TreeCategory[], id: number, prefix: string[]): string[] | null {
  for (const n of nodes) {
    const path = [...prefix, n.name]
    if (n.id === id) return path
    const found = findCategoryPath(n.children ?? [], id, path)
    if (found) return found
  }
  return null
}

type DatasetsTableProps = {
  data: DatasetSummary[]
  isLoading?: boolean
}

export function DatasetsTable({ data, isLoading }: DatasetsTableProps) {
  const { total, page, pageSize, setPage, setPageSize, filters, searchDatasets, viewMode, setOpen, setCurrentRow } =
    useDatasets()
  const canDeleteDataset = useCanDeleteDataset()

  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({ tags: false })
  // 툴바 입력값 — 주소의 조건으로 시작하고, 주소가 바뀌면(링크·뒤로 가기) 다시 맞춘다. 검색 버튼·Enter 로 주소에 반영.
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>(() => toColumnFilters(filters))
  useEffect(() => {
    setColumnFilters(toColumnFilters(filters))
  }, [filters])
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [tags, setTags] = useState<Tag[]>([])

  // 필터 선택지 — 데이터 소스·태그
  useEffect(() => {
    fetchDatasources({ includeManual: true }).then(setDatasources).catch(() => {})
    fetchTags().then(setTags).catch(() => {})
  }, [])

  const tagOptions = useMemo(
    () => tags.map((t) => ({ label: t.name, value: String(t.id) })),
    [tags]
  )

  // 용어·분류 조건은 툴바 필터가 아니라 링크(용어집·분류·데이터셋 상세)로 걸린다 — 이름을 찾아 칩으로 보여 준다.
  const [termName, setTermName] = useState<string | null>(null)
  useEffect(() => {
    if (!filters.term) return setTermName(null)
    let alive = true
    fetchGlossaryTerms()
      .then((terms: GlossaryTerm[]) => {
        if (alive) setTermName(terms.find((t) => String(t.id) === filters.term)?.name ?? `#${filters.term}`)
      })
      .catch(() => alive && setTermName(`#${filters.term}`))
    return () => {
      alive = false
    }
  }, [filters.term])

  const [categoryLabel, setCategoryLabel] = useState<string | null>(null)
  useEffect(() => {
    if (!filters.category) return setCategoryLabel(null)
    const fallback = `#${filters.category}`
    if (!filters.taxonomy) return setCategoryLabel(fallback)
    let alive = true
    fetchTaxonomyTree(Number(filters.taxonomy))
      .then((tree) => {
        const path = findCategoryPath(tree.categories ?? [], Number(filters.category), [tree.taxonomy.name])
        if (alive) setCategoryLabel(path ? path.join(" > ") : fallback)
      })
      .catch(() => alive && setCategoryLabel(fallback))
    return () => {
      alive = false
    }
  }, [filters.category, filters.taxonomy])

  // 필터 옵션은 "지원되는" 데이터 소스 type 만 노출. 등록은 되어 있지만 sync 어댑터가
  // 없는 종류(kafka/s3/snowflake 등) 까지 보여주면 사용자가 선택해도 데이터셋이
  // 실제로 들어오지 않으므로 혼란만 준다.
  const datasourceOptions = useMemo(
    () =>
      datasources
        // 수동 등록 데이터 소스(직접 등록한 카탈로그, TODO-CAT-001)는 수집하지 않지만 데이터셋이 있으므로 거를 수 있게 둔다
        .filter((p) => isDatasourceTypeImplemented(p.type) || p.type === MANUAL_DATASOURCE_TYPE)
        .map((p) => ({
          label: p.type === MANUAL_DATASOURCE_TYPE ? `${p.name} (수동 등록)` : p.name,
          value: p.datasource_id,
        })),
    [datasources]
  )

  const statusOptions = [
    { label: "활성", value: "active" },
    { label: "비활성", value: "inactive" },
    { label: "사용 중단", value: "deprecated" },
  ]

  // 조직/시스템 필터 (토폴로지 기반) — 고르면 바로 주소에 반영된다
  const orgId = filters.org
  const systemId = filters.system
  const [topology, setTopology] = useState<Topology | null>(null)
  useEffect(() => {
    fetchTopology().then(setTopology).catch(() => {})
  }, [])

  const orgOptions = useMemo(() => {
    const acc: { label: string; value: string }[] = []
    const walk = (orgs: TopologyOrganization[], depth: number) => {
      for (const o of orgs) {
        acc.push({ label: `${"  ".repeat(depth)}${o.name}`, value: String(o.id) })
        walk(o.children, depth + 1)
      }
    }
    if (topology) walk(topology.organizations, 0)
    return acc
  }, [topology])

  const systemOptions = useMemo(() => {
    const acc: { label: string; value: string }[] = []
    const walk = (orgs: TopologyOrganization[]) => {
      for (const o of orgs) {
        for (const s of o.systems) acc.push({ label: `${s.name} · ${o.name}`, value: String(s.id) })
        walk(o.children)
      }
    }
    if (topology) {
      walk(topology.organizations)
      for (const s of topology.unassigned.systems ?? []) {
        acc.push({ label: `${s.name} · 미분류`, value: String(s.id) })
      }
    }
    return acc
  }, [topology])

  const pageCount = useMemo(
    () => Math.ceil(total / pageSize),
    [total, pageSize]
  )

  const table = useReactTable({
    data,
    columns: tableColumns,
    pageCount,
    state: {
      sorting,
      pagination: { pageIndex: page - 1, pageSize },
      columnFilters,
      columnVisibility,
    },
    manualPagination: true,
    manualFiltering: true,
    onPaginationChange: (updater) => {
      const next =
        typeof updater === "function"
          ? updater({ pageIndex: page - 1, pageSize })
          : updater
      if (next.pageSize !== pageSize) {
        setPageSize(next.pageSize)
      } else {
        setPage(next.pageIndex + 1)
      }
    },
    onColumnFiltersChange: setColumnFilters,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  // 툴바 입력값 + 조직/시스템을 합쳐 주소에 반영(→ 다시 조회). 용어·분류 조건은 칩을 닫기 전까지 유지한다.
  // org/system 은 셀렉트 변경 직후 값을 인자로 받는다.
  const runSearch = useCallback(
    (overrides: Partial<DatasetsFilters> = {}) => {
      const value = (id: string) => columnFilters.find((f) => f.id === id)?.value
      const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : [])
      const q = value("name")
      searchDatasets({
        ...filters,
        q: typeof q === "string" ? q.trim() : "",
        datasource: list(value("datasource_name")),
        origin: list(value("origin")),
        status: list(value("status")),
        tag: list(value("tags")),
        ...overrides,
      })
    },
    [columnFilters, filters, searchDatasets]
  )

  const handleSearch = useCallback(() => runSearch(), [runSearch])

  const handleClear = useCallback(() => {
    table.resetColumnFilters()
    table.setGlobalFilter("")
    const { page: _p, size: _s, ...empty } = EMPTY_LIST_STATE
    searchDatasets(empty)
  }, [table, searchDatasets])

  return (
    <div className={cn("flex flex-1 flex-col gap-4")}>
      {/* 조직 · 시스템 필터 (토폴로지 기반) */}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={orgId || "all"}
          onValueChange={(v) => {
            runSearch({ org: v === "all" ? "" : v })
          }}
        >
          <SelectTrigger className="h-9 w-44 text-sm">
            <SelectValue placeholder="조직: 전체" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all" className="text-sm">조직: 전체</SelectItem>
            {orgOptions.map((o) => (
              <SelectItem key={o.value} value={o.value} className="text-sm whitespace-pre">
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={systemId || "all"}
          onValueChange={(v) => {
            runSearch({ system: v === "all" ? "" : v })
          }}
        >
          <SelectTrigger className="h-9 w-52 text-sm">
            <SelectValue placeholder="시스템: 전체" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all" className="text-sm">시스템: 전체</SelectItem>
            {systemOptions.map((s) => (
              <SelectItem key={s.value} value={s.value} className="text-sm">
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {(filters.term || filters.category) && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span >조건</span>
          {filters.term && (
            <Badge variant="secondary" className="gap-1 pr-1">
              용어: {termName ?? "…"}
              <Tip label="용어 조건 지우기"><button
                type="button"
                aria-label="용어 조건 지우기"
                className="rounded-sm p-0.5 hover:bg-muted-foreground/20"
                onClick={() => runSearch({ term: "" })}
              >
                <X className="h-3 w-3" />
              </button></Tip>
            </Badge>
          )}
          {filters.category && (
            <Badge variant="secondary" className="gap-1 pr-1">
              분류: {categoryLabel ?? "…"}
              <Tip label="분류 조건 지우기"><button
                type="button"
                aria-label="분류 조건 지우기"
                className="rounded-sm p-0.5 hover:bg-muted-foreground/20"
                onClick={() => runSearch({ category: "", taxonomy: "" })}
              >
                <X className="h-3 w-3" />
              </button></Tip>
            </Badge>
          )}
        </div>
      )}

      <DataTableToolbar
        table={table}
        searchPlaceholder="데이터셋 검색…"
        searchKey="name"
        filters={[
          {
            columnId: "datasource_name",
            title: "데이터 소스",
            options: datasourceOptions,
          },
          {
            columnId: "origin",
            title: "환경",
            options: [
              { label: "PROD", value: "PROD" },
              { label: "DEV", value: "DEV" },
              { label: "STAGING", value: "STAGING" },
            ],
          },
          {
            columnId: "status",
            title: "상태",
            options: statusOptions,
          },
          {
            columnId: "tags",
            title: "태그",
            options: tagOptions,
          },
        ]}
        onSearch={handleSearch}
        onClear={handleClear}
        extraActions={
          <>
            <ViewModeToggle />
            <DatasetsPrimaryButtons />
          </>
        }
      />

      {viewMode === "card" ? (
        isLoading ? (
          <div className="rounded-md border py-12 text-center text-sm text-muted-foreground">
            데이터셋을 불러오는 중…
          </div>
        ) : (
          <DatasetsCardView
            rows={data}
            canDelete={canDeleteDataset}
            onDelete={(row) => {
              setCurrentRow(row)
              setOpen("delete")
            }}
          />
        )
      ) : (
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="group/row">
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    colSpan={header.colSpan}
                    className={cn(
                      "bg-background group-hover/row:bg-muted",
                      (header.column.columnDef.meta as { className?: string })
                        ?.className
                    )}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  <p className="text-muted-foreground">데이터셋을 불러오는 중…</p>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} className="group/row">
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        "bg-background group-hover/row:bg-muted",
                        (cell.column.columnDef.meta as { className?: string })
                          ?.className
                      )}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  데이터셋이 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      )}

      <DataTablePagination table={table} className="mt-auto" />
    </div>
  )
}
