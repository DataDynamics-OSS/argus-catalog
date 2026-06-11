"use client"

import { useMemo } from "react"
import { AgGridReact } from "ag-grid-react"
import { AllCommunityModule, ModuleRegistry, type ColDef } from "ag-grid-community"

ModuleRegistry.registerModules([AllCommunityModule])

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type SampleGridProps = {
  columns: string[]
  rows: (string | null)[][]
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SampleGrid({ columns, rows }: SampleGridProps) {
  const columnDefs = useMemo<ColDef[]>(() => {
    const defs: ColDef[] = [
      {
        headerName: "#",
        valueGetter: (params) => (params.node?.rowIndex ?? 0) + 1,
        width: 60,
        minWidth: 50,
        maxWidth: 80,
        sortable: false,
        resizable: false,
        cellStyle: { color: "#9ca3af", textAlign: "right" },
        suppressMovable: true,
        pinned: "left",
      },
    ]

    columns.forEach((colName, ci) => {
      defs.push({
        headerName: colName,
        valueGetter: (params) => params.data?.[ci] ?? "",
        minWidth: 80,
        resizable: true,
        sortable: true,
        filter: true,
      })
    })

    return defs
  }, [columns])

  const rowData = useMemo(() => rows, [rows])

  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-[200px]">
        <p className="text-sm text-muted-foreground">No data</p>
      </div>
    )
  }

  return (
    <div
      className="border rounded ag-theme-alpine"
      style={{
        height: Math.min(rows.length * 32 + 48, 500),
        "--ag-font-family": "var(--font-d2coding), 'D2Coding', Consolas, monospace",
        "--ag-font-size": "13px",
      } as React.CSSProperties}
    >
      <AgGridReact
        columnDefs={columnDefs}
        rowData={rowData}
        defaultColDef={{
          resizable: true,
          sortable: false,
          filter: false,
          minWidth: 60,
        }}
        // 모든 컬럼을 셀 내용 폭에 맞게 자동 조정 (행 선택/체크박스는 사용 안 함).
        // autoSizeStrategy 는 grid 생성 시 1회만 적용되어 탭 전환/지연 렌더에서
        // 빗나가므로, 데이터가 실제로 그려진 시점의 이벤트에서 직접 호출한다.
        onFirstDataRendered={(e) => e.api.autoSizeAllColumns()}
        onRowDataUpdated={(e) => e.api.autoSizeAllColumns()}
        headerHeight={32}
        rowHeight={28}
        suppressCellFocus
        animateRows={false}
      />
    </div>
  )
}
