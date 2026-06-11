"use client"

type SlideData = {
  slide_number: number
  texts: string[]
  notes: string
}

type DocumentPreviewData = {
  format: string
  html: string
  slides?: SlideData[] | null
}

type PptxViewerProps = {
  /** Server-side preview data. */
  data: DocumentPreviewData
}

export function PptxViewer({ data }: PptxViewerProps) {
  const slides = data.slides ?? []

  return (
    <div className="overflow-auto max-h-[600px] space-y-4">
      {slides.map((slide) => (
        <div
          key={slide.slide_number}
          className="border rounded p-4 bg-white dark:bg-background"
        >
          <h3 className="text-sm font-semibold text-muted-foreground mb-2">
            슬라이드 {slide.slide_number}
          </h3>
          <div className="space-y-1">
            {slide.texts.map((text, i) => (
              <p key={i} className="text-sm">{text}</p>
            ))}
          </div>
          {slide.notes && (
            <blockquote className="mt-3 pl-3 border-l-2 text-xs text-muted-foreground italic">
              {slide.notes}
            </blockquote>
          )}
        </div>
      ))}
      {slides.length === 0 && (
        <div className="flex items-center justify-center h-[200px]">
          <p className="text-sm text-muted-foreground">슬라이드가 없습니다</p>
        </div>
      )}
    </div>
  )
}
