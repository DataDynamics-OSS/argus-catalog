"use client"

import { Check } from "lucide-react"

import { Avatar, AvatarImage } from "@workspace/ui/components/avatar"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { cn } from "@workspace/ui/lib/utils"
import { AVATAR_PRESETS, type AvatarPreset } from "@/lib/avatar"

type AvatarPickerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedId: string | null
  onSelect: (preset: AvatarPreset) => void
}

export function AvatarPicker({
  open,
  onOpenChange,
  selectedId,
  onSelect,
}: AvatarPickerProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>아바타 선택</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          원하는 아바타를 선택하세요.
        </p>
        <div className="grid grid-cols-5 gap-3 pt-2">
          {AVATAR_PRESETS.map((preset) => {
            const selected = preset.id === selectedId
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => {
                  onSelect(preset)
                  onOpenChange(false)
                }}
                className={cn(
                  "relative rounded-full p-1 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                  selected
                    ? "ring-2 ring-primary"
                    : "ring-1 ring-transparent hover:ring-border"
                )}
                aria-label={preset.label}
                aria-pressed={selected}
              >
                <Avatar className="size-14">
                  <AvatarImage src={preset.dataUri} alt={preset.label} />
                </Avatar>
                {selected && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow">
                    <Check className="size-3" />
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
