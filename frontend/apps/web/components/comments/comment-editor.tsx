"use client"

/**
 * 댓글 작성용 풀 기능 TipTap 에디터.
 *
 * 확장: StarterKit + Underline + Link + ResizableImage + Table 4종 +
 *       TextAlign + TaskList/Item + TextStyle + Color + Indent + Placeholder.
 * Toolbar: 텍스트 서식 / 색상 / 이모지 / 헤딩 / 목록 / 정렬 / 블록 / 링크·이미지 /
 *          표 / Undo·Redo.
 * 부가 기능: 이미지 paste & drop 자동 압축(base64), 표 우클릭 컨텍스트 메뉴, 격자 표 삽입,
 *           코드 블록 줄 번호·언어 선택, Tab/Shift-Tab 들여쓰기.
 */

import { Tip } from "@/components/tip"
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react"
import { useEditor, EditorContent, type Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import type { EditorProps } from "@tiptap/pm/view"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
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
  BetweenHorizontalEnd, BetweenHorizontalStart, BetweenVerticalEnd, BetweenVerticalStart,
  Bold, Code, Code2,
  Heading1, Heading2, Heading3,
  Image as ImageIcon,
  Indent as IndentIcon, Italic,
  Link as LinkIcon, List, ListChecks, ListOrdered,
  Minus, Outdent as OutdentIcon, Palette, Quote,
  Redo, RemoveFormatting, Sigma, Smile, Strikethrough,
  Table as TableIcon, TableCellsMerge, TableCellsSplit, Trash2,
  Underline as UnderlineIcon, Undo,
} from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"

import { compressImageToDataUrl } from "@/lib/image-compress"
import { ResizableImage } from "./resizable-image"
import { Indent as IndentExtension } from "./indent-extension"
import { usePrompt } from "@/components/confirm-provider"
import { LANGUAGE_OPTIONS, lowlight } from "./lowlight"
import { CodeBlockWithGutter } from "./code-block"
import { ToolbarPopover } from "./toolbar-popover"
import {
  EMOJI_CATEGORIES,
  type EmojiItem,
  findEmojiByChar,
  getRecentEmojis,
  pushRecentEmoji,
  searchEmojis,
} from "./emoji-data"
import { SearchInput } from "@/components/search-input"

