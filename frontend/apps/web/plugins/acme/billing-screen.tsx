"use client"

// 예시 화면 — ACME 고객사 전용 '정산' 화면.
// 코어 컴포넌트(DashboardHeader, @workspace/ui)를 그대로 재사용하므로 룩앤필이 일관된다.
// 백엔드 데이터가 필요하면 코어의 authFetch 로 플러그인 API(/api/v1/acme/...)를 호출하면 된다.

import { Receipt } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { DashboardHeader } from "@/components/dashboard-header"

export function BillingScreen() {
  return (
    <>
      <DashboardHeader title="정산 (ACME)" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <p className="text-sm text-muted-foreground">
          이 화면은 ACME 고객사 전용 플러그인 화면입니다. 오픈소스 코어에는 존재하지
          않으며, plugins/acme/ 폴더만으로 추가되었습니다.
        </p>
        <div className="grid gap-3 md:grid-cols-3">
          {[
            { label: "이번 달 청구액", value: "₩12,400,000" },
            { label: "미정산 건", value: "37" },
            { label: "연체", value: "2" },
          ].map((m) => (
            <Card key={m.label}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Receipt className="h-4 w-4" />
                  {m.label}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold">{m.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </>
  )
}
