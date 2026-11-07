/**
 * 권한 레지스트리 — 메뉴/기능의 단일 정의처.
 *
 * 백엔드는 key 문자열만 저장하므로(open-by-default), 여기 정의가 곧
 * 권한 관리 화면의 행이 된다. 새 메뉴/기능을 통제하려면 이 파일에
 * 항목을 추가하면 끝 — 백엔드 변경 불필요.
 *
 * 메뉴 key 는 사이드바(menu.json)의 url 과 매핑된다 (urlToMenuKey).
 * 플러그인 메뉴는 plugins/registry 에서 자동으로 합쳐진다 (key = "plugin.<item.id>").
 */

import { pluginMenus } from "@/plugins/registry"

export const MANAGED_ROLES = ["argus-superuser", "argus-user"] as const
export type ManagedRole = (typeof MANAGED_ROLES)[number]

export const ROLE_LABELS: Record<string, string> = {
  "argus-admin": "Admin",
  "argus-superuser": "Superuser",
  "argus-user": "User",
}

export type MenuEntry = {
  key: string
  label: string
  group: string
  url: string
  /** 관리 메뉴 — 항상 관리자 전용(매트릭스로 열 수 없음, 서버도 저장하지 않음) */
  adminOnly?: boolean
  /**
   * 서버에서도 이 메뉴 전용 API 를 403 으로 막는다(백엔드 SERVER_ENFORCED_MENUS 와 일치).
   * 없으면 화면(사이드바·페이지 접근)만 막는다 — 여러 메뉴가 함께 쓰는 공용 API 라서.
   */
  serverEnforced?: boolean
}
export type FeatureEntry = {
  key: string
  label: string
  menuKey: string
  description: string
  /**
   * 명시 허용형 — 미설정이면 admin 전용(일반 기능은 미설정 = 전 역할 허용).
   * 원래 admin 전용이던 기능이라 기본 권한을 넓히지 않기 위함. 백엔드 DEFAULT_DENY_FEATURES 와 일치해야 한다.
   */
  defaultDeny?: boolean
  /** 화면에서만 숨기는 기능(서버 데이터 노출과 무관한 UI 편의) — 권한 관리 화면에 표시 */
  uiOnly?: boolean
}

// ---------------------------------------------------------------------------
// 메뉴 — 사이드바 메뉴 (menu.json 과 1:1)
// ---------------------------------------------------------------------------

const CORE_MENU_REGISTRY: MenuEntry[] = [
  { key: "dashboard", label: "대시보드", group: "데이터 카탈로그", url: "/dashboard" },
  { key: "datasets", label: "데이터셋", group: "데이터 카탈로그", url: "/dashboard/datasets" },
  { key: "datasources", label: "데이터 소스", group: "데이터 카탈로그", url: "/dashboard/datasources" },
  { key: "taxonomies", label: "분류 체계", group: "데이터 카탈로그", url: "/dashboard/taxonomies" },
  { key: "changes", label: "변경 관리", group: "데이터 카탈로그", url: "/dashboard/changes" },
  { key: "my-approvals", label: "내 결재함", group: "데이터 카탈로그", url: "/dashboard/my-approvals" },
  { key: "federation", label: "페더레이션", group: "데이터 카탈로그", url: "/dashboard/federation", serverEnforced: true },
  { key: "ai-agents", label: "AI Agent", group: "AI 카탈로그", url: "/dashboard/ai-agents", serverEnforced: true },
  { key: "mcp-servers", label: "MCP Server", group: "AI 카탈로그", url: "/dashboard/mcp-servers", serverEnforced: true },
  { key: "models", label: "MLflow 모델", group: "AI 카탈로그", url: "/dashboard/models", serverEnforced: true },
  { key: "mlflow-files", label: "MLflow 모델 파일", group: "AI 카탈로그", url: "/dashboard/mlflow-files", adminOnly: true },
  { key: "oci-hub", label: "OCI 모델 허브", group: "AI 카탈로그", url: "/dashboard/oci-hub", serverEnforced: true },
  { key: "oci-files", label: "OCI 모델 파일", group: "AI 카탈로그", url: "/dashboard/oci-files", adminOnly: true },
  { key: "apis", label: "API", group: "API 카탈로그", url: "/dashboard/apis", serverEnforced: true },
  { key: "apis-favorites", label: "즐겨찾기", group: "API 카탈로그", url: "/dashboard/apis/favorites" },
  { key: "standards", label: "데이터 표준", group: "거버넌스", url: "/dashboard/standards" },
  { key: "glossary", label: "용어집", group: "거버넌스", url: "/dashboard/glossary" },
  { key: "tags", label: "태그", group: "거버넌스", url: "/dashboard/tags" },
  { key: "alerts", label: "알림", group: "거버넌스", url: "/dashboard/alerts" },
  { key: "users", label: "사용자 관리", group: "관리", url: "/dashboard/users", adminOnly: true },
  { key: "settings", label: "설정", group: "관리", url: "/dashboard/settings", adminOnly: true },
  { key: "permissions", label: "권한 관리", group: "관리", url: "/dashboard/permissions", adminOnly: true },
]