type CommentEditorProps = {
  onSubmit: (content: string, contentPlain: string, category: string) => Promise<void>
  placeholder?: string
  submitLabel?: string
  autoFocus?: boolean
  onCancel?: () => void
  /** 편집 시 표시할 초기 HTML — 댓글 외 컨텍스트(예: 데이터셋 설명 편집) 재사용용. */
  initialContent?: string
  /** 카테고리 초기값(댓글 수정 시 기존 분류). 기본 "general". */
  initialCategory?: string
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
  placeholder = "댓글을 작성하세요…",
  submitLabel = "등록",
  autoFocus = false,
  onCancel,
  initialContent,
  initialCategory = "general",
  showCategory = true,
  onChange,
  hideActions = false,
  onReady,
}: CommentEditorProps) {
  const [category, setCategory] = useState(initialCategory)
  const [submitting, setSubmitting] = useState(false)
  // 초기 콘텐츠가 있으면 처음부터 빈 상태가 아님 (등록 버튼 활성화 조건).
  const [isEmpty, setIsEmpty] = useState(!initialContent)
  const [tableMenu, setTableMenu] = useState<{ x: number; y: number } | null>(null)

  // onChange·onReady 는 ref 로 보관 — 호출부가 매 렌더 새 함수를 넘겨도 useEditor 옵션·effect 가 흔들리지 않게.
  const onChangeRef = useRef(onChange)
  const onReadyRef = useRef(onReady)
  useEffect(() => {
    onChangeRef.current = onChange
    onReadyRef.current = onReady
  })
  // content 는 초기값만 쓴다(이후 prop 이 바뀌어도 옵션 참조가 변하지 않게).
  const [initialHtml] = useState(initialContent)

  // ⚠️ extensions / editorProps 는 반드시 메모이즈해 참조를 고정한다.
  // @tiptap/react 의 useEditor 는 매 렌더마다 옵션을 비교해 다르면 editor.setOptions() 를 부른다.
  // .configure() 는 매 렌더 새 확장 인스턴스를, 인라인 editorProps 는 매 렌더 새 객체를 만들어 "항상 다름"이
  // 된다. 그러면 키 입력 → onUpdate → 부모 setState(예: 변경 요청 설명의 onChange) → 리렌더 → setOptions() 가
  // 매 키 입력마다 호출되고, 한글 IME 조합 중 view props 가 재적용돼 "한글" → "ㅎ하한한ㄱ그글글" 처럼
  // 글자가 중복된다. 참조를 고정하면 setOptions 가 키 입력마다 호출되지 않아 조합이 보존된다.
  const extensions = useMemo(
    () => [
      // StarterKit 의 기본 codeBlock 은 CodeBlockLowlight 로 교체 — syntax highlight
      // 지원 + language 속성으로 직렬화. ``<pre><code class="language-xxx">...``.
      // tiptap v3 의 StarterKit 은 link/underline 도 번들 — 아래에서 따로 설정해 추가하므로
      // 중복(Duplicate extension names) 방지로 끈다.
      StarterKit.configure({ codeBlock: false, link: false, underline: false }),
      // 코드 블록 — lowlight 문법 강조 + 줄 번호 + hover 언어 선택(code-block.tsx).
      CodeBlockWithGutter.configure({
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
    [placeholder],
  )

  const editorProps = useMemo<EditorProps>(
    () => ({
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
          "[&_pre]:whitespace-pre [&_pre]:font-[D2Coding,monospace] " +
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
        // Excel/Sheets 등은 이미지 비트맵 + <table> HTML 을 함께 클립보드에 담는다.
        // 표가 포함된 HTML 이면 이미지 삽입을 양보해 ProseMirror 가 표로 파싱하게 한다.
        const html = event.clipboardData?.getData("text/html") || ""
        if (/<table[\s>]/i.test(html)) return false
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
    }),
    [],
  )

  const editor = useEditor({
    immediatelyRender: false,
    content: initialHtml,
    extensions,
    autofocus: autoFocus,
    editorProps,
    onUpdate: ({ editor: e }) => {
      setIsEmpty(e.isEmpty)
      onChangeRef.current?.(e.getHTML())
    },
  })

  // 에디터 생성/변경 시 인스턴스 전달(immediatelyRender:false 환경에서 onCreate 보다 신뢰적).
  useEffect(() => {
    if (editor) onReadyRef.current?.(editor)
  }, [editor])

  const handleSubmit = useCallback(async () => {
    if (!editor || editor.isEmpty) return
    setSubmitting(true)
    try {
      const html = editor.getHTML()
      const plain = editor.getText()
      await onSubmit(html, plain, category)
      editor.commands.clearContent()
      setCategory(initialCategory)
      setIsEmpty(true)
    } catch {
      // 실패 알림은 호출자(onSubmit)가 한다 — 입력한 내용은 지우지 않고 그대로 둔다
    } finally {
      setSubmitting(false)
    }
  }, [editor, category, onSubmit, initialCategory])

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
              <SelectItem value="suggestion">개선 요청</SelectItem>
              <SelectItem value="feature">기능 요청</SelectItem>
              <SelectItem value="bug">오류 신고</SelectItem>
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
            {submitting ? "등록 중…" : submitLabel}
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
  const askInput = usePrompt()
  // tiptap v3 의 useEditor 는 트랜잭션마다 리렌더하지 않는다(shouldRerenderOnTransaction 기본 false).
  // 클릭·방향키로 커서만 옮기면 리렌더가 없어 isActive() 기반 활성 표시(굵게·제목 등)와 undo/redo
  // 가능 여부가 갱신되지 않는다. 트랜잭션을 구독해 툴바만 다시 그린다(에디터 옵션과 무관 — IME 영향 없음).
  const [, rerender] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    editor.on("transaction", rerender)
    return () => {
      editor.off("transaction", rerender)
    }
  }, [editor])
  const btn =
    "h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed text-muted-foreground"
  const btnActive =
    "h-7 w-7 inline-flex items-center justify-center rounded bg-muted text-foreground"
  const sep = "mx-0.5 h-4 w-px bg-border"

  return (
    <div className="flex flex-wrap items-center gap-0.5 px-2 py-1 border-b bg-muted/30">
      {/* 텍스트 서식 */}
      <Tip label="굵게 (Ctrl+B)"><button
        type="button" aria-label="굵게 (Ctrl+B)"
        onClick={() => editor.chain().focus().toggleBold().run()}
        className={editor.isActive("bold") ? btnActive : btn}
      ><Bold className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="기울임 (Ctrl+I)"><button
        type="button" aria-label="기울임 (Ctrl+I)"
        onClick={() => editor.chain().focus().toggleItalic().run()}
        className={editor.isActive("italic") ? btnActive : btn}
      ><Italic className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="밑줄 (Ctrl+U)"><button
        type="button" aria-label="밑줄 (Ctrl+U)"
        onClick={() => editor.chain().focus().toggleUnderline().run()}
        className={editor.isActive("underline") ? btnActive : btn}
      ><UnderlineIcon className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="취소선"><button
        type="button" aria-label="취소선"
        onClick={() => editor.chain().focus().toggleStrike().run()}
        className={editor.isActive("strike") ? btnActive : btn}
      ><Strikethrough className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="인라인 코드"><button
        type="button" aria-label="인라인 코드"
        onClick={() => editor.chain().focus().toggleCode().run()}
        className={editor.isActive("code") ? btnActive : btn}
      ><Code className="h-3.5 w-3.5" /></button></Tip>
      <ColorButton editor={editor} />
      <EmojiButton editor={editor} />
      <EmojiButton editor={editor} defaultTab="sym" icon={<Sigma className="h-3.5 w-3.5" />} title="특수문자" />

      <span className={sep} />

      {/* 헤딩 */}
      <Tip label="제목 1"><button
        type="button" aria-label="제목 1"
        onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
        className={editor.isActive("heading", { level: 1 }) ? btnActive : btn}
      ><Heading1 className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="제목 2"><button
        type="button" aria-label="제목 2"
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        className={editor.isActive("heading", { level: 2 }) ? btnActive : btn}
      ><Heading2 className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="제목 3"><button
        type="button" aria-label="제목 3"
        onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        className={editor.isActive("heading", { level: 3 }) ? btnActive : btn}
      ><Heading3 className="h-3.5 w-3.5" /></button></Tip>

      <span className={sep} />

      {/* 목록 */}
      <Tip label="글머리 기호"><button
        type="button" aria-label="글머리 기호"
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        className={editor.isActive("bulletList") ? btnActive : btn}
      ><List className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="번호 매기기"><button
        type="button" aria-label="번호 매기기"
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        className={editor.isActive("orderedList") ? btnActive : btn}
      ><ListOrdered className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="체크리스트"><button
        type="button" aria-label="체크리스트"
        onClick={() => editor.chain().focus().toggleTaskList().run()}
        className={editor.isActive("taskList") ? btnActive : btn}
      ><ListChecks className="h-3.5 w-3.5" /></button></Tip>
      {/* 들여쓰기 — 리스트 안이면 sinkListItem, 그 외엔 IndentExtension */}
      <Tip label="들여쓰기"><button
        type="button" aria-label="들여쓰기"
        onClick={() => {
          const c = editor.chain().focus()
          if (editor.isActive("taskItem")) c.sinkListItem("taskItem").run()
          else if (editor.isActive("listItem")) c.sinkListItem("listItem").run()
          else c.indent().run()
        }}
        className={btn}
      ><IndentIcon className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="내어쓰기"><button
        type="button" aria-label="내어쓰기"
        onClick={() => {
          const c = editor.chain().focus()
          if (editor.isActive("taskItem")) c.liftListItem("taskItem").run()
          else if (editor.isActive("listItem")) c.liftListItem("listItem").run()
          else c.outdent().run()
        }}
        className={btn}
      ><OutdentIcon className="h-3.5 w-3.5" /></button></Tip>

      <span className={sep} />

      {/* 정렬 */}
      <Tip label="왼쪽 정렬"><button
        type="button" aria-label="왼쪽 정렬"
        onClick={() => editor.chain().focus().setTextAlign("left").run()}
        className={editor.isActive({ textAlign: "left" }) ? btnActive : btn}
      ><AlignLeft className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="가운데 정렬"><button
        type="button" aria-label="가운데 정렬"
        onClick={() => editor.chain().focus().setTextAlign("center").run()}
        className={editor.isActive({ textAlign: "center" }) ? btnActive : btn}
      ><AlignCenter className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="오른쪽 정렬"><button
        type="button" aria-label="오른쪽 정렬"
        onClick={() => editor.chain().focus().setTextAlign("right").run()}
        className={editor.isActive({ textAlign: "right" }) ? btnActive : btn}
      ><AlignRight className="h-3.5 w-3.5" /></button></Tip>

      <span className={sep} />

      {/* 블록 */}
      <Tip label="인용"><button
        type="button" aria-label="인용"
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        className={editor.isActive("blockquote") ? btnActive : btn}
      ><Quote className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="코드 블록"><button
        type="button" aria-label="코드 블록"
        onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        className={editor.isActive("codeBlock") ? btnActive : btn}
      ><Code2 className="h-3.5 w-3.5" /></button></Tip>
      <LanguageSelect editor={editor} />
      <Tip label="가로선"><button
        type="button" aria-label="가로선"
        onClick={() => editor.chain().focus().setHorizontalRule().run()}
        className={btn}
      ><Minus className="h-3.5 w-3.5" /></button></Tip>

      <span className={sep} />

      {/* 링크 / 이미지 */}
      <Tip label="링크"><button
        type="button" aria-label="링크"
        onClick={async () => {
          const prev = editor.getAttributes("link").href as string | undefined
          const input = await askInput({
            title: "링크",
            label: "링크 URL",
            description: "비워 두고 확인하면 링크를 제거합니다.",
            defaultValue: prev ?? "https://",
          })
          if (input === null) return
          const url = input.trim()
          if (url === "") {
            editor.chain().focus().extendMarkRange("link").unsetLink().run()
            return
          }
          editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run()
        }}
        className={editor.isActive("link") ? btnActive : btn}
      ><LinkIcon className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="이미지 (URL)"><button
        type="button" aria-label="이미지 (URL)"
        onClick={async () => {
          const url = (await askInput({ title: "이미지 삽입", label: "이미지 URL", defaultValue: "https://", required: true }))?.trim()
          if (!url) return
          editor.chain().focus().setImage({ src: url }).run()
        }}
        className={btn}
      ><ImageIcon className="h-3.5 w-3.5" /></button></Tip>

      <span className={sep} />

      {/* 표 — 격자에서 행·열을 골라 삽입 (MS Word 식). 편집은 표 안 우클릭 메뉴. */}
      <TableInsertButton editor={editor} />

      <span className={sep} />

      {/* 서식 지우기 / Undo / Redo */}
      <Tip label="서식 지우기"><button
        type="button" aria-label="서식 지우기"
        onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}
        className={btn}
      ><RemoveFormatting className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="실행 취소 (Ctrl+Z)"><button
        type="button" aria-label="실행 취소 (Ctrl+Z)"
        onClick={() => editor.chain().focus().undo().run()}
        disabled={!editor.can().undo()}
        className={btn}
      ><Undo className="h-3.5 w-3.5" /></button></Tip>
      <Tip label="다시 실행 (Ctrl+Y)"><button
        type="button" aria-label="다시 실행 (Ctrl+Y)"
        onClick={() => editor.chain().focus().redo().run()}
        disabled={!editor.can().redo()}
        className={btn}
      ><Redo className="h-3.5 w-3.5" /></button></Tip>
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
// Table Context Menu — 표 안에서 우클릭하면 그 자리에 여는 메뉴.
// 공용 DropdownMenu 를 마우스 위치의 보이지 않는 기준점에 붙여 키보드 이동 · Esc · 바깥 클릭을 그대로 쓴다.
// 순서: 추가 → 셀 병합 · 분리 → 머리글 → (구분선) → 삭제(빨강)
// ---------------------------------------------------------------------------

/** 커서가 있는 표의 첫 행 · 첫 열이 머리글인지. */
function tableHeaderState(editor: Editor): { row: boolean; col: boolean } {
  const { $from } = editor.state.selection
  for (let d = $from.depth; d >= 0; d--) {
    const node = $from.node(d)
    if (node.type.name !== "table") continue
    const rows: ProseMirrorNode[] = []
    node.forEach((r) => { rows.push(r) })
    const isHeader = (cell: ProseMirrorNode | null | undefined) => cell?.type.name === "tableHeader"
    const first = rows[0]
    const firstRowCells: ProseMirrorNode[] = []
    first?.forEach((c) => { firstRowCells.push(c) })
    return {
      row: firstRowCells.length > 0 && firstRowCells.every(isHeader),
      col: rows.length > 0 && rows.every((r) => isHeader(r.firstChild)),
    }
  }
  return { row: false, col: false }
}

function TableContextMenu({
  editor, x, y, onClose,
}: {
  editor: Editor
  x: number
  y: number
  onClose: () => void
}) {
  const can = editor.can()
  const header = tableHeaderState(editor)
  const run = (fn: () => void) => () => {
    fn()
    onClose()
  }
  const chain = () => editor.chain().focus()

  return (
    <DropdownMenu open onOpenChange={(o) => { if (!o) onClose() }} modal={false}>
      <DropdownMenuTrigger asChild>
        <span aria-hidden style={{ position: "fixed", left: x, top: y, width: 0, height: 0 }} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="bottom" sideOffset={2} className="w-52" aria-label="표 메뉴" data-tt-table-menu>
        <DropdownMenuItem disabled={!can.addRowBefore()} onSelect={run(() => chain().addRowBefore().run())}>
          <BetweenHorizontalStart /> 위에 행 추가
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!can.addRowAfter()} onSelect={run(() => chain().addRowAfter().run())}>
          <BetweenHorizontalEnd /> 아래에 행 추가
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!can.addColumnBefore()} onSelect={run(() => chain().addColumnBefore().run())}>
          <BetweenVerticalStart /> 왼쪽에 열 추가
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!can.addColumnAfter()} onSelect={run(() => chain().addColumnAfter().run())}>
          <BetweenVerticalEnd /> 오른쪽에 열 추가
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={!can.mergeCells()} onSelect={run(() => chain().mergeCells().run())}>
          <TableCellsMerge /> 셀 병합
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!can.splitCell()} onSelect={run(() => chain().splitCell().run())}>
          <TableCellsSplit /> 셀 분리
        </DropdownMenuItem>
        <DropdownMenuCheckboxItem
          checked={header.row}
          disabled={!can.toggleHeaderRow()}
          onSelect={run(() => chain().toggleHeaderRow().run())}
        >
          첫 행을 머리글로
        </DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem
          checked={header.col}
          disabled={!can.toggleHeaderColumn()}
          onSelect={run(() => chain().toggleHeaderColumn().run())}
        >
          첫 열을 머리글로
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" disabled={!can.deleteRow()} onSelect={run(() => chain().deleteRow().run())}>
          <Trash2 /> 행 삭제
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" disabled={!can.deleteColumn()} onSelect={run(() => chain().deleteColumn().run())}>
          <Trash2 /> 열 삭제
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" disabled={!can.deleteTable()} onSelect={run(() => chain().deleteTable().run())}>
          <Trash2 /> 표 삭제
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// ---------------------------------------------------------------------------
// 표 삽입 — 격자 위에서 마우스를 움직여 행×열을 고르고 클릭해 삽입한다.
// ---------------------------------------------------------------------------

