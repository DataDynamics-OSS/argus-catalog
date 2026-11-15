/**
 * 사이드바 브랜드(제품명 · 회사명) 링크 규칙 (TODO-145).
 *
 * - 주소가 비면 데이터셋 목록 카드 보기(`?view=card` — 데이터셋 목록이 이 값을 보기 방식으로 쓴다)
 * - `http(s)://` 는 다른 사이트 → 새 탭
 * - `/` 로 시작하면 이 화면 안 경로
 * 그 밖의 값(`javascript:` 등)은 서버가 저장을 거부하지만, 혹시 들어와도 기본 주소로 간다.
 */
export const DEFAULT_BRAND_HREF = "/dashboard/datasets?view=card"

export function brandLink(linkUrl: string | null | undefined): { href: string; external: boolean } {
  const url = (linkUrl ?? "").trim()
  if (/^https?:\/\//i.test(url)) return { href: url, external: true }
  if (url.startsWith("/") && !url.startsWith("//")) return { href: url, external: false }
  return { href: DEFAULT_BRAND_HREF, external: false }
}
