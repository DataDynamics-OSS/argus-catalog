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

  // Tab / Shift-Tab 들여쓰기. 편집기가 Tab 을 가로채지 않으면 브라우저 기본 포커스 이동이 일어나
  // 편집 영역 밖(툴바·등록 버튼 등)으로 포커스가 튄다. 컨텍스트별로 들여쓰기를 적용하고, 표 안을
  // 제외하고는 항상 true 를 반환해 Tab 이 편집기 밖으로 새지 않게 한다.
  addKeyboardShortcuts() {
    const handle = (dir: 1 | -1) => (): boolean => {
      const editor = this.editor
      // 표 안에서는 Table 확장의 셀 이동(Tab/Shift-Tab)에 양보.
      if (editor.isActive("table")) return false
      // 코드 블록: Tab = 2칸 들여쓰기 삽입. Shift-Tab 은 동작 없이 소비만.
      if (editor.isActive("codeBlock")) {
        if (dir === 1) editor.commands.insertContent("  ")
        return true
      }
      // 체크리스트(taskItem)·글머리/번호 목록: 한 단계 sink / lift.
      for (const item of ["taskItem", "listItem"]) {
        if (editor.isActive(item)) {
          if (dir === 1) editor.commands.sinkListItem(item)
          else editor.commands.liftListItem(item)
          return true
        }
      }
      // 그 외 paragraph / heading: 커스텀 indent / outdent (최대·최소여도 소비).
      if (dir === 1) editor.commands.indent()
      else editor.commands.outdent()
      return true
    }
    return { Tab: handle(1), "Shift-Tab": handle(-1) }
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
