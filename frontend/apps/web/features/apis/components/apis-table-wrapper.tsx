"use client"

import { useApis } from "./apis-provider"
import { ApisTable } from "./apis-table"
import { ApisDetail } from "./apis-detail"
import { ApisStats } from "./apis-stats"

export function ApisTableWrapper() {
  // 상세 여부는 주소의 ?api= 로 결정(provider) — 즐겨찾기 등 외부 링크·뒤로 가기·새로고침 모두 같은 경로.
  const { selectedApiName } = useApis()

  // key: 다른 API 로 바뀌면 상세를 새로 마운트해 이전 API 의 탭 상태(편집·입력 폼)가 남지 않게 한다.
  if (selectedApiName) return <ApisDetail key={selectedApiName} name={selectedApiName} />
  return (
    <>
      <ApisStats />
      <ApisTable />
    </>
  )
}
