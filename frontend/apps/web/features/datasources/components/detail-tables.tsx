"use client"

import type { ReactNode } from "react"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@workspace/ui/components/table"
import type { TopologyDatasource } from "@/features/topology/api"
import { OriginText } from "@/features/datasets/components/origin-text"

// 키-값 정보 테이블
export function InfoTable({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.label}>
              <TableCell className="w-32 bg-muted/40 font-medium">
                {r.label}
              </TableCell>
              <TableCell>{r.value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// 데이터 소스 목록 테이블 — 이름을 누르면(키보드로도) 그 데이터 소스를 선택한다
export function DatasourcesTable({
  rows,
  onSelect,
}: {
  rows: TopologyDatasource[]
  onSelect: (id: number) => void
}) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead className="w-32 text-center">유형</TableHead>
            <TableHead className="w-36 text-center">환경</TableHead>
            <TableHead className="w-24 text-center">데이터셋</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="truncate">
                <button type="button" className="font-medium text-primary underline-offset-2 hover:underline"
                  title={p.name} onClick={() => onSelect(p.id)}>
                  {p.name}
                </button>
              </TableCell>
              <TableCell className="text-center font-mono text-xs">{p.type}</TableCell>
              <TableCell className="text-center"><OriginText origin={p.origin} /></TableCell>
              <TableCell className="text-center tabular-nums">{p.dataset_count.toLocaleString()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