const CORE_GROUP_ORDER = ["데이터 카탈로그", "AI 카탈로그", "API 카탈로그", "거버넌스", "관리"]

/**
 * 플러그인 메뉴 — plugins/registry 의 활성 플러그인 메뉴 아이템마다 행을 하나씩 만든다.
 * key 에 "plugin." 접두어를 붙여 코어 메뉴 key 와 충돌하지 않게 한다. 이 행이 없으면
 * /dashboard/x/... 가 가장 긴 prefix 인 "dashboard" 에 매핑돼 개별 통제가 불가능하다.
 */
const PLUGIN_MENU_REGISTRY: MenuEntry[] = pluginMenus.flatMap((group) =>
  group.items.map((item) => ({
    key: `plugin.${item.id}`,
    label: item.title,
    group: group.label,
    url: item.url,
  })),
)

export const MENU_REGISTRY: MenuEntry[] = [...CORE_MENU_REGISTRY, ...PLUGIN_MENU_REGISTRY]

/** 권한 관리 화면의 그룹 순서 — 플러그인 그룹은 '관리' 앞에 둔다(사이드바와 동일). */
export const MENU_GROUP_ORDER = Array.from(
  new Set([
    ...CORE_GROUP_ORDER.filter((g) => g !== "관리"),
    ...PLUGIN_MENU_REGISTRY.map((m) => m.group),
    "관리",
  ]),
)

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
// 기능 — 메뉴 내부 버튼·탭·민감 데이터
//
// 실제로 화면/서버가 검사하는 key 만 둔다(백엔드 FEATURE_KEYS 와 일치 — 서버는 목록에 없는 key 를
// 저장하지 않는다). 검사 코드 없이 행만 두면 관리자가 '잠갔다'고 오해하므로, 새 기능을 통제하려면
// 검사 코드(서버 require_feature 등)와 함께 추가한다.
// ---------------------------------------------------------------------------

