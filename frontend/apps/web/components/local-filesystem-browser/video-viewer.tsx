"use client"

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

/** Map extension to MIME type for the <video> source element. */
const videoMimeTypes: Record<string, string> = {
  mp4: "video/mp4",
  webm: "video/webm",
  ogg: "video/ogg",
  mov: "video/quicktime",
  m4v: "video/x-m4v",
  avi: "video/x-msvideo",
  mkv: "video/x-matroska",
}

type VideoViewerProps = {
  /** Presigned download URL. */
  url: string
  /** File extension (without dot). */
  extension: string
}

export function VideoViewer({ url, extension }: VideoViewerProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let revoke: string | null = null

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to fetch video (${res.status})`)
        return res.blob()
      })
      .then((blob) => {
        const objUrl = URL.createObjectURL(blob)
        revoke = objUrl
        setBlobUrl(objUrl)
        setIsLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load video")
        setIsLoading(false)
      })

    return () => {
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [url])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[400px]">
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

  const mime = videoMimeTypes[extension] ?? "video/mp4"

  return (
    <div className="flex items-center justify-center">
      <video
        controls
        className="max-w-full max-h-[65vh] rounded"
        preload="metadata"
      >
        <source src={blobUrl!} type={mime} />
        Your browser does not support this video format.
      </video>
    </div>
  )
}
