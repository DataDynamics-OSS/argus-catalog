"use client"

import { Plus } from "lucide-react"
import { Button } from "@workspace/ui/components/button"
import { usePermissions } from "@/features/permissions/use-permissions"
import { useDatasets } from "./datasets-provider"

export function DatasetsPrimaryButtons() {
  const { setOpen } = useDatasets()
  const { loaded, canUseFeature } = usePermissions()

  // 서버 판정과 같다: 관리자는 항상, 일반 사용자는 설정 › 데이터셋 등록이 켜져 있고
  // datasets.create 가 허용될 때만 (/permissions/me 의 denied_features 에 반영됨).
  // 권한 로드 전에는 숨겨서 일반 사용자에게 버튼이 잠깐 보였다 사라지지 않게 한다.
  if (!loaded || !canUseFeature("datasets.create")) return null

  return (
    <Button size="sm" onClick={() => setOpen("add")}>
      <Plus className="mr-1 h-4 w-4" />
      데이터셋 추가
    </Button>
  )
}
