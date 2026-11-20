"use client"

/**
 * 데이터셋 상세 › AI-Ready 탭 — 배포본과 내보내기 (TODO-096 3단계).
 *
 * - 배포본: 데이터를 받는 곳(접속 URL 필수, 내려받기 URL·형식·크기·체크섬). DCAT dcat:distribution, Croissant cr:FileObject 로 나간다.
 *   체크리스트 C05(필수 메타데이터)·C27(다운로드 형식)·C33(무결성 정보)의 자동 판정 근거가 된다.
 * - 내보내기: DCAT JSON-LD · Croissant JSON-LD · 데이터 카드(Markdown)를 보고 내려받는다. 같은 내용을 외부 연동 API 로도 준다.
 *   보기는 Monaco 읽기 전용 뷰어(text-sm), 데이터 카드는 '미리보기'(렌더링) · 'Markdown'(원문) 탭.
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useState, type ReactNode } from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowDown, ArrowUp, Copy, Download, Eye, Loader2, Pencil, Plus, Trash2 } from "lucide-react"

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@workspace/ui/components/alert-dialog"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"

import { CodeViewer } from "@/components/code-viewer"
import { DetailSection } from "@/components/detail-section"
import { errorMessage, notifyError, notifySuccess } from "@/lib/notify"

import {
  aiReadyExportUrl, deleteDistribution, fetchAiReadyExport, fetchAiReadyPublicBase, fetchDistributions, reorderDistributions,
  saveDistribution,
  type AiReadyExportKind, type AiReadyPublicBase, type DatasetDistribution, type DistributionInput,
} from "../api"
import { copyText } from "@/lib/clipboard"

const FORMATS = ["CSV", "TSV", "JSON", "JSONL", "XML", "PARQUET", "AVRO", "ORC", "XLSX", "ZIP", "PDF", "GEOJSON", "API"]
const CHECKSUM_ALGORITHMS = ["SHA-256", "SHA-512", "SHA-1", "MD5"]

function formatBytes(n: number | null): string {
  if (n === null || n === undefined) return "-"
  const units = ["B", "KB", "MB", "GB", "TB"]
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}

// ---------------------------------------------------------------------------
// 배포본
// ---------------------------------------------------------------------------

export function DistributionsCard({
  datasetId, canManage, onChanged,
}: { datasetId: number; canManage: boolean; onChanged: () => void }) {
  const [rows, setRows] = useState<DatasetDistribution[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<DatasetDistribution | "new" | null>(null)
  const [removing, setRemoving] = useState<DatasetDistribution | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setRows(await fetchDistributions(datasetId))
    } catch (e) {
      setError(errorMessage(e) ?? "배포본을 불러오지 못했습니다.")
    }
  }, [datasetId])

  useEffect(() => { load() }, [load])

  const changed = () => { load(); onChanged() }

  const remove = async () => {
    if (!removing) return
    try {
      await deleteDistribution(datasetId, removing.id)
      notifySuccess("배포본을 지웠습니다.")
      changed()
    } catch (e) {
      notifyError(e, "배포본을 지우지 못했습니다.")
    } finally {
      setRemoving(null)
    }
  }

  // 순서 바꾸기 — 화면을 먼저 바꾸고 서버에 전체 순서를 보낸다(실패하면 다시 불러옴). 내보내기·데이터 카드가 이 순서를 따른다.
  const [moving, setMoving] = useState(false)
  const move = async (index: number, delta: -1 | 1) => {
    if (!rows) return
    const next = [...rows]
    const [item] = next.splice(index, 1)
    next.splice(index + delta, 0, item!)
    setRows(next)
    setMoving(true)
    try {
      setRows(await reorderDistributions(datasetId, next.map((d) => d.id)))
    } catch (e) {
      notifyError(e, "배포본 순서를 바꾸지 못했습니다.")
      load()
    } finally {
      setMoving(false)
    }
  }

  return (
    <DetailSection
      title="배포본"
      description="데이터를 받는 곳입니다. DCAT·Croissant 로 내보낼 때 이 순서대로 들어가고, 내려받기 URL·체크섬이 있으면 C27·C33 이 적정이 됩니다."
      actions={canManage ? (
        <Button size="sm" variant="outline" onClick={() => setEditing("new")}>
          <Plus className="mr-1 h-4 w-4" /> 배포본 추가
        </Button>
      ) : undefined}
    >
        {error && <div className="text-sm text-destructive">{error}</div>}
        {rows === null && !error && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        {rows && rows.length === 0 && (
          <div className="rounded-md border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
            등록한 배포본이 없습니다. 공공데이터포털 파일·API 주소나 내부 내려받기 주소를 등록하세요.
          </div>
        )}
        {rows && rows.length > 0 && (
          <DistributionTable
            rows={rows}
            actions={canManage ? (d, i) => (
              <>
                {rows.length > 1 && <>
                <Tip label="위로"><Button size="icon" variant="ghost" className="h-7 w-7" aria-label="배포본 위로"
                  disabled={i === 0 || moving} onClick={() => move(i, -1)}>
                  <ArrowUp className="h-3.5 w-3.5" />
                </Button></Tip>
                <Tip label="아래로"><Button size="icon" variant="ghost" className="h-7 w-7" aria-label="배포본 아래로"
                  disabled={i === rows.length - 1 || moving} onClick={() => move(i, 1)}>
                  <ArrowDown className="h-3.5 w-3.5" />
                </Button></Tip>
                </>}
                <Tip label="배포본 고치기"><Button size="icon" variant="ghost" className="h-7 w-7" aria-label="배포본 고치기" onClick={() => setEditing(d)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button></Tip>
                <Tip label="배포본 지우기"><Button size="icon" variant="ghost" className="h-7 w-7" aria-label="배포본 지우기" onClick={() => setRemoving(d)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button></Tip>
              </>
            ) : undefined}
          />
        )}

      <DistributionDialog
        datasetId={datasetId}
        target={editing}
        onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); changed() }}
      />

      <AlertDialog open={!!removing} onOpenChange={(open) => { if (!open) setRemoving(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>배포본을 지울까요?</AlertDialogTitle>
            <AlertDialogDescription>
              {removing?.title || removing?.access_url} — DCAT·Croissant 내보내기와 체크리스트 판정에서 빠집니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={remove}>지우기</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DetailSection>
  )
}

/** 배포본 표 한 줄에 필요한 값 — 로컬 배포본과 페더레이션(원격) 상세가 보내는 배포본이 함께 쓴다. */
export type DistributionRow = Pick<DatasetDistribution,
  "title" | "description" | "format" | "media_type" | "access_url" | "download_url" | "byte_size" | "checksum_algorithm" | "checksum"