export const FEATURE_REGISTRY: FeatureEntry[] = [
  // 데이터셋
  { key: "datasets.sample-view", label: "샘플 데이터 조회", menuKey: "datasets", description: "실데이터 샘플 노출 — 민감" },
  { key: "datasets.ddl-view", label: "DDL 조회", menuKey: "datasets", description: "테이블 DDL 노출(로컬·페더레이션 상세)" },
  { key: "datasets.transfer-code", label: "이관 코드 탭", menuKey: "datasets", description: "PySpark/Kestra/Airflow 적재 코드(스키마로 화면에서 생성)", uiOnly: true },
  { key: "datasets.create", label: "데이터셋 등록", menuKey: "datasets", description: "데이터셋 직접 등록 (동기화로 생기는 데이터셋과 무관). 일반 사용자는 설정 > 데이터셋 등록이 켜져 있을 때만 적용" },
  // 데이터 소스
  { key: "datasources.manage", label: "데이터 소스 관리", menuKey: "datasources", description: "데이터 소스·조직·시스템 생성/수정/삭제, 접속 설정 저장 (미설정 시 관리자만)", defaultDeny: true },
  { key: "datasources.connection", label: "연결 설정 조회", menuKey: "datasources", description: "접속 정보(호스트·계정 등, 비밀번호는 항상 마스킹) (미설정 시 관리자만)", defaultDeny: true },
  { key: "datasources.sync", label: "메타데이터 동기화", menuKey: "datasources", description: "원본 스키마 동기화 실행 (미설정 시 관리자만)", defaultDeny: true },
  // AI 카탈로그
  { key: "models.register", label: "MLflow 모델 등록", menuKey: "models", description: "새 모델 등록 (화면·SDK·MLflow 클라이언트) — 버전 추가는 모델 소유자" },
  { key: "models.promote", label: "운영(Production) 단계 변경", menuKey: "models", description: "버전을 Production 으로 올리거나 내림 — 모델 소유자여도 이 권한 필요 (미설정 시 관리자만)", defaultDeny: true },
  { key: "oci.register", label: "OCI 모델 등록", menuKey: "oci-hub", description: "새 OCI 모델 등록 (HuggingFace·로컬 반입은 관리자만)" },
  { key: "agents.register", label: "AI Agent 등록", menuKey: "ai-agents", description: "에이전트 등록·매니페스트 적용·Agent Card 가져오기·발견 항목 등록" },
  { key: "mcp.register", label: "MCP 서버·배포 등록", menuKey: "mcp-servers", description: "MCP 서버 등록·가져오기, 배포 등록" },
  { key: "mcp.credential", label: "MCP 배포 자격증명 저장", menuKey: "mcp-servers", description: "배포 접속 자격증명 저장·삭제 (자기 배포만) — 민감 (미설정 시 관리자만)", defaultDeny: true },
  { key: "mcp.policy", label: "MCP 승인 정책 변경", menuKey: "mcp-servers", description: "배포 승인 정책 저장·관측값 채택 (자기 배포만 — 해제한 역할은 자기 배포여도 불가)" },
  // API 카탈로그
  { key: "apis.register", label: "API 등록", menuKey: "apis", description: "새 API 등록 (스펙 가져오기 포함)" },
  { key: "apis.credentials", label: "저장된 자격증명 사용", menuKey: "apis", description: "API 자격증명 조회·Try-it 호출에 사용 (추가·삭제는 관리자만) — 민감" },
  // 거버넌스
  { key: "standards.edit", label: "표준 사전 편집", menuKey: "standards", description: "사전/단어/용어/도메인/코드 관리 (미설정 시 관리자만)", defaultDeny: true },
  { key: "glossary.edit", label: "용어집 편집", menuKey: "glossary", description: "분류/용어 추가·수정·이동·삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "tags.manage", label: "태그 관리", menuKey: "tags", description: "태그 생성·수정·삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "alerts.rules", label: "알림 규칙 관리", menuKey: "alerts", description: "규칙 추가/수정/삭제·모든 알림 상태 변경 (미설정 시 관리자만)", defaultDeny: true },
  { key: "taxonomies.edit", label: "분류 체계 편집", menuKey: "taxonomies", description: "분류체계·분류 추가/수정/삭제, 데이터셋 매핑 (미설정 시 관리자만)", defaultDeny: true },
  // 데이터 품질 (데이터셋 상세 › 품질 탭)
  { key: "quality.rules-manage", label: "품질 규칙 관리", menuKey: "datasets", description: "품질 규칙 추가/수정/삭제·규칙 추천" },
  { key: "quality.run", label: "품질 검증 실행", menuKey: "datasets", description: "프로파일·검증 실행 (원천 DB 에 쿼리)" },
  { key: "quality.schedule", label: "검증 주기 설정", menuKey: "datasets", description: "데이터셋별 자동 검증 주기 변경" },
  // 변경 관리
  { key: "changes.create", label: "변경 요청 생성", menuKey: "changes", description: "변경 요청(CR) 작성" },
  { key: "changes.approve", label: "변경 요청 결재", menuKey: "changes", description: "승인/반려 (현재 차례 결재자 본인만 — 이 권한이 없으면 결재 차례여도 불가)" },
  { key: "changes.consumers", label: "데이터 소비자 등록", menuKey: "changes", description: "데이터셋 소비자(변경 통지 대상) 등록" },
  // AI
  { key: "ai.assistant", label: "AI 어시스턴트 사용", menuKey: "dashboard", description: "플로팅 챗 노출·어시스턴트 대화 API" },
  { key: "ai.generate", label: "AI 메타데이터 생성", menuKey: "datasets", description: "데이터셋 설명·요약·컬럼·태그·PII 생성 (자기 데이터셋만 — LLM 호출 비용)" },
]

/** 명시 허용형 기능 key — 권한 관리 화면에서 미설정 상태를 '역할 미허용'으로 보여 준다. */
export const DEFAULT_DENY_FEATURE_KEYS: ReadonlySet<string> = new Set(
  FEATURE_REGISTRY.filter((f) => f.defaultDeny).map((f) => f.key),
)
