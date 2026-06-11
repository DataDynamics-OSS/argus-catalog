"use client"

// Dataset 상세 페이지의 "샘플" 탭 — 서버에 저장된 샘플 데이터를 Parquet 미리보기 또는 CSV
// 인코딩/구분자 설정으로 표시하고, CSV 업로드 → Parquet 변환 파이프라인을 관리한다.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Papa from "papaparse"
import { Loader2, RefreshCw, Save, Trash2, Upload } from "lucide-react"
import { toast } from "sonner"

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
import { Card, CardContent } from "@workspace/ui/components/card"
import {
  convertSampleToParquet,
  deleteSampleData,
  fetchDelimiterConfig,
  fetchSampleData,
  saveDelimiterConfig,
  uploadSampleData,
} from "@/features/datasets/api"
import { SampleGrid } from "./sample-grid"

// ---------------------------------------------------------------------------
// Constants (matches argus-insight-ui csv-viewer)
// ---------------------------------------------------------------------------

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

const MAX_FILE_SIZE = 100 * 1024 // 100 KB

const DELIM_ESCAPE = "__escape__"
const DELIM_CUSTOM = "__custom__"
const NO_QUOTE = "__none__"

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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type SampleDataTabProps = {
  datasetId: number
  /** When true (synced dataset), the Delete button is hidden to prevent
   *  accidental removal of sync-managed sample data. */
  isSynced?: boolean
  isAdmin?: boolean
}

