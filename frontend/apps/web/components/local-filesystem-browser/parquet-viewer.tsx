"use client"

type TablePreviewData = {
  format: string
  columns: string[]
  rows: unknown[][]
  total_rows: number
}

type ParquetViewerProps = {
  /** Server-side preview data. */
  data: TablePreviewData
}

export function ParquetViewer({ data }: ParquetViewerProps) {
  const { columns, rows, total_rows } = data

  const columnCount = columns.length

  return (
    <div className="flex flex-col gap-3 h-full text-sm">
      <div className="flex items-end gap-3">
        <span className="text-sm text-muted-foreground ml-auto">
          {rows.length === total_rows
            ? `${total_rows} rows`
            : `Showing ${rows.length} of ${total_rows} rows`}{" "}
          · {columnCount} columns
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
                {columns.map((col) => (
                  <th
                    key={col}
                    className="px-2 py-1.5 text-left font-semibold border-r last:border-r-0 whitespace-nowrap"
                  >
                    {col}
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
                  {columns.map((_, ci) => {
                    const val = row[ci]
                    const display = val === null || val === undefined ? "" : String(val)
                    return (
                      <td
                        key={ci}
                        className="px-2 py-1 border-r last:border-r-0 whitespace-nowrap max-w-[300px] truncate"
                        title={display}
                      >
                        {display}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">No data</p>
        </div>
      )}
    </div>
  )
}
