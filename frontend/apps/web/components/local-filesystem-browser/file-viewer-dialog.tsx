"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"

import type { FilesystemEntry } from "./types"
import { getExtension } from "./utils"
import { CsvViewer } from "./csv-viewer"
import { PdfViewer } from "./pdf-viewer"
import { VideoViewer } from "./video-viewer"
import { AudioViewer } from "./audio-viewer"
import { XlsxViewer } from "./xlsx-viewer"
import { DocxViewer } from "./docx-viewer"
import { ParquetViewer } from "./parquet-viewer"
import { PptxViewer } from "./pptx-viewer"
import { CodeViewer } from "@/components/code-viewer"

/** Maximum file size in bytes that the text viewer can display (20 KB). */
const MAX_VIEW_SIZE = 20 * 1024

/** Map exact filenames (without extension) to Monaco Editor language identifiers. */
const filenameToLanguage: Record<string, string> = {
  MLmodel: "yaml",
  Dockerfile: "dockerfile",
  Makefile: "plaintext",
}

/** Map file extensions to Monaco Editor language identifiers. */
const extensionToLanguage: Record<string, string> = {
  py: "python",
  java: "java",
  ipynb: "json",
  c: "c",
  cpp: "cpp",
  h: "c",
  hpp: "cpp",
  html: "html",
  htm: "html",
  css: "css",
  js: "javascript",
  ts: "typescript",
  go: "go",
  rs: "rust",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  xml: "xml",
  ini: "ini",
  conf: "ini",
  config: "ini",
  md: "markdown",
  log: "plaintext",
  env: "plaintext",
  txt: "plaintext",
}

/** Image extensions that should be rendered as an image preview. */
const imageExtensions = new Set([
  "jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "tiff",
])

/** CSV/TSV extensions that use the dedicated table viewer. */
const csvExtensions: Record<string, string> = {
  csv: ",",
  tsv: "\t",
}

const pdfExtensions = new Set(["pdf"])
const videoExtensions = new Set(["mp4", "webm", "ogg", "mov", "m4v", "avi", "mkv"])
const audioExtensions = new Set(["mp3", "wav", "m4a", "flac", "aac", "wma"])

/** Extensions handled by server-side preview API. */
const serverPreviewExtensions = new Set(["xls", "xlsx", "docx", "pptx", "parquet"])

type FileCategory =
  | "text"
  | "image"
  | "csv"
  | "pdf"
  | "video"
  | "audio"
  | "xlsx"
  | "docx"
  | "pptx"
  | "parquet"
  | null

function getFileCategoryByName(name: string, ext: string): FileCategory {
  if (name in filenameToLanguage) return "text"
  return getFileCategoryByExt(ext)
}

function getFileCategoryByExt(ext: string): FileCategory {
  if (ext in extensionToLanguage) return "text"
  if (imageExtensions.has(ext)) return "image"
  if (ext in csvExtensions) return "csv"
  if (pdfExtensions.has(ext)) return "pdf"
  if (videoExtensions.has(ext)) return "video"
  if (audioExtensions.has(ext)) return "audio"
  if (ext === "xls" || ext === "xlsx") return "xlsx"
  if (ext === "docx") return "docx"
  if (ext === "pptx") return "pptx"
  if (ext === "parquet") return "parquet"
  return null
}

const viewableExtensions = new Set([
  ...Object.keys(extensionToLanguage),
  ...imageExtensions,
  ...Object.keys(csvExtensions),
  ...pdfExtensions,
  ...videoExtensions,
  ...audioExtensions,
  ...serverPreviewExtensions,
])

/** Check whether a file can be viewed. */
export function isViewableFile(name: string): boolean {
  return name in filenameToLanguage || viewableExtensions.has(getExtension(name))
}

/** Check whether a file is CSV or TSV. */
export function isCsvTsvFile(name: string): boolean {
  return getExtension(name) in csvExtensions
}

const SIZE_UNLIMITED_CATEGORIES = new Set<FileCategory>([
  "image", "csv", "pdf", "video", "audio", "xlsx", "docx", "pptx", "parquet",
])

const WIDE_DIALOG_CATEGORIES = new Set<FileCategory>([
  "csv", "xlsx", "parquet", "video", "pdf", "pptx",
])

type FileViewerDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  entry: FilesystemEntry | null
  /** Returns a download URL for the given path. */
  getDownloadUrl: (path: string) => Promise<string>
  /** Server-side file preview (parquet, xlsx, xls, docx, pptx). */
  previewFile?: (
    path: string,
    options?: { sheet?: string; maxRows?: number },
  ) => Promise<unknown>
}

