/** MLflow 모델 · OCI 모델 허브 공통 표시 형식. */

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "-"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  return `${(bytes / 1024 ** 4).toFixed(1)} TB`
}

/** 차트 축 라벨용 약식 크기 — 소수점 없이 "512M / 2G" 식. */
export function formatBytesAxis(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)}B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)}K`
  if (bytes < 1024 ** 3) return `${Math.round(bytes / 1024 ** 2)}M`
  if (bytes < 1024 ** 4) return `${Math.round(bytes / 1024 ** 3)}G`
  return `${Math.round(bytes / 1024 ** 4)}T`
}

/** ISO → `yyyy-MM-dd HH:mm` (로컬 시간대). 공백 구분 naive 값은 UTC 로 해석한다. */
export function formatDateTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return ""
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(iso) ? iso.replace(" ", "T") + "Z" : iso
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` +
    (withSeconds ? `:${pad(d.getSeconds())}` : "")
  )
}

/** `yyyy-MM-dd` (로컬 시간대). */
export function formatDate(iso: string | null | undefined): string {
  return formatDateTime(iso).slice(0, 10)
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "-"
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return "방금"
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}일 전`
  if (days < 365) return `${Math.floor(days / 30)}개월 전`
  return `${Math.floor(days / 365)}년 전`
}

/** User-Agent 를 사람이 읽을 클라이언트 종류로 요약한다. */
export function clientKind(ua: string | null | undefined): string {
  if (!ua) return "알 수 없음"
  const s = ua.toLowerCase()
  if (s.includes("argus") || s.includes("python-httpx")) return "Argus SDK·CLI"
  if (s.includes("mlflow")) return "MLflow 클라이언트"
  if (s.includes("python-requests") || s.includes("python")) return "Python"
  if (s.includes("curl")) return "curl"
  if (s.includes("mozilla")) return "웹 브라우저"
  return ua.split(/[\s/]/)[0] || "기타"
}
