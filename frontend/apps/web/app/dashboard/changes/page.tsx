"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { AlertTriangle, Plus } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { DataTableColumnHeader, DataTablePagination } from "@/components/data-table"
import { DashboardHeader } from "@/components/dashboard-header"
import { type ChangeRequest, listChangeRequests, listMyInbox } from "@/features/change-mgmt/api"
import {
  CHANGE_TYPE_LABEL,
  PRIORITY_LABEL,
  StatusBadge,
  useUserNames,
} from "@/features/change-mgmt/labels"
import { useAuth } from "@/features/auth"

/** ISO 문자열 → yyyy-MM-dd (없으면 —). */
function fmtDate(s?: string | null): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return "—"
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 라벨에서 한글 부분만 추출 ("호환성 깨짐 (Breaking)" → "호환성 깨짐"). */
const short = (label: string) => label.split(" (")[0]

const PRIORITY_COLOR: Record<string, string> = {
  EMERGENCY: "text-red-600 font-medium",
  HIGH: "text-amber-600",
  NORMAL: "text-foreground",
  LOW: "text-muted-foreground",
}

/** 결재 진행도: 승인 단계/전체 단계 (결재선 없으면 —). */
function approvalProgress(cr: ChangeRequest): string {
  const total = cr.approval_steps.length
  if (total === 0) return "—"
  const approved = cr.approval_steps.filter((s) => s.decision === "APPROVED").length
  return `${approved}/${total}`
}

export default function ChangeRequestsPage() {
  const router = useRouter()
  const { user } = useAuth()
  const { userMap } = useUserNames()
  const [crs, setCrs] = useState<ChangeRequest[]>([])
  // 내 결재 대기 — 서버 인박스(현재 단계 결재자=나)를 단일 기준으로 사용한다
  const [inbox, setInbox] = useState<ChangeRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<"all" | "mine" | "approve">("all")
  const [sorting, setSorting] = useState<SortingState>([])

  const me = user?.username
  // 내가 생성한 요청
  const isMine = (cr: ChangeRequest) => !!me && cr.requested_by === me

  const mineCount = crs.filter(isMine).length
  const approveCount = inbox.length
  // visible 은 반드시 useMemo 로 참조를 안정화한다.
  // (filter 결과가 매 렌더 새 배열이면 react-table autoReset 으로 무한 루프 발생)
  const visible = useMemo(() => {
    if (tab === "mine") return crs.filter(isMine)
    if (tab === "approve") return inbox // 서버 인박스 결과를 그대로 사용
    return crs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, crs, inbox, me])

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // 전체 목록과 내 결재 인박스를 병렬 조회 (인박스는 best-effort)
      const [all, myInbox] = await Promise.all([
        listChangeRequests(),
        listMyInbox().catch(() => [] as ChangeRequest[]),
      ])
      setCrs(all)
      setInbox(myInbox)
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const columns = useMemo<ColumnDef<ChangeRequest>[]>(
    () => [
      {
        accessorKey: "cr_code",
        header: "코드",
        cell: ({ row }) => (
          <span className="font-mono text-xs text-muted-foreground">{row.original.cr_code}</span>
        ),
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "title",
        header: "제목",
        cell: ({ row }) => (
          <div className="max-w-[280px] truncate font-medium" title={row.original.title}>
            {row.original.title}
          </div>
        ),
      },
      {
        accessorKey: "status",
        header: "상태",
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "change_type",
        header: "유형",
        cell: ({ row }) => short(CHANGE_TYPE_LABEL[row.original.change_type]),
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "priority",
        header: "우선순위",
        cell: ({ row }) => (
          <span className={PRIORITY_COLOR[row.original.priority] ?? ""}>
            {short(PRIORITY_LABEL[row.original.priority])}
          </span>
        ),
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "requested_by",
        header: "요청자",
        cell: ({ row }) => userMap[row.original.requested_by] ?? row.original.requested_by,
        meta: { className: "whitespace-nowrap" },
      },
      {
        id: "approval",
        header: () => <div className="text-center">결재</div>,
        cell: ({ row }) => (
          <div className="text-center tabular-nums">
            {row.original.approval_steps.some((s) => s.decision === "REJECTED") ? (
              <span className="font-medium text-red-600">반려</span>
            ) : (
              approvalProgress(row.original)
            )}
          </div>
        ),
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "scheduled_at",
        header: ({ column }) => <DataTableColumnHeader column={column} title="적용 예정" />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDate(row.original.scheduled_at)}</span>
        ),
        meta: { className: "whitespace-nowrap" },
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => <DataTableColumnHeader column={column} title="생성일" />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDate(row.original.created_at)}</span>
        ),
        meta: { className: "whitespace-nowrap" },
      },
    ],
    [userMap],
  )

  const table = useReactTable({
    data: visible,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    autoResetPageIndex: false,
    initialState: { pagination: { pageSize: 20 } },
  })

  // 탭 전환 시 1페이지로 복귀. (table 은 매 렌더마다 새 인스턴스라 의존성에서 제외 — 포함 시 무한 루프)
  useEffect(() => {
    table.setPageIndex(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  return (
    <>
      <DashboardHeader title="변경 관리" />
      <div className="flex flex-1 flex-col gap-4 p-4">

      <div className="flex items-center justify-between gap-3">
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="all">전체 {crs.length}</TabsTrigger>
            <TabsTrigger value="mine">내 요청 {mineCount}</TabsTrigger>
            <TabsTrigger value="approve">내 결재 대기 {approveCount}</TabsTrigger>
          </TabsList>
        </Tabs>
        <Button size="sm" asChild>
          <Link href="/dashboard/changes/new">
            <Plus className="mr-1 h-4 w-4" /> 변경 요청 생성
          </Link>
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}

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
                      "bg-background",
                      (header.column.columnDef.meta as { className?: string })?.className,
                    )}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-24 text-center">
                  <p className="text-muted-foreground">불러오는 중...</p>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  role="link"
                  tabIndex={0}
                  onClick={() => router.push(`/dashboard/changes/${row.original.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") router.push(`/dashboard/changes/${row.original.id}`)
                  }}
                  className="cursor-pointer"
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={(cell.column.columnDef.meta as { className?: string })?.className}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-24 text-center">
                  <p className="text-muted-foreground">
                    {tab === "approve"
                      ? "결재할 변경 요청이 없습니다."
                      : tab === "mine"
                        ? "내가 생성한 변경 요청이 없습니다."
                        : "등록된 변경 요청이 없습니다."}
                  </p>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {!loading && visible.length > 0 && <DataTablePagination table={table} />}
      </div>
    </>
  )
}
