// API 카탈로그 공용 라벨·상수 — 목록(apis-table)·상세(apis-detail, 탭들)·등록 다이얼로그가 함께 쓴다.
// UI 컴포넌트 파일끼리 상수 때문에 서로 import 하지 않도록 별도 모듈로 둔다.

export const STATUS_ORDER = ["draft", "published", "deprecated", "retired"]

export const API_STATUS_LABEL: Record<string, string> = {
  draft: "초안", published: "게시", deprecated: "사용 중단", retired: "폐기",
}
export const API_STATUS_VARIANTS: Record<string, string> = {
  draft: "bg-muted text-foreground",
  published: "bg-emerald-600 text-white",
  deprecated: "bg-amber-500 text-white",
  retired: "bg-zinc-500 text-white",
}

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
