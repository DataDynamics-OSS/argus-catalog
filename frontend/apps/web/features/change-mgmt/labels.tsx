"use client"

import { useEffect, useState } from "react"
import { ChevronDown } from "lucide-react"

import { StatusBadge as ToneBadge, type StatusTone } from "@workspace/ui/components/status-badge"
import { RichTextViewer } from "@/components/comments/rich-text-viewer"
import { fetchUsers } from "@/features/users/api"
import type { User } from "@/features/users/data/schema"
import type { ChangeType, CRStatus, Priority } from "./api"

/** 상태 톤(TODO-107) — 진행(상신 · 결재 · 적용 예정) 파랑, 승인 · 적용 초록, 반려 빨강, 롤백 주황, 취소 · 종료 무채색 */
export const STATUS_TONE: Record<CRStatus, StatusTone> = {
  DRAFT: "neutral",
  SUBMITTED: "info",
  APPROVING: "info",
  APPROVED: "success",
  REJECTED: "danger",
  SCHEDULED: "info",
  APPLIED: "success",
  ROLLED_BACK: "warning",
  CANCELLED: "muted",
  CLOSED: "muted",
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
  // 기록용 — 승인·적용해도 데이터셋을 바꾸지 않는다(DEF-065)
  COSMETIC: "표기·메타데이터 — 기록용 (Cosmetic)",
}

export const PRIORITY_LABEL: Record<Priority, string> = {
  EMERGENCY: "긴급 (Emergency)",
  HIGH: "높음 (High)",
  NORMAL: "보통 (Normal)",
  LOW: "낮음 (Low)",
}

/** 우선순위 톤(DEF-116) — 긴급 빨강 · 높음 주황 · 보통 · 낮음 무채색 */
export const PRIORITY_TONE: Record<Priority, StatusTone> = {
  EMERGENCY: "danger",
  HIGH: "warning",
  NORMAL: "neutral",
  LOW: "neutral",
}

/** 우선순위 배지 — 영어 병기 없이 "긴급" · "높음" … (선택 목록은 `PRIORITY_LABEL` 그대로) */
export function PriorityBadge({ priority }: { priority: Priority }) {
  return <ToneBadge tone={PRIORITY_TONE[priority] ?? "neutral"} label={(PRIORITY_LABEL[priority] ?? priority).split(" (")[0]} />
}

export function StatusBadge({ status }: { status: CRStatus }) {
  return <ToneBadge tone={STATUS_TONE[status] ?? "neutral"} label={STATUS_LABEL[status] ?? status} />
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
    <div className="mt-2 text-sm">
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
