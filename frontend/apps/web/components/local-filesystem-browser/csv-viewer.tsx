"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Papa from "papaparse"
import { Loader2, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

/** Encoding options: label is what the user sees, value is the TextDecoder label. */
const ENCODINGS: { label: string; value: string }[] = [
  { label: "UTF-8", value: "UTF-8" },
  { label: "EUC-KR", value: "EUC-KR" },
  { label: "MS949 (CP949)", value: "windows-949" },
  { label: "ISO-8859-1", value: "ISO-8859-1" },
  { label: "Shift_JIS", value: "Shift_JIS" },
  { label: "Windows-1252", value: "Windows-1252" },
  { label: "UTF-16LE", value: "UTF-16LE" },
  { label: "UTF-16BE", value: "UTF-16BE" },
]

const LINE_DELIMITERS: { label: string; value: string }[] = [
  { label: "\\n (LF)", value: "\n" },
  { label: "\\r\\n (CRLF)", value: "\r\n" },
]

const DELIMITER_PRESETS: { label: string; value: string }[] = [
  { label: "Comma (,)", value: "," },
  { label: "Tab (\\t)", value: "\t" },
  { label: "Semicolon (;)", value: ";" },
  { label: "Pipe (|)", value: "|" },
]

/** Sentinel values for the Delimiter select. */
const DELIM_ESCAPE = "__escape__"
const DELIM_CUSTOM = "__custom__"

/** Sentinel value used by the Select component to represent "no quote character". */
const NO_QUOTE = "__none__"

/**
 * Convert escape sequences in user input to actual characters.
 * Supports: \xHH, \uHHHH, \n, \r, \t, \0, \\
 */
function parseEscapeSequences(input: string): string {
  return input.replace(
    /\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})|\\([nrt0\\])/g,
    (_, hex2, hex4, simple) => {
      if (hex2) return String.fromCharCode(parseInt(hex2, 16))
      if (hex4) return String.fromCharCode(parseInt(hex4, 16))
      switch (simple) {
        case "n": return "\n"
        case "r": return "\r"
        case "t": return "\t"
        case "0": return "\0"
        case "\\": return "\\"
        default: return simple
      }
    },
  )
}

type CsvViewerProps = {
  /** Raw file URL to fetch the CSV/TSV data from. */
  url: string
  /** Default delimiter – "," for CSV, "\t" for TSV. */
  defaultDelimiter: string
}

