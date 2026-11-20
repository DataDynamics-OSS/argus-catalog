/**
 * AI-Ready 체크리스트 항목 → 고칠 곳 (TODO-151).
 *
 * 미흡 · 판정 필요 항목 옆에 링크를 둔다. 고칠 곳이 분명한 항목만 — 메타데이터 탭의 줄(`field` 는 메타데이터 탭의
 * 항목 이름과 같아야 스크롤 · 강조된다), 다른 탭, 같은 AI-Ready 탭 아래의 배포본 · 내보내기 카드.
 * Argus 밖의 일(제공 플랫폼 · 외부 문서)이거나 담당자 판정만 있는 항목은 두지 않는다.
 */

import type { DatasetTab } from "./links"

export type AiReadyTarget =
  | { kind: "tab"; tab: DatasetTab; field?: string; label: string }
  | { kind: "card"; card: "distributions" | "export"; label: string }

const meta = (field: string, label = field): AiReadyTarget => ({ kind: "tab", tab: "metadata", field, label: `메타데이터 › ${label}` })
const tab = (t: DatasetTab, label: string): AiReadyTarget => ({ kind: "tab", tab: t, label })
const card = (c: "distributions" | "export", label: string): AiReadyTarget => ({ kind: "card", card: c, label })

export const AI_READY_TARGETS: Record<string, AiReadyTarget> = {
  C01: meta("데이터 형식"),
  C02: meta("데이터 유형"),
  C03: tab("schema", "스키마"),
  C04: card("export", "내보내기"),
  C05: meta("관련법령", "AI 활용"),
  C06: tab("quality", "품질"),
  C07: meta("압축"),
  C08: tab("schema", "스키마"),
  C09: tab("quality", "품질"),
  C10: tab("terms", "표준 용어"),
  C11: meta("결측·특수값 표기"),
  C13: meta("결측·특수값 표기"),
  C14: tab("quality", "품질"),
  C15: meta("합성 데이터"),
  C16: meta("결측 정보"),
  C17: tab("lineage", "리니지"),
  C20: tab("schema", "스키마"),
  C21: meta("알려진 편향"),
  C22: meta("개인정보 보호조치"),
  C23: meta("라이선스"),
  C25: meta("영구 식별자"),
  C27: card("distributions", "배포본"),
  C30: meta("문의 연락처"),
  C31: tab("comments", "댓글"),
  C32: meta("버전"),
  C33: card("distributions", "배포본"),
  T5: meta("개인정보 보호조치"),
}
