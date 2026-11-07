"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import {
  ArrowLeft, Clock, Download, ExternalLink, FileJson, FolderOpen, Globe, Loader2, Pencil, Plus, RefreshCw, Trash2, X,
} from "lucide-react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"
import { errorMessage, notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Popover, PopoverContent, PopoverTrigger } from "@workspace/ui/components/popover"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { CommentSection } from "@/components/comments"
import { CodeViewer } from "@/components/code-viewer"
import { useAuth } from "@/features/auth"
import { MONACO_FONT_SIZE } from "@/lib/monaco"

import {
  addTag, deleteOciModel, deleteVersion, fetchCatalogTags, fetchOciModel, fetchVersions, removeTag, updateReadme,
  type CatalogTag, type OciModelDetail, type OciModelVersion,
} from "@/features/oci-hub/api"

import { StatCard } from "@/components/stat-card"
import { DETAIL_TAB_LABELS, DetailHeader, FactList, SideCard } from "@/components/model-hub/detail-layout"
import { formatDate } from "@/components/model-hub/format"

import { formatDateTime, formatSize, SourceBadge, StatusBadge, timeAgo } from "./oci-common"
import { OciFilesTab } from "./oci-files-tab"
import { OciUsageGuide } from "./oci-guides"
import { OciLineageTab } from "./oci-lineage"
import { OciModelDialog } from "./oci-model-dialog"

const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-[200px]"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>,
})

const TABS = ["overview", "versions", "files", "lineage", "usage", "activity"] as const
// 예전 주소(?tab=info 등)로 들어와도 맞는 탭이 열리게 한다.
const LEGACY_TABS: Record<string, string> = { info: "overview", "model-card": "overview", comments: "activity" }
type Tab = (typeof TABS)[number]

// ─── 태그 편집 ───

function TagEditor({
  model, canEdit, onChanged,
}: { model: OciModelDetail; canEdit: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [all, setAll] = useState<CatalogTag[] | null>(null)
  const [filter, setFilter] = useState("")
  const [busy, setBusy] = useState<number | null>(null)

  useEffect(() => {
    if (open && all === null) {
      fetchCatalogTags().then(setAll).catch((e) => { toast.error(errorMessage(e) ?? "태그를 불러오지 못했습니다."); setAll([]) })
    }
  }, [open, all])

  const attached = new Set(model.tags.map((t) => t.id))
  const candidates = (all ?? []).filter(
    (t) => !attached.has(t.id) && t.name.toLowerCase().includes(filter.trim().toLowerCase()),
  )

  async function run(id: number, fn: () => Promise<void>, ok: string) {
    setBusy(id)
    try {
      await fn()
      toast.success(ok)
      onChanged()
    } catch (e) {
      toast.error(errorMessage(e) ?? "처리하지 못했습니다.")
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      {model.tags.map((t) => (
        <Badge key={t.id} variant="outline" className="text-xs gap-1" style={{ borderColor: t.color, color: t.color }}>
          {t.name}
          {canEdit && (
            <button
              type="button"
              className="ml-0.5 rounded hover:bg-muted disabled:opacity-50"
              aria-label={`${t.name} 태그 제거`}
              disabled={busy === t.id}
              onClick={() => run(t.id, () => removeTag(model.name, t.id), `'${t.name}' 태그를 제거했습니다.`)}
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </Badge>
      ))}
      {canEdit && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-5 px-1.5 text-xs">
              <Plus className="h-3 w-3 mr-0.5" />태그
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-2" align="start">
            <Input
              placeholder="태그 검색" value={filter} onChange={(e) => setFilter(e.target.value)}
              className="h-8 mb-2" autoFocus
            />
            <div className="max-h-60 overflow-y-auto space-y-0.5">
              {all === null ? (
                <p className="text-xs text-muted-foreground p-2">불러오는 중…</p>
              ) : candidates.length === 0 ? (
                <p className="text-xs text-muted-foreground p-2">
                  {all.length === 0 ? "등록된 태그가 없습니다. 태그 메뉴에서 먼저 만드세요." : "추가할 태그가 없습니다."}
                </p>
              ) : candidates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  disabled={busy !== null}
                  className="flex w-full items-center gap-2 rounded px-2 py-1 text-sm hover:bg-muted text-left"
                  onClick={() => run(t.id, () => addTag(model.name, t.id), `'${t.name}' 태그를 추가했습니다.`)}
                >
                  <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: t.color }} />
                  {t.name}
                </button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
      )}
    </>
  )
}

// ─── README ───

const README_PROSE =
  "prose prose-base max-w-none prose-headings:text-foreground prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground prose-code:text-foreground prose-code:bg-zinc-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:font-[D2Coding,monospace] [&_table]:border-collapse [&_table]:text-sm [&_th]:border [&_th]:px-3 [&_th]:py-1.5 [&_th]:bg-muted/60 [&_th]:text-foreground [&_td]:border [&_td]:px-3 [&_td]:py-1.5 [&_td]:text-foreground [&_code]:font-[D2Coding,monospace]"

/** README 본문 렌더링 — 읽기 화면과 편집 중 미리보기가 같은 결과를 보이도록 공용으로 쓴다. */
function ReadmeMarkdown({ source }: { source: string }) {
  return (
    <div className={README_PROSE}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || "")
            const codeStr = String(children).replace(/\n$/, "")
            if (match) {
              return (
                <SyntaxHighlighter
                  style={oneDark} language={match[1]} PreTag="div"
                  customStyle={{ borderRadius: "0.5rem", fontSize: "13px", fontFamily: "D2Coding, monospace" }}
                >
                  {codeStr}
                </SyntaxHighlighter>
              )
            }
            return <code className={className} {...props}>{children}</code>
          },
        }}
      >
        {source}
      </Markdown>
    </div>
  )
}

