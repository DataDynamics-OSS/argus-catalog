/**
 * TipTap 커스텀 indent 확장 — paragraph / heading 에 ``indent`` 정수 속성을 부여.
 *
 * 동작:
 *   - ``indent`` 명령: 현재 노드의 indent +1 (최대 ``MAX_INDENT``).
 *   - ``outdent`` 명령: 현재 노드의 indent -1 (최소 0).
 *   - 렌더링: ``padding-left: <N * STEP_EM>em;`` + ``data-indent="<N>"`` HTML 속성.
 *
 * 리스트(listItem) 들여쓰기는 sinkListItem / liftListItem 이 별개로 처리하므로
 * 여기서는 listItem 을 대상에 넣지 않는다. 그래야 리스트 안에서 indent 버튼이
 * 두 메커니즘과 충돌하지 않음.
 */

import { Extension } from "@tiptap/core"

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    indent: {
      indent: () => ReturnType
      outdent: () => ReturnType
    }
  }
}

const MAX_INDENT = 8
const STEP_EM = 2

export interface IndentOptions {
  types: string[]
}

export const Indent = Extension.create<IndentOptions>({
  name: "indent",

  addOptions() {
    return {
      types: ["paragraph", "heading"],
    }
  },

  addGlobalAttributes() {
    return [
      {
        types: this.options.types,
        attributes: {
          indent: {
            default: 0,
            parseHTML: (el) => {
              const v = el.getAttribute("data-indent")
              if (v) return parseInt(v, 10) || 0
              // fallback — 다른 에디터/CMS 에서 paste 시 padding-left 추정.
              const pl = (el as HTMLElement).style?.paddingLeft
              if (pl?.endsWith("em")) {
                const n = Math.round(parseFloat(pl) / STEP_EM)
                return Number.isFinite(n) ? Math.max(0, Math.min(MAX_INDENT, n)) : 0
              }
              return 0
            },
            renderHTML: (attrs) => {
              const lv = Math.max(0, Math.min(MAX_INDENT, Number(attrs.indent) || 0))
              if (!lv) return {}
              return {
                "data-indent": String(lv),
                style: `padding-left: ${lv * STEP_EM}em;`,
              }
            },
          },
        },
      },
    ]
  },

  addCommands() {
    type EditorLike = { isActive: (name: string) => boolean }
    function currentType(editor: EditorLike): string {
      // heading 이면 heading, 그 외엔 paragraph 로 가정.
      // table cell 안의 paragraph 도 paragraph 로 잡힌다.
      if (editor.isActive("heading")) return "heading"
      return "paragraph"
    }
    return {
      indent:
        () =>
        ({ commands, editor }) => {
          const type = currentType(editor as unknown as EditorLike)
          const cur = (editor.getAttributes(type).indent as number | undefined) ?? 0
          if (cur >= MAX_INDENT) return false
          return commands.updateAttributes(type, { indent: cur + 1 })
        },
      outdent:
        () =>
        ({ commands, editor }) => {
          const type = currentType(editor as unknown as EditorLike)
          const cur = (editor.getAttributes(type).indent as number | undefined) ?? 0
          if (cur <= 0) return false
          return commands.updateAttributes(type, { indent: cur - 1 })
        },
    }
  },
})

export default Indent
