"use client"

import { useEffect, useState } from "react"
import { Loader2, Music } from "lucide-react"

/** Map extension to MIME type for the <audio> source element. */
const audioMimeTypes: Record<string, string> = {
  mp3: "audio/mpeg",
  wav: "audio/wav",
  m4a: "audio/mp4",
  flac: "audio/flac",
  aac: "audio/aac",
  ogg: "audio/ogg",
  wma: "audio/x-ms-wma",
}

type AudioViewerProps = {
  /** Presigned download URL. */
  url: string
  /** File extension (without dot). */
  extension: string
  /** File name for display. */
  fileName: string
}

export function AudioViewer({ url, extension, fileName }: AudioViewerProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let revoke: string | null = null

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to fetch audio (${res.status})`)
        return res.blob()
      })
      .then((blob) => {
        const objUrl = URL.createObjectURL(blob)
        revoke = objUrl
        setBlobUrl(objUrl)
        setIsLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load audio")
        setIsLoading(false)
      })

    return () => {
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [url])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[200px]">
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

  const mime = audioMimeTypes[extension] ?? "audio/mpeg"

  return (
    <div className="flex flex-col items-center justify-center gap-6 py-8">
      <div className="flex items-center justify-center w-24 h-24 rounded-full bg-muted">
        <Music className="h-10 w-10 text-muted-foreground" />
      </div>
      <p className="text-sm text-muted-foreground truncate max-w-[400px]">{fileName}</p>
      <audio controls preload="metadata" className="w-full max-w-[500px]">
        <source src={blobUrl!} type={mime} />
        Your browser does not support this audio format.
      </audio>
    </div>
  )
}