function formatSizeLimit(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(0)} MB`
}

export function FileViewerDialog({
  open,
  onOpenChange,
  entry,
  getDownloadUrl,
  previewFile,
}: FileViewerDialogProps) {
  const [content, setContent] = useState<string | null>(null)
  const [mediaUrl, setMediaUrl] = useState<string | null>(null)
  const [previewData, setPreviewData] = useState<unknown>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reset = useCallback(() => {
    setContent(null)
    setMediaUrl(null)
    setPreviewData(null)
    setIsLoading(false)
    setError(null)
  }, [])

  useEffect(() => {
    if (!open || !entry || entry.kind === "folder") {
      reset()
      return
    }

    const ext = getExtension(entry.name)
    const category = getFileCategoryByName(entry.name, ext)

    if (!category) {
      setError("Unsupported file format.")
      return
    }

    const sizeLimit = SIZE_UNLIMITED_CATEGORIES.has(category) ? null : MAX_VIEW_SIZE
    if (sizeLimit !== null && entry.size > sizeLimit) {
      setError(`Only files of ${formatSizeLimit(sizeLimit)} or smaller can be displayed.`)
      return
    }

    setIsLoading(true)
    setError(null)

    // Server-side preview types
    if (serverPreviewExtensions.has(ext) && previewFile) {
      previewFile(entry.key)
        .then((data) => {
          setPreviewData(data)
          setIsLoading(false)
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "Failed to load file")
          setIsLoading(false)
        })
      return
    }

    getDownloadUrl(entry.key)
      .then((url) => {
        if (category === "text") {
          return fetch(url)
            .then((res) => {
              if (!res.ok) throw new Error(`Failed to fetch file (${res.status})`)
              return res.text()
            })
            .then((text) => {
              setContent(text)
              setIsLoading(false)
            })
        }
        setMediaUrl(url)
        setIsLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load file")
        setIsLoading(false)
      })
  }, [open, entry, getDownloadUrl, previewFile, reset])

  if (!entry) return null

  const ext = getExtension(entry.name)
  const category = getFileCategoryByName(entry.name, ext)
  const language = filenameToLanguage[entry.name] ?? extensionToLanguage[ext] ?? "plaintext"
  const isWide = WIDE_DIALOG_CATEGORIES.has(category)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={`max-h-[85vh] flex flex-col ${isWide ? "sm:max-w-[1000px]" : "sm:max-w-[800px]"}`}>
        <DialogHeader>
          <DialogTitle className="truncate">{entry.name}</DialogTitle>
        </DialogHeader>

        <div className="flex-1 min-h-0 mt-2">
          {isLoading && (
            <div className="flex items-center justify-center h-[500px]">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {error && (
            <div className="flex items-center justify-center h-[200px]">
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          )}

          {!isLoading && !error && category === "image" && mediaUrl && (
            <div className="flex items-center justify-center overflow-auto max-h-[70vh]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={mediaUrl}
                alt={entry.name}
                className="max-w-full max-h-[70vh] object-contain rounded"
              />
            </div>
          )}

          {!isLoading && !error && category === "csv" && mediaUrl && (
            <CsvViewer url={mediaUrl} defaultDelimiter={csvExtensions[ext] ?? ","} />
          )}

          {!isLoading && !error && category === "pdf" && mediaUrl && (
            <PdfViewer url={mediaUrl} />
          )}

          {!isLoading && !error && category === "video" && mediaUrl && (
            <VideoViewer url={mediaUrl} extension={ext} />
          )}

          {!isLoading && !error && category === "audio" && mediaUrl && (
            <AudioViewer url={mediaUrl} extension={ext} fileName={entry.name} />
          )}

          {!isLoading && !error && category === "xlsx" && previewData != null && (
            <XlsxViewer
              data={previewData as never}
              entryKey={entry.key}
              previewFile={previewFile}
            />
          )}

          {!isLoading && !error && category === "docx" && previewData != null && (
            <DocxViewer data={previewData as never} />
          )}

          {!isLoading && !error && category === "pptx" && previewData != null && (
            <PptxViewer data={previewData as never} />
          )}

          {!isLoading && !error && category === "parquet" && previewData != null && (
            <ParquetViewer data={previewData as never} />
          )}

          {!isLoading && !error && category === "text" && content !== null && (
            <CodeViewer
              code={content}
              language={language}
              height={500}
              wordWrap="on"
              copyLabel={entry.name}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