type ReadmeView = "edit" | "split" | "preview"
const README_VIEWS: { value: ReadmeView; label: string }[] = [
  { value: "edit", label: "편집" },
  { value: "split", label: "분할" },
  { value: "preview", label: "미리보기" },
]

function ReadmeTab({
  model, canEdit, onSaved,
}: { model: OciModelDetail; canEdit: boolean; onSaved: (readme: string) => void }) {
  const askConfirm = useConfirm()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(model.readme || "")
  const [saving, setSaving] = useState(false)
  const [view, setView] = useState<ReadmeView>("split")

  useEffect(() => { if (!editing) setText(model.readme || "") }, [model.readme, editing])

  async function save() {
    setSaving(true)
    try {
      await updateReadme(model.name, text)
      onSaved(text)
      setEditing(false)
      toast.success("README 를 저장했습니다.")
    } catch (e) {
      notifyError(e, "README 를 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  async function cancel() {
    if (text !== (model.readme || "") && !(await askConfirm({
      title: "변경 내용 버리기",
      description: "저장하지 않은 변경 내용을 버릴까요?",
      confirmText: "버리기",
      destructive: true,
    }))) return
    setEditing(false)
    setText(model.readme || "")
  }

  return (
    <div>
      {canEdit && (
        <div className="flex items-center justify-end gap-2 mb-2">
          {editing && (
            <div role="group" aria-label="README 보기 방식" className="mr-auto inline-flex rounded-md border p-0.5">
              {README_VIEWS.map((v) => (
                <button
                  key={v.value}
                  type="button"
                  aria-pressed={view === v.value}
                  onClick={() => setView(v.value)}
                  className={`rounded px-2.5 py-1 text-xs transition-colors ${
                    view === v.value ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {v.label}
                </button>
              ))}
            </div>
          )}
          {editing ? (
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => { void cancel() }} disabled={saving}>취소</Button>
              <Button size="sm" onClick={save} disabled={saving || text === (model.readme || "")}>
                {saving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}저장
              </Button>
            </div>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil className="h-3.5 w-3.5 mr-1.5" />README 수정
            </Button>
          )}
        </div>
      )}
      {editing ? (
        <div
          className={`grid auto-rows-fr gap-3 ${view === "split" ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}
          style={{ height: "calc(100vh - 340px)", minHeight: 300 }}
        >
          {/* 편집기는 미리보기 전용 보기에서도 마운트해 둬 커서·실행 취소 기록이 유지되게 한다. */}
          <div className={`overflow-hidden rounded border min-h-0 ${view === "preview" ? "hidden" : ""}`}>
            <MonacoEditor
              height="100%" language="markdown" value={text}
              onChange={(v) => setText(v || "")}
              theme="light"
              options={{
                minimap: { enabled: false }, wordWrap: "on", fontSize: MONACO_FONT_SIZE,
                fontFamily: "D2Coding, monospace", lineNumbers: "on", renderLineHighlight: "none",
                overviewRulerBorder: false, hideCursorInOverviewRuler: true, automaticLayout: true,
              }}
            />
          </div>
          {view !== "edit" && (
            <div className="overflow-auto rounded border bg-background px-5 py-4 min-h-0" aria-label="README 미리보기">
              {text.trim() ? (
                <ReadmeMarkdown source={text} />
              ) : (
                <p className="py-12 text-center text-sm text-muted-foreground">미리 볼 내용이 없습니다.</p>
              )}
            </div>
          )}
        </div>
      ) : model.readme ? (
        <ReadmeMarkdown source={model.readme} />
      ) : (
        <div className="text-center py-12 space-y-3 text-sm text-muted-foreground">
          <p>아직 README 가 없습니다.</p>
          {canEdit && (
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              <Pencil className="h-3.5 w-3.5 mr-1.5" />README 작성
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

// ─── 버전 ───

function VersionsTab({
  modelName, versions, canEdit, onShowFiles, onDeleted,
}: {
  modelName: string
  versions: OciModelVersion[]
  canEdit: boolean
  onShowFiles: (v: number) => void
  onDeleted: () => void
}) {
  const [manifest, setManifest] = useState<OciModelVersion | null>(null)
  const [deletingVersion, setDeletingVersion] = useState<number | null>(null)
  const askConfirm = useConfirm()

  async function removeVersion(v: OciModelVersion) {
    const files = v.status === "ready" ? ` 저장소 파일(${v.file_count}개, ${formatSize(v.total_size)})도 함께 삭제되며` : " 저장소에 남은 파일도 함께 삭제되며"
    const ok = await askConfirm({
      title: `v${v.version} 삭제`,
      description: `${modelName} 의 v${v.version} 기록을 지웁니다.${files} 되돌릴 수 없습니다.`,
      confirmText: "삭제",
      destructive: true,
    })
    if (!ok) return
    setDeletingVersion(v.version)
    try {
      await deleteVersion(modelName, v.version)
      toast.success(`v${v.version} 을(를) 삭제했습니다.`)
      onDeleted()
    } catch (e) {
      notifyError(e, `v${v.version} 을(를) 삭제하지 못했습니다.`)
    } finally {
      setDeletingVersion(null)
    }
  }
  if (versions.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-12">아직 버전이 없습니다. CLI·SDK 로 push 하면 버전이 생깁니다.</p>
  }
  const pretty = (m: string | null) => {
    if (!m) return ""
    try { return JSON.stringify(JSON.parse(m), null, 2) } catch { return m }
  }
  return (
    <>
      <div className="border rounded-md overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left font-medium w-20">버전</th>
              <th className="px-3 py-2 text-center font-medium w-28">상태</th>
              <th className="px-3 py-2 text-center font-medium w-16">파일</th>
              <th className="px-3 py-2 text-center font-medium w-24">크기</th>
              <th className="px-3 py-2 text-left font-medium">다이제스트 / 오류</th>
              <th className="px-3 py-2 text-left font-medium w-44">생성</th>
              <th className="px-3 py-2 text-right font-medium w-64">작업</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {versions.map((v) => {
              const err = v.status === "failed" ? String(v.extra_metadata?.error ?? "") : ""
              return (
                <tr key={v.id} className="hover:bg-muted/30">
                  <td className="px-3 py-2 font-medium">v{v.version}</td>
                  <td className="px-3 py-2 text-center">
                    <span className="inline-flex items-center gap-1">
                      {v.status === "importing" && <Loader2 className="h-3 w-3 animate-spin text-sky-600" />}
                      <StatusBadge status={v.status} />
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center">{v.status === "ready" ? v.file_count : "-"}</td>
                  <td className="px-3 py-2 text-center">{v.status === "ready" ? formatSize(v.total_size) : "-"}</td>
                  <td className="px-3 py-2 font-mono text-xs break-all">
                    {err ? <span className="text-destructive font-sans">{err}</span>
                      : v.content_digest
                        ? <span title={v.content_digest}>{v.content_digest.slice(0, 19)}…{v.content_digest.slice(-8)}</span>
                        : <span className="text-muted-foreground">-</span>}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground" title={formatDateTime(v.created_at)}>
                    {formatDateTime(v.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1">
                      {v.status === "ready" && (
                        <>
                          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => onShowFiles(v.version)}>파일 보기</Button>
                          {v.manifest && (
                            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setManifest(v)}>
                              <FileJson className="h-3 w-3 mr-1" />manifest
                            </Button>
                          )}
                        </>
                      )}
                      {canEdit && (
                        <Button
                          variant="ghost" size="sm" className="h-6 text-xs text-destructive hover:text-destructive"
                          aria-label={`v${v.version} 삭제`}
                          title={v.status === "importing" ? "가져오는 중인 버전은 끝난 뒤 삭제할 수 있습니다." : `v${v.version} 삭제`}
                          disabled={v.status === "importing" || deletingVersion !== null}
                          onClick={() => removeVersion(v)}
                        >
                          {deletingVersion === v.version
                            ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                            : <Trash2 className="h-3 w-3 mr-1" />}
                          삭제
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <Dialog open={manifest !== null} onOpenChange={(o) => { if (!o) setManifest(null) }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>v{manifest?.version} OCI manifest</DialogTitle>
            {manifest?.content_digest && (
              <DialogDescription className="font-mono text-xs break-all">{manifest.content_digest}</DialogDescription>
            )}
          </DialogHeader>
          {manifest && <CodeViewer code={pretty(manifest.manifest)} language="json" height={480} copyLabel="manifest" />}
        </DialogContent>
      </Dialog>
    </>
  )
}

// ─── 상세 ───

/** OCI 모델 상세 — `/dashboard/oci-hub/{name}?tab=...&version=...`. */
export function OciModelDetailView({ name }: { name: string }) {
  const { user } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const rawTab = params.get("tab") ?? ""
  const tabParam = (LEGACY_TABS[rawTab] ?? rawTab) as Tab
  const tab: Tab = TABS.includes(tabParam) ? tabParam : "overview"

  const [detail, setDetail] = useState<OciModelDetail | null>(null)
  const [versions, setVersions] = useState<OciModelVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState("")
  const [deleting, setDeleting] = useState(false)

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    let cancelled = false
    if (reloadKey === 0) setLoading(true)
    setError(null)
    Promise.all([fetchOciModel(name), fetchVersions(name)])
      .then(([d, v]) => {
        if (cancelled) return
        setDetail(d)
        setVersions(v)
      })
      .catch((e) => {
        if (cancelled) return
        const message = errorMessage(e) ?? "모델을 불러오지 못했습니다."
        setError({ message, notFound: /찾을 수 없|not found|404/i.test(message) })
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [name, reloadKey])

  // 가져오는 중인 버전이 있으면 5초마다 갱신
  const importing = versions.some((v) => v.status === "importing")
  useEffect(() => {
    if (!importing) return
    const id = window.setInterval(reload, 5000)
    return () => window.clearInterval(id)
  }, [importing, reload])

  const setQuery = useCallback((patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    const qs = next.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
  }, [params, pathname, router])

  const readyVersions = useMemo(() => versions.filter((v) => v.status === "ready"), [versions])
  const versionParam = Number(params.get("version")) || null
  const fileVersion = readyVersions.some((v) => v.version === versionParam)
    ? versionParam
    : (readyVersions[0]?.version ?? null)
  const latestReady = readyVersions[0] ?? null

  const canEdit = !!user && !!detail && (
    user.is_admin || [detail.owner, detail.created_by].includes(user.username)
  )

  const backHref = "/dashboard/oci-hub?tab=models"

  async function doDelete() {
    if (!detail) return
    setDeleting(true)
    try {
      await deleteOciModel(detail.name)
      toast.success(`모델 '${detail.name}' 을(를) 삭제했습니다.`)
      router.push(backHref)
    } catch (e) {
      toast.error(errorMessage(e) ?? "삭제하지 못했습니다.")
      setDeleting(false)
    }
  }

  const backButton = (
    <Button
      variant="ghost" size="sm"
      className="h-7 -ml-2 self-start px-2 text-muted-foreground hover:text-foreground"
      onClick={() => (window.history.length > 1 ? router.back() : router.push(backHref))}
    >
      <ArrowLeft className="h-4 w-4 mr-1" />모델 목록으로
    </Button>
  )

  if (loading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  if (error || !detail) {
    return (
      <div className="space-y-4">
        {backButton}
        <div className="text-center py-16 space-y-3">
          <p className="text-base font-medium">{error?.notFound ? `모델 '${name}' 을(를) 찾을 수 없습니다.` : "모델을 불러오지 못했습니다."}</p>
          {error && !error.notFound && <p className="text-sm text-destructive">{error.message}</p>}
          <div className="flex justify-center gap-2">
            {!error?.notFound && <Button variant="outline" size="sm" onClick={reload}>다시 시도</Button>}
            <Button variant="outline" size="sm" onClick={() => router.push(backHref)}>목록으로</Button>
          </div>
        </div>
      </div>
    )
  }

  const sourceLink = detail.source_type === "huggingface" && detail.source_id
    ? { href: `https://huggingface.co/${detail.source_id}`, label: `HuggingFace · ${detail.source_id}`, external: true }
    : detail.source_type === "mlflow" && detail.source_id
      ? { href: `/dashboard/models/${encodeURIComponent(detail.source_id)}`, label: `MLflow 모델 · ${detail.source_id}`, external: false }
      : null
  const arch = Object.entries(latestReady?.extra_metadata ?? {})
    .filter(([k, v]) => k !== "error" && v != null && (typeof v !== "object" || Array.isArray(v)))
    .slice(0, 8)

  return (
    <div className="space-y-4">
      {backButton}

      <DetailHeader
        icon={<Globe className="h-5 w-5 text-primary" />}
        title={detail.display_name || detail.name}
        subtitle={detail.display_name && detail.display_name !== detail.name ? detail.name : null}
        badges={<><StatusBadge status={detail.status} /><SourceBadge sourceType={detail.source_type} /></>}
        description={detail.description}
        actions={
          <>
            <Button variant="ghost" size="icon-sm" onClick={reload} aria-label="새로고침" title="새로고침">
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            {canEdit && (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                  <Pencil className="h-3.5 w-3.5 mr-1.5" />수정
                </Button>
                <Button
                  variant="outline" size="sm" className="text-destructive hover:text-destructive"
                  onClick={() => { setDeleteConfirm(""); setDeleteOpen(true) }}
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1.5" />삭제
                </Button>
              </>
            )}
          </>
        }
      />
      {importing && (
        <p className="flex items-center gap-1.5 text-sm text-sky-700">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />가져오는 중인 버전이 있습니다 — 끝나면 자동으로 갱신됩니다.
        </p>
      )}

      <Tabs value={tab} onValueChange={(v) => setQuery({ tab: v === "overview" ? null : v, version: v === "files" ? params.get("version") : null })}>
        <TabsList variant="line">
          <TabsTrigger value="overview" className="text-base">{DETAIL_TAB_LABELS.overview}</TabsTrigger>
          <TabsTrigger value="versions" className="text-base">{DETAIL_TAB_LABELS.versions} <span className="ml-1 text-xs text-muted-foreground">{versions.length}</span></TabsTrigger>
          <TabsTrigger value="files" className="text-base">{DETAIL_TAB_LABELS.files}</TabsTrigger>
          <TabsTrigger value="lineage" className="text-base">{DETAIL_TAB_LABELS.lineage} <span className="ml-1 text-xs text-muted-foreground">{detail.lineage.length}</span></TabsTrigger>
          <TabsTrigger value="usage" className="text-base">{DETAIL_TAB_LABELS.usage}</TabsTrigger>
          <TabsTrigger value="activity" className="text-base">{DETAIL_TAB_LABELS.activity}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0">
              <ReadmeTab
                model={detail} canEdit={canEdit}
                onSaved={(readme) => setDetail((d) => (d ? { ...d, readme } : d))}
              />
            </div>
            <aside className="space-y-4">
              <SideCard title="모델 정보">
                <FactList items={[
                  ["태스크", detail.task],
                  ["프레임워크", detail.framework],
                  ["언어", detail.language],
                  ["라이선스", detail.license],
                  ["소유자", detail.owner],
                  ["등록자", detail.created_by && detail.created_by !== detail.owner ? detail.created_by : null],
                  ["다운로드", `${detail.download_count.toLocaleString()}회`],
                  ["등록", <span key="c" title={formatDateTime(detail.created_at)}>{formatDate(detail.created_at)}</span>],
                  ["수정", <span key="u" title={formatDateTime(detail.updated_at)}>{timeAgo(detail.updated_at)}</span>],
                ]} />
              </SideCard>

              <SideCard
                title="최신 버전"
                action={versions.length > 0 && (
                  <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => setQuery({ tab: "versions", version: null })}>
                    모든 버전
                  </Button>
                )}
              >
                {latestReady ? (
                  <div className="space-y-2 text-sm">
                    <div className="flex items-baseline gap-2">
                      <span className="text-lg font-semibold">v{latestReady.version}</span>
                      <span className="text-muted-foreground">{formatSize(latestReady.total_size)} · 파일 {latestReady.file_count}개</span>
                    </div>
                    {latestReady.content_digest && (
                      <p className="truncate font-mono text-xs text-muted-foreground" title={latestReady.content_digest}>
                        {latestReady.content_digest}
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground">{formatDateTime(latestReady.created_at)} 게시</p>
                    <Button variant="outline" size="sm" className="w-full" onClick={() => setQuery({ tab: "files", version: null })}>
                      <FolderOpen className="h-3.5 w-3.5 mr-1.5" />파일 보기
                    </Button>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">준비된 버전이 없습니다. CLI·SDK 로 push 하면 여기에 표시됩니다.</p>
                )}
              </SideCard>

              {sourceLink && (
                <SideCard title="출처">
                  {sourceLink.external ? (
                    <a href={sourceLink.href} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-sm text-primary hover:underline break-all">
                      {sourceLink.label}<ExternalLink className="h-3.5 w-3.5 shrink-0" />
                    </a>
                  ) : (
                    <Link href={sourceLink.href} className="text-sm text-primary hover:underline break-all">{sourceLink.label}</Link>
                  )}
                  {detail.source_revision && detail.source_revision !== "main" && (
                    <p className="mt-1 text-xs text-muted-foreground">리비전 {detail.source_revision}</p>
                  )}
                </SideCard>
              )}

              <SideCard title="태그">
                <div className="flex flex-wrap items-center gap-1">
                  <TagEditor model={detail} canEdit={canEdit} onChanged={reload} />
                  {detail.tags.length === 0 && !canEdit && <span className="text-sm text-muted-foreground">태그가 없습니다.</span>}
                </div>
              </SideCard>

              {arch.length > 0 && (
                <SideCard title={`아키텍처 (v${latestReady?.version})`}>
                  <FactList items={arch.map(([k, v]) => [
                    k.replace(/_/g, " "),
                    <span key={k} className="font-mono text-xs">{Array.isArray(v) ? v.join(", ") : String(v)}</span>,
                  ])} />
                </SideCard>
              )}
            </aside>
          </div>
        </TabsContent>

        <TabsContent value="versions" className="mt-4">
          <VersionsTab
            modelName={detail.name}
            versions={versions}
            canEdit={canEdit}
            onShowFiles={(v) => setQuery({ tab: "files", version: v === readyVersions[0]?.version ? null : String(v) })}
            onDeleted={reload}
          />
        </TabsContent>

        <TabsContent value="files" className="mt-4">
          <OciFilesTab
            modelName={detail.name}
            versions={versions}
            version={fileVersion}
            onVersionChange={(v) => setQuery({ version: v === readyVersions[0]?.version ? null : String(v) })}
          />
        </TabsContent>

        <TabsContent value="lineage" className="mt-4">
          <OciLineageTab modelName={detail.name} lineage={detail.lineage} canEdit={canEdit} onChanged={reload} />
        </TabsContent>

        <TabsContent value="usage" className="mt-4">
          <OciUsageGuide name={detail.name} version={latestReady?.version ?? 0} framework={detail.framework} />
        </TabsContent>

        <TabsContent value="activity" className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <StatCard icon={Download} label="다운로드 (누적)" value={detail.download_count.toLocaleString()} hint="다운로드 주소 발급 기준" />
            <StatCard icon={Clock} label="최근 수정" value={timeAgo(detail.updated_at)} hint={formatDateTime(detail.updated_at)} />
            <StatCard icon={FileJson} label="게시된 버전" value={readyVersions.length} hint={latestReady ? `최신 v${latestReady.version}` : "없음"} />
          </div>
          <CommentSection entityType="oci-model" entityId={detail.name} />
        </TabsContent>
      </Tabs>

      <OciModelDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        model={detail}
        onSaved={(m) => setDetail((d) => (d ? { ...d, ...m } : m))}
      />

      <Dialog open={deleteOpen} onOpenChange={(o) => { if (!deleting) setDeleteOpen(o) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>모델 삭제</DialogTitle>
            <DialogDescription>
              모델 정보·버전·태그·리니지와 저장소의 모델 파일({formatSize(detail.total_size)})이 모두 삭제되며 되돌릴 수 없습니다.
              확인을 위해 모델 이름 <strong className="font-mono">{detail.name}</strong> 을(를) 입력하세요.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={deleteConfirm} onChange={(e) => setDeleteConfirm(e.target.value)}
            placeholder={detail.name} disabled={deleting} autoFocus
            onKeyDown={(e) => { if (e.key === "Enter" && deleteConfirm === detail.name && !deleting) doDelete() }}
          />
          {importing && <p className="text-sm text-amber-700">가져오는 중인 버전이 있어 지금은 삭제할 수 없습니다.</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={deleting}>취소</Button>
            <Button variant="destructive" onClick={doDelete} disabled={deleting || importing || deleteConfirm !== detail.name}>
              {deleting && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