export function CsvViewer({ url, defaultDelimiter }: CsvViewerProps) {
  const [encoding, setEncoding] = useState("UTF-8")
  const [lineDelimiter, setLineDelimiter] = useState("\n")
  const [delimiter, setDelimiter] = useState(defaultDelimiter)
  /** Tracks which special mode is active: null = preset, "escape" or "custom". */
  const [delimiterMode, setDelimiterMode] = useState<null | "escape" | "custom">(null)
  /** Raw text the user typed into the auxiliary input (escape sequences or custom char). */
  const [delimiterInput, setDelimiterInput] = useState("")

  const [hasHeader, setHasHeader] = useState(true)
  const [quoteChar, setQuoteChar] = useState(NO_QUOTE)
  const [customQuoteChar, setCustomQuoteChar] = useState("")
  const [isCustomQuote, setIsCustomQuote] = useState(false)

  const [rows, setRows] = useState<string[][]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** Resolve the effective single-char delimiter for PapaParse. */
  const effectiveDelimiter = useMemo(() => {
    if (delimiterMode === "escape") {
      const parsed = parseEscapeSequences(delimiterInput)
      return parsed.charAt(0) || delimiter
    }
    if (delimiterMode === "custom") {
      return delimiterInput.charAt(0) || delimiter
    }
    return delimiter
  }, [delimiterMode, delimiterInput, delimiter])

  /** Resolve the effective escape char (only when delimiter mode is "escape"). */
  const effectiveEscapeChar = useMemo(() => {
    if (delimiterMode === "escape" && delimiterInput) {
      return parseEscapeSequences(delimiterInput).charAt(0) || undefined
    }
    return undefined
  }, [delimiterMode, delimiterInput])

  const effectiveQuoteChar = useMemo(() => {
    if (isCustomQuote) return customQuoteChar || false
    if (quoteChar === NO_QUOTE) return false
    return quoteChar
  }, [isCustomQuote, customQuoteChar, quoteChar])

  const fetchAndParse = useCallback(
    async (enc: string, delim: string, escape: string | undefined, quote: string | false, lineDelim: string) => {
      setIsLoading(true)
      setError(null)
      setRows([])
      try {
        const res = await fetch(url)
        if (!res.ok) throw new Error(`Failed to fetch file (${res.status})`)
        const buf = await res.arrayBuffer()
        let decoded = new TextDecoder(enc).decode(buf)

        // Normalize line endings to the selected delimiter so PapaParse
        // strictly respects the user's choice.
        if (lineDelim === "\n") {
          // \r\n → \n, then stray \r → \n
          decoded = decoded.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
        } else {
          // lineDelim === "\r\n"
          // First normalize everything to \n, then convert to \r\n
          decoded = decoded.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n/g, "\r\n")
        }

        const parseConfig: Papa.ParseConfig = {
          delimiter: delim,
          newline: lineDelim as "\n" | "\r\n" | "\r",
          header: false,
          skipEmptyLines: true,
        }

        if (escape) {
          parseConfig.escapeChar = escape
        }

        if (quote !== false) {
          parseConfig.quoteChar = quote
        } else {
          parseConfig.quoteChar = "\0"
        }

        const result = Papa.parse<string[]>(decoded, parseConfig)
        if (result.errors.length > 0) {
          console.warn("CSV parse warnings:", result.errors)
        }
        setRows(result.data)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load file")
      } finally {
        setIsLoading(false)
      }
    },
    [url],
  )

  // Initial load
  useEffect(() => {
    fetchAndParse(encoding, effectiveDelimiter, effectiveEscapeChar, effectiveQuoteChar, lineDelimiter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  function handleRefresh() {
    fetchAndParse(encoding, effectiveDelimiter, effectiveEscapeChar, effectiveQuoteChar, lineDelimiter)
  }

  const displayRows = useMemo(() => {
    if (!rows.length) return { header: undefined, data: [], allRows: rows }
    if (hasHeader) {
      return { header: rows[0], data: rows.slice(1), allRows: rows }
    }
    return { header: undefined, data: rows, allRows: rows }
  }, [rows, hasHeader])

  const columnCount = useMemo(() => {
    if (!rows.length) return 0
    return Math.max(...rows.map((r) => r.length))
  }, [rows])

  /** The value shown in the Delimiter Select. */
  const delimiterSelectValue = useMemo(() => {
    if (delimiterMode === "escape") return DELIM_ESCAPE
    if (delimiterMode === "custom") return DELIM_CUSTOM
    return delimiter
  }, [delimiterMode, delimiter])

  return (
    <div className="flex flex-col gap-3 h-full text-sm">
      {/* Controls */}
      <div className="flex items-end gap-3 flex-wrap">
        <div className="space-y-1">
          <Label className="text-sm">Encoding</Label>
          <Select value={encoding} onValueChange={setEncoding}>
            <SelectTrigger className="w-[130px] h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ENCODINGS.map((enc) => (
                <SelectItem key={enc.value} value={enc.value} className="text-sm">
                  {enc.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">Line Delimiter</Label>
          <Select value={lineDelimiter} onValueChange={setLineDelimiter}>
            <SelectTrigger className="w-[130px] h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LINE_DELIMITERS.map((ld) => (
                <SelectItem key={ld.value} value={ld.value} className="text-sm">
                  {ld.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">Delimiter</Label>
          <div className="flex items-center gap-1.5">
            <Select
              value={delimiterSelectValue}
              onValueChange={(v) => {
                if (v === DELIM_ESCAPE) {
                  setDelimiterMode("escape")
                  setDelimiterInput("")
                } else if (v === DELIM_CUSTOM) {
                  setDelimiterMode("custom")
                  setDelimiterInput("")
                } else {
                  setDelimiterMode(null)
                  setDelimiter(v)
                }
              }}
            >
              <SelectTrigger className="w-[150px] h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DELIMITER_PRESETS.map((p) => (
                  <SelectItem key={p.value} value={p.value} className="text-sm">
                    {p.label}
                  </SelectItem>
                ))}
                <SelectItem value={DELIM_ESCAPE} className="text-sm">
                  Escape
                </SelectItem>
                <SelectItem value={DELIM_CUSTOM} className="text-sm">
                  Custom...
                </SelectItem>
              </SelectContent>
            </Select>

            {delimiterMode === "escape" && (
              <Input
                value={delimiterInput}
                onChange={(e) => setDelimiterInput(e.target.value)}
                placeholder="e.g. \x1b"
                className="w-[100px] h-8 text-sm font-[D2Coding,monospace]"
              />
            )}

            {delimiterMode === "custom" && (
              <Input
                value={delimiterInput}
                onChange={(e) => setDelimiterInput(e.target.value.slice(0, 1))}
                placeholder="e.g. :"
                className="w-[80px] h-8 text-sm font-[D2Coding,monospace]"
                maxLength={1}
              />
            )}
          </div>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">Header</Label>
          <Select value={hasHeader ? "yes" : "no"} onValueChange={(v) => setHasHeader(v === "yes")}>
            <SelectTrigger className="w-[120px] h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="yes" className="text-sm">With Header</SelectItem>
              <SelectItem value="no" className="text-sm">No Header</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">Quote</Label>
          <div className="flex items-center gap-1.5">
            <Select
              value={isCustomQuote ? "__custom__" : quoteChar}
              onValueChange={(v) => {
                if (v === "__custom__") {
                  setIsCustomQuote(true)
                } else {
                  setIsCustomQuote(false)
                  setQuoteChar(v)
                }
              }}
            >
              <SelectTrigger className="w-[120px] h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_QUOTE} className="text-sm">None</SelectItem>
                <SelectItem value={'"'} className="text-sm">Double (&quot;)</SelectItem>
                <SelectItem value="'" className="text-sm">Single (&apos;)</SelectItem>
                <SelectItem value="__custom__" className="text-sm">Custom...</SelectItem>
              </SelectContent>
            </Select>

            {isCustomQuote && (
              <Input
                value={customQuoteChar}
                onChange={(e) => setCustomQuoteChar(e.target.value.slice(0, 1))}
                placeholder="e.g. `"
                className="w-[80px] h-8 text-sm font-[D2Coding,monospace]"
                maxLength={1}
              />
            )}
          </div>
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={isLoading}
          className="h-8"
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Refresh
        </Button>

        {!isLoading && rows.length > 0 && (
          <span className="text-sm text-muted-foreground ml-auto">
            {displayRows.data.length} rows · {columnCount} columns
          </span>
        )}
      </div>

      {/* Table */}
      {isLoading && (
        <div className="flex items-center justify-center h-[400px]">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">{error}</p>
        </div>
      )}

      {!isLoading && !error && rows.length > 0 && (
        <div className="border rounded overflow-auto flex-1 min-h-0 max-h-[500px]">
          <table className="w-full text-sm font-[D2Coding,monospace] border-collapse">
            {displayRows.header && (
              <thead className="bg-muted/60 sticky top-0 z-10">
                <tr>
                  <th className="px-2 py-1.5 text-right text-muted-foreground border-r w-10 font-medium">
                    #
                  </th>
                  {displayRows.header.map((cell, i) => (
                    <th
                      key={i}
                      className="px-2 py-1.5 text-left font-semibold border-r last:border-r-0 whitespace-nowrap"
                    >
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody className="divide-y">
              {displayRows.data.map((row, ri) => (
                <tr key={ri} className="hover:bg-muted/30">
                  <td className="px-2 py-1 text-right text-muted-foreground border-r tabular-nums">
                    {ri + 1}
                  </td>
                  {Array.from({ length: columnCount }, (_, ci) => (
                    <td
                      key={ci}
                      className="px-2 py-1 border-r last:border-r-0 whitespace-nowrap max-w-[300px] truncate"
                      title={row[ci] ?? ""}
                    >
                      {row[ci] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">No data</p>
        </div>
      )}
    </div>
  )
}
