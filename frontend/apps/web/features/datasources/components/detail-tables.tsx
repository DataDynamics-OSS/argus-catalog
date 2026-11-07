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

// 키-값 정보 테이블
export function InfoTable({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.label}>
              <TableCell className="w-32 bg-muted/40 font-medium text-muted-foreground">
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

// 데이터 소스 목록 테이블 (행 클릭 시 해당 데이터 소스 선택)
export function DatasourcesTable({
  rows,
  onSelect,
}: {
  rows: TopologyDatasource[]
  onSelect: (id: number) => void
}) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead>유형</TableHead>
            <TableHead>환경</TableHead>
            <TableHead className="text-right">데이터셋</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => (
            <TableRow
              key={p.id}
              className="cursor-pointer"
              onClick={() => onSelect(p.id)}
            >
              <TableCell className="font-medium">{p.name}</TableCell>
              <TableCell className="text-muted-foreground">{p.type}</TableCell>
              <TableCell className="text-muted-foreground">{p.origin}</TableCell>
              <TableCell className="text-right tabular-nums">{p.dataset_count}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