const TABLE_PICKER_COLS = 10
const TABLE_PICKER_ROWS = 8
const TABLE_PICKER_WIDTH = TABLE_PICKER_COLS * 21 + 16 // 셀 18px + gap 3px, 좌우 패딩

function TableInsertButton({ editor }: { editor: Editor }) {
  const [open, setOpen] = useState(false)
  const [hover, setHover] = useState<{ rows: number; cols: number }>({ rows: 3, cols: 3 })
  const [withHeaderRow, setWithHeaderRow] = useState(true)
  const wrapRef = useRef<HTMLSpanElement | null>(null)
  const close = useCallback(() => setOpen(false), [])

  const toggle = () => {
    setHover({ rows: 3, cols: 3 })
    setOpen((v) => !v)
  }

  const insert = (rows: number, cols: number) => {
    editor.chain().focus().insertTable({ rows, cols, withHeaderRow }).run()
    setOpen(false)
  }

  return (
    <span ref={wrapRef} className="relative inline-flex">
      <Tip label="표 삽입"><button
        type="button" aria-label="표 삽입"
        onClick={toggle}
        disabled={!editor.can().insertTable()}
        className={
          "h-7 w-7 inline-flex items-center justify-center rounded disabled:opacity-30 disabled:cursor-not-allowed " +
          (open ? "bg-muted text-foreground" : "hover:bg-muted text-muted-foreground")
        }
      ><TableIcon className="h-3.5 w-3.5" /></button></Tip>
      <ToolbarPopover anchorRef={wrapRef} open={open} onClose={close} width={TABLE_PICKER_WIDTH} className="select-none">
        <div onMouseLeave={() => setHover({ rows: 3, cols: 3 })}>
          <div
            className="grid gap-[3px]"
            style={{ gridTemplateColumns: `repeat(${TABLE_PICKER_COLS}, 18px)` }}
            role="grid"
            aria-label="표 크기 선택"
          >
            {Array.from({ length: TABLE_PICKER_ROWS * TABLE_PICKER_COLS }, (_, i) => {
              const r = Math.floor(i / TABLE_PICKER_COLS) + 1
              const c = (i % TABLE_PICKER_COLS) + 1
              const active = r <= hover.rows && c <= hover.cols
              return (
                <button
                  key={i}
                  type="button"
                  role="gridcell"
                  aria-label={`${r} × ${c}`}
                  onMouseEnter={() => setHover({ rows: r, cols: c })}
                  onFocus={() => setHover({ rows: r, cols: c })}
                  onClick={() => insert(r, c)}
                  className={
                    "h-[18px] w-[18px] rounded-[2px] border " +
                    (active ? "border-primary bg-primary/25" : "border-slate-300 bg-white hover:border-slate-400")
                  }
                />
              )
            })}
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 text-[11px]">
            <span className="tabular-nums text-foreground">{hover.rows} 행 × {hover.cols} 열</span>
            <label className="inline-flex items-center gap-1 cursor-pointer text-muted-foreground">
              <input type="checkbox" checked={withHeaderRow} onChange={(e) => setWithHeaderRow(e.target.checked)} />
              머리글 행
            </label>
          </div>
        </div>
      </ToolbarPopover>
    </span>
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
  const close = useCallback(() => setOpen(false), [])

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
      <Tip label="글자 색"><button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted relative text-muted-foreground"
        aria-label="글자 색"
      >
        <Palette className="h-3.5 w-3.5" />
        <span
          aria-hidden
          className="absolute bottom-1 left-1.5 right-1.5 h-0.5 rounded-sm"
          style={{ backgroundColor: current || "transparent" }}
        />
      </button></Tip>
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
      <ToolbarPopover anchorRef={wrapRef} open={open} onClose={close} width={180}>
        <div>
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
      </ToolbarPopover>
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
  const close = useCallback(() => setOpen(false), [])

  useEffect(() => {
    if (!open) return
    setRecent(getRecentEmojis())
    setQuery("")
    setTab(() => {
      if (defaultTab) return defaultTab
      return getRecentEmojis().length > 0 ? "recent" : (EMOJI_CATEGORIES[0]?.key ?? "face")
    })
  }, [open, defaultTab])

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
      <Tip label={title}><button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-7 w-7 inline-flex items-center justify-center rounded hover:bg-muted text-muted-foreground"
        aria-label={title}
      >
        {icon ?? <Smile className="h-3.5 w-3.5" />}
      </button></Tip>
      <ToolbarPopover anchorRef={wrapRef} open={open} onClose={close} width={360}>
        <div>
          <SearchInput
            size="xs"
            className="mb-2"
            value={query}
            onValueChange={setQuery}
            placeholder="이모지 검색(예: 체크, smile, →)…"
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
      </ToolbarPopover>
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
