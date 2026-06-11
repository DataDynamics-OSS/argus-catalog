"use client"

import { Trash2 } from "lucide-react"
import { Button } from "@workspace/ui/components/button"
import { useAuth } from "@/features/auth"
import { useModels } from "./models-provider"

export function ModelsPrimaryButtons() {
  const { user } = useAuth()
  const { setOpen, selectedNames, setDeleteTargetNames } = useModels()

  if (!user?.is_admin) return null

  return (
    <Button
      size="sm"
      variant="destructive"
      disabled={selectedNames.length === 0}
      onClick={() => {
        setDeleteTargetNames(selectedNames)
        setOpen("delete")
      }}
    >
      <Trash2 className="mr-1 h-4 w-4" />
      모델 삭제
    </Button>
  )
}
