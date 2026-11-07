/** Represents a directory in the local filesystem. */
export type FilesystemFolder = {
  kind: "folder"
  /** Absolute path (with trailing /). */
  key: string
  /** Display name (basename). */
  name: string
  /** Owner of the directory. */
  owner?: string
  /** Group of the directory. */
  group?: string
  /** Permission string (e.g. rwxr-xr-x). */
  permissions?: string
}

/** Represents a file in the local filesystem. */
export type FilesystemFile = {
  kind: "file"
  /** Absolute path. */
  key: string
  /** Display name (basename). */
  name: string
  /** Size in bytes. */
  size: number
  /** Last modified timestamp (ISO 8601). */
  lastModified: string
  /** Owner of the file. */
  owner?: string
  /** Group of the file. */
  group?: string
  /** Permission string (e.g. rw-r--r--). */
  permissions?: string
}

/** Union type for items displayed in the browser table. */
export type FilesystemEntry = FilesystemFolder | FilesystemFile

/** Response shape returned by the list API. */
export type ListDirectoryResponse = {
  /** Directories at this level. */
  folders: FilesystemFolder[]
  /** Files at this level. */
  files: FilesystemFile[]
  /** Absolute path of the listed directory. */
  currentPath: string
}

/** Progress callback for a single file upload. */
export type UploadProgressCallback = (
  fileIndex: number,
  progress: number,
) => void

/** Callbacks that the browser delegates to the parent / data layer. */
export type FilesystemDataSource = {
  /** List files and directories under a given path. */
  listDirectory: (path: string) => Promise<ListDirectoryResponse>

  /** Delete one or more paths. */
  deletePaths: (paths: string[]) => Promise<void>

  /** Create a directory. */
  createFolder: (path: string) => Promise<void>

  /** Upload files to the given directory. Returns when complete. */
  uploadFiles: (directoryPath: string, files: File[]) => Promise<void>

  /**
   * Upload a single file with progress reporting.
   * If provided, the browser uses this for drag-and-drop uploads.
   */
  uploadFileWithProgress?: (
    directoryPath: string,
    file: File,
    onProgress: (progress: number) => void,
  ) => Promise<void>

  /**
   * 보기(미리보기)용 주소 — 화면이 fetch·<img src> 로 읽는다(로그인 필요한 API 는 blob: 주소로 돌려준다).
   * 크기 제한이 있는 보기 전용이다. 내려받기에는 getDirectDownloadUrl 을 쓴다.
   */
  getDownloadUrl: (path: string) => Promise<string>

  /**
   * 내려받기용 주소 — 브라우저가 링크로 바로 저장하도록 짧은 다운로드 토큰을 붙인 주소.
   * 파일을 메모리에 올리지 않아 수 GB 모델 파일도 내려받을 수 있다. 없으면 getDownloadUrl 로 대신한다.
   */
  getDirectDownloadUrl?: (path: string) => Promise<string>

  /** Rename/move a path. */
  renamePath?: (sourcePath: string, destinationPath: string) => Promise<void>

  /** Server-side file preview (parquet, xlsx, xls, docx, pptx). */
  previewFile?: (
    path: string,
    options?: { sheet?: string; maxRows?: number },
  ) => Promise<unknown>
}

/** Sort direction. */
export type SortDirection = "asc" | "desc"

/** Sort configuration. */
export type SortConfig = {
  column: "name" | "size" | "lastModified"
  direction: SortDirection
}
