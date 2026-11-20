/**
 * 서식 있는 글(편집기 HTML · 마크다운) → 한 줄 평문.
 *
 * 목록 · 검색 결과 · 카드처럼 설명을 한두 줄로 보여 주는 자리에서 쓴다. 문자열 처리만 하므로(DOM 없음)
 * 서버 렌더 · 단위 테스트에서도 같은 결과다. 결과는 텍스트로만 렌더링한다(HTML 로 주입하지 않는다).
 */

// 리치 편집기(TipTap ``getHTML``)가 저장한 글은 항상 블록 태그로 시작한다
const RICH_HTML_START = /^\s*<(p|h[1-6]|ul|ol|pre|blockquote|table|div|hr|img)[\s/>]/i

/** 글이 리치 편집기가 저장한 HTML 인지 — 아니면 마크다운 · 평문(등록 창, AI 생성, API · 수집으로 들어온 값) */
export function isRichHtml(text: string | null | undefined): boolean {
  return !!text && RICH_HTML_START.test(text)
}

// 글자 사이에 끼는 태그 — 지워도 앞뒤 글자가 붙어야 한다(``<strong>굵</strong>게``). 그 밖의 태그는 경계를 공백으로 둔다.
const INLINE_TAGS = new Set([
  "a", "abbr", "b", "bdi", "bdo", "cite", "code", "del", "dfn", "em", "font", "i", "ins", "kbd", "mark", "q", "s",
  "samp", "small", "span", "strike", "strong", "sub", "sup", "time", "tt", "u", "var", "wbr",
])

// 마크다운 본문에 섞여 들어오는 HTML 태그 — 이 이름만 태그로 보고 지운다(``List<String>`` · ``<profile>`` 같은 글자는 남긴다)
const KNOWN_TAGS = new Set([
  ...INLINE_TAGS,
  "p", "br", "hr", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "pre", "blockquote",
  "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col", "img", "figure", "figcaption",
  "label", "input", "details", "summary", "section", "article", "center", "script", "style",
])

const HTML_ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", hellip: "…", middot: "·",
  lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", copy: "©", reg: "®", trade: "™", times: "×", rarr: "→", larr: "←",
}

/** HTML 엔티티(``&amp;`` · ``&#39;`` · ``&#x27;``)를 글자로 푼다. 모르는 이름은 그대로 둔다. */
function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]*);/gi, (m, ent: string) => {
    if (ent[0] === "#") {
      const code = ent[1]?.toLowerCase() === "x" ? Number.parseInt(ent.slice(2), 16) : Number.parseInt(ent.slice(1), 10)
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m
    }
    return HTML_ENTITIES[ent] ?? HTML_ENTITIES[ent.toLowerCase()] ?? m
  })
}

type StripOptions = {
  /** 제목(``<h1>``~``<h6>``)은 내용까지 뺀다 — 한 줄 요약에서 "개요" 같은 제목 대신 본문부터 보이게 */
  dropHeadings?: boolean
  /** 알려진 HTML 태그 이름만 지운다 — 마크다운 · 평문에 섞인 ``<br>`` 등은 지우고 ``<profile>`` 같은 글자는 남긴다 */
  knownTagsOnly?: boolean
}

/** 태그를 걷어내고 엔티티를 푼다. 블록 경계는 줄바꿈으로 남긴다(공백 정리는 호출하는 쪽). */
function stripTags(html: string, { dropHeadings = false, knownTagsOnly = false }: StripOptions): string {
  let text = html
    .replace(/<!--[\s\S]*?-->/g, " ")
    // ``<script>`` · ``<style>`` 은 내용까지 버린다(닫히지 않았으면 끝까지)
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, " ")
    .replace(/<(script|style)\b[^>]*>[\s\S]*$/gi, " ")
  if (dropHeadings) text = text.replace(/<(h[1-6])\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "\n")
  text = text.replace(/<\/?([a-z][a-z0-9-]*)\b[^>]*>/gi, (m, name: string) => {
    const tag = name.toLowerCase()
    if (knownTagsOnly && !KNOWN_TAGS.has(tag)) return m
    return INLINE_TAGS.has(tag) ? "" : "\n"
  })
  if (!knownTagsOnly) text = text.replace(/<![^>]*>/g, " ")
  return decodeEntities(text)
}

function collapse(text: string): string {
  return text.replace(/\s+/g, " ").trim()
}

/**
 * HTML → 한 줄 평문. 블록 태그 경계는 공백으로, 엔티티는 글자로, ``<script>`` · ``<style>`` 내용은 버린다.
 * 변경 관리 본문 · 댓글처럼 항상 편집기 HTML 인 값에 쓴다.
 */
export function htmlToPlain(html: string | null | undefined, options: Pick<StripOptions, "dropHeadings"> = {}): string {
  if (!html) return ""
  return collapse(stripTags(html, options))
}

/**
 * 마크다운 → 한 줄 평문.
 *
 * 데이터셋 설명은 마크다운(`## 개요`, `**굵게**`, 표·목록)이라 그대로 한 줄로 자르면
 * "## 개요 DVD 대여점이 보유한 **영화 …" 처럼 기호가 보인다. HTML 태그는 건드리지 않는다 —
 * 태그가 섞일 수 있는 값은 ``richTextToPlain`` 을 쓴다.
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

/**
 * 설명(리치 편집기 HTML · 마크다운 · 평문 어느 쪽이든) → 한 줄 평문.
 *
 * 데이터셋 설명은 개요 탭 편집기로 저장하면 HTML(``<h3>개요</h3><p>…``), 등록 창 · AI 생성 · 수집으로 들어오면
 * 마크다운 · 평문이다(DEF-192). 제목은 양쪽 모두 뺀다 — 제목뿐인 글이면 제목이라도 보여 준다.
 */
export function richTextToPlain(text: string | null | undefined): string {
  if (!text) return ""
  if (isRichHtml(text)) {
    return collapse(stripTags(text, { dropHeadings: true })) || collapse(stripTags(text, {}))
  }
  // 마크다운 · 평문 — 섞여 있는 HTML 태그(``<br>`` · ``<b>``)를 먼저 걷고 마크다운 기호를 뺀다
  const withoutTags = stripTags(text, { dropHeadings: true, knownTagsOnly: true })
  return markdownToPlain(withoutTags) || markdownToPlain(stripTags(text, { knownTagsOnly: true }).replace(/^\s{0,3}#{1,6}\s+/gm, ""))
}
