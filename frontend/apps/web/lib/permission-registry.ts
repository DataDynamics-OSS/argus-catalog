/**
 * 권한 레지스트리 — 메뉴/기능의 단일 정의처.
 *
 * 백엔드는 key 문자열만 저장하므로(open-by-default), 여기 정의가 곧
 * 권한 관리 화면의 행이 된다. 새 메뉴/기능을 통제하려면 이 파일에
 * 항목을 추가하면 끝 — 백엔드 변경 불필요.
 *
 * 메뉴 key 는 사이드바(menu.json)의 url 과 매핑된다 (urlToMenuKey).
 */

export const MANAGED_ROLES = ["argus-superuser", "argus-user"] as const
export type ManagedRole = (typeof MANAGED_ROLES)[number]

export const ROLE_LABELS: Record<string, string> = {
  "argus-admin": "Admin",
  "argus-superuser": "Superuser",
  "argus-user": "User",
}

export type MenuEntry = { key: string; label: string; group: string; url: string }
export type FeatureEntry = { key: string; label: string; menuKey: string; description: string }

// ---------------------------------------------------------------------------
// 메뉴 — 사이드바 18개 (menu.json 과 1:1)
// ---------------------------------------------------------------------------

export const MENU_REGISTRY: MenuEntry[] = [
  { key: "dashboard", label: "대시보드", group: "데이터 카탈로그", url: "/dashboard" },
  { key: "datasets", label: "데이터셋", group: "데이터 카탈로그", url: "/dashboard/datasets" },
  { key: "datasources", label: "데이터 소스", group: "데이터 카탈로그", url: "/dashboard/datasources" },
  { key: "taxonomies", label: "분류 체계", group: "데이터 카탈로그", url: "/dashboard/taxonomies" },
  { key: "changes", label: "변경 관리", group: "데이터 카탈로그", url: "/dashboard/changes" },
  { key: "my-approvals", label: "내 결재함", group: "데이터 카탈로그", url: "/dashboard/my-approvals" },
  { key: "ai-agents", label: "AI Agent", group: "AI 카탈로그", url: "/dashboard/ai-agents" },
  { key: "models", label: "MLflow 모델", group: "AI 카탈로그", url: "/dashboard/models" },
  { key: "mlflow-files", label: "MLflow 모델 파일", group: "AI 카탈로그", url: "/dashboard/mlflow-files" },
  { key: "oci-hub", label: "OCI 모델 허브", group: "AI 카탈로그", url: "/dashboard/oci-hub" },
  { key: "oci-files", label: "OCI 모델 파일", group: "AI 카탈로그", url: "/dashboard/oci-files" },
  { key: "apis", label: "API", group: "API 카탈로그", url: "/dashboard/apis" },
  { key: "apis-favorites", label: "즐겨찾기", group: "API 카탈로그", url: "/dashboard/apis/favorites" },
  { key: "standards", label: "데이터 표준", group: "거버넌스", url: "/dashboard/standards" },
  { key: "glossary", label: "용어집", group: "거버넌스", url: "/dashboard/glossary" },
  { key: "tags", label: "태그", group: "거버넌스", url: "/dashboard/tags" },
  { key: "alerts", label: "알림", group: "거버넌스", url: "/dashboard/alerts" },
  { key: "users", label: "사용자 관리", group: "관리", url: "/dashboard/users" },
  { key: "settings", label: "설정", group: "관리", url: "/dashboard/settings" },
  { key: "permissions", label: "권한 관리", group: "관리", url: "/dashboard/permissions" },
]

export const MENU_GROUP_ORDER = ["데이터 카탈로그", "AI 카탈로그", "API 카탈로그", "거버넌스", "관리"]

/** 사이드바 url → 메뉴 key (필터링용). 가장 긴 prefix 매칭. */
export function urlToMenuKey(url: string): string | null {
  let best: MenuEntry | null = null
  for (const m of MENU_REGISTRY) {
    if (url === m.url || url.startsWith(m.url + "/")) {
      if (!best || m.url.length > best.url.length) best = m
    }
  }
  return best?.key ?? null
}

