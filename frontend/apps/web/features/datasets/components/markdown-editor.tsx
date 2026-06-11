"use client"

import { useEffect } from "react"
import { useEditor, EditorContent } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import Placeholder from "@tiptap/extension-placeholder"
// Underline is included in StarterKit — do not import separately
import {
  Bold, Italic, Strikethrough, Underline as UnderlineIcon, Code, CodeSquare,
  List, ListOrdered, Heading1, Heading2, Heading3,
  Quote, Minus, Link, Unlink, WrapText, Undo, Redo, Eraser,
} from "lucide-react"
import { Separator } from "@workspace/ui/components/separator"
import TurndownService from "turndown"
import { marked } from "marked"

// ---------------------------------------------------------------------------
// Markdown ↔ HTML conversion
// ---------------------------------------------------------------------------

const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-",
})

function markdownToHtml(md: string): string {
  if (!md) return ""
  return marked.parse(md, { async: false }) as string
}

function htmlToMarkdown(html: string): string {
  if (!html) return ""
  return turndown.turndown(html)
}

// ---------------------------------------------------------------------------
// Toolbar button
// ---------------------------------------------------------------------------

function ToolbarButton({
  onClick,
  active = false,
  disabled = false,
  children,
  title,
}: {
  onClick: () => void
  active?: boolean
  disabled?: boolean
  children: React.ReactNode
  title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`p-1.5 rounded hover:bg-muted transition-colors ${
        active ? "bg-muted text-foreground" : "text-muted-foreground"
      } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Editor component
// ---------------------------------------------------------------------------

type MarkdownEditorProps = {
  value: string
  onChange: (markdown: string) => void
  editable?: boolean
  placeholder?: string
}

export function MarkdownEditor({
  value,
  onChange,
  editable = true,
  placeholder = "Write description in Markdown...",
}: MarkdownEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        codeBlock: { HTMLAttributes: { class: "tiptap-code-block" } },
        link: { openOnClick: false, HTMLAttributes: { class: "text-primary underline" } },
      }),
      Placeholder.configure({ placeholder }),
    ],
    content: markdownToHtml(value),
    editable,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: "prose prose-sm max-w-none focus:outline-none min-h-[80px] px-3 py-2",
        style: "font-family: var(--font-d2coding), 'D2Coding', monospace; font-size: 13px;",
      },
    },
    onUpdate: ({ editor: e }) => {
      const md = htmlToMarkdown(e.getHTML())
      onChange(md)
    },
  })

  // Sync external value changes
  useEffect(() => {
    if (!editor) return
    const currentMd = htmlToMarkdown(editor.getHTML())
    if (currentMd !== value) {
      editor.commands.setContent(markdownToHtml(value))
    }
  // Only update when value changes from outside, not from typing
   
  }, [value, editor])

  // Sync editable state
  useEffect(() => {
    if (editor) editor.setEditable(editable)
  }, [editor, editable])

  if (!editor) return null

  return (
    <div className={`border rounded-md ${editable ? "ring-ring/20 focus-within:ring-2" : ""}`}>
      {/* Toolbar (only in edit mode) */}
      {editable && (
        <div className="flex items-center gap-0.5 px-2 py-1 border-b bg-muted/30 flex-wrap">
          {/* Headings */}
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
            active={editor.isActive("heading", { level: 1 })}
            title="Heading 1 (Ctrl+Alt+1)"
          >
            <Heading1 className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
            active={editor.isActive("heading", { level: 2 })}
            title="Heading 2 (Ctrl+Alt+2)"
          >
            <Heading2 className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
            active={editor.isActive("heading", { level: 3 })}
            title="Heading 3 (Ctrl+Alt+3)"
          >
            <Heading3 className="h-4 w-4" />
          </ToolbarButton>

          <Separator orientation="vertical" className="h-5 mx-1" />

          {/* Text formatting */}
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleBold().run()}
            active={editor.isActive("bold")}
            title="Bold (Ctrl+B)"
          >
            <Bold className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleItalic().run()}
            active={editor.isActive("italic")}
            title="Italic (Ctrl+I)"
          >
            <Italic className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleUnderline().run()}
            active={editor.isActive("underline")}
            title="Underline (Ctrl+U)"
          >
            <UnderlineIcon className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleStrike().run()}
            active={editor.isActive("strike")}
            title="Strikethrough (Ctrl+Shift+S)"
          >
            <Strikethrough className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleCode().run()}
            active={editor.isActive("code")}
            title="Inline Code (Ctrl+E)"
          >
            <Code className="h-4 w-4" />
          </ToolbarButton>

          <Separator orientation="vertical" className="h-5 mx-1" />

          {/* Lists & blocks */}
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleBulletList().run()}
            active={editor.isActive("bulletList")}
            title="Bullet List (Ctrl+Shift+8)"
          >
            <List className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
            active={editor.isActive("orderedList")}
            title="Numbered List (Ctrl+Shift+7)"
          >
            <ListOrdered className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleBlockquote().run()}
            active={editor.isActive("blockquote")}
            title="Blockquote (Ctrl+Shift+B)"
          >
            <Quote className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleCodeBlock().run()}
            active={editor.isActive("codeBlock")}
            title="Code Block (Ctrl+Alt+C)"
          >
            <CodeSquare className="h-4 w-4" />
          </ToolbarButton>

          <Separator orientation="vertical" className="h-5 mx-1" />

          {/* Insert */}
          <ToolbarButton
            onClick={() => editor.chain().focus().setHorizontalRule().run()}
            title="Horizontal Rule"
          >
            <Minus className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().setHardBreak().run()}
            title="Line Break (Shift+Enter)"
          >
            <WrapText className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => {
              const url = window.prompt("Enter URL:")
              if (url) editor.chain().focus().setLink({ href: url }).run()
            }}
            active={editor.isActive("link")}
            title="Add Link"
          >
            <Link className="h-4 w-4" />
          </ToolbarButton>
          {editor.isActive("link") && (
            <ToolbarButton
              onClick={() => editor.chain().focus().unsetLink().run()}
              title="Remove Link"
            >
              <Unlink className="h-4 w-4" />
            </ToolbarButton>
          )}

          <Separator orientation="vertical" className="h-5 mx-1" />

          {/* Undo / Redo / Clear */}
          <ToolbarButton
            onClick={() => editor.chain().focus().undo().run()}
            disabled={!editor.can().undo()}
            title="Undo (Ctrl+Z)"
          >
            <Undo className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().redo().run()}
            disabled={!editor.can().redo()}
            title="Redo (Ctrl+Shift+Z)"
          >
            <Redo className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().clearNodes().unsetAllMarks().run()}
            title="Clear Formatting"
          >
            <Eraser className="h-4 w-4" />
          </ToolbarButton>
        </div>
      )}

      {/* Editor content */}
      <EditorContent editor={editor} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Viewer component (readonly markdown rendering)
// ---------------------------------------------------------------------------

type MarkdownViewerProps = {
  value: string
  onClick?: () => void
  className?: string
}

export function MarkdownViewer({ value, onClick, className = "" }: MarkdownViewerProps) {
  const editor = useEditor({
    extensions: [StarterKit],
    content: markdownToHtml(value),
    editable: false,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: `prose prose-sm max-w-none ${className}`,
        style: "font-family: var(--font-d2coding), 'D2Coding', monospace; font-size: 13px;",
      },
    },
  })

  useEffect(() => {
    if (editor && value) {
      editor.commands.setContent(markdownToHtml(value))
    }
  }, [editor, value])

  return (
    <div
      onClick={onClick}
      className={onClick ? "cursor-pointer rounded-md px-2 py-1 -mx-2 hover:bg-muted transition-colors" : ""}
      title={onClick ? "Click to edit" : undefined}
    >
      {editor ? (
        <EditorContent editor={editor} />
      ) : (
        <p className="text-sm text-muted-foreground italic">No description</p>
      )}
    </div>
  )
}
