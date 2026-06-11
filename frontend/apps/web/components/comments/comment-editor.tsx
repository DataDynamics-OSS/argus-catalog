"use client"

/**
 * 댓글 작성용 풀 기능 TipTap 에디터.
 *
 * 확장: StarterKit + Underline + Link + ResizableImage + Table 4종 +
 *       TextAlign + TaskList/Item + TextStyle + Color + Indent + Placeholder.
 * Toolbar: 텍스트 서식 / 색상 / 이모지 / 헤딩 / 목록 / 정렬 / 블록 / 링크·이미지 /
 *          표 / Undo·Redo.
 * 부가 기능: 이미지 paste & drop 자동 압축(base64), 표 우클릭 컨텍스트 메뉴.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useEditor, EditorContent, type Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight"
import { Underline } from "@tiptap/extension-underline"
import { Link } from "@tiptap/extension-link"
import { Placeholder } from "@tiptap/extension-placeholder"
import { Table } from "@tiptap/extension-table"
import { TableRow } from "@tiptap/extension-table-row"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableCell } from "@tiptap/extension-table-cell"
import { TextAlign } from "@tiptap/extension-text-align"
import { TaskList } from "@tiptap/extension-task-list"
import { TaskItem } from "@tiptap/extension-task-item"
import { TextStyle } from "@tiptap/extension-text-style"
import { Color } from "@tiptap/extension-color"
import {
  AlignCenter, AlignLeft, AlignRight,
  Bold, Code, Code2,
  Heading1, Heading2, Heading3,
  Image as ImageIcon,
  Indent as IndentIcon, Italic,
  Link as LinkIcon, List, ListChecks, ListOrdered,
  Minus, Outdent as OutdentIcon, Palette, Quote,
  Redo, RemoveFormatting, Sigma, Smile, Strikethrough,
  Table as TableIcon,
  Underline as UnderlineIcon, Undo,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"

import { compressImageToDataUrl } from "@/lib/image-compress"
import { ResizableImage } from "./resizable-image"
import { Indent as IndentExtension } from "./indent-extension"
import { LANGUAGE_OPTIONS, lowlight } from "./lowlight"
import {
  EMOJI_CATEGORIES,
  type EmojiItem,
  findEmojiByChar,
  getRecentEmojis,
  pushRecentEmoji,
  searchEmojis,
} from "./emoji-data"

type CommentEditorProps = {
  onSubmit: (content: string, contentPlain: string, category: string) => Promise<void>
  placeholder?: string
  submitLabel?: string
  autoFocus?: boolean
  onCancel?: () => void
  /** 편집 시 표시할 초기 HTML — 댓글 외 컨텍스트(예: 데이터셋 설명 편집) 재사용용. */
  initialContent?: string
  /** 카테고리 셀렉트 노출 여부. 댓글이 아닌 일반 텍스트 편집 시 ``false`` 권장. */
  showCategory?: boolean
  /** 입력값 실시간 동기화(폼 필드로 사용 시). 지정 시 편집 내용이 바뀔 때마다 HTML 을 전달. */
  onChange?: (html: string) => void
  /** 하단 등록/취소 액션 숨김(외부 폼이 제출을 담당할 때). */
  hideActions?: boolean
  /** 에디터 생성 시 인스턴스 전달(외부에서 표 삽입 등 명령 실행용). */
  onReady?: (editor: Editor) => void
}

// paste / drop 한 이미지 파일들을 압축 → base64 → image node 로 삽입.
async function insertImagesFromFiles(
  view: import("@tiptap/pm/view").EditorView,
  files: File[],
  pos?: number,
): Promise<void> {
  for (const f of files) {
    let dataUrl: string
    try {
      dataUrl = await compressImageToDataUrl(f, { maxDim: 1280, quality: 0.75 })
    } catch {
      continue
    }
    const node = view.state.schema.nodes.image?.create({ src: dataUrl })
    if (!node) continue
    const tr = view.state.tr
    if (typeof pos === "number") tr.insert(pos, node)
    else tr.replaceSelectionWith(node)
    view.dispatch(tr)
  }
}

