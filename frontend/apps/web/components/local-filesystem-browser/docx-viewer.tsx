"use client"

type DocumentPreviewData = {
  format: string
  html: string
}

type DocxViewerProps = {
  /** Server-side preview data. */
  data: DocumentPreviewData
}

export function DocxViewer({ data }: DocxViewerProps) {
  return (
    <div
      className="prose prose-sm max-w-none overflow-auto max-h-[600px] p-4 border rounded bg-white dark:bg-background"
      dangerouslySetInnerHTML={{ __html: data.html }}
    />
  )
}
