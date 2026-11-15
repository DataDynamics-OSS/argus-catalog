/**
 * 브라우저 탭 제목(TODO-147) — 서버에서 설정 > 사이드바 표시의 제품명을 읽는다.
 * 루트 레이아웃 `generateMetadata` 가 쓴다. 30초마다 다시 읽고, 백엔드가 없으면(빌드 때 등) 기본값.
 */
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:4600"
export const DEFAULT_TITLE = "Argus Catalog"

export async function brandTitle(): Promise<string> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/settings/branding/public`, {
      next: { revalidate: 30 },
      signal: AbortSignal.timeout(2000),
    })
    if (!res.ok) return DEFAULT_TITLE
    const body = (await res.json()) as { title?: string }
    return body.title?.trim() || DEFAULT_TITLE
  } catch {
    return DEFAULT_TITLE
  }
}
