"use client"

import { useMemo } from "react"

type HexViewerProps = {
  /** Raw file content as ArrayBuffer. */
  data: ArrayBuffer
}

/** Number of bytes per row in the hex display. */
const BYTES_PER_ROW = 16

export function HexViewer({ data }: HexViewerProps) {
  const bytes = useMemo(() => new Uint8Array(data), [data])

  const rows = useMemo(() => {
    const result: { offset: number; hexParts: string[]; ascii: string }[] = []
    for (let i = 0; i < bytes.length; i += BYTES_PER_ROW) {
      const slice = bytes.slice(i, i + BYTES_PER_ROW)
      const hexParts: string[] = []
      let ascii = ""
      for (let j = 0; j < BYTES_PER_ROW; j++) {
        if (j < slice.length) {
          const byte = slice[j]!
          hexParts.push(byte.toString(16).padStart(2, "0").toUpperCase())
          // Printable ASCII range: 0x20–0x7E
          ascii += byte >= 0x20 && byte <= 0x7e ? String.fromCharCode(byte) : "."
        } else {
          hexParts.push("  ")
          ascii += " "
        }
      }
      result.push({ offset: i, hexParts, ascii })
    }
    return result
  }, [bytes])

  return (
    <div className="border rounded overflow-auto max-h-[500px] bg-background">
      <table className="w-full border-collapse text-sm font-[D2Coding,monospace] leading-5">
        <thead className="bg-muted/60 sticky top-0 z-10">
          <tr>
            <th className="px-3 py-1.5 text-left text-muted-foreground font-medium w-[80px]">
              Offset
            </th>
            <th className="px-3 py-1.5 text-left text-muted-foreground font-medium">
              <div className="flex">
                {Array.from({ length: BYTES_PER_ROW }, (_, i) => (
                  <span key={i} className="w-[26px] text-center shrink-0">
                    {i.toString(16).toUpperCase().padStart(2, "0")}
                  </span>
                ))}
              </div>
            </th>
            <th className="px-3 py-1.5 text-left text-muted-foreground font-medium border-l">
              ASCII
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {rows.map((row) => (
            <tr key={row.offset} className="hover:bg-muted/30">
              <td className="px-3 py-0.5 text-muted-foreground tabular-nums">
                {row.offset.toString(16).padStart(8, "0").toUpperCase()}
              </td>
              <td className="px-3 py-0.5">
                <div className="flex">
                  {row.hexParts.map((hex, i) => (
                    <span
                      key={i}
                      className={`w-[26px] text-center shrink-0 ${hex === "  " ? "" : "tabular-nums"}`}
                    >
                      {hex}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-3 py-0.5 border-l whitespace-pre text-muted-foreground">
                {row.ascii}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
