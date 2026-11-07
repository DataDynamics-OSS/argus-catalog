"use client"

import { errorMessage, notifyError } from "@/lib/notify"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, RefreshCw, Upload } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"

import type {
  FilesystemDataSource,
  FilesystemEntry,
  FilesystemFile,
  FilesystemFolder,
  SortConfig,
} from "./types"
import { useAuth } from "@/features/auth"
import { BrowserBreadcrumb } from "./browser-breadcrumb"
import { BrowserToolbar } from "./browser-toolbar"
import { BrowserTable, type EntryContextAction } from "./browser-table"
import { CreateFolderDialog } from "./create-folder-dialog"
import { UploadDialog } from "./upload-dialog"
import { DeleteDialog } from "./delete-dialog"
import { PropertiesDialog } from "./properties-dialog"
import { RenameDialog } from "./rename-dialog"
import { UploadProgressDialog, type FileUploadStatus } from "./upload-progress-dialog"
import { FileViewerDialog, isViewableFile } from "./file-viewer-dialog"
import { CatViewerDialog } from "./cat-viewer-dialog"
import { entryId } from "./utils"

type LocalFilesystemBrowserProps = {
  /** Initial path to browse. Defaults to "/". */
  initialPath?: string
  /** Data source callbacks. */
  dataSource: FilesystemDataSource
  /** Optional CSS class for the root container. */
  className?: string
  /** 읽기 전용 — 업로드·폴더 생성·삭제·이름 변경을 숨긴다 (관리자라도). */
  readOnly?: boolean
  /** 최상위 경로가 비어 있을 때 표 대신 보여 줄 안내. */
  emptyState?: React.ReactNode
  /** 이 경로 위로는 이동할 수 없다(일반 사용자가 보는 모델 폴더 등). 없으면 "/" 까지. */
  rootPath?: string
}

/** fetch 의 네트워크 오류("Failed to fetch")는 사용자에게 뜻이 통하는 문구로 바꾼다. */
function downloadErrorMessage(err: unknown): string {
  if (err instanceof TypeError) return "서버 또는 저장소에 연결하지 못했습니다."
  return errorMessage(err) ?? "알 수 없는 오류"
}

