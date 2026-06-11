"use client"

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

type PdfViewerProps = {
  /** Presigned download URL for the PDF file. */
  url: string
}

export function PdfViewer({ url }: PdfViewerProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let revoke: string | null = null

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to fetch PDF (${res.status})`)
        return res.blob()
      })
      .then((blob) => {
        const objUrl = URL.createObjectURL(blob)
        revoke = objUrl
        setBlobUrl(objUrl)
        setIsLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load PDF")
        setIsLoading(false)
      })

    return () => {
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [url])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[600px]">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-[200px]">
        <p className="text-sm text-muted-foreground">{error}</p>
      </div>
    )
  }

  return (
    <iframe
      src={blobUrl!}
      className="w-full h-[600px] rounded border"
      title="PDF Viewer"
    />
  )
}
