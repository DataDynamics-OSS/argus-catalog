/** Format bytes to human-readable string. */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B"
  const units = ["B", "KB", "MB", "GB", "TB", "PB"]
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const value = bytes / Math.pow(1024, i)
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** ISO 날짜 문자열을 ``yyyy-MM-dd HH:mm:ss`` (로컬 timezone) 형식으로 변환. */
export function formatDate(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** Extract the display name from a full path. */
export function getNameFromPath(path: string): string {
  const trimmed = path.endsWith("/") ? path.slice(0, -1) : path
  const parts = trimmed.split("/")
  return parts[parts.length - 1] || path
}

/**
 * Build a unique identifier for a filesystem entry.
 * Folder and file with the same name could coexist (unlikely on Linux, but for consistency).
 */
export function entryId(kind: "folder" | "file", key: string): string {
  return `${kind}:${key}`
}

/** Get the file extension from a filename. */
export function getExtension(name: string): string {
  const i = name.lastIndexOf(".")
  return i > 0 ? name.slice(i + 1).toLowerCase() : ""
}

/** Get a category for an extension (for icon mapping). */
export function getFileCategory(
  name: string,
): "image" | "video" | "audio" | "archive" | "code" | "document" | "text" | "data" | "generic" {
  const ext = getExtension(name)
  if (["jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "tiff"].includes(ext)) return "image"
  if (["mp4", "avi", "mov", "mkv", "webm", "flv"].includes(ext)) return "video"
  if (["mp3", "wav", "ogg", "flac", "aac", "wma"].includes(ext)) return "audio"
  if (["zip", "tar", "gz", "bz2", "7z", "rar", "xz"].includes(ext)) return "archive"
  if (["js", "ts", "tsx", "jsx", "py", "java", "go", "rs", "c", "cpp", "h", "rb", "sh"].includes(ext)) return "code"
  if (["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "hwp"].includes(ext)) return "document"
  if (["txt", "md", "rst", "log"].includes(ext)) return "text"
  if (["json", "xml", "yaml", "yml", "csv", "tsv", "parquet", "avro"].includes(ext)) return "data"
  return "generic"
}
