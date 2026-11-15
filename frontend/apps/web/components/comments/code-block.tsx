"use client"

/**
 * 코드 블록 NodeView — CodeBlockLowlight(문법 강조)에 줄 번호 gutter 와 hover 시 나타나는
 * 언어 선택(select)을 얹는다.
 *
 * 저장 HTML 은 기본 renderHTML 그대로(``<pre class="hljs"><code class="language-xx">``) — gutter 는
 * 편집기(여기)와 뷰어(rich-text-viewer.tsx 의 ``decorateCodeBlock``)가 각자 렌더 시점에 붙인다.
 * 구조·클래스(``pre.code-block > .code-gutter + code``)가 양쪽이 같아 highlight-styles.css 한 벌로 스타일링한다.
 */

import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight"
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react"
import type { NodeViewProps } from "@tiptap/react"

import { LANGUAGE_OPTIONS } from "./lowlight"

/** 1..n 을 줄바꿈으로 이은 gutter 텍스트. */
export function lineNumbersText(lineCount: number): string {
  return Array.from({ length: Math.max(1, lineCount) }, (_, i) => String(i + 1)).join("\n")
}

/**
 * 뷰어용 코드 블록 장식 — 편집기 NodeView 와 같은 구조로
 * ``<pre class="code-block code-block-static" data-language><span class="code-gutter">1\n2…</span><code>`` 를 만든다.
 * 줄 번호는 CSS 카운터가 아니라 텍스트로 넣어 code 와 같은 폰트·줄 높이로 정렬되게 한다.
 */
export function decorateCodeBlock(code: HTMLElement): void {
  const pre = code.parentElement
  if (!pre || pre.tagName !== "PRE" || pre.querySelector(":scope > .code-gutter")) return
  const lang = code.className.match(/(?:language|lang)-([\w-]+)/i)?.[1]
  pre.classList.add("hljs", "code-block", "code-block-static")
  pre.dataset.language = lang && lang !== "plaintext" ? lang : ""
  // <pre> 는 끝의 개행 하나를 줄로 그리지 않으므로 제외하고 센다.
  const lines = (code.textContent ?? "").replace(/\n$/, "").split("\n").length
  const gutter = pre.ownerDocument.createElement("span")
  gutter.className = "code-gutter"
  gutter.setAttribute("aria-hidden", "true")
  gutter.textContent = lineNumbersText(lines)
  pre.insertBefore(gutter, code)
}

function CodeBlockView({ node, updateAttributes, editor }: NodeViewProps) {
  const lang = (node.attrs.language as string | null) || "plaintext"
  // 편집기는 끝의 빈 줄도 한 줄로 보여 주므로 split 길이 그대로.
  const lines = node.textContent.split("\n").length
  return (
    <NodeViewWrapper as="pre" className="hljs code-block" data-language={lang}>
      <span className="code-gutter" contentEditable={false} aria-hidden>
        {lineNumbersText(lines)}
      </span>
      {/* NodeViewContent 기본 white-space 는 pre-wrap — 줄바꿈되면 gutter 와 어긋나므로 pre. */}
      <NodeViewContent<"code"> as="code" style={{ whiteSpace: "pre" }} />
      {editor.isEditable && (
        <select
          contentEditable={false}
          className="code-lang"
          value={lang}
          title="코드 언어"
          onMouseDown={(e) => e.stopPropagation()}
          onChange={(e) => updateAttributes({ language: e.target.value })}
        >
          {LANGUAGE_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </NodeViewWrapper>
  )
}

/** CodeBlockLowlight + 줄 번호 + hover 언어 선택. ``.configure({ lowlight, ... })`` 로 사용. */
export const CodeBlockWithGutter = CodeBlockLowlight.extend({
  addNodeView() {
    return ReactNodeViewRenderer(CodeBlockView)
  },
})
