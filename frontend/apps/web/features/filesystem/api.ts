import type {
  FilesystemDataSource,
  FilesystemFile,
  FilesystemFolder,
  ListDirectoryResponse,
} from "@/components/local-filesystem-browser"
import { authFetch } from "@/features/auth/auth-fetch" // Added for SSO AUTH

const API_BASE = "/api/v1/filesystem"

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authFetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || `API 오류 ${res.status}`)
  }
  return res.json()
}

/** Convert server folder response to UI type. */
function toFolder(f: {
  key: string
  name: string
  owner?: string
  group?: string
  permissions?: string
}): FilesystemFolder {
  return {
    kind: "folder",
    key: f.key,
    name: f.name,
    owner: f.owner,
    group: f.group,
    permissions: f.permissions,
  }
}

/** Convert server file response to UI type. */
function toFile(f: {
  key: string
  name: string
  size: number
  last_modified: string
  owner?: string
  group?: string
  permissions?: string
}): FilesystemFile {
  return {
    kind: "file",
    key: f.key,
    name: f.name,
    size: f.size,
    lastModified: f.last_modified,
    owner: f.owner,
    group: f.group,
    permissions: f.permissions,
  }
}

/** Append root_sub to URL params if provided. */
function withRootSub(params: URLSearchParams, rootSub?: string): URLSearchParams {
  if (rootSub) params.set("root_sub", rootSub)
  return params
}

/**
 * Create a FilesystemDataSource that talks to the catalog server.
 *
 * @param rootSub - Optional subdirectory of data_dir to scope the browser.
 *   e.g. "model-artifacts" for MLflow, "oci-artifacts" for OCI.
 */
export function createFilesystemDataSource(rootSub?: string): FilesystemDataSource {
  return {
    async listDirectory(path: string): Promise<ListDirectoryResponse> {
      const params = withRootSub(new URLSearchParams({ path }), rootSub)
      const data = await apiFetch<{
        folders: Array<{
          key: string
          name: string
          owner?: string
          group?: string
          permissions?: string
        }>
        files: Array<{
          key: string
          name: string
          size: number
          last_modified: string
          owner?: string
          group?: string
          permissions?: string
        }>
        current_path: string
      }>(`${API_BASE}/list?${params}`)

      return {
        folders: data.folders.map(toFolder),
        files: data.files.map(toFile),
        currentPath: data.current_path,
      }
    },

    async deletePaths(paths: string[]): Promise<void> {
      const params = withRootSub(new URLSearchParams(), rootSub)
      await apiFetch(`${API_BASE}/delete?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      })
    },

    async createFolder(path: string): Promise<void> {
      const params = withRootSub(new URLSearchParams(), rootSub)
      await apiFetch(`${API_BASE}/folders?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      })
    },

    async uploadFiles(directoryPath: string, files: File[]): Promise<void> {
      for (const file of files) {
        const formData = new FormData()
        formData.append("file", file)
        const params = withRootSub(new URLSearchParams({ path: directoryPath }), rootSub)
        await apiFetch(`${API_BASE}/upload?${params}`, {
          method: "POST",
          body: formData,
        })
      }
    },

    async getDownloadUrl(path: string): Promise<string> {
      const params = withRootSub(new URLSearchParams({ path }), rootSub)
      return `${API_BASE}/download?${params}`
    },

    async renamePath(sourcePath: string, destinationPath: string): Promise<void> {
      const params = withRootSub(new URLSearchParams(), rootSub)
      await apiFetch(`${API_BASE}/rename?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_path: sourcePath,
          destination_path: destinationPath,
        }),
      })
    },

    async previewFile(
      path: string,
      options?: { sheet?: string; maxRows?: number },
    ): Promise<unknown> {
      const params = withRootSub(new URLSearchParams({ path }), rootSub)
      if (options?.sheet) params.set("sheet", options.sheet)
      if (options?.maxRows) params.set("max_rows", String(options.maxRows))
      return apiFetch(`${API_BASE}/preview?${params}`)
    },
  }
}

// ---------------------------------------------------------------------------
// S3 Bucket Browser (for OCI Model Files)
// ---------------------------------------------------------------------------

const S3_BROWSE_BASE = "/api/v1/model-store/browse"

/**
 * Create a FilesystemDataSource that browses S3 bucket via model-store API.
 * Used for OCI Model Files page.
 */
export function createS3DataSource(): FilesystemDataSource {
  return {
    async listDirectory(path: string): Promise<ListDirectoryResponse> {
      const params = new URLSearchParams({ path })
      const data = await apiFetch<{
        folders: Array<{ key: string; name: string; owner?: string; group?: string; permissions?: string }>
        files: Array<{ key: string; name: string; size: number; last_modified: string; owner?: string; group?: string; permissions?: string }>
        current_path: string
      }>(`${S3_BROWSE_BASE}/list?${params}`)
      return {
        folders: data.folders.map(toFolder),
        files: data.files.map(toFile),
        currentPath: data.current_path,
      }
    },

    async deletePaths(): Promise<void> {
      // S3 delete not supported from browser
      throw new Error("브라우저에서는 S3 객체 삭제를 지원하지 않습니다.")
    },

    async createFolder(): Promise<void> {
      throw new Error("브라우저에서는 S3 폴더 생성을 지원하지 않습니다.")
    },

    async uploadFiles(): Promise<void> {
      throw new Error("브라우저에서는 업로드를 지원하지 않습니다. SDK 또는 API를 사용하세요.")
    },

    async getDownloadUrl(path: string): Promise<string> {
      const params = new URLSearchParams({ path })
      const data = await apiFetch<{ url: string }>(`${S3_BROWSE_BASE}/download?${params}`)
      return data.url
    },

    async renamePath(): Promise<void> {
      throw new Error("브라우저에서는 S3 이름 변경을 지원하지 않습니다.")
    },

    async previewFile(): Promise<unknown> {
      throw new Error("S3 파일 미리보기는 지원하지 않습니다.")
    },
  }
}
