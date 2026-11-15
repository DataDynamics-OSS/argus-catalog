"use client"

import { useCallback } from "react"

import { useAuth } from "@/features/auth"
import { usePermissions } from "@/features/permissions/use-permissions"
import { canManageDataset } from "./permissions"

/**
 * 데이터셋 삭제 버튼을 보일지 — 생성자 · 관리자이면서 역할에 삭제 기능 권한(datasets.delete)이 있어야 한다.
 * 서버 `delete_dataset` 과 같은 기준(TODO-122). 화면은 발견성 용도이고 최종 판단은 서버가 한다.
 */
export function useCanDeleteDataset(): (row: { created_by?: string | null }) => boolean {
  const { user } = useAuth()
  const { canUseFeature } = usePermissions()
  const allowed = canUseFeature("datasets.delete")
  return useCallback((row) => allowed && canManageDataset(user, row.created_by), [allowed, user])
}
