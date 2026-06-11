"use client"

/**
 * TipTap CommentEditor 가 만든 HTML 을 표시하는 읽기 전용 뷰어.
 *
 * - editor 와 동일한 prose 스타일 + 추가 노드 보조 utility (코드블록·표·
 *   체크리스트·이미지·인라인 코드의 백틱/bold 제거, 리스트 marker 색 등).
 * - 마운트/업데이트 시 ``<pre><code>`` 들에 ``hljs.highlightElement`` 를
 *   적용해 토큰 span 을 생성한다. TipTap 의 lowlight 출력이 ProseMirror
 *   decoration 으로만 그려지므로 직렬화된 HTML 에는 토큰이 빠져 있어
 *   viewer 측에서 다시 highlight 해 줘야 색이 입혀진다.
 */

import { useEffect, useRef } from "react"
import hljs from "highlight.js/lib/core"
import { common } from "lowlight"

import { cn } from "@workspace/ui/lib/utils"

// editor 의 lowlight 와 동일한 ``common`` 언어 셋을 highlight.js core 인스턴스에도
// 한 번만 등록. 같은 모듈을 import 한 다른 viewer 들과 공유된다.
for (const [name, value] of Object.entries(common as Record<string, unknown>)) {
  try {
    if (!hljs.listLanguages().includes(name)) {
      hljs.registerLanguage(name, value as Parameters<typeof hljs.registerLanguage>[1])
    }
  } catch {
    // 미지원 언어는 plain 으로 폴백.
  }
}

// editor·viewer 양쪽에서 동일하게 사용하는 prose 보강 스타일.
//   - 코드블록: ``.hljs`` 가 배경/토큰 색을 부여하므로 ``text-foreground`` 류
//     일괄 색은 부여하지 않음 (토큰 색이 묻히지 않게).
//   - 인라인 ``<code>``: prose 가 자동으로 붙이는 백틱(``""``) 및 bold 제거.
//   - blockquote: prose 가 자동으로 붙이는 따옴표 제거.
//   - 리스트 marker: 본문과 같은 ``text-foreground`` 로 통일.
//   - 표: border-collapse + 헤더 배경 + 폰트 14px 통일.
const VIEWER_PROSE_CLASS =
  "prose prose-sm max-w-none text-sm " +
  // Paragraph / 헤딩 / 리스트 / blockquote / 코드블록 간격을 editor 와 동일하게
  // 컴팩트하게 통일. prose-sm 의 기본 margin (1~1.7em) 이 viewer 만 적용돼 줄간격이
  // 들쭉날쭉해 보이던 문제 보정.
  "[&_p]:my-1 [&_h1]:mt-3 [&_h1]:mb-2 [&_h2]:mt-3 [&_h2]:mb-2 [&_h3]:mt-2 [&_h3]:mb-1 " +
  "[&_ul]:my-2 [&_ol]:my-2 [&_blockquote]:my-2 [&_pre]:my-2 " +
  // 코드블록
  "[&_pre]:bg-muted [&_pre]:p-3 [&_pre]:rounded [&_pre]:text-sm " +
  "[&_pre]:overflow-x-auto [&_pre]:whitespace-pre [&_pre]:font-[D2Coding,monospace] " +
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0 " +
  // 인라인 코드 — ``py-0.5`` 가 인라인 line-box 를 키워 그 줄만 들뜨던 문제 보정.
  // padding-y 는 0 으로 두고 padding-x 만 살짝 부여, 박스 시각화는 background+rounded 로.
  "[&_code]:font-[D2Coding,monospace] [&_code]:font-normal [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0 [&_code]:rounded [&_code]:text-sm " +
  "[&_code]:before:content-none [&_code]:after:content-none " +
  // blockquote
  "[&_blockquote]:before:content-none [&_blockquote]:after:content-none " +
  "[&_blockquote_p]:before:content-none [&_blockquote_p]:after:content-none " +
  // 리스트 marker
  "[&_ul]:marker:text-foreground [&_ol]:marker:text-foreground " +
  // 표
  "[&_table]:border-collapse [&_table]:my-2 [&_table]:text-sm " +
  "[&_table_th]:border [&_table_th]:bg-muted/50 [&_table_th]:px-2 [&_table_th]:py-1 " +
  "[&_table_td]:border [&_table_td]:px-2 [&_table_td]:py-1 " +
  // 체크리스트
  "[&_ul[data-type=taskList]]:list-none [&_ul[data-type=taskList]]:pl-0 " +
  "[&_li[data-type=taskItem]]:flex [&_li[data-type=taskItem]]:items-start [&_li[data-type=taskItem]]:gap-1 " +
  "[&_li[data-type=taskItem]>label]:mt-1 " +
  // 이미지
  "[&_img]:max-w-full [&_img]:h-auto [&_img]:rounded"

type RichTextViewerProps = {
  html: string
  /** 컨테이너에 추가 부여할 className (예: 클릭 핸들러용 cursor-pointer). */
  className?: string
  /** 클릭 핸들러 — 부모가 호버 시 편집 모드 진입 등을 처리할 때 사용. */
  onClick?: () => void
  /** ``title`` 속성 — 클릭 가능 안내 툴팁 등에 활용. */
  title?: string
}

export function RichTextViewer({ html, className, onClick, title }: RichTextViewerProps) {
  const ref = useRef<HTMLDivElement>(null)

  // 본문이 바뀔 때마다(또는 처음 마운트될 때) <pre><code> 들에 highlight 적용.
  // highlight.js 가 ``data-highlighted="yes"`` 를 붙여 더블 토큰화를 방지한다.
  useEffect(() => {
    if (!ref.current) return
    const codes = ref.current.querySelectorAll<HTMLElement>("pre code")
    codes.forEach((el) => {
      if (el.dataset.highlighted === "yes") return
      try {
        hljs.highlightElement(el)
      } catch {
        // 미등록 언어 등 — 그대로 둠.
      }
    })
  }, [html])

  return (
    <div
      ref={ref}
      className={cn(VIEWER_PROSE_CLASS, className)}
      onClick={onClick}
      title={title}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
