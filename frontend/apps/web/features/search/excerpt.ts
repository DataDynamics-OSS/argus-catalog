import { markdownToPlain, richTextToPlain } from "@/lib/plain-text"

/**
 * 검색 결과 한 줄 요약용 — 설명을 평문으로 바꾼다.
 *
 * 목록 화면처럼 요약(summary)을 우선하고, 없을 때만 설명(편집기 HTML · 마크다운)을 평문으로 바꿔 보여 준다.
 * 평문화는 공용 함수(lib/plain-text.ts)가 한다.
 */
export { markdownToPlain }

/** 데이터셋 검색 결과 · 카드의 둘째 줄: 요약 → (없으면) 설명 평문. */
export function datasetExcerpt(ds: { summary?: string | null; description?: string | null }): string {
  const summary = ds.summary?.trim()
  if (summary) return summary
  return richTextToPlain(ds.description)
}