export function CommentEditor({
  onSubmit,
  placeholder = "댓글을 작성하세요...",
  submitLabel = "등록",
  autoFocus = false,
  onCancel,
  initialContent,
  showCategory = true,
  onChange,
  hideActions = false,
  onReady,
}: CommentEditorProps) {
  const [category, setCategory] = useState("general")
  const [submitting, setSubmitting] = useState(false)
  // 초기 콘텐츠가 있으면 처음부터 빈 상태가 아님 (등록 버튼 활성화 조건).
  const [isEmpty, setIsEmpty] = useState(!initialContent)
  const [tableMenu, setTableMenu] = useState<{ x: number; y: number } | null>(null)

  const editor = useEditor({
    immediatelyRender: false,
    content: initialContent,
    extensions: [
      // StarterKit 의 기본 codeBlock 은 CodeBlockLowlight 로 교체 — syntax highlight
      // 지원 + language 속성으로 직렬화. ``<pre><code class="language-xxx">...``.
      StarterKit.configure({ codeBlock: false }),
      CodeBlockLowlight.configure({
        lowlight,
        defaultLanguage: "plaintext",
        HTMLAttributes: { class: "hljs" },
      }),
      Underline,
      Link.configure({
        openOnClick: false,
        autolink: true,
        HTMLAttributes: {
          class: "text-primary underline",
          rel: "noopener noreferrer",
          target: "_blank",
        },
      }),
      ResizableImage.configure({
        HTMLAttributes: { class: "max-w-full rounded" },
      }),
      Table.configure({
        resizable: true,
        HTMLAttributes: { class: "tiptap-table" },
      }),
      TableRow,
      TableHeader,
      TableCell,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TaskList,
      TaskItem.configure({ nested: true }),
      // TextStyle 는 Color 의 의존성 (인라인 마크 wrapper).
      TextStyle,
      Color,
      // paragraph / heading 에 indent 정수 속성 부여.
      IndentExtension,
      Placeholder.configure({ placeholder }),
    ],
    autofocus: autoFocus,
    editorProps: {
      attributes: {
        class:
          // resize-y: 우하단 그립으로 높이 조절 가능(overflow-auto 필요).
          "prose prose-sm max-w-none min-h-[160px] max-h-[800px] resize-y overflow-auto px-3 py-2 focus:outline-none " +
          // 코드블록: highlight.js 의 ``.hljs`` 클래스가 배경/토큰 색을 부여하므로
          // 여기서는 폰트·padding·줄바꿈만 명시. ``text-foreground`` 같은 일괄 색은
          // 부여하지 않는다 (토큰 색을 덮어쓰면 syntax highlight 무력화).
          // 코드블록 컨테이너: 회색 배경 + padding + rounded + 가로 스크롤.
          // 폰트 크기는 본문(prose-sm = 14px) 과 동일한 ``text-sm`` 으로 명시 —
          // prose-sm 의 기본 ``font-size: 0.857em`` (~12px) 가 코드블록에 적용되지
          // 않게 한다. 안의 <code> 는 부모 pre 의 text-sm 을 상속하고 토큰 색은
          // ``.hljs-xxx`` 셀렉터가 더 specific 하므로 그대로 보존된다.
          "[&_pre]:bg-muted [&_pre]:p-3 [&_pre]:rounded [&_pre]:text-sm " +
          "[&_pre]:overflow-x-auto [&_pre]:whitespace-pre [&_pre]:font-[D2Coding,monospace] " +
          "[&_pre_code]:bg-transparent [&_pre_code]:p-0 " +
          // 인라인 <code> — 본문과 동일한 14px(text-sm) + 본문과 동일한 font-weight.
          // prose 의 ``.prose code { font-weight: 600 }`` 기본은 ``font-normal`` 로 override.
          "[&_code]:font-[D2Coding,monospace] [&_code]:font-normal [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0 [&_code]:rounded [&_code]:text-sm " +
          // prose 가 인라인 <code> 앞뒤에 자동으로 그리는 백틱 pseudo-content 제거
          "[&_code]:before:content-none [&_code]:after:content-none " +
          // prose 가 blockquote 앞뒤에 자동으로 그리는 따옴표(``""``) 제거
          "[&_blockquote]:before:content-none [&_blockquote]:after:content-none " +
          "[&_blockquote_p]:before:content-none [&_blockquote_p]:after:content-none " +
          // 리스트 marker(dot/번호) 색을 본문과 동일한 ``text-foreground`` 로
          "[&_ul]:marker:text-foreground [&_ol]:marker:text-foreground " +
          // 표 — viewer 와 동일한 보더 + 헤더 배경. editor 의 prose 는 기본적으로
          // table border 를 그리지 않아 명시적으로 부여. prose-sm 의 ``table``
          // 기본 ``font-size: 0.857em`` (~12px) 도 ``text-sm`` 으로 본문과 통일.
          "[&_table]:border-collapse [&_table]:my-2 [&_table]:text-sm " +
          "[&_table_th]:border [&_table_th]:bg-muted/50 [&_table_th]:px-2 [&_table_th]:py-1 " +
          "[&_table_td]:border [&_table_td]:px-2 [&_table_td]:py-1",
      },
      // 이미지 클립보드 paste / drag-drop — 압축 후 base64 inline.
      handlePaste(view, event) {
        const files = Array.from(event.clipboardData?.files || []).filter((f) =>
          f.type.startsWith("image/"),
        )
        if (files.length === 0) return false
        event.preventDefault()
        void insertImagesFromFiles(view, files)
        return true
      },
      handleDrop(view, event) {
        const dt = (event as DragEvent).dataTransfer
        const files = Array.from(dt?.files || []).filter((f) =>
          f.type.startsWith("image/"),
        )
        if (files.length === 0) return false
        event.preventDefault()
        const e = event as DragEvent
        const coords = view.posAtCoords({ left: e.clientX, top: e.clientY })
        void insertImagesFromFiles(view, files, coords?.pos)
        return true
      },
    },
    onUpdate: ({ editor: e }) => {
      setIsEmpty(e.isEmpty)
      onChange?.(e.getHTML())
    },
  })

  // 에디터 생성/변경 시 인스턴스 전달(immediatelyRender:false 환경에서 onCreate 보다 신뢰적).
  useEffect(() => {
    if (editor) onReady?.(editor)
  }, [editor, onReady])

  const handleSubmit = useCallback(async () => {
    if (!editor || editor.isEmpty) return
    setSubmitting(true)
    try {
      const html = editor.getHTML()
      const plain = editor.getText()
      await onSubmit(html, plain, category)
      editor.commands.clearContent()
      setCategory("general")
      setIsEmpty(true)
    } finally {
      setSubmitting(false)
    }
  }, [editor, category, onSubmit])

  // 표 안 우클릭 시 컨텍스트 메뉴 (행/열 add·del, 표 삭제 등)
  const onContextMenu = (e: React.MouseEvent) => {
    if (!editor) return
    if (!editor.isActive("table")) return // 표 밖이면 브라우저 기본 메뉴
    e.preventDefault()
    setTableMenu({ x: e.clientX, y: e.clientY })
  }

  if (!editor) return null

  return (
    <div className="border rounded-lg overflow-hidden">
      <Toolbar editor={editor} />
      <div onContextMenu={onContextMenu}>
        <EditorContent editor={editor} />
      </div>
      {tableMenu && (
        <TableContextMenu
          editor={editor}
          x={tableMenu.x}
          y={tableMenu.y}
          onClose={() => setTableMenu(null)}
        />
      )}

      {/* Footer — 카테고리(옵션) / 등록·취소.
          showCategory=false 면 우측에 버튼만 표시. hideActions 면 외부 폼이 제출을 담당하므로 미표시. */}
      {!hideActions && (
      <div className="flex items-center justify-between px-3 py-2 border-t bg-muted/10">
        {showCategory ? (
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="w-[160px] h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="general">일반</SelectItem>
              <SelectItem value="suggestion">제안</SelectItem>
              <SelectItem value="feature">기능 요청</SelectItem>
              <SelectItem value="bug">버그</SelectItem>
            </SelectContent>
          </Select>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          {onCancel && (
            <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitting}>
              취소
            </Button>
          )}
          <Button size="sm" onClick={handleSubmit} disabled={submitting || isEmpty}>
            {submitting ? "등록 중..." : submitLabel}
          </Button>
        </div>
      </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Toolbar
// ---------------------------------------------------------------------------

function Toolbar({ editor }: { editor: Editor }) {
  const btn =
    "h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed text-muted-foreground"
  const btnActive =
    "h-7 w-7 inline-flex items-center justify-center rounded bg-muted text-foreground"
  const sep = "mx-0.5 h-4 w-px bg-border"

  return (
    <div className="flex flex-wrap items-center gap-0.5 px-2 py-1 border-b bg-muted/30">
      {/* 텍스트 서식 */}
      <button
        type="button" title="굵게 (Ctrl+B)"
        onClick={() => editor.chain().focus().toggleBold().run()}
        className={editor.isActive("bold") ? btnActive : btn}
      ><Bold className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="기울임 (Ctrl+I)"
        onClick={() => editor.chain().focus().toggleItalic().run()}
        className={editor.isActive("italic") ? btnActive : btn}
      ><Italic className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="밑줄 (Ctrl+U)"
        onClick={() => editor.chain().focus().toggleUnderline().run()}
        className={editor.isActive("underline") ? btnActive : btn}
      ><UnderlineIcon className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="취소선"
        onClick={() => editor.chain().focus().toggleStrike().run()}
        className={editor.isActive("strike") ? btnActive : btn}
      ><Strikethrough className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="인라인 코드"
        onClick={() => editor.chain().focus().toggleCode().run()}
        className={editor.isActive("code") ? btnActive : btn}
      ><Code className="h-3.5 w-3.5" /></button>
      <ColorButton editor={editor} />
      <EmojiButton editor={editor} />
      <EmojiButton editor={editor} defaultTab="sym" icon={<Sigma className="h-3.5 w-3.5" />} title="특수문자" />

      <span className={sep} />

      {/* 헤딩 */}
      <button
        type="button" title="제목 1"
        onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
        className={editor.isActive("heading", { level: 1 }) ? btnActive : btn}
      ><Heading1 className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="제목 2"
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        className={editor.isActive("heading", { level: 2 }) ? btnActive : btn}
      ><Heading2 className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="제목 3"
        onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        className={editor.isActive("heading", { level: 3 }) ? btnActive : btn}
      ><Heading3 className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 목록 */}
      <button
        type="button" title="글머리 기호"
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        className={editor.isActive("bulletList") ? btnActive : btn}
      ><List className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="번호 매기기"
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        className={editor.isActive("orderedList") ? btnActive : btn}
      ><ListOrdered className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="체크리스트"
        onClick={() => editor.chain().focus().toggleTaskList().run()}
        className={editor.isActive("taskList") ? btnActive : btn}
      ><ListChecks className="h-3.5 w-3.5" /></button>
      {/* 들여쓰기 — 리스트 안이면 sinkListItem, 그 외엔 IndentExtension */}
      <button
        type="button" title="들여쓰기"
        onClick={() => {
          const c = editor.chain().focus()
          if (editor.isActive("taskItem")) c.sinkListItem("taskItem").run()
          else if (editor.isActive("listItem")) c.sinkListItem("listItem").run()
          else c.indent().run()
        }}
        className={btn}
      ><IndentIcon className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="내어쓰기"
        onClick={() => {
          const c = editor.chain().focus()
          if (editor.isActive("taskItem")) c.liftListItem("taskItem").run()
          else if (editor.isActive("listItem")) c.liftListItem("listItem").run()
          else c.outdent().run()
        }}
        className={btn}
      ><OutdentIcon className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 정렬 */}
      <button
        type="button" title="왼쪽 정렬"
        onClick={() => editor.chain().focus().setTextAlign("left").run()}
        className={editor.isActive({ textAlign: "left" }) ? btnActive : btn}
      ><AlignLeft className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="가운데 정렬"
        onClick={() => editor.chain().focus().setTextAlign("center").run()}
        className={editor.isActive({ textAlign: "center" }) ? btnActive : btn}
      ><AlignCenter className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="오른쪽 정렬"
        onClick={() => editor.chain().focus().setTextAlign("right").run()}
        className={editor.isActive({ textAlign: "right" }) ? btnActive : btn}
      ><AlignRight className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 블록 */}
      <button
        type="button" title="인용"
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        className={editor.isActive("blockquote") ? btnActive : btn}
      ><Quote className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="코드 블록"
        onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        className={editor.isActive("codeBlock") ? btnActive : btn}
      ><Code2 className="h-3.5 w-3.5" /></button>
      <LanguageSelect editor={editor} />
      <button
        type="button" title="가로선"
        onClick={() => editor.chain().focus().setHorizontalRule().run()}
        className={btn}
      ><Minus className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 링크 / 이미지 */}
      <button
        type="button" title="링크"
        onClick={() => {
          const prev = editor.getAttributes("link").href as string | undefined
          const url = window.prompt("링크 URL", prev ?? "https://")
          if (url === null) return
          if (url === "") {
            editor.chain().focus().extendMarkRange("link").unsetLink().run()
            return
          }
          editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run()
        }}
        className={editor.isActive("link") ? btnActive : btn}
      ><LinkIcon className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="이미지 (URL)"
        onClick={() => {
          const url = window.prompt("이미지 URL", "https://")
          if (!url) return
          editor.chain().focus().setImage({ src: url }).run()
        }}
        className={btn}
      ><ImageIcon className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 표 */}
      <button
        type="button" title="표 삽입 (3×3)"
        onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        className={btn}
      ><TableIcon className="h-3.5 w-3.5" /></button>

      <span className={sep} />

      {/* 서식 지우기 / Undo / Redo */}
      <button
        type="button" title="서식 지우기"
        onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}
        className={btn}
      ><RemoveFormatting className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="실행 취소 (Ctrl+Z)"
        onClick={() => editor.chain().focus().undo().run()}
        disabled={!editor.can().undo()}
        className={btn}
      ><Undo className="h-3.5 w-3.5" /></button>
      <button
        type="button" title="다시 실행 (Ctrl+Y)"
        onClick={() => editor.chain().focus().redo().run()}
        disabled={!editor.can().redo()}
        className={btn}
      ><Redo className="h-3.5 w-3.5" /></button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 코드블록 언어 선택 — codeBlock 활성 시에만 셀렉트가 노출된다.
// 변경하면 active codeBlock node 의 ``language`` 속성을 갱신해
// ``<pre><code class="language-xxx">`` 로 재직렬화된다.
// ---------------------------------------------------------------------------

function LanguageSelect({ editor }: { editor: Editor }) {
  const active = editor.isActive("codeBlock")
  if (!active) return null
  const current = (editor.getAttributes("codeBlock").language as string) || "plaintext"
  return (
    <select
      value={current}
      onChange={(e) => {
        editor.chain().focus().updateAttributes("codeBlock", { language: e.target.value }).run()
      }}
      className="h-7 rounded border border-input bg-background px-1.5 text-xs text-foreground"
      title="코드 언어"
    >
      {LANGUAGE_OPTIONS.map((o) => (
        <option key={o.key} value={o.key}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

// ---------------------------------------------------------------------------
// Table Context Menu — 표 안에서 우클릭 시 노출되는 floating 메뉴.
// Portal 로 body 에 렌더 → overflow 컨테이너 영향 없음.
// ---------------------------------------------------------------------------

function TableContextMenu({
  editor, x, y, onClose,
}: {
  editor: Editor
  x: number
  y: number
  onClose: () => void
}) {
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const tgt = e.target as Node
      if (tgt instanceof HTMLElement && !tgt.closest("[data-tt-table-menu]")) {
        onClose()
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [onClose])

  const left = typeof window !== "undefined" ? Math.min(x, window.innerWidth - 220) : x
  const top = typeof window !== "undefined" ? Math.min(y, window.innerHeight - 380) : y

  const run = (fn: () => void) => () => {
    fn()
    onClose()
  }

  type Item =
    | { kind: "sep" }
    | { kind: "action"; label: string; disabled?: boolean; onClick: () => void; danger?: boolean }

  const items: Item[] = [
    { kind: "action", label: "↑ 위에 행 추가", disabled: !editor.can().addRowBefore(),
      onClick: run(() => editor.chain().focus().addRowBefore().run()) },
    { kind: "action", label: "↓ 아래에 행 추가", disabled: !editor.can().addRowAfter(),
      onClick: run(() => editor.chain().focus().addRowAfter().run()) },
    { kind: "action", label: "← 왼쪽 열 추가", disabled: !editor.can().addColumnBefore(),
      onClick: run(() => editor.chain().focus().addColumnBefore().run()) },
    { kind: "action", label: "→ 오른쪽 열 추가", disabled: !editor.can().addColumnAfter(),
      onClick: run(() => editor.chain().focus().addColumnAfter().run()) },
    { kind: "sep" },
    { kind: "action", label: "행 삭제", disabled: !editor.can().deleteRow(),
      onClick: run(() => editor.chain().focus().deleteRow().run()) },
    { kind: "action", label: "열 삭제", disabled: !editor.can().deleteColumn(),
      onClick: run(() => editor.chain().focus().deleteColumn().run()) },
    { kind: "sep" },
    { kind: "action", label: "셀 병합", disabled: !editor.can().mergeCells(),
      onClick: run(() => editor.chain().focus().mergeCells().run()) },
    { kind: "action", label: "셀 분리", disabled: !editor.can().splitCell(),
      onClick: run(() => editor.chain().focus().splitCell().run()) },
    { kind: "action", label: "헤더 행 토글", disabled: !editor.can().toggleHeaderRow(),
      onClick: run(() => editor.chain().focus().toggleHeaderRow().run()) },
    { kind: "action", label: "헤더 열 토글", disabled: !editor.can().toggleHeaderColumn(),
      onClick: run(() => editor.chain().focus().toggleHeaderColumn().run()) },
    { kind: "sep" },
    { kind: "action", label: "표 삭제", disabled: !editor.can().deleteTable(), danger: true,
      onClick: run(() => editor.chain().focus().deleteTable().run()) },
  ]

  return createPortal(
    <div
      data-tt-table-menu
      style={{ position: "fixed", top, left, zIndex: 1000, minWidth: 200 }}
      className="rounded-md border border-input bg-white shadow-lg py-1 text-sm"
    >
      {items.map((it, i) =>
        it.kind === "sep" ? (
          <div key={i} className="my-1 h-px bg-border" />
        ) : (
          <button
            key={i}
            type="button"
            disabled={it.disabled}
            onClick={it.onClick}
            className={
              "block w-full text-left px-3 py-1.5 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed " +
              (it.danger ? "text-rose-600" : "")
            }
          >
            {it.label}
          </button>
        ),
      )}
    </div>,
    document.body,
  )
}

// ---------------------------------------------------------------------------
// 글자 색상 버튼 — 팔레트 + 사용자 지정 + 지우기.
// ---------------------------------------------------------------------------

const COLOR_PALETTE = [
  "#0f172a", "#dc2626", "#ea580c", "#ca8a04", "#16a34a",
  "#0891b2", "#2563eb", "#7c3aed", "#db2777", "#64748b",
  "#7f1d1d", "#fbbf24", "#84cc16", "#10b981", "#0d9488",
  "#0284c7", "#4f46e5", "#c026d3", "#f43f5e", "#92400e",
]

function ColorButton({ editor }: { editor: Editor }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLSpanElement | null>(null)
  const colorInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const tgt = e.target as Node
      if (wrapRef.current && !wrapRef.current.contains(tgt)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const current = (editor.getAttributes("textStyle").color as string) || ""

  const apply = (c: string) => { editor.chain().focus().setColor(c).run(); setOpen(false) }
  const clear = () => { editor.chain().focus().unsetColor().run(); setOpen(false) }
  const custom = () => {
    if (colorInputRef.current) {
      colorInputRef.current.value = current || "#000000"
      colorInputRef.current.click()
    }
  }

  return (
    <span ref={wrapRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted relative text-muted-foreground"
        title="글자 색"
      >
        <Palette className="h-3.5 w-3.5" />
        <span
          aria-hidden
          className="absolute bottom-1 left-1.5 right-1.5 h-0.5 rounded-sm"
          style={{ backgroundColor: current || "transparent" }}
        />
      </button>
      <input
        ref={colorInputRef}
        type="color"
        defaultValue={current || "#000000"}
        onChange={(e) => apply(e.target.value)}
        className="absolute opacity-0 pointer-events-none"
        style={{ width: 0, height: 0 }}
        tabIndex={-1}
        aria-hidden
      />
      {open && (
        <div
          className="absolute top-full left-0 mt-1 z-50 rounded-md border border-input bg-white shadow-lg p-2"
          style={{ minWidth: 180 }}
        >
          <div className="grid grid-cols-5 gap-1 mb-2">
            {COLOR_PALETTE.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => apply(c)}
                className={
                  "h-6 w-6 rounded border-2 transition " +
                  (current.toLowerCase() === c.toLowerCase()
                    ? "border-primary"
                    : "border-transparent hover:border-slate-300")
                }
                style={{ backgroundColor: c }}
                title={c}
              />
            ))}
          </div>
          <div className="flex gap-1 text-[11px]">
            <button
              type="button"
              onClick={custom}
              className="flex-1 rounded border border-input bg-background px-2 py-1 hover:bg-muted"
            >
              사용자 지정
            </button>
            <button
              type="button"
              onClick={clear}
              className="flex-1 rounded border border-input bg-background px-2 py-1 hover:bg-muted text-muted-foreground"
            >
              지우기
            </button>
          </div>
        </div>
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Emoji / 특수문자 picker — 큐레이션 5탭 (자주 + 표정 / 기호 / 화살표 / 수학·통화) + 검색.
// ---------------------------------------------------------------------------

function EmojiButton({
  editor,
  defaultTab,
  icon,
  title = "이모지",
}: {
  editor: Editor
  defaultTab?: string
  icon?: React.ReactNode
  title?: string
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<string>(defaultTab || "recent")
  const [query, setQuery] = useState("")
  const [recent, setRecent] = useState<string[]>([])
  const wrapRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!open) return
    setRecent(getRecentEmojis())
    setQuery("")
    setTab(() => {
      if (defaultTab) return defaultTab
      return getRecentEmojis().length > 0 ? "recent" : (EMOJI_CATEGORIES[0]?.key ?? "face")
    })
  }, [open, defaultTab])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const tgt = e.target as Node
      if (wrapRef.current && !wrapRef.current.contains(tgt)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const insert = (it: EmojiItem) => {
    editor.chain().focus().insertContent(it.char).run()
    setRecent(pushRecentEmoji(it.char))
  }

  const items: EmojiItem[] = (() => {
    if (query.trim()) return searchEmojis(query)
    if (tab === "recent") {
      return recent.map((c) => findEmojiByChar(c) ?? { char: c, name: c }).filter((x) => x.char)
    }
    const cat = EMOJI_CATEGORIES.find((c) => c.key === tab)
    return cat ? cat.items : []
  })()

  return (
    <span ref={wrapRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted text-muted-foreground"
        title={title}
      >
        {icon ?? <Smile className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div
          className="absolute top-full left-0 mt-1 z-50 rounded-md border border-input bg-white shadow-lg p-2"
          style={{ width: 360 }}
        >
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="검색 (예: 체크, smile, →)"
            className="w-full mb-2 h-7 rounded border border-input bg-background px-2 text-xs"
            autoFocus
          />
          {!query.trim() && (
            <div className="flex items-center gap-0 mb-2 border-b border-border text-xs">
              <EmojiTab
                active={tab === "recent"}
                onClick={() => setTab("recent")}
                disabled={recent.length === 0}
              >자주</EmojiTab>
              {EMOJI_CATEGORIES.map((c) => (
                <EmojiTab key={c.key} active={tab === c.key} onClick={() => setTab(c.key)}>
                  {c.label}
                </EmojiTab>
              ))}
            </div>
          )}
          <div className="grid grid-cols-8 gap-0.5 max-h-[260px] overflow-y-auto">
            {items.length === 0 ? (
              <div className="col-span-8 text-center text-xs text-muted-foreground py-4">
                {query.trim() ? "검색 결과 없음" : "최근 사용 항목이 없습니다."}
              </div>
            ) : (
              items.map((it) => (
                <button
                  key={it.char + "/" + it.name}
                  type="button"
                  onClick={() => insert(it)}
                  className="h-8 w-8 text-lg rounded hover:bg-muted flex items-center justify-center"
                  title={it.name}
                >
                  {it.char}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </span>
  )
}

function EmojiTab({
  active, onClick, disabled, children,
}: {
  active: boolean
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={
        "h-7 px-2 -mb-px border-b-2 " +
        (disabled
          ? "border-transparent text-muted-foreground/40 cursor-not-allowed"
          : active
            ? "border-primary text-foreground font-semibold"
            : "border-transparent text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  )
}