> & { effective_media_type?: string | null }

/** 배포본 표(읽기 전용). ``actions`` 를 주면 줄마다 끝 칸에 단추를 그린다. */
export function DistributionTable<T extends DistributionRow>({
  rows, actions,
}: { rows: T[]; actions?: (row: T, index: number) => ReactNode }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead className="w-24">형식</TableHead>
            <TableHead>주소</TableHead>
            <TableHead className="w-20">크기</TableHead>
            <TableHead className="w-28">체크섬</TableHead>
            {actions && <TableHead className="w-36" />}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((d, i) => {
            const mime = d.effective_media_type ?? d.media_type
            return (
              <TableRow key={(d as { id?: number }).id ?? i} className="align-top">
                <TableCell className="whitespace-normal text-sm">
                  {d.title || <span className="text-muted-foreground">(이름 없음)</span>}
                  {d.description && <div className="text-xs">{d.description}</div>}
                </TableCell>
                <TableCell className="text-sm">
                  {d.format ?? "-"}
                  {mime && <div className="text-[11px]">{mime}</div>}
                </TableCell>
                <TableCell className="whitespace-normal text-xs">
                  <div className="break-all"><span >접속 </span>{d.access_url}</div>
                  {d.download_url && (
                    <div className="break-all"><span >내려받기 </span>{d.download_url}</div>
                  )}
                </TableCell>
                <TableCell className="text-sm tabular-nums">{formatBytes(d.byte_size)}</TableCell>
                <TableCell className="text-xs">
                  {d.checksum ? (
                    <span title={d.checksum}>{d.checksum_algorithm} {d.checksum.slice(0, 8)}…</span>
                  ) : <span className="text-muted-foreground">-</span>}
                </TableCell>
                {actions && <TableCell className="whitespace-nowrap">{actions(d, i)}</TableCell>}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

type Form = Record<keyof DistributionInput, string>

const EMPTY: Form = {
  title: "", description: "", format: "", media_type: "", access_url: "", download_url: "",
  byte_size: "", checksum_algorithm: "SHA-256", checksum: "",
}

function toForm(d: DatasetDistribution | null): Form {
  if (!d) return EMPTY
  return {
    title: d.title ?? "", description: d.description ?? "", format: d.format ?? "", media_type: d.media_type ?? "",
    access_url: d.access_url, download_url: d.download_url ?? "",
    byte_size: d.byte_size === null ? "" : String(d.byte_size),
    checksum_algorithm: d.checksum_algorithm ?? "SHA-256", checksum: d.checksum ?? "",
  }
}

function DistributionDialog({
  datasetId, target, onClose, onSaved,
}: {
  datasetId: number
  target: DatasetDistribution | "new" | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Form>(EMPTY)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (target) setForm(toForm(target === "new" ? null : target))
  }, [target])

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  const sizeInvalid = form.byte_size.trim() !== "" && !/^\d+$/.test(form.byte_size.trim())
  const canSave = form.access_url.trim() !== "" && !sizeInvalid && !saving

  const save = async () => {
    if (!target) return
    setSaving(true)
    const opt = (v: string) => v.trim() || null
    try {
      await saveDistribution(datasetId, {
        title: opt(form.title), description: opt(form.description), format: opt(form.format),
        media_type: opt(form.media_type), access_url: form.access_url.trim(), download_url: opt(form.download_url),
        byte_size: form.byte_size.trim() ? Number(form.byte_size.trim()) : null,
        checksum: opt(form.checksum), checksum_algorithm: form.checksum.trim() ? form.checksum_algorithm : null,
      }, target === "new" ? undefined : target.id)
      notifySuccess("배포본을 저장했습니다.")
      onSaved()
    } catch (e) {
      notifyError(e, "배포본을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!target} onOpenChange={(open) => { if (!open && !saving) onClose() }}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{target === "new" ? "배포본 추가" : "배포본 고치기"}</DialogTitle>
          <DialogDescription>접속 URL 만 필수입니다. 파일로 받을 수 있으면 내려받기 URL 과 체크섬을 함께 적어 주세요.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="dist-access">접속 URL *</Label>
            <Input id="dist-access" value={form.access_url} onChange={set("access_url")} placeholder="https://www.data.go.kr/data/…" />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="dist-download">내려받기 URL</Label>
            <Input id="dist-download" value={form.download_url} onChange={set("download_url")} placeholder="https://…/file.csv" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dist-title">이름</Label>
            <Input id="dist-title" value={form.title} onChange={set("title")} placeholder="예: 2025년 전체 CSV" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dist-format">형식</Label>
            <Input id="dist-format" list="dist-formats" value={form.format} onChange={set("format")} placeholder="CSV" />
            <datalist id="dist-formats">{FORMATS.map((f) => <option key={f} value={f} />)}</datalist>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dist-media">MIME 유형</Label>
            <Input id="dist-media" value={form.media_type} onChange={set("media_type")} placeholder="비우면 형식에서 추정" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dist-size">크기(바이트)</Label>
            <Input id="dist-size" inputMode="numeric" value={form.byte_size} onChange={set("byte_size")}
              aria-invalid={sizeInvalid} />
          </div>
          <div className="space-y-1.5">
            <Label>체크섬 알고리즘</Label>
            <Select value={form.checksum_algorithm} onValueChange={(v) => setForm((f) => ({ ...f, checksum_algorithm: v }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {CHECKSUM_ALGORITHMS.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dist-checksum">체크섬(16진수)</Label>
            <Input id="dist-checksum" className="font-mono text-xs" value={form.checksum} onChange={set("checksum")} />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="dist-desc">설명</Label>
            <Textarea id="dist-desc" rows={2} value={form.description} onChange={set("description")} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={saving}>취소</Button>
          <Button onClick={save} disabled={!canSave}>
            {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} 저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// 내보내기
// ---------------------------------------------------------------------------

const EXPORTS: { kind: AiReadyExportKind; label: string; note: string; ext: string; mime: string }[] = [
  { kind: "dcat", label: "DCAT", note: "W3C DCAT v3 JSON-LD — 데이터 포털·카탈로그 연계", ext: "dcat.jsonld", mime: "application/ld+json" },
  { kind: "croissant", label: "Croissant", note: "MLCommons Croissant 1.0 JSON-LD — AI 학습 도구 연계", ext: "croissant.json", mime: "application/ld+json" },
  { kind: "data-card", label: "데이터 카드", note: "가이드라인 부록 5 양식(Markdown) — 빈 항목은 '작성 필요'로 표시", ext: "data-card.md", mime: "text/markdown" },
]

/** 데이터 카드 미리보기 — 본문 text-sm, 표는 테두리. */
const CARD_PROSE =
  "prose prose-sm max-w-none dark:prose-invert prose-headings:text-foreground prose-p:text-foreground prose-li:text-foreground " +
  "prose-strong:text-foreground prose-h1:text-xl prose-h2:mt-6 prose-h2:text-base prose-h2:border-b prose-h2:pb-1 " +
  "[&_table]:border-collapse [&_table]:text-sm [&_th]:border [&_th]:px-3 [&_th]:py-1.5 [&_th]:bg-muted/60 " +
  "[&_td]:border [&_td]:px-3 [&_td]:py-1.5 [&_code]:font-[D2Coding,monospace]"

export function ExportCard({ datasetId, datasetName }: { datasetId: number; datasetName: string }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ kind: AiReadyExportKind; label: string; text: string } | null>(null)

  const run = async (kind: AiReadyExportKind, action: "view" | "download") => {
    const def = EXPORTS.find((e) => e.kind === kind)!
    setBusy(`${kind}-${action}`)
    try {
      const text = await fetchAiReadyExport(datasetId, kind)
      if (action === "view") {
        setPreview({ kind, label: def.label, text })
      } else {
        const blob = new Blob([text], { type: `${def.mime};charset=utf-8` })
        const a = document.createElement("a")
        a.href = URL.createObjectURL(blob)
        a.download = `${datasetName.replace(/[^\w.-]+/g, "_") || `dataset-${datasetId}`}.${def.ext}`
        a.click()
        URL.revokeObjectURL(a.href)
      }
    } catch (e) {
      notifyError(e, "내보내기 결과를 만들지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  // 복사하는 주소도 DCAT `@id` · 데이터 카드처럼 설정 › AI-Ready 의 외부 공개 주소를 따른다(DEF-142).
  // 클립보드 쓰기가 클릭 직후여야 하므로 주소는 미리 받아 둔다(못 받았으면 누를 때 다시 받는다).
  const [publicBase, setPublicBase] = useState<AiReadyPublicBase | null>(null)
  useEffect(() => {
    let alive = true
    fetchAiReadyPublicBase()
      .then((p) => { if (alive) setPublicBase(p) })
      .catch((e) => console.warn("외부 공개 주소를 미리 불러오지 못함", e))
    return () => { alive = false }
  }, [])

  const copyPath = async (kind: AiReadyExportKind) => {
    try {
      const pub = publicBase ?? await fetchAiReadyPublicBase()
      await copyText(aiReadyExportUrl(pub, window.location.origin, datasetId, kind))
      notifySuccess("API 주소를 복사했습니다.")
    } catch (e) {
      notifyError(e, "복사하지 못했습니다.")
    }
  }

  return (
    <DetailSection
      title="내보내기"
      description={<>
        카탈로그 메타데이터를 기계가 읽을 수 있는 형식으로 내보냅니다. 같은 내용을 외부 연동 API 로도 받을 수 있습니다
        (전체 목록은 <code className="text-xs">/api/v1/external/dcat/catalog</code>).
      </>}
    >
        <div className="divide-y rounded-md border">
          {EXPORTS.map((e) => (
            <div key={e.kind} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <div className="min-w-0">
                <div className="text-sm">{e.label}</div>
                <div className="text-xs">{e.note}</div>
              </div>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => run(e.kind, "view")} disabled={busy !== null}>
                  {busy === `${e.kind}-view` ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Eye className="mr-1 h-3.5 w-3.5" />}
                  보기
                </Button>
                <Button size="sm" variant="ghost" onClick={() => run(e.kind, "download")} disabled={busy !== null}>
                  {busy === `${e.kind}-download` ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1 h-3.5 w-3.5" />}
                  내려받기
                </Button>
                <Button size="sm" variant="ghost" onClick={() => copyPath(e.kind)}>
                  <Copy className="mr-1 h-3.5 w-3.5" /> API 주소
                </Button>
              </div>
            </div>
          ))}
        </div>

      <Dialog open={!!preview} onOpenChange={(open) => { if (!open) setPreview(null) }}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>{preview?.label}</DialogTitle>
            <DialogDescription>저장된 메타데이터로 지금 만든 결과입니다.</DialogDescription>
          </DialogHeader>
          {preview && (preview.kind === "data-card" ? (
            <Tabs defaultValue="preview">
              <TabsList variant="line">
                <TabsTrigger value="preview">미리보기</TabsTrigger>
                <TabsTrigger value="markdown">Markdown</TabsTrigger>
              </TabsList>
              <TabsContent value="preview" className="mt-3">
                <div className={cn(CARD_PROSE, "h-[60vh] overflow-auto rounded-md border px-5 py-4")}>
                  <Markdown remarkPlugins={[remarkGfm]}>{preview.text}</Markdown>
                </div>
              </TabsContent>
              <TabsContent value="markdown" className="mt-3">
                <CodeViewer code={preview.text} language="markdown" height="60vh" wordWrap="on" border copyLabel="데이터 카드" />
              </TabsContent>
            </Tabs>
          ) : (
            <CodeViewer code={preview.text} language="json" height="60vh" border copyLabel={preview.label} />
          ))}
        </DialogContent>
      </Dialog>
    </DetailSection>
  )
}
