// API 카탈로그 공용 라벨·상수 — 목록(apis-table)·상세(apis-detail, 탭들)·등록 다이얼로그가 함께 쓴다.
// UI 컴포넌트 파일끼리 상수 때문에 서로 import 하지 않도록 별도 모듈로 둔다.
import type { StatusMeta, StatusTone } from "@workspace/ui/components/status-badge"

export const STATUS_ORDER = ["draft", "published", "deprecated", "retired"]

export const API_STATUS_LABEL: Record<string, string> = {
  draft: "초안", published: "게시", deprecated: "사용 중단", retired: "폐기",
}
/** 상태 배지 톤(TODO-107) — 게시 초록 · 사용 중단 주황 · 폐기 무채색 칠 · 초안 테두리 */
const API_STATUS_TONE: Record<string, StatusTone> = {
  draft: "neutral", published: "success", deprecated: "warning", retired: "muted",
}
export const apiStatus = (status: string): StatusMeta => ({
  label: API_STATUS_LABEL[status] ?? status,
  tone: API_STATUS_TONE[status] ?? "neutral",
})

export const SPEC_FORMAT_LABEL: Record<string, string> = {
  openapi: "OpenAPI", openapi2: "OpenAPI 2", openapi3: "OpenAPI 3", asyncapi: "AsyncAPI",
}

// 프로토콜 표시 라벨(저장값 정규화) — 예: graphql→GraphQL, grpc→gRPC
export const PROTOCOL_LABEL: Record<string, string> = { rest: "REST", graphql: "GraphQL", grpc: "gRPC", soap: "SOAP", webhook: "Webhook", asyncapi: "AsyncAPI" }
// 수동 등록에서 선택 가능한 프로토콜(저장값) — 엔드포인트 편집기(endpoint-edit-dialog PROTOCOL_OPS)의 키와 일치.
// "기타"는 오퍼레이션 정의가 없어 REST 폼으로 편집된다. AsyncAPI 는 오퍼레이션 정의가 없어 수동 등록 대상에서 제외.
export const MANUAL_PROTOCOLS = ["REST", "GraphQL", "gRPC", "SOAP", "Webhook", "기타"] as const
export const protocolLabel = (p?: string | null) => (p ? (PROTOCOL_LABEL[p.toLowerCase()] ?? p) : null)

// 인증 상태·등급 라벨
export const CERT_LABEL: Record<string, string> = { NONE: "없음", CERTIFIED: "인증됨", IN_REVIEW: "검토 중", DEPRECATED: "지원 중단" }
export const TIER_LABEL: Record<string, string> = { GOLD: "골드", SILVER: "실버", BRONZE: "브론즈" }

// 메서드 · 오퍼레이션 유형별 배지 색 — 키는 소문자(값은 대소문자 섞여 저장된다: REST 는 GET, 수동 등록 유형은 publish).
// 성격이 같으면 같은 색: 읽기 · 받기 = info, 만들기 · 보내기 = success, 바꾸기 = warning, 지우기 = danger, 보조 = muted.
// 표에 없는 유형만 무채색(neutral).
const METHOD_TONE: Record<string, StatusTone> = {
  get: "info", post: "success", put: "warning", patch: "warning", delete: "danger", head: "muted", options: "muted",
  // GraphQL 오퍼레이션 유형
  query: "info", mutation: "warning", subscription: "success",
  // gRPC RPC 유형
  unary: "info", "server-streaming": "success", "client-streaming": "warning", "bidi-streaming": "warning",
  // Webhook / 이벤트 — 발행(보냄) · 구독(받음) · 이벤트
  publish: "success", subscribe: "info", event: "warning",
  // SOAP · 기타
  operation: "info",
}

/** 메서드 배지 색 — 대소문자 · 밑줄(server_streaming) 구분 없이 찾는다. */
export const methodTone = (method: string): StatusTone =>
  METHOD_TONE[method.trim().toLowerCase().replace(/_/g, "-")] ?? "neutral"
