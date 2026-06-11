"use client"

import { useEffect } from "react"

import { useDatasets } from "./datasets-provider"
import { DatasetsTable } from "./datasets-table"

// 목록 스크롤 위치를 모듈 레벨에 보관 — 상세 라우트로 갔다가 router.back() 으로
// 돌아올 때(목록 페이지 remount + 재조회) 위치를 복원한다. 하드 리로드 시 0 으로 초기화.
let savedListScroll = 0

export function DatasetsTableWrapper() {
  const { datasets, isLoading } = useDatasets()

  // 목록에 머무는 동안 스크롤 위치 추적
  useEffect(() => {
    const onScroll = () => {
      savedListScroll = window.scrollY
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  // 데이터 로드가 끝나 목록 높이가 확정되면 저장된 위치로 복원(레이아웃 안정화 후 2-rAF)
  useEffect(() => {
    if (isLoading || savedListScroll <= 0) return
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => window.scrollTo(0, savedListScroll))
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [isLoading])

  return <DatasetsTable data={datasets} isLoading={isLoading} />
}
