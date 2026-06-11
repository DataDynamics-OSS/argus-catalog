"use client"

import { useEffect, useState } from "react"
import { ChevronDown } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { RichTextViewer } from "@/components/comments/rich-text-viewer"
import { fetchUsers } from "@/features/users/api"
import type { User } from "@/features/users/data/schema"
import type { ChangeType, CRStatus, Priority } from "./api"

export const STATUS_STYLE: Record<CRStatus, string> = {
  DRAFT: "bg-gray-200 text-gray-700",
  SUBMITTED: "bg-blue-200 text-blue-800",
  APPROVING: "bg-amber-200 text-amber-800",
  APPROVED: "bg-emerald-200 text-emerald-800",
  REJECTED: "bg-rose-200 text-rose-800",
  SCHEDULED: "bg-violet-200 text-violet-800",
  APPLIED: "bg-green-300 text-green-900",
  ROLLED_BACK: "bg-orange-200 text-orange-800",
  CANCELLED: "bg-gray-300 text-gray-800",
  CLOSED: "bg-gray-400 text-gray-900",
}

export const STATUS_LABEL: Record<CRStatus, string> = {
  DRAFT: "임시저장",
  SUBMITTED: "상신됨",
  APPROVING: "결재중",
  APPROVED: "승인됨",
  REJECTED: "반려됨",
  SCHEDULED: "적용예정",
  APPLIED: "적용완료",
  ROLLED_BACK: "롤백됨",
  CANCELLED: "취소됨",
  CLOSED: "종료됨",
}

export const CHANGE_TYPE_LABEL: Record<ChangeType, string> = {
  BREAKING: "호환성 깨짐 (Breaking)",
  NON_BREAKING: "호환 유지 (Non-breaking)",
  ADDITIVE: "추가 (Additive)",
  COSMETIC: "표기·메타데이터 (Cosmetic)",
}

export const PRIORITY_LABEL: Record<Priority, string> = {
  EMERGENCY: "긴급 (Emergency)",
  HIGH: "높음 (High)",
  NORMAL: "보통 (Normal)",
  LOW: "낮음 (Low)",
}

export function StatusBadge({ status }: { status: CRStatus }) {
  return <Badge className={STATUS_STYLE[status]}>{STATUS_LABEL[status]}</Badge>
}

const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

/**
 * 사용자 목록을 받아 username/email → 표시 이름 매핑과 라벨 헬퍼를 제공한다.
 * 결재자(username)·참조자(name/email) 를 이름으로 표시할 때 사용.
 */
export function useUserNames() {
  const [userMap, setUserMap] = useState<Record<string, string>>({})
  const [emailMap, setEmailMap] = useState<Record<string, string>>({})

  useEffect(() => {
    fetchUsers({ pageSize: 0 })
      .then((r) => {
        const m: Record<string, string> = {}
        const e: Record<string, string> = {}
        for (const u of r.items) {
          m[u.username] = userDisplayName(u)
          if (u.email) e[u.email] = userDisplayName(u)
        }
        setUserMap(m)
        setEmailMap(e)
      })
      .catch(() => {})
  }, [])

  /** 결재자(username) → '이름 (username)' 또는 username. */
  const approverLabel = (username: string) =>
    userMap[username] ? `${userMap[username]} (${username})` : username

  /** 참조자(name/email) → '이름 (이메일)' 또는 이메일/이름. */
  const referrerLabel = (name?: string | null, email?: string | null) => {
    const resolved = name || (email ? emailMap[email] : undefined)
    if (resolved && email) return `${resolved} (${email})`
    return resolved ?? email ?? "-"
  }

  return { userMap, emailMap, approverLabel, referrerLabel }
}

/** 리치 HTML 본문을 미리보기용 순수 텍스트로 변환한다. */
export function htmlToText(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<\/(p|div|li|h[1-6]|tr)>/gi, " ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim()
}

/** 목록 카드 본문: 기본 2줄 미리보기, 길면 더보기로 서식 포함 전체 표시. */
export function DescriptionPreview({ html }: { html: string }) {
  const [open, setOpen] = useState(false)
  const text = htmlToText(html)
  if (!text) return null
  const long = text.length > 100
  return (
    <div className="mt-2 text-sm text-muted-foreground">
      {open ? (
        <RichTextViewer html={html} />
      ) : (
        <p className="line-clamp-2">{text}</p>
      )}
      {long && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setOpen((v) => !v)
          }}
          className="mt-1 inline-flex items-center gap-0.5 text-xs font-medium text-primary hover:underline"
        >
          {open ? "접기" : "더보기"}
          <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      )}
    </div>
  )
}
