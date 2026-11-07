"use client"

import { ModelsProvider } from "@/features/models/components/models-provider"

/**
 * MLflow 모델 목록(`/dashboard/models`)과 상세(`/dashboard/models/[name]`)가 공유하는 레이아웃.
 * Provider 를 레이아웃에 두어 상세로 이동했다 돌아와도 검색어·필터·페이지·스크롤 위치가 유지된다.
 */
export default function ModelsLayout({ children }: { children: React.ReactNode }) {
  return <ModelsProvider>{children}</ModelsProvider>
}
