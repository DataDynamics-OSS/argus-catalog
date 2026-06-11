"use client"

import { useCallback, useState } from "react"
import { Loader2 } from "lucide-react"

import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

type TablePreviewData = {
  format: string
  columns: string[]
  rows: unknown[][]
  total_rows: number
  sheet_names: string[]
  active_sheet: string
}

type XlsxViewerProps = {
  /** Server-side preview data. */
  data: TablePreviewData
  /** Object key (needed for sheet switching). */
  entryKey: string
  /** Server-side preview function for sheet switching. */
  previewFile?: (
    key: string,
    options?: { sheet?: string; maxRows?: number },
  ) => Promise<unknown>
}

export function XlsxViewer({ data: initialData, entryKey, previewFile }: XlsxViewerProps) {
  const [data, setData] = useState<TablePreviewData>(initialData)
  const [isLoadingSheet, setIsLoadingSheet] = useState(false)

  const { columns, rows, sheet_names, active_sheet } = data
  const columnCount = columns.length

  const handleSheetChange = useCallback(
    async (sheetName: string) => {
      if (!previewFile || sheetName === active_sheet) return
      setIsLoadingSheet(true)
      try {
        const result = (await previewFile(entryKey, { sheet: sheetName })) as TablePreviewData
        setData(result)
      } catch (err) {
        console.error("Failed to switch sheet:", err)
      } finally {
        setIsLoadingSheet(false)
      }
    },
    [previewFile, entryKey, active_sheet],
  )

  return (
    <div className="flex flex-col gap-3 h-full text-sm">
      <div className="flex items-end gap-3">
        {sheet_names.length > 1 && (
          <div className="space-y-1">
            <Label className="text-sm">시트</Label>
            <Select value={active_sheet} onValueChange={handleSheetChange}>
              <SelectTrigger className="w-[200px] h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sheet_names.map((name) => (
                  <SelectItem key={name} value={name} className="text-sm">
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <span className="text-sm text-muted-foreground ml-auto">
          {isLoadingSheet && <Loader2 className="inline h-3.5 w-3.5 animate-spin mr-1.5" />}
          {rows.length}행 · {columnCount}열
          {sheet_names.length > 1 && ` · 시트 ${sheet_names.length}개`}
        </span>
      </div>

      {rows.length > 0 ? (
        <div className="border rounded overflow-auto flex-1 min-h-0 max-h-[500px]">
          <table className="w-full text-sm font-[D2Coding,monospace] border-collapse">
            <thead className="bg-muted/60 sticky top-0 z-10">
              <tr>
                <th className="px-2 py-1.5 text-right text-muted-foreground border-r w-10 font-medium">
                  #
                </th>
                {columns.map((col, i) => (
                  <th
                    key={i}
                    className="px-2 py-1.5 text-left font-semibold border-r last:border-r-0 whitespace-nowrap"
                  >
                    {String(col)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-muted/30">
                  <td className="px-2 py-1 text-right text-muted-foreground border-r tabular-nums">
                    {ri + 1}
                  </td>
                  {Array.from({ length: columnCount }, (_, ci) => (
                    <td
                      key={ci}
                      className="px-2 py-1 border-r last:border-r-0 whitespace-nowrap max-w-[300px] truncate"
                      title={String(row[ci] ?? "")}
                    >
                      {String(row[ci] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">No data in this sheet</p>
        </div>
      )}
    </div>
  )
}
