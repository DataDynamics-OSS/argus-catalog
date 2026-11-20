"use client"

// 관리 › 감사 로그(TODO-152) — 사용자 관리 · 설정 · 권한(변경 · 거부) · 운영 작업 감사 로그를 한곳에서 본다.
// 메뉴는 관리자 또는 audit.view(명시 허용형)를 받은 역할에 열린다(permission-registry grantedBy · 서버 ADMIN_MENU_GRANTS).
// 각 화면의 "변경 이력" 단추와 같은 API(GET /admin/audit)를 쓴다.

import { DashboardHeader } from "@/components/dashboard-header"
import { AuditLogList } from "@/features/audit/admin-audit-sheet"

export default function AuditPage() {
  return (
    <>
      <DashboardHeader title="감사 로그" />
      <div className="flex flex-1 flex-col gap-3 p-4">
        <p className="text-sm text-muted-foreground">
          누가 언제 무엇을 바꿨는지, 어떤 요청이 권한이 없어 거부됐는지 최신순으로 보여 줍니다. 행을 누르면 바뀐 항목을
          펼칩니다. 비밀값과 전화번호는 값 없이 &ldquo;(변경됨)&rdquo;으로만 남습니다.
        </p>
        <AuditLogList filters />
      </div>
    </>
  )
}
