import DOMPurify from "dompurify"

/**
 * 저장된 리치 텍스트 HTML(댓글 · 설명 · 변경 관리 본문 · 원격 카탈로그 설명)을 화면에 주입하기 전에 정화한다.
 *
 * DOMPurify 기본 규칙으로 ``<script>``·``on*`` 핸들러·``javascript:`` URL·``<iframe>`` 을 걷어낸다.
 * ``<style>`` · ``<form>`` 은 DOMPurify 기본 허용이지만 편집기는 만들지 않는다 — 다른 카탈로그가 보낸
 * 설명(페더레이션 원격 상세)이 화면 전체 스타일을 바꾸거나 입력 양식을 끼워 넣지 못하게 막는다.
 * TipTap 출력(style 속성·data-*·data: 이미지·체크박스)은 유지한다. DOM 이 있어야 동작한다(브라우저 · jsdom).
 */
export function sanitizeRichHtml(html: string | null | undefined): string {
  return DOMPurify.sanitize(html ?? "", { FORBID_TAGS: ["style", "form"] })
}
