"use client"

import { CircleHelp } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { useCurrentHelp } from "./help-context"
import { openHelp } from "./open-help"

/** 헤더의 `?` 아이콘 — 지금 화면의 도움말을 별도 창으로 연다. */
export function HelpButton() {
  const help = useCurrentHelp()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-9 w-9"
          aria-label={`도움말: ${help.title} (새 창)`}
          onClick={() => openHelp(help.url, help.title)}
        >
          <CircleHelp className="h-4 w-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        도움말: {help.title} <span className="opacity-70">(F1)</span>
      </TooltipContent>
    </Tooltip>
  )
}
