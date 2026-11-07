"use client"

import { useCallback, useEffect, useState } from "react"
import { Download, Eye, Loader2 } from "lucide-react"
import { errorMessage, toast } from "@/lib/notify"

import { Button } from "@workspace/ui/components/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { fetchBlobUrl } from "@/features/filesystem/api"
import {
  FileViewerDialog,
  isViewableFile,
} from "@/components/local-filesystem-browser/file-viewer-dialog"
import type { FilesystemFile } from "@/components/local-filesystem-browser/types"

import type { OciModelVersion } from "@/features/oci-hub/api"

import { formatDateTime, formatSize } from "./oci-common"

type S3File = { key: string; name: string; size: number; last_modified: string }
type S3Folder = { key: string; name: string }

// model-store browse API 는 서버 측 미리보기를 제공하지 않으므로 그것이 필요한 형식은 미리보기 버튼을 숨긴다.
const SERVER_PREVIEW_ONLY = new Set(["xlsx", "docx", "pptx", "parquet"])

function canPreview(name: string): boolean {
  if (!isViewableFile(name)) return false
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  return !SERVER_PREVIEW_ONLY.has(ext)
}

// 파일은 서버가 저장소에서 읽어 흘려보낸다(/model-store/browse/file). presigned URL 은 저장소 주소
// (예: http://minio:9000)가 브라우저에서 닿지 않으면 열리지 않으므로 화면에서는 쓰지 않는다.
const viewUrl = (filePath: string) => fetchBlobUrl(`/api/v1/model-store/browse/file?${new URLSearchParams({ path: filePath })}`)

/** 짧은 다운로드 토큰을 붙인 주소 — 브라우저가 바로 저장한다(큰 파일도 메모리에 올리지 않음). */
async function directDownloadUrl(filePath: string): Promise<string> {
  const params = new URLSearchParams({ path: filePath })
  const res = await authFetch(`/api/v1/model-store/browse/download-link?${params}`, { method: "POST" })
  if (!res.ok) await throwOnError(res, "다운로드 링크를 받지 못했습니다")
  params.set("token", (await res.json()).token as string)
  return `/api/v1/model-store/browse/file?${params}`
}

/** 파일 탭 — 준비된(ready) 버전을 골라 그 버전의 파일을 탐색한다. */
export function OciFilesTab({
  modelName, versions, version, onVersionChange,
}: {
  modelName: string
  versions: OciModelVersion[]
  version: number | null
  onVersionChange: (v: number) => void
}) {
  const ready = versions.filter((v) => v.status === "ready")
  const basePath = version ? `/${modelName}/v${version}` : ""
  const [folders, setFolders] = useState<S3Folder[]>([])
  const [files, setFiles] = useState<S3File[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentPath, setCurrentPath] = useState(basePath)
  const [preview, setPreview] = useState<FilesystemFile | null>(null)

  const load = useCallback(async (p: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await authFetch(`/api/v1/model-store/browse/list?${new URLSearchParams({ path: p })}`)
      if (!res.ok) await throwOnError(res, "파일 목록을 불러오지 못했습니다")
      const data = await res.json()
      setFolders(data.folders || [])
      setFiles(data.files || [])
      setCurrentPath(p)
    } catch (e) {
      setError(errorMessage(e) ?? "파일 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (basePath) load(basePath)
  }, [basePath, load])

  const download = useCallback(async (key: string) => {
    try {
      const a = document.createElement("a")
      a.href = await directDownloadUrl(key)
      a.download = key.split("/").pop() ?? "download"
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch (e) {
      toast.error(errorMessage(e) ?? "내려받지 못했습니다.")
    }
  }, [])

  if (ready.length === 0 || !version) {
    return (
      <p className="text-sm text-muted-foreground text-center py-12">
        준비된 버전이 없어 표시할 파일이 없습니다. CLI·SDK 로 push 하거나 가져오기가 끝나면 여기에 표시됩니다.
      </p>
    )
  }

  const segments = currentPath.split("/").filter(Boolean)
  const totalSize = files.reduce((acc, f) => acc + f.size, 0)

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Select value={String(version)} onValueChange={(v) => onVersionChange(Number(v))}>
          <SelectTrigger className="h-8 w-40" aria-label="버전 선택"><SelectValue /></SelectTrigger>
          <SelectContent>
            {ready.map((v) => (
              <SelectItem key={v.id} value={String(v.version)}>
                v{v.version}{v.version === ready[0]?.version ? " (최신)" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="font-mono text-xs text-muted-foreground truncate">{currentPath}</span>
        {!loading && !error && (
          <span className="ml-auto text-sm text-muted-foreground">
            {folders.length > 0 && `폴더 ${folders.length}개, `}파일 {files.length}개
            {totalSize > 0 && ` · ${formatSize(totalSize)}`}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />파일을 불러오는 중…
        </div>
      ) : error ? (
        <div className="text-center py-12 space-y-3">
          <p className="text-sm text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={() => load(currentPath)}>다시 시도</Button>
        </div>
      ) : (
        <div className="border rounded-md overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/60 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left font-medium">이름</th>
                <th className="px-3 py-2 text-right font-medium w-28">크기</th>
                <th className="px-3 py-2 text-left font-medium w-44">수정</th>
                <th className="px-3 py-2 text-center font-medium w-44">작업</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {currentPath !== basePath && (
                <tr className="hover:bg-muted/30 cursor-pointer" onClick={() => load("/" + segments.slice(0, -1).join("/"))}>
                  <td className="px-3 py-2 text-primary" colSpan={4}>📁 ..</td>
                </tr>
              )}
              {folders.map((f) => (
                <tr key={f.key} className="hover:bg-muted/30 cursor-pointer" onClick={() => load("/" + f.key.replace(/\/$/, ""))}>
                  <td className="px-3 py-2 text-primary">📁 {f.name}</td>
                  <td className="px-3 py-2 text-right text-muted-foreground">-</td>
                  <td className="px-3 py-2 text-muted-foreground">-</td>
                  <td className="px-3 py-2 text-center">-</td>
                </tr>
              ))}
              {files.map((f) => (
                <tr key={f.key} className="hover:bg-muted/30">
                  <td className="px-3 py-2">📄 {f.name}</td>
                  <td className="px-3 py-2 text-right text-muted-foreground">{formatSize(f.size)}</td>
                  <td className="px-3 py-2 text-muted-foreground text-xs">{f.last_modified ? formatDateTime(f.last_modified) : "-"}</td>
                  <td className="px-3 py-2 text-center">
                    <div className="inline-flex items-center gap-1">
                      <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => download(f.key)}>
                        <Download className="h-3 w-3 mr-1" />다운로드
                      </Button>
                      {canPreview(f.name) && (
                        <Button
                          variant="ghost" size="sm" className="h-6 text-xs"
                          onClick={() => setPreview({ kind: "file", key: f.key, name: f.name, size: f.size, lastModified: f.last_modified })}
                        >
                          <Eye className="h-3 w-3 mr-1" />미리보기
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {folders.length === 0 && files.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-8 text-center text-muted-foreground">
                    이 버전에 파일이 없습니다 (저장소에서 지워졌을 수 있습니다).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <FileViewerDialog
        open={preview !== null}
        onOpenChange={(o) => { if (!o) setPreview(null) }}
        entry={preview}
        getDownloadUrl={viewUrl}
      />
    </div>
  )
}
