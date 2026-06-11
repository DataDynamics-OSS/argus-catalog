"use client"

import { Plus } from "lucide-react"
import { Button } from "@workspace/ui/components/button"
import { useAuth } from "@/features/auth"
import { useDatasets } from "./datasets-provider"

export function DatasetsPrimaryButtons() {
  const { setOpen } = useDatasets()
  const { user } = useAuth()

  if (!user?.is_admin) return null

  return (
    <Button size="sm" onClick={() => setOpen("add")}>
      <Plus className="mr-1 h-4 w-4" />
      데이터셋 추가
    </Button>
  )
}
