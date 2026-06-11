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

  /** Get a download URL for a given path. */
  getDownloadUrl: (path: string) => Promise<string>

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
