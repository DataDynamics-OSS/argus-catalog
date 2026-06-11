"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
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
import { DataTablePagination, DataTableToolbar } from "@/components/data-table"
import { fetchDatasources } from "../api"
import { type DatasetSummary, type Datasource } from "../data/schema"
import {
  fetchTopology,
  type Topology,
  type TopologyOrganization,
} from "@/features/topology/api"
import { isDatasourceTypeImplemented } from "@/features/datasources/datasource-configs"
import { DatasetsCardView } from "./datasets-card-view"
import { ViewModeToggle } from "./view-mode-toggle"
import { datasetsColumns as columns } from "./datasets-columns"
import { DatasetsPrimaryButtons } from "./datasets-primary-buttons"
import { useDatasets } from "./datasets-provider"

type DatasetsTableProps = {
  data: DatasetSummary[]
  isLoading?: boolean
}

export function DatasetsTable({ data, isLoading }: DatasetsTableProps) {
  const { total, page, pageSize, setPage, setPageSize, searchDatasets, viewMode, setOpen, setCurrentRow } =
    useDatasets()

  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [datasources, setDatasources] = useState<Datasource[]>([])

  // Fetch datasources for filter options
  useEffect(() => {
    fetchDatasources().then(setDatasources).catch(() => {})
  }, [])

  // 필터 옵션은 "지원되는" 데이터 소스 type 만 노출. 등록은 되어 있지만 sync 어댑터가
  // 없는 종류(kafka/s3/snowflake 등) 까지 보여주면 사용자가 선택해도 데이터셋이
  // 실제로 들어오지 않으므로 혼란만 준다.
  const datasourceOptions = useMemo(
    () =>
      datasources
        .filter((p) => isDatasourceTypeImplemented(p.type))
        .map((p) => ({
          label: p.name,
          value: p.datasource_id,
        })),
    [datasources]
  )

  const statusOptions = [
    { label: "활성", value: "active" },
    { label: "비활성", value: "inactive" },
    { label: "사용 중단", value: "deprecated" },
  ]

  // 조직/시스템 필터 (토폴로지 기반)
  const [orgId, setOrgId] = useState("")
  const [systemId, setSystemId] = useState("")
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
    columns,
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

  // columnFilters + 조직/시스템 상태를 합쳐 검색 실행. org/system 은 셀렉트 변경 시
  // 비동기 state 갱신 전이므로 override 인자로 즉시 값을 받는다.
  const runSearch = useCallback(
    (nextOrg = orgId, nextSystem = systemId) => {
      const searchVal = columnFilters.find((f) => f.id === "name")?.value
      const datasourceVal = columnFilters.find((f) => f.id === "datasource_name")?.value
      const originVal = columnFilters.find((f) => f.id === "origin")?.value
      const statusVal = columnFilters.find((f) => f.id === "status")?.value

      const search = typeof searchVal === "string" ? searchVal : ""
      let datasource = ""
      if (Array.isArray(datasourceVal) && datasourceVal.length === 1) datasource = datasourceVal[0]
      let origin = ""
      if (Array.isArray(originVal) && originVal.length === 1) origin = originVal[0]
      let status = ""
      if (Array.isArray(statusVal) && statusVal.length === 1) status = statusVal[0]

      searchDatasets({
        search, datasource, origin, status, tag: "",
        orgId: nextOrg, systemId: nextSystem,
      })
    },
    [columnFilters, searchDatasets, orgId, systemId]
  )

  const handleSearch = useCallback(() => runSearch(), [runSearch])

  const handleClear = useCallback(() => {
    table.resetColumnFilters()
    table.setGlobalFilter("")
    setOrgId("")
    setSystemId("")
    searchDatasets({
      search: "", datasource: "", origin: "", status: "", tag: "",
      orgId: "", systemId: "",
    })
  }, [table, searchDatasets])

  return (
    <div className={cn("flex flex-1 flex-col gap-4")}>
      {/* 조직 · 시스템 필터 (토폴로지 기반) */}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={orgId || "all"}
          onValueChange={(v) => {
            const nv = v === "all" ? "" : v
            setOrgId(nv)
            runSearch(nv, systemId)
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
            const nv = v === "all" ? "" : v
            setSystemId(nv)
            runSearch(orgId, nv)
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

      <DataTableToolbar
        table={table}
        searchPlaceholder="데이터셋 검색..."
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
            데이터셋을 불러오는 중...
          </div>
        ) : (
          <DatasetsCardView
            rows={data}
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
                  <p className="text-muted-foreground">데이터셋을 불러오는 중...</p>
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
