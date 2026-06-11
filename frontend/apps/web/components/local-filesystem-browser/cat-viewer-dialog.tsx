"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2 } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Label } from "@workspace/ui/components/label"
import { RadioGroup, RadioGroupItem } from "@workspace/ui/components/radio-group"

import type { FilesystemEntry } from "./types"
import { HexViewer } from "./hex-viewer"
import { CodeViewer } from "@/components/code-viewer"

type ViewType = "raw" | "hex"

type CatViewerDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  entry: FilesystemEntry | null
  /** Returns a download URL for the given path. */
  getDownloadUrl: (path: string) => Promise<string>
}

export function CatViewerDialog({
  open,
  onOpenChange,
  entry,
  getDownloadUrl,
}: CatViewerDialogProps) {
  const [viewType, setViewType] = useState<ViewType>("raw")
  const [rawContent, setRawContent] = useState<string | null>(null)
  const [hexData, setHexData] = useState<ArrayBuffer | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reset = useCallback(() => {
    setRawContent(null)
    setHexData(null)
    setIsLoading(false)
    setError(null)
  }, [])

  useEffect(() => {
    if (!open || !entry || entry.kind === "folder") {
      reset()
      return
    }

    setIsLoading(true)
    setError(null)

    getDownloadUrl(entry.key)
      .then(async (url) => {
        const res = await fetch(url)
        if (!res.ok) throw new Error(`Failed to fetch file (${res.status})`)
        const buf = await res.arrayBuffer()
        setHexData(buf)
        setRawContent(new TextDecoder("UTF-8", { fatal: false }).decode(buf))
        setIsLoading(false)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load file")
        setIsLoading(false)
      })
  }, [open, entry, getDownloadUrl, reset])

  if (!entry) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px] max-h-[85vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-center gap-4">
            <DialogTitle className="truncate">{entry.name}</DialogTitle>
            <RadioGroup
              value={viewType}
              onValueChange={(v) => setViewType(v as ViewType)}
              className="flex items-center gap-4 ml-4"
            >
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="raw" id="cat-raw" />
                <Label htmlFor="cat-raw" className="text-sm cursor-pointer">
                  RAW
                </Label>
              </div>
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="hex" id="cat-hex" />
                <Label htmlFor="cat-hex" className="text-sm cursor-pointer">
                  Hexa
                </Label>
              </div>
            </RadioGroup>
          </div>
        </DialogHeader>

        <div className="flex-1 min-h-0 mt-2">
          {isLoading && (
            <div className="flex items-center justify-center h-[500px]">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {error && (
            <div className="flex items-center justify-center h-[200px]">
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          )}

          {!isLoading && !error && viewType === "raw" && rawContent !== null && (
            <CodeViewer
              code={rawContent}
              language="plaintext"
              height={500}
              wordWrap="on"
              copyLabel={entry.name}
            />
          )}

          {!isLoading && !error && viewType === "hex" && hexData !== null && (
            <HexViewer data={hexData} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