// ---------------------------------------------------------------------------
// 기능 — 메뉴 내부 버튼·탭·민감 데이터 (28개)
// ---------------------------------------------------------------------------

export const FEATURE_REGISTRY: FeatureEntry[] = [
  // 데이터셋
  { key: "datasets.manage", label: "데이터셋 생성/삭제", menuKey: "datasets", description: "데이터셋 등록·삭제 버튼" },
  { key: "datasets.edit", label: "메타데이터 편집", menuKey: "datasets", description: "설명·태그·소유자·용어 매핑 편집" },
  { key: "datasets.schema-edit", label: "스키마 편집", menuKey: "datasets", description: "컬럼 추가/수정/삭제" },
  { key: "datasets.sample-view", label: "샘플 데이터 조회", menuKey: "datasets", description: "실데이터 샘플 노출 — 민감" },
  { key: "datasets.ddl-view", label: "DDL 조회", menuKey: "datasets", description: "테이블 DDL 노출" },
  { key: "datasets.lineage-edit", label: "리니지 편집", menuKey: "datasets", description: "리니지 추가/삭제" },
  { key: "datasets.transfer-code", label: "이관 코드 탭", menuKey: "datasets", description: "PySpark/Kestra/Airflow 적재 코드" },
  // 품질
  { key: "quality.rules-manage", label: "품질 규칙 관리", menuKey: "datasets", description: "규칙 추가/수정/삭제·추천" },
  { key: "quality.run", label: "검증/프로파일 실행", menuKey: "datasets", description: "품질 탭 실행 버튼" },
  { key: "quality.schedule", label: "검증 주기 설정", menuKey: "datasets", description: "자동 검증 스케줄" },
  // 데이터 소스
  { key: "datasources.manage", label: "데이터 소스 관리", menuKey: "datasources", description: "생성/수정/삭제" },
  { key: "datasources.connection", label: "연결 설정 조회/편집", menuKey: "datasources", description: "접속 정보(비밀번호 포함) — 최고 민감" },
  { key: "datasources.sync", label: "메타데이터 동기화", menuKey: "datasources", description: "원본 스키마 동기화 실행" },
  // 변경 관리
  { key: "changes.create", label: "변경 요청 생성", menuKey: "changes", description: "새 변경 요청" },
  { key: "changes.approve", label: "승인/반려", menuKey: "changes", description: "결재 처리" },
  { key: "changes.deploy", label: "배포 실행", menuKey: "changes", description: "승인된 변경의 배포" },
  // AI 카탈로그
  { key: "ai.manage", label: "모델/에이전트 등록·편집", menuKey: "ai-agents", description: "AI 자산 관리" },
  { key: "ai.download", label: "모델 파일 다운로드", menuKey: "oci-hub", description: "아티팩트 다운로드" },
  { key: "ai.stage", label: "스테이지 변경", menuKey: "models", description: "Staging/Production 전환" },
  // API 카탈로그
  { key: "apis.manage", label: "API 등록·편집", menuKey: "apis", description: "API·엔드포인트 관리" },
  { key: "apis.credentials", label: "자격증명 관리", menuKey: "apis", description: "API 자격증명 — 민감" },
  // 거버넌스
  { key: "standards.edit", label: "표준 사전 편집", menuKey: "standards", description: "단어/용어/도메인/코드 관리" },
  { key: "glossary.edit", label: "용어집 편집", menuKey: "glossary", description: "분류/용어 관리" },
  { key: "tags.manage", label: "태그 관리", menuKey: "tags", description: "태그 생성/삭제" },
  { key: "alerts.rules", label: "알림 규칙 관리", menuKey: "alerts", description: "규칙 추가/수정/삭제" },
  // AI
  { key: "ai.assistant", label: "AI 어시스턴트 사용", menuKey: "dashboard", description: "플로팅 챗 노출" },
  { key: "ai.generate", label: "AI 자동 생성", menuKey: "datasets", description: "설명/태그/PII 생성 메뉴" },
  // 분류 체계
  { key: "taxonomies.edit", label: "분류 체계 편집", menuKey: "taxonomies", description: "카테고리·배정 관리" },
]