export function LocalFilesystemBrowser({
  initialPath = "/",
  dataSource,
  className,
  readOnly = false,
  emptyState,
  rootPath,
}: LocalFilesystemBrowserProps) {
  // 파일 변경 작업(업로드/생성/삭제/이름변경)은 관리자만 — MinIO 사용자 연동이
  // 없어 스토리지 단 권한을 못 거는 대신, 파일 브라우저 진입점을 admin 으로 통제.
  const { user } = useAuth()
  const canManage = !readOnly && !!user?.is_admin
  // --- Navigation state ---
  const [currentPath, setCurrentPath] = useState(initialPath)
  const currentPathRef = useRef(currentPath)
  currentPathRef.current = currentPath
  const [, setNavigationHistory] = useState<string[]>([])

  // --- Data state ---
  const [folders, setFolders] = useState<FilesystemFolder[]>([])
  const [files, setFiles] = useState<FilesystemFile[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  // --- UI state ---
  const [searchValue, setSearchValue] = useState("")
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set())
  const [sort, setSort] = useState<SortConfig>({ column: "name", direction: "asc" })

  // --- Dialog state ---
  const [createFolderOpen, setCreateFolderOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [dialogLoading, setDialogLoading] = useState(false)

  // --- Properties dialog state ---
  const [propertiesEntry, setPropertiesEntry] = useState<FilesystemEntry | null>(null)
  const [propertiesOpen, setPropertiesOpen] = useState(false)

  // --- File viewer dialog state ---
  const [viewerEntry, setViewerEntry] = useState<FilesystemEntry | null>(null)
  const [viewerOpen, setViewerOpen] = useState(false)

  // --- Cat viewer dialog state ---
  const [catViewerEntry, setCatViewerEntry] = useState<FilesystemEntry | null>(null)
  const [catViewerOpen, setCatViewerOpen] = useState(false)

  // --- Context menu dialog state ---
  const [contextEntry, setContextEntry] = useState<FilesystemEntry | null>(null)
  const [renameOpen, setRenameOpen] = useState(false)
  const [contextDeleteOpen, setContextDeleteOpen] = useState(false)

  // --- Drag-and-drop state ---
  const [isDragOver, setIsDragOver] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<FileUploadStatus[]>([])
  const [uploadProgressOpen, setUploadProgressOpen] = useState(false)
  const uploadNeedsRefresh = useRef(false)

  // --- Data fetching ---
  const fetchData = useCallback(
    async (targetPath: string) => {
      setIsLoading(true)
      setLoadError(null)
      try {
        const response = await dataSource.listDirectory(targetPath)
        setFolders(response.folders)
        setFiles(response.files)
      } catch (err) {
        // 오브젝트 스토리지(MinIO) 연결 실패 등 — 페이지를 깨뜨리지 않고
        // 화면 경고 배너로 안내하고 빈 목록을 보여준다. 의도된 처리라 warn 으로
        // 둔다 (console.error 면 Next dev 오버레이가 가로채 빨간 에러 화면을 띄움).
        console.warn("Failed to list directory", { targetPath, err })
        const raw = errorMessage(err) ?? String(err)
        const friendly = /connect|endpoint|ECONN|fetch failed|network|9000/i.test(raw)
          ? "스토리지에 연결할 수 없습니다. 오브젝트 스토리지(MinIO)가 실행 중인지, 설정 > 오브젝트 스토리지를 확인하세요."
          : raw
        setLoadError(friendly)
        setFolders([])
        setFiles([])
      } finally {
        setIsLoading(false)
      }
    },
    [dataSource],
  )

  // Fetch on path change
  useEffect(() => {
    setSelectedKeys(new Set())
    setSearchValue("")
    fetchData(currentPath)
  }, [currentPath, fetchData])

  // --- Navigation ---
  function navigateTo(targetPath: string) {
    setNavigationHistory((prev) => [...prev, currentPath])
    setCurrentPath(targetPath)
  }

  // --- Sorting & filtering ---
  const entries = useMemo(() => {
    const all: FilesystemEntry[] = [...folders, ...files]

    // Filter
    const filtered = searchValue
      ? all.filter((e) =>
          e.name.toLowerCase().includes(searchValue.toLowerCase()),
        )
      : all

    // Sort: folders always first, then sort within each group
    const sortedFolders = filtered
      .filter((e): e is FilesystemFolder => e.kind === "folder")
      .sort((a, b) => {
        if (sort.column === "name") {
          const cmp = a.name.localeCompare(b.name)
          return sort.direction === "asc" ? cmp : -cmp
        }
        return 0
      })

    const sortedFiles = filtered
      .filter((e): e is FilesystemFile => e.kind === "file")
      .sort((a, b) => {
        let cmp = 0
        switch (sort.column) {
          case "name":
            cmp = a.name.localeCompare(b.name)
            break
          case "size":
            cmp = a.size - b.size
            break
          case "lastModified":
            cmp = new Date(a.lastModified).getTime() - new Date(b.lastModified).getTime()
            break
        }
        return sort.direction === "asc" ? cmp : -cmp
      })

    return [...sortedFolders, ...sortedFiles]
  }, [folders, files, searchValue, sort])

  /** Find an entry by its entryId (kind:key). */
  function findEntryById(id: string): FilesystemEntry | undefined {
    return entries.find((e) => entryId(e.kind, e.key) === id)
  }

  /** Extract real paths from selectedKeys (entryId set). */
  function selectedRealPaths(): string[] {
    return Array.from(selectedKeys).map((id) => {
      const colonIdx = id.indexOf(":")
      return colonIdx >= 0 ? id.slice(colonIdx + 1) : id
    })
  }

  // --- Actions ---
  async function handleCreateFolder(folderName: string) {
    setDialogLoading(true)
    try {
      const newPath = currentPath.endsWith("/")
        ? `${currentPath}${folderName}`
        : `${currentPath}/${folderName}`
      await dataSource.createFolder(newPath)
      setCreateFolderOpen(false)
      await fetchData(currentPath)
    } catch (err) {
      console.warn("Failed to create folder", { currentPath, folderName, err })
      notifyError(err, "폴더를 만들지 못했습니다.")
    } finally {
      setDialogLoading(false)
    }
  }

  async function handleUpload(uploadedFiles: File[]) {
    setDialogLoading(true)
    try {
      await dataSource.uploadFiles(currentPath, uploadedFiles)
      setUploadOpen(false)
      await fetchData(currentPath)
    } catch (err) {
      console.warn("Failed to upload files", { currentPath, err })
      notifyError(err, "파일을 업로드하지 못했습니다.")
    } finally {
      setDialogLoading(false)
    }
  }

  async function handleDelete() {
    setDialogLoading(true)
    try {
      await dataSource.deletePaths(selectedRealPaths())
      setDeleteOpen(false)
      setSelectedKeys(new Set())
      await fetchData(currentPath)
    } catch (err) {
      // 예: 등록된 모델의 파일은 서버가 409 로 거부 — 사유를 그대로 보여준다
      console.warn("Failed to delete paths", { err })
      notifyError(err, "선택한 항목을 삭제하지 못했습니다.")
    } finally {
      setDialogLoading(false)
    }
  }

  /** 링크를 눌러 저장하게 한다(같은 출처 주소라 download 속성의 파일 이름이 적용된다). */
  function clickLink(href: string, name: string) {
    const a = document.createElement("a")
    a.href = href
    a.download = name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  /** 파일 하나를 내려받는다. 실패하면 사유를 알림으로 보여 주고 false 를 돌려준다. */
  async function downloadFile(path: string): Promise<boolean> {
    const name = path.split("/").filter(Boolean).pop() ?? "download"
    try {
      if (dataSource.getDirectDownloadUrl) {
        // 서버가 흘려보내는 주소를 브라우저가 바로 저장 — 큰 파일도 메모리에 올리지 않는다
        clickLink(await dataSource.getDirectDownloadUrl(path), name)
      } else {
        const url = await dataSource.getDownloadUrl(path)
        const res = await fetch(url)
        if (!res.ok) throw new Error(`파일을 받지 못했습니다 (HTTP ${res.status})`)
        const blobUrl = URL.createObjectURL(new Blob([await res.blob()], { type: "application/octet-stream" }))
        if (url.startsWith("blob:")) URL.revokeObjectURL(url)
        clickLink(blobUrl, name)
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
      }
      return true
    } catch (err) {
      console.warn("Failed to download file", { path, err })
      notifyError(downloadErrorMessage(err), `'${name}' 파일을 내려받지 못했습니다.`)
      return false
    }
  }

  async function handleDownload() {
    const filePaths = selectedRealPaths().filter((k) => !k.endsWith("/"))
    for (const [i, path] of filePaths.entries()) {
      // 여러 파일을 한 번에 누르면 브라우저가 연속 다운로드를 막을 수 있어 조금씩 띄운다
      if (i > 0) await new Promise((r) => setTimeout(r, 400))
      await downloadFile(path)
    }
  }

  // --- Double-click ---
  function handleEntryDoubleClick(entry: FilesystemEntry) {
    if (entry.kind === "folder") {
      navigateTo(entry.key)
      return
    }
    setPropertiesEntry(entry)
    setPropertiesOpen(true)
  }

  // --- Context menu actions ---
  function handleContextAction(action: EntryContextAction, entry: FilesystemEntry) {
    setContextEntry(entry)
    switch (action) {
      case "rename":
        setRenameOpen(true)
        break
      case "delete":
        setSelectedKeys(new Set([entryId(entry.kind, entry.key)]))
        setContextDeleteOpen(true)
        break
      case "properties":
        setPropertiesEntry(entry)
        setPropertiesOpen(true)
        break
      case "view":
        setViewerEntry(entry)
        setViewerOpen(true)
        break
      case "view-cat":
        setCatViewerEntry(entry)
        setCatViewerOpen(true)
        break
      case "download":
        downloadFile(entry.key)
        break
    }
  }

  async function handleRename(newName: string) {
    if (!contextEntry || !dataSource.renamePath) return
    setDialogLoading(true)
    try {
      const oldPath = contextEntry.key
      const isFolder = contextEntry.kind === "folder"
      // Remove trailing slash for folder, then get parent
      const cleanPath = isFolder ? oldPath.slice(0, -1) : oldPath
      const parentDir = cleanPath.substring(0, cleanPath.lastIndexOf("/") + 1)
      const newPath = parentDir + newName + (isFolder ? "/" : "")

      await dataSource.renamePath(oldPath.replace(/\/$/, ""), newPath.replace(/\/$/, ""))
      setRenameOpen(false)
      // 예전 경로가 선택에 남으면 이어지는 삭제가 없어진 경로까지 보낸다("폴더 2개") — 선택을 비운다
      setSelectedKeys(new Set())
      await fetchData(currentPathRef.current)
    } catch (err) {
      console.warn("Failed to rename path", { err })
      notifyError(err, "이름을 바꾸지 못했습니다.")
    } finally {
      setDialogLoading(false)
    }
  }

  async function handleContextDelete() {
    setDialogLoading(true)
    try {
      await dataSource.deletePaths(selectedRealPaths())
      setContextDeleteOpen(false)
      setSelectedKeys(new Set())
      await fetchData(currentPathRef.current)
    } catch (err) {
      console.warn("Failed to delete paths", { err })
      notifyError(err, "선택한 항목을 삭제하지 못했습니다.")
    } finally {
      setDialogLoading(false)
    }
  }

  // --- Drag-and-drop upload with progress ---
  function handleDragOver(e: React.DragEvent) {
    if (!canManage) return
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragOver(false)
    }
  }

  async function handleDrop(e: React.DragEvent) {
    if (!canManage) return
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)

    const droppedFiles = Array.from(e.dataTransfer.files)
    if (droppedFiles.length === 0) return

    const uploadDir = currentPathRef.current

    const initialStatus: FileUploadStatus[] = droppedFiles.map((file) => ({
      file,
      progress: 0,
      status: "pending",
    }))
    setUploadProgress(initialStatus)
    setUploadProgressOpen(true)
    uploadNeedsRefresh.current = true

    const uploadFn = dataSource.uploadFileWithProgress

    for (let i = 0; i < droppedFiles.length; i++) {
      setUploadProgress((prev) =>
        prev.map((item, idx) =>
          idx === i ? { ...item, status: "uploading" } : item,
        ),
      )

      try {
        if (uploadFn) {
          await uploadFn(uploadDir, droppedFiles[i]!, (progress) => {
            setUploadProgress((prev) =>
              prev.map((item, idx) =>
                idx === i ? { ...item, progress } : item,
              ),
            )
          })
        } else {
          await dataSource.uploadFiles(uploadDir, [droppedFiles[i]!])
        }

        setUploadProgress((prev) =>
          prev.map((item, idx) =>
            idx === i ? { ...item, progress: 100, status: "done" } : item,
          ),
        )
      } catch (err) {
        setUploadProgress((prev) =>
          prev.map((item, idx) =>
            idx === i
              ? {
                  ...item,
                  status: "error",
                  error: errorMessage(err) ?? "업로드하지 못했습니다.",
                }
              : item,
          ),
        )
      }
    }
  }

  function handleUploadProgressClose(open: boolean) {
    setUploadProgressOpen(open)
    if (!open && uploadNeedsRefresh.current) {
      uploadNeedsRefresh.current = false
      fetchData(currentPathRef.current)
    }
  }

  return (
    <div
      className={cn("relative", className)}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm border-2 border-dashed border-primary rounded-lg">
          <div className="flex flex-col items-center gap-3 text-primary">
            <Upload className="h-12 w-12" />
            <p className="text-lg font-medium">파일을 놓아 업로드</p>
            <p className="text-sm text-muted-foreground">
              파일은 현재 디렉터리에 업로드됩니다
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {/* Breadcrumb */}
        <BrowserBreadcrumb
          currentPath={currentPath}
          rootPath={rootPath}
          onNavigate={(p) => {
            setNavigationHistory([])
            setCurrentPath(p)
          }}
        />

        {/* Toolbar */}
        <BrowserToolbar
          searchValue={searchValue}
          onSearchChange={setSearchValue}
          selectedCount={selectedKeys.size}
          onUpload={() => setUploadOpen(true)}
          onCreateFolder={() => setCreateFolderOpen(true)}
          onDelete={() => setDeleteOpen(true)}
          onDownload={handleDownload}
          onRename={() => {
            const id = Array.from(selectedKeys)[0]!
            const entry = findEntryById(id)
            if (entry) {
              setContextEntry(entry)
              setRenameOpen(true)
            }
          }}
          onProperties={() => {
            const id = Array.from(selectedKeys)[0]!
            const entry = findEntryById(id)
            if (entry) {
              setPropertiesEntry(entry)
              setPropertiesOpen(true)
            }
          }}
          onView={() => {
            const id = Array.from(selectedKeys)[0]!
            const entry = findEntryById(id)
            if (entry) {
              setViewerEntry(entry)
              setViewerOpen(true)
            }
          }}
          viewDisabled={(() => {
            if (selectedKeys.size !== 1) return true
            const id = Array.from(selectedKeys)[0]!
            const entry = findEntryById(id)
            return !entry || entry.kind === "folder" || !isViewableFile(entry.name)
          })()}
          onRefresh={() => fetchData(currentPath)}
          isLoading={isLoading}
          canManage={canManage}
        />

        {/* 스토리지 연결 실패 등 — 친절한 배너 (페이지 크래시 대신) */}
        {loadError && (
          <div className="flex items-start gap-2.5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-700 dark:bg-amber-950/40">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
            <div className="flex-1">
              <p className="font-medium text-amber-900 dark:text-amber-200">목록을 불러오지 못했습니다.</p>
              <p className="mt-0.5 text-amber-800 dark:text-amber-300">{loadError}</p>
            </div>
            <button
              type="button"
              onClick={() => fetchData(currentPath)}
              className="inline-flex items-center gap-1 rounded-md border border-amber-400 px-2 py-1 text-xs text-amber-800 hover:bg-amber-100 dark:text-amber-200 dark:hover:bg-amber-900/40"
            >
              <RefreshCw className="h-3 w-3" /> 다시 시도
            </button>
          </div>
        )}

        {/* Table — 최상위가 비어 있으면 호출 측 안내를 대신 보여 준다 */}
        {emptyState && !isLoading && !loadError && !searchValue && currentPath === "/" && folders.length + files.length === 0 ? (
          <div className="rounded-lg border border-dashed px-6 py-12">{emptyState}</div>
        ) : (
        <BrowserTable
          currentPath={currentPath}
          entries={entries}
          totalEntryCount={folders.length + files.length}
          selectedKeys={selectedKeys}
          onSelectionChange={setSelectedKeys}
          onFolderOpen={navigateTo}
          onEntryDoubleClick={handleEntryDoubleClick}
          onContextAction={handleContextAction}
          canManage={canManage}
          sort={sort}
          onSortChange={setSort}
          isLoading={isLoading}
        />
        )}

        {/* Status bar */}
        <div className="flex items-center justify-between text-sm text-muted-foreground px-1">
          <span>
            {folders.length} 폴더, {files.length} 파일
            {searchValue && ` (filtered: ${entries.length})`}
          </span>
        </div>
      </div>

      {/* Dialogs */}
      <CreateFolderDialog
        open={createFolderOpen}
        onOpenChange={setCreateFolderOpen}
        currentPath={currentPath}
        onConfirm={handleCreateFolder}
        isLoading={dialogLoading}
      />
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        currentPath={currentPath}
        onConfirm={handleUpload}
        isLoading={dialogLoading}
      />
      <DeleteDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        selectedPaths={selectedRealPaths()}
        onConfirm={handleDelete}
        isLoading={dialogLoading}
      />
      <PropertiesDialog
        open={propertiesOpen}
        onOpenChange={setPropertiesOpen}
        entry={propertiesEntry}
      />
      <UploadProgressDialog
        open={uploadProgressOpen}
        onOpenChange={handleUploadProgressClose}
        items={uploadProgress}
      />
      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        currentName={contextEntry?.name ?? ""}
        onConfirm={handleRename}
        isLoading={dialogLoading}
      />
      <DeleteDialog
        open={contextDeleteOpen}
        onOpenChange={setContextDeleteOpen}
        selectedPaths={contextEntry ? [contextEntry.key] : []}
        onConfirm={handleContextDelete}
        isLoading={dialogLoading}
      />
      <FileViewerDialog
        open={viewerOpen}
        onOpenChange={setViewerOpen}
        entry={viewerEntry}
        getDownloadUrl={(path) => dataSource.getDownloadUrl(path)}
        previewFile={
          dataSource.previewFile
            ? (path, options) => dataSource.previewFile!(path, options)
            : undefined
        }
      />
      <CatViewerDialog
        open={catViewerOpen}
        onOpenChange={setCatViewerOpen}
        entry={catViewerEntry}
        getDownloadUrl={(path) => dataSource.getDownloadUrl(path)}
      />
    </div>
  )
}
