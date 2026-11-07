/**
 * 검색 결과 한 줄 요약용 — 마크다운 설명을 평문으로 바꾼다.
 *
 * 데이터셋 설명은 마크다운(`## 개요`, `**굵게**`, 표·목록)이라 그대로 한 줄로 자르면
 * "## 개요 DVD 대여점이 보유한 **영화 …" 처럼 기호가 보인다. 목록 화면처럼 요약(summary)을
 * 우선하고, 없을 때만 설명을 평문으로 바꿔 보여 준다.
 */
export function markdownToPlain(md: string | null | undefined): string {
  if (!md) return ""
  return md
    .replace(/```[\s\S]*?```/g, " ") // 코드 블록
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ") // 이미지
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // 링크 → 텍스트
    .replace(/^\s{0,3}#{1,6}\s+[^\n]*$/gm, " ") // 제목 줄(## 개요 등)은 통째로 뺀다
    .replace(/^\s*>\s?/gm, "") // 인용
    .replace(/^\s*([-*+]|\d+\.)\s+/gm, "") // 목록 기호
    .replace(/^\s*\|?\s*:?-{3,}.*$/gm, " ") // 표 구분선
    .replace(/\|/g, " ") // 표 칸
    .replace(/`([^`]*)`/g, "$1") // 인라인 코드
    .replace(/(\*\*|__|~~)(?=\S)([\s\S]*?\S)\1/g, "$2") // 굵게·취소선
    .replace(/(^|[^\w*])\*(?=\S)([^*]*?\S)\*(?!\w)/g, "$1$2") // 기울임(*)
    .replace(/(^|\W)_(?=\S)([^_]*?\S)_(?!\w)/g, "$1$2") // 기울임(_) — 식별자 안 밑줄은 유지
    .replace(/\s+/g, " ")
    .trim()
}

/** 데이터셋 검색 결과 둘째 줄: 요약 → (없으면) 설명 평문. */
export function datasetExcerpt(ds: { summary?: string | null; description?: string | null }): string {
  const summary = ds.summary?.trim()
  if (summary) return summary
  return markdownToPlain(ds.description)
}