export function SampleDataTab({ datasetId, isSynced = false, isAdmin = false }: SampleDataTabProps) {
  // Format: "parquet" when served from synced parquet, "csv" for uploaded CSV
  const [sampleFormat, setSampleFormat] = useState<"parquet" | "csv" | null>(null)

  // Parquet data (returned as JSON from server)
  const [parquetColumns, setParquetColumns] = useState<string[]>([])
  const [parquetRows, setParquetRows] = useState<(string | null)[][]>([])

  // CSV parse settings
  const [encoding, setEncoding] = useState("UTF-8")
  const [lineDelimiter, setLineDelimiter] = useState("\n")
  const [delimiter, setDelimiter] = useState(",")
  const [delimiterMode, setDelimiterMode] = useState<null | "escape" | "custom">(null)
  const [delimiterInput, setDelimiterInput] = useState("")
  const [hasHeader, setHasHeader] = useState(true)
  const [quoteChar, setQuoteChar] = useState(NO_QUOTE)
  const [customQuoteChar, setCustomQuoteChar] = useState("")
  const [isCustomQuote, setIsCustomQuote] = useState(false)

  // CSV data state
  const [rows, setRows] = useState<string[][]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasSample, setHasSample] = useState<boolean | null>(null)

  // Upload state
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Derived delimiter values
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

  const delimiterSelectValue = useMemo(() => {
    if (delimiterMode === "escape") return DELIM_ESCAPE
    if (delimiterMode === "custom") return DELIM_CUSTOM
    return delimiter
  }, [delimiterMode, delimiter])

  // ---------------------------------------------------------------------------
  // Parse raw CSV text with current settings
  // ---------------------------------------------------------------------------
  // 동일한 ArrayBuffer 를 인코딩/구분자/인용 부호 등 다양한 조합으로 다시 파싱하기 위한 함수.
  // useCallback 으로 안정화하여 설정 변경 시 effect 의존성에서 무한 루프를 피한다.
  const parseCsv = useCallback(
    (buf: ArrayBuffer, enc: string, delim: string, escape: string | undefined, quote: string | false, lineDelim: string) => {
      try {
        let decoded = new TextDecoder(enc).decode(buf)

        if (lineDelim === "\n") {
          decoded = decoded.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
        } else {
          decoded = decoded.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n/g, "\r\n")
        }

        const parseConfig: Papa.ParseConfig = {
          delimiter: delim,
          newline: lineDelim as "\n" | "\r\n" | "\r" | undefined,
          header: false,
          skipEmptyLines: true,
        }
        if (escape) parseConfig.escapeChar = escape
        if (quote !== false) {
          parseConfig.quoteChar = quote
        } else {
          // 인용 부호 없음 — 절대 매칭되지 않는 NULL 문자로 설정해 PapaParse 의 quote 처리 비활성화.
          parseConfig.quoteChar = "\0"
        }

        const result = Papa.parse<string[]>(decoded, parseConfig)
        if (result.errors.length > 0) {
          console.warn("CSV parse warnings", { errors: result.errors })
        }
        setRows(result.data)
        setError(null)
      } catch (err) {
        console.error("Failed to parse CSV", { encoding: enc, delimiter: delim, err })
        setError("Unable to display sample data. The file may be corrupted or use an unsupported format.")
        setRows([])
      }
    },
    [],
  )

  // Keep raw buffer for re-parsing with different settings
  const rawBufferRef = useRef<ArrayBuffer | null>(null)

  // ---------------------------------------------------------------------------
  // Fetch sample from server
  // ---------------------------------------------------------------------------
  // Content-Type 분기:
  //   - application/json + format=parquet → 변환된 Parquet 미리보기를 표 형태로 렌더
  //   - 그 외(기본 text/csv) → rawBufferRef 에 보관 후 현재 설정으로 다시 파싱
  // 의존성에는 datasetId/parseCsv 만 포함시켜 인코딩 등 설정 변경 시 서버 재호출을 막는다.
  const loadSample = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    setRows([])
    setParquetColumns([])
    setParquetRows([])
    setSampleFormat(null)
    try {
      const res = await fetchSampleData(datasetId)
      if (res.status === 404) {
        setHasSample(false)
        rawBufferRef.current = null
        return
      }
      if (!res.ok) throw new Error(`Failed to fetch sample (${res.status})`)

      const contentType = res.headers.get("content-type") || ""

      if (contentType.includes("application/json")) {
        // Parquet 응답은 JSON 으로 columns/rows 가 직렬화되어 도착.
        const data = await res.json()
        if (data.format === "parquet") {
          setSampleFormat("parquet")
          setParquetColumns(data.columns || [])
          setParquetRows(data.rows || [])
          setHasSample(true)
          return
        }
      }

      // CSV 응답
      setSampleFormat("csv")
      const buf = await res.arrayBuffer()
      rawBufferRef.current = buf
      setHasSample(true)
      parseCsv(buf, encoding, effectiveDelimiter, effectiveEscapeChar, effectiveQuoteChar, lineDelimiter)
    } catch (err) {
      console.error("Failed to fetch sample data", { datasetId, err })
      setError(err instanceof Error ? err.message : "샘플 데이터를 불러오지 못했습니다")
      setHasSample(false)
    } finally {
      setIsLoading(false)
    }
    // Only re-fetch from server on mount / datasetId change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId, parseCsv])

  useEffect(() => {
    loadSample()
  }, [loadSample])

  // ---------------------------------------------------------------------------
  // Load delimiter config from server (if exists) and re-parse (CSV only)
  // ---------------------------------------------------------------------------
  // 저장해 둔 인코딩/구분자 설정이 있으면 적용하고 같은 버퍼를 다시 파싱. cancelled 플래그는
  // datasetId 가 빠르게 바뀌는 경우 이전 응답이 새 state 를 덮어쓰지 못하게 하는 가드.
  useEffect(() => {
    if (sampleFormat !== "csv") return
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetchDelimiterConfig(datasetId)
        if (!res.ok || cancelled) return
        const cfg = await res.json()
        const enc = cfg.encoding ?? "UTF-8"
        const lineDel = cfg.line_delimiter ?? "\n"
        const del = cfg.delimiter ?? ","
        const delMode: "escape" | "custom" | null = cfg.delimiter_mode ?? null
        const delInput = cfg.delimiter_input ?? ""
        const header = cfg.has_header ?? true
        const qChar = cfg.quote_char ?? NO_QUOTE
        const cqChar = cfg.custom_quote_char ?? ""
        const isCQ = cfg.is_custom_quote ?? false

        setEncoding(enc)
        setLineDelimiter(lineDel)
        setDelimiter(del)
        setDelimiterMode(delMode)
        setDelimiterInput(delInput)
        setHasHeader(header)
        setQuoteChar(qChar)
        setCustomQuoteChar(cqChar)
        setIsCustomQuote(isCQ)

        // Re-parse with loaded config if buffer is available
        if (rawBufferRef.current) {
          let effDelim = del
          if (delMode === "escape") {
            const parsed = parseEscapeSequences(delInput)
            effDelim = parsed.charAt(0) || del
          } else if (delMode === "custom") {
            effDelim = delInput.charAt(0) || del
          }
          const effEscape = delMode === "escape" && delInput
            ? parseEscapeSequences(delInput).charAt(0) || undefined
            : undefined
          let effQuote: string | false
          if (isCQ) effQuote = cqChar || false
          else if (qChar === NO_QUOTE) effQuote = false
          else effQuote = qChar

          parseCsv(rawBufferRef.current, enc, effDelim, effEscape, effQuote, lineDel)
        }
      } catch (err) {
        // 저장된 설정이 없을 수 있어 silent fallback 이 정상 흐름 — 디버깅용 trace 로만 남김.
        console.warn("Delimiter config not loaded, using defaults", { datasetId, err })
      }
    })()
    return () => { cancelled = true }
  }, [datasetId, sampleFormat, parseCsv])

  // ---------------------------------------------------------------------------
  // Save: CSV → convert to parquet on server
  // ---------------------------------------------------------------------------
  // 1) 현재 화면 설정을 서버에 저장 → 2) Parquet 변환 트리거 → 3) 변환 결과를 다시 로드.
  // 변환 후에는 sampleFormat 이 "parquet" 으로 바뀌어 CSV 툴바가 사라진다.
  const handleSave = async () => {
    setSaving(true)
    try {
      await saveDelimiterConfig(datasetId, {
        encoding,
        line_delimiter: lineDelimiter,
        delimiter,
        delimiter_mode: delimiterMode,
        delimiter_input: delimiterInput,
        has_header: hasHeader,
        quote_char: quoteChar,
        custom_quote_char: customQuoteChar,
        is_custom_quote: isCustomQuote,
      })
      await convertSampleToParquet(datasetId)
      console.info("Sample data converted to parquet", { datasetId })
      toast.success("샘플 데이터를 Parquet 형식으로 변환했습니다.")
      await loadSample()
    } catch (err) {
      console.error("Failed to save sample/convert parquet", { datasetId, err })
      toast.error("샘플 데이터의 Parquet 변환에 실패했습니다.")
    } finally {
      setSaving(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Re-parse with current settings (no server call, CSV only)
  // ---------------------------------------------------------------------------
  // 서버 호출 없이 현재 인코딩/구분자 조합으로만 다시 파싱. 버퍼가 비어 있으면 전체 reload 로 fallback.
  const handleRefresh = () => {
    if (rawBufferRef.current) {
      parseCsv(rawBufferRef.current, encoding, effectiveDelimiter, effectiveEscapeChar, effectiveQuoteChar, lineDelimiter)
    } else {
      loadSample()
    }
  }

  // ---------------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------------
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // 동일 파일 재선택을 위해 input value 를 비움 (change 이벤트는 값 변화로만 트리거됨).
    e.target.value = ""

    if (file.size > MAX_FILE_SIZE) {
      setUploadError(`File size (${(file.size / 1024).toFixed(1)} KB) exceeds the 100 KB limit.`)
      return
    }

    setUploading(true)
    setUploadError(null)
    try {
      await uploadSampleData(datasetId, file)
      console.info("Sample data uploaded", { datasetId, sizeKb: (file.size / 1024).toFixed(1) })
      await loadSample()
    } catch (err) {
      console.error("Failed to upload sample data", { datasetId, err })
      setUploadError(err instanceof Error ? err.message : "업로드에 실패했습니다")
    } finally {
      setUploading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Delete
  // ---------------------------------------------------------------------------
  const handleDelete = async () => {
    try {
      await deleteSampleData(datasetId)
      console.info("Sample data deleted", { datasetId })
      setHasSample(false)
      setRows([])
      setParquetColumns([])
      setParquetRows([])
      rawBufferRef.current = null
      setSampleFormat(null)
      setError(null)
    } catch (err) {
      console.error("Failed to delete sample data", { datasetId, err })
    }
  }

  // ---------------------------------------------------------------------------
  // Display helpers
  // ---------------------------------------------------------------------------
  const csvDisplay = useMemo(() => {
    if (!rows.length) return { header: undefined, data: [], columnCount: 0 }
    const columnCount = Math.max(...rows.map((r) => r.length))
    if (hasHeader) {
      return { header: rows[0], data: rows.slice(1), columnCount }
    }
    return { header: undefined, data: rows, columnCount }
  }, [rows, hasHeader])

  // ---------------------------------------------------------------------------
  // Render: Parquet table (no toolbar)
  // ---------------------------------------------------------------------------
  const renderParquetTable = () => (
    <div className="flex flex-col gap-3 p-4 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground font-[family-name:var(--font-d2coding)]">
          Parquet · {parquetRows.length} rows · {parquetColumns.length} columns
        </span>
      </div>
      <SampleGrid columns={parquetColumns} rows={parquetRows} />
    </div>
  )

  // ---------------------------------------------------------------------------
  // Render: CSV view (with toolbar)
  // ---------------------------------------------------------------------------
  const renderCsvView = () => (
    <div className="flex flex-col gap-3 p-4 text-sm">
      {/* CSV Parse Controls */}
      <div className="flex items-end gap-3 flex-wrap">
        <div className="space-y-1">
          <Label className="text-sm">인코딩</Label>
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
          <Label className="text-sm">줄 구분자</Label>
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
          <Label className="text-sm">필드 구분자</Label>
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
                  이스케이프
                </SelectItem>
                <SelectItem value={DELIM_CUSTOM} className="text-sm">
                  직접 입력...
                </SelectItem>
              </SelectContent>
            </Select>

            {delimiterMode === "escape" && (
              <Input
                value={delimiterInput}
                onChange={(e) => setDelimiterInput(e.target.value)}
                placeholder="e.g. \x1b"
                className="w-[100px] h-8 text-sm font-mono"
              />
            )}

            {delimiterMode === "custom" && (
              <Input
                value={delimiterInput}
                onChange={(e) => setDelimiterInput(e.target.value.slice(0, 1))}
                placeholder="e.g. :"
                className="w-[80px] h-8 text-sm font-mono"
                maxLength={1}
              />
            )}
          </div>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">헤더</Label>
          <Select value={hasHeader ? "yes" : "no"} onValueChange={(v) => setHasHeader(v === "yes")}>
            <SelectTrigger className="w-[120px] h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="yes" className="text-sm">헤더 있음</SelectItem>
              <SelectItem value="no" className="text-sm">헤더 없음</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label className="text-sm">인용 부호</Label>
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
                <SelectItem value={NO_QUOTE} className="text-sm">없음</SelectItem>
                <SelectItem value={'"'} className="text-sm">큰따옴표 (&quot;)</SelectItem>
                <SelectItem value="'" className="text-sm">작은따옴표 (&apos;)</SelectItem>
                <SelectItem value="__custom__" className="text-sm">직접 입력...</SelectItem>
              </SelectContent>
            </Select>

            {isCustomQuote && (
              <Input
                value={customQuoteChar}
                onChange={(e) => setCustomQuoteChar(e.target.value.slice(0, 1))}
                placeholder="e.g. `"
                className="w-[80px] h-8 text-sm font-mono"
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
          새로 고침
        </Button>

        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleSave}
          disabled={saving}
          className="h-8"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5 mr-1.5" />
          )}
          저장
        </Button>

        {!isLoading && rows.length > 0 && (
          <span className="text-sm text-muted-foreground ml-auto font-[family-name:var(--font-d2coding)]">
            {csvDisplay.data.length}행 · {csvDisplay.columnCount}컬럼
          </span>
        )}
      </div>

      {/* Table */}
      {isLoading && (
        <div className="flex items-center justify-center h-[200px]">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">{error}</p>
        </div>
      )}

      {!isLoading && !error && rows.length > 0 && (
        <SampleGrid
          columns={csvDisplay.header ?? Array.from({ length: csvDisplay.columnCount }, (_, i) => `col_${i + 1}`)}
          rows={csvDisplay.data}
        />
      )}

      {!isLoading && !error && rows.length === 0 && (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">데이터가 없습니다</p>
        </div>
      )}
    </div>
  )

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <Card>
      {/* Hide Delete button for synced datasets and non-admin users */}
      {hasSample && !isSynced && isAdmin && (
        <div className="flex justify-end px-4 pt-3">
          <Button size="sm" variant="outline" onClick={handleDelete} className="text-destructive">
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            삭제
          </Button>
        </div>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.tsv"
        className="hidden"
        onChange={handleFileSelect}
      />
      <CardContent className="p-0">
        {/* Parquet view: no toolbar */}
        {hasSample && sampleFormat === "parquet" && renderParquetTable()}

        {/* CSV view: with toolbar */}
        {hasSample && sampleFormat === "csv" && renderCsvView()}

        {/* Empty state: no sample file */}
        {hasSample === false && isAdmin && (
          <div className="flex flex-col items-center justify-center gap-4 p-8">
            {uploadError && (
              <p className="text-sm text-destructive">{uploadError}</p>
            )}
            <div
              className="flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-lg p-8 w-full cursor-pointer hover:border-primary/50 hover:bg-muted/30 transition-colors"
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">
                {uploading ? "업로드 중..." : "CSV 파일을 업로드하려면 클릭하세요"}
              </p>
              <p className="text-xs text-muted-foreground">
                지원 형식: .csv, .tsv (최대 100 KB)
              </p>
            </div>
          </div>
        )}
        {hasSample === false && !isAdmin && (
          <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
            샘플 데이터가 없습니다.
          </div>
        )}

        {/* Initial loading */}
        {hasSample === null && isLoading && (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
