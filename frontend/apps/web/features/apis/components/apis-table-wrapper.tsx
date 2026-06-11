"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"

import { useApis } from "./apis-provider"
import { ApisTable } from "./apis-table"
import { ApisDetail } from "./apis-detail"

export function ApisTableWrapper() {
  const { apis, isLoading, selectedApiName, setSelectedApiName } = useApis()
  const params = useSearchParams()

  // 즐겨찾기 등 외부에서 ?api=<name> 으로 진입하면 해당 API 상세를 연다(최초 1회).
  useEffect(() => {
    const a = params.get("api")
    if (a) setSelectedApiName(a)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (selectedApiName) return <ApisDetail name={selectedApiName} />
  return <ApisTable data={apis} isLoading={isLoading} />
}
