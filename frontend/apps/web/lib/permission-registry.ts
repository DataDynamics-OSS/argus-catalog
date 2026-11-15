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

import { pluginFeatures, pluginMenus } from "@/plugins/registry"

/** 권한 표의 기본 열 — 사용자 정의 역할(TODO-124)은 GET /permissions/roles 의 columns 로 더해진다. */
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
   * 관리 메뉴지만 이 기능(명시 허용형) 중 하나라도 허용받은 역할에도 연다 — 백엔드 ADMIN_MENU_GRANTS 와 일치.
   * 예: 메타데이터 수집은 수집 권한(datasources.sync)을 받은 사람도 진행·결과를 본다(TODO-074).
   * 설정은 settings.* 위임 키 중 하나라도 받으면 열리고 받은 항목의 탭만 보인다(TODO-123).
   */
  grantedBy?: readonly string[]
  /**
   * 서버에서도 이 메뉴 전용 API 를 403 으로 막는다(백엔드 SERVER_ENFORCED_MENUS 와 일치).
   * 없으면 화면(사이드바·페이지 접근)만 막는다 — 여러 메뉴가 함께 쓰는 공용 API 라서.
   */
  serverEnforced?: boolean
  /**
   * 공용 API 라 메뉴는 화면만 막지만, 이 메뉴의 편집 기능은 메뉴가 막히면 서버도 403(TODO-124,
   * 백엔드 SHARED_MENU_FEATURES). 조회 API 는 데이터셋 화면도 쓰므로 열어 둔다.
   */
  serverWrite?: boolean
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
  /**
   * 조회 기능 — 메뉴가 보기 전용인 역할도 쓴다(TODO-124). 표시가 없는 기능은 편집 기능이라 보기 전용 메뉴에서 막힌다
   * (백엔드 WRITE_FEATURE_MENUS 와 일치 — tests/test_permission_stage4.py 가 비교한다).
   */
  view?: boolean
}

/**
 * 설정 메뉴를 여는 위임 키(TODO-123) — 백엔드 SETTINGS_DELEGATE_FEATURES 와 일치.
 * 설정 화면은 받은 키의 탭만 보인다(app/dashboard/settings/page.tsx). 인증 · CORS · 데이터셋 등록 · 변경관리 ·
 * 쿼리 분석은 관리자 고정.
 */
export const SETTINGS_DELEGATE_FEATURES = [
  "settings.llm", "settings.embedding", "settings.assistant", "settings.ai-ready", "settings.notify",
  "settings.mail", "settings.object-storage", "settings.metadata-sync", "settings.cache", "settings.api-keys",
] as const

// ---------------------------------------------------------------------------
// 메뉴 — 사이드바 메뉴 (menu.json 과 1:1)
// ---------------------------------------------------------------------------

const CORE_MENU_REGISTRY: MenuEntry[] = [
  { key: "dashboard", label: "대시보드", group: "데이터 카탈로그", url: "/dashboard" },
  { key: "datasets", label: "데이터셋", group: "데이터 카탈로그", url: "/dashboard/datasets" },
  { key: "datasources", label: "데이터 소스", group: "데이터 카탈로그", url: "/dashboard/datasources" },
  { key: "taxonomies", label: "분류 체계", group: "데이터 카탈로그", url: "/dashboard/taxonomies", serverWrite: true },
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
  { key: "standards", label: "데이터 표준", group: "거버넌스", url: "/dashboard/standards", serverWrite: true },
  { key: "glossary", label: "용어집", group: "거버넌스", url: "/dashboard/glossary", serverWrite: true },
  { key: "tags", label: "태그", group: "거버넌스", url: "/dashboard/tags", serverWrite: true },
  { key: "alerts", label: "알림", group: "거버넌스", url: "/dashboard/alerts", serverEnforced: true },
  { key: "ai-ready", label: "AI-Ready 점검", group: "거버넌스", url: "/dashboard/ai-ready" },
  { key: "data-reports", label: "오류 신고 처리", group: "거버넌스", url: "/dashboard/data-reports", serverWrite: true },
  { key: "users", label: "사용자 관리", group: "관리", url: "/dashboard/users", adminOnly: true, grantedBy: ["users.manage"] },
  { key: "sync-runs", label: "메타데이터 수집", group: "관리", url: "/dashboard/sync-runs", adminOnly: true, grantedBy: ["datasources.sync"] },
  { key: "quality-runs", label: "데이터 품질", group: "관리", url: "/dashboard/quality-runs", adminOnly: true, grantedBy: ["quality.manage"] },
  { key: "settings", label: "설정", group: "관리", url: "/dashboard/settings", adminOnly: true, grantedBy: SETTINGS_DELEGATE_FEATURES },
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
    ...(item.adminOnly ? { adminOnly: true } : {}),
    ...(item.grantedBy?.length ? { grantedBy: item.grantedBy } : {}),
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

/** 기능 권한으로 여는 관리 메뉴인지 — grantedBy 기능(명시 허용형)을 쓸 수 있으면 true. */
export function menuGrantedByFeature(key: string | null, canUseFeature: (feature: string) => boolean): boolean {
  const m = key ? MENU_REGISTRY.find((x) => x.key === key) : undefined
  return !!m?.grantedBy?.some((f) => canUseFeature(f))
}

/**
 * 관리자가 아닌 사용자에게 메뉴를 열지 — 관리자 전용이 아니면 true(매트릭스는 따로 본다),
 * 관리자 전용이면 grantedBy 기능을 허용받았을 때만.
 */
export function adminMenuOpenTo(key: string | null, canUseFeature: (feature: string) => boolean): boolean {
  const m = key ? MENU_REGISTRY.find((x) => x.key === key) : undefined
  return !m?.adminOnly || menuGrantedByFeature(key, canUseFeature)
}

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
  { key: "datasets.sample-view", label: "샘플 데이터 조회", menuKey: "datasets", description: "실데이터 샘플 노출 — 민감", view: true },
  { key: "datasets.ddl-view", label: "DDL 조회", menuKey: "datasets", description: "테이블 DDL 노출(로컬·페더레이션 상세)", view: true },
  { key: "datasets.transfer-code", label: "이관 코드 탭", menuKey: "datasets", description: "PySpark/Kestra/Airflow 적재 코드(스키마로 화면에서 생성)", uiOnly: true, view: true },
  { key: "datasets.edit", label: "데이터셋 정보 수정", menuKey: "datasets", description: "설명 · 상태 · 소유자 · 태그 · 용어 · 속성 · AI-Ready 판정 · 배포 정보 수정, 버전 이력 삭제 (자기 데이터셋만)" },
  { key: "datasets.schema-edit", label: "스키마 편집", menuKey: "datasets", description: "컬럼 · Avro 스키마 편집 (자기 데이터셋만, 메타데이터 수집 계정은 제외)" },
  { key: "datasets.sample-manage", label: "샘플 데이터 관리", menuKey: "datasets", description: "샘플 업로드 · 삭제 · 구분자 · Parquet 변환 (자기 데이터셋만)" },
  { key: "datasets.delete", label: "데이터셋 삭제", menuKey: "datasets", description: "데이터셋 삭제 (자기 데이터셋만, 되돌릴 수 없음)" },
  { key: "lineage.edit", label: "리니지 편집", menuKey: "datasets", description: "데이터셋 · API 리니지와 파이프라인 추가 · 수정 · 삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "standards.map", label: "표준 용어 매핑", menuKey: "datasets", description: "데이터셋 컬럼 ↔ 표준 용어 매핑 · 자동 매핑 · 기본 사전 선택 (자기 데이터셋만)" },
  { key: "aiready.judge", label: "AI-Ready 수동 판정", menuKey: "datasets", description: "AI-Ready 체크리스트 항목의 수동 판정 저장 · 해제 (자기 데이터셋만, 데이터셋 정보 수정 권한도 필요)" },
  { key: "comments.write", label: "댓글 · 오류 신고 작성", menuKey: "datasets", description: "데이터셋 · 모델 댓글과 오류 신고 · 개선 요청 작성 · 수정" },
  { key: "data-reports.handle", label: "오류 신고 처리", menuKey: "data-reports", description: "오류 신고 · 개선 요청의 처리 상태 변경 (자기 데이터셋만)" },
  { key: "datasets.create", label: "데이터셋 등록", menuKey: "datasets", description: "데이터셋 직접 등록 (동기화로 생기는 데이터셋과 무관). 일반 사용자는 설정 > 데이터셋 등록이 켜져 있을 때만 적용" },
  // 페더레이션
  { key: "federation.manage", label: "페더레이션 관리", menuKey: "federation", description: "인스턴스 등록 · 수정 · 삭제, 수집 · 재동기화, 노출 설정, 관측성 (미설정 시 관리자만)", defaultDeny: true },
  { key: "federation.import", label: "원격 데이터셋 가져오기", menuKey: "federation", description: "원격 데이터셋을 로컬 데이터셋으로 가져오기 (미설정 시 관리자만)", defaultDeny: true },
  // 데이터 소스
  { key: "datasources.manage", label: "데이터 소스 관리", menuKey: "datasources", description: "데이터 소스·조직·시스템 생성/수정/삭제, 접속 설정 저장 (미설정 시 관리자만)", defaultDeny: true },
  { key: "datasources.connection", label: "연결 설정 조회", menuKey: "datasources", description: "접속 정보(호스트·계정 등, 비밀번호는 항상 마스킹) (미설정 시 관리자만)", defaultDeny: true, view: true },
  { key: "datasources.sync", label: "메타데이터 동기화", menuKey: "datasources", description: "원본 스키마 동기화 실행 (미설정 시 관리자만)", defaultDeny: true },
  // AI 카탈로그
  { key: "models.register", label: "MLflow 모델 등록", menuKey: "models", description: "새 모델 등록 (화면·SDK·MLflow 클라이언트) — 버전 추가는 모델 소유자" },
  { key: "models.edit", label: "MLflow 모델 수정", menuKey: "models", description: "모델 카드 · 버전 · 단계 · 지표 · 리니지 · 파일 올리기 (자기 모델만)" },
  { key: "models.delete", label: "MLflow 모델 삭제", menuKey: "models", description: "모델 · 버전 삭제 (자기 모델만 — 복원 · 영구 삭제는 관리자만)" },
  { key: "models.download", label: "MLflow 모델 파일 내려받기", menuKey: "models", description: "모델 파일 내려받기 · 다운로드 주소 발급 (SDK · CLI 포함)", view: true },
  { key: "models.promote", label: "운영(Production) 단계 변경", menuKey: "models", description: "버전을 Production 으로 올리거나 내림 — 모델 소유자여도 이 권한 필요 (미설정 시 관리자만)", defaultDeny: true },
  { key: "oci.register", label: "OCI 모델 등록", menuKey: "oci-hub", description: "새 OCI 모델 등록 (HuggingFace·로컬 반입은 관리자만)" },
  { key: "oci.edit", label: "OCI 모델 수정", menuKey: "oci-hub", description: "정보 · README · 태그 · 리니지 수정, 버전 확정 · 파일 올리기 (자기 모델만)" },
  { key: "oci.delete", label: "OCI 모델 삭제", menuKey: "oci-hub", description: "모델 · 버전 삭제 (자기 모델만)" },
  { key: "oci.import-hf", label: "모델 반입 (HuggingFace · 로컬)", menuKey: "oci-hub", description: "HuggingFace 에서 가져오기, 서버 로컬 디렉터리 반입 (미설정 시 관리자만)", defaultDeny: true },
  { key: "oci.download", label: "OCI 모델 파일 내려받기", menuKey: "oci-hub", description: "모델 파일 내려받기 · 다운로드 주소 발급 (SDK · CLI 포함)", view: true },
  { key: "agents.register", label: "AI Agent 등록", menuKey: "ai-agents", description: "에이전트 등록·매니페스트 적용·Agent Card 가져오기·발견 항목 등록" },
  { key: "agents.edit", label: "AI Agent 수정", menuKey: "ai-agents", description: "정보 · 도구 · MCP · 스킬 · 데이터 접근 · 리니지 · 버전 · 평가 수정 (자기 에이전트만)" },
  { key: "agents.bulk-edit", label: "AI Agent 일괄 변경", menuKey: "ai-agents", description: "여러 에이전트에 같은 값 넣기 (고칠 수 있는 에이전트만, AI Agent 수정 권한도 필요)" },
  { key: "agents.delete", label: "AI Agent 삭제", menuKey: "ai-agents", description: "에이전트 삭제 (자기 에이전트만)" },
  { key: "mcp.register", label: "MCP 서버·배포 등록", menuKey: "mcp-servers", description: "MCP 서버 등록·가져오기, 배포 등록" },
  { key: "mcp.edit", label: "MCP 서버·배포 수정", menuKey: "mcp-servers", description: "정보 · 도구 · 리소스 · 프롬프트 · 리소스 범위 · finding 처리 (자기 서버 · 배포만)" },
  { key: "mcp.delete", label: "MCP 서버·배포 폐기", menuKey: "mcp-servers", description: "서버 · 배포 폐기 (자기 서버 · 배포만)" },
  { key: "mcp.collect", label: "MCP 배포 수집", menuKey: "mcp-servers", description: "헬스 체크 · 수집 · 설정 · Manifest 가져오기 (자기 서버 · 배포만, 대상 서버에 접속)" },
  { key: "mcp.audit-view", label: "MCP 감사 이벤트 조회", menuKey: "mcp-servers", description: "감사 이벤트(호출 인자 포함) · 통제 현황의 최근 호출 (자기 배포만) — 민감", view: true },
  { key: "mcp.audit-ingest", label: "MCP 감사 이벤트 업로드", menuKey: "mcp-servers", description: "화면에서 JSONL 업로드, 수집 토큰 발급 · 폐기 (자기 배포만)" },
  { key: "mcp.credential", label: "MCP 배포 자격증명 저장", menuKey: "mcp-servers", description: "배포 접속 자격증명 저장·삭제 (자기 배포만) — 민감 (미설정 시 관리자만)", defaultDeny: true },
  { key: "mcp.policy", label: "MCP 승인 정책 변경", menuKey: "mcp-servers", description: "배포 승인 정책 저장·관측값 채택 (자기 배포만 — 해제한 역할은 자기 배포여도 불가)" },
  // API 카탈로그
  { key: "apis.register", label: "API 등록", menuKey: "apis", description: "새 API 등록 (스펙 가져오기 포함)" },
  { key: "apis.edit", label: "API 수정", menuKey: "apis", description: "정보 · 상태 · 스펙 · 엔드포인트 수정, 경보 확인 (자기 API 만)" },
  { key: "apis.delete", label: "API 삭제", menuKey: "apis", description: "API 삭제 (자기 API 만)" },
  { key: "apis.try-it", label: "API 호출 (Try-it)", menuKey: "apis", description: "엔드포인트 호출 탭 — 서버가 대상 API 를 대신 호출", view: true },
  { key: "apis.credentials", label: "저장된 자격증명 사용", menuKey: "apis", description: "API 자격증명 조회·Try-it 호출에 사용 (추가·삭제는 관리자만) — 민감", view: true },
  // 거버넌스
  { key: "standards.edit", label: "표준 사전 편집", menuKey: "standards", description: "사전/단어/용어/도메인/코드 관리 (미설정 시 관리자만)", defaultDeny: true },
  { key: "glossary.edit", label: "용어집 편집", menuKey: "glossary", description: "분류/용어 추가·수정·이동·삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "tags.manage", label: "태그 관리", menuKey: "tags", description: "태그 생성·수정·삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "alerts.purge", label: "오래된 알림 정리", menuKey: "alerts", description: "닫힌 알림 일괄 삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "alerts.ack", label: "알림 처리", menuKey: "alerts", description: "알림 확인 · 해결 · 무시 · 다시 열기, 일괄 처리 (규칙 관리자 또는 관련 데이터셋 담당자만)" },
  { key: "alerts.rules", label: "알림 규칙 관리", menuKey: "alerts", description: "규칙 추가/수정/삭제·모든 알림 상태 변경 (미설정 시 관리자만)", defaultDeny: true },
  { key: "taxonomies.edit", label: "분류 체계 편집", menuKey: "taxonomies", description: "분류체계·분류 추가/수정/삭제 (미설정 시 관리자만)", defaultDeny: true },
  { key: "taxonomies.map", label: "데이터셋 분류 매핑", menuKey: "datasets", description: "데이터셋을 분류에 붙이거나 떼기 — 데이터셋 상세 · 분류 체계 화면 (미설정 시 관리자만, 분류 체계 편집을 받았던 역할은 이어받음)", defaultDeny: true },
  // 데이터 품질 (데이터셋 상세 › 품질 탭)
  { key: "quality.rules-manage", label: "품질 규칙 관리", menuKey: "datasets", description: "품질 규칙 추가/수정/삭제·규칙 추천" },
  { key: "quality.run", label: "품질 진단 실행", menuKey: "datasets", description: "프로파일·진단 실행 (원천 DB 에 쿼리)" },
  { key: "quality.schedule", label: "진단 주기 설정", menuKey: "datasets", description: "품질 진단 작업의 주기 지정 (주기가 있는 작업 저장)" },
  { key: "quality.export", label: "품질 리포트 내려받기", menuKey: "datasets", description: "데이터 품질 리포트 PDF · 엑셀 내려받기 (데이터 품질 관리 권한도 필요)", view: true },
  { key: "quality.manage", label: "데이터 품질 관리", menuKey: "datasets", description: "관리 › 데이터 품질 메뉴 — 품질 작업 등록 · 실행, 전체 진단 이력 · 결과, 취소 · 다시 실행, 품질 지표 추가 · 수정 · 삭제 (미설정 시 관리자만)", defaultDeny: true },
  // 변경 관리
  { key: "changes.create", label: "변경 요청 생성", menuKey: "changes", description: "변경 요청(CR) 작성" },
  { key: "changes.approve", label: "변경 요청 결재", menuKey: "changes", description: "승인/반려 (현재 차례 결재자 본인만 — 이 권한이 없으면 결재 차례여도 불가)" },
  { key: "changes.rollback", label: "적용한 변경 되돌리기", menuKey: "changes", description: "적용 완료(APPLIED)된 스키마 변경 되돌리기 (요청자 본인만)" },
  { key: "changes.consumers", label: "데이터 소비자 등록", menuKey: "changes", description: "데이터셋 소비자(변경 통지 대상) 등록" },
  // AI
  { key: "ai.assistant", label: "AI 어시스턴트 사용", menuKey: "dashboard", description: "플로팅 챗 노출·어시스턴트 대화 API" },
  { key: "ai.generate", label: "AI 메타데이터 생성", menuKey: "datasets", description: "데이터셋 설명·요약·컬럼·태그·PII 생성 (자기 데이터셋만 — LLM 호출 비용)" },
  // 관리 기능 위임(TODO-123) — 모두 명시 허용형. 인증 · CORS · 권한 관리 · 역할 변경은 관리자 고정
  { key: "users.manage", label: "사용자 관리", menuKey: "users", description: "사용자 관리 메뉴 — 사용자 추가 · 정보 수정 · 비밀번호 재설정 · 활성 · 비활성 · 잠금 해제 · 삭제 (관리자 계정 · 역할 변경은 관리자만)", defaultDeny: true },
  { key: "audit.view", label: "관리 감사 로그 조회", menuKey: "users", description: "사용자 · 설정 · 권한 변경 감사 로그 보기 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.llm", label: "LLM 설정", menuKey: "settings", description: "설정 › LLM / AI — 제공자 · 모델 · 키 저장, 연결 시험 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.embedding", label: "임베딩 설정", menuKey: "settings", description: "설정 › 임베딩 — 제공자 · 모델, 연결 시험 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.assistant", label: "AI 어시스턴트 설정", menuKey: "settings", description: "설정 › AI 어시스턴트 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.ai-ready", label: "AI-Ready 설정", menuKey: "settings", description: "설정 › AI-Ready — 공유 요건 · 외부 공개 주소 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.notify", label: "알림 채널 설정", menuKey: "settings", description: "설정 › 알림 — 웹훅 · 메신저 채널, 발송 시험 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.mail", label: "이메일 설정", menuKey: "settings", description: "설정 › 이메일 — SMTP 서버, 발송 시험 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.object-storage", label: "오브젝트 스토리지 설정", menuKey: "settings", description: "설정 › 오브젝트 스토리지 — 접속 정보 · 버킷 초기화 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.metadata-sync", label: "수집 서비스 설정", menuKey: "settings", description: "설정 › 수집 서비스 — 메타데이터 수집 서비스 주소, 연결 시험 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.cache", label: "캐시 관리", menuKey: "settings", description: "설정 › 캐시 — 통계 · 설정 변경 · 비우기 (미설정 시 관리자만)", defaultDeny: true },
  { key: "settings.api-keys", label: "API 키 관리", menuKey: "settings", description: "설정 › API 키 — 발급 · 수정 · 재발급 · 폐기 (관리자 역할 키는 관리자만, 미설정 시 관리자만)", defaultDeny: true },
  // 플러그인이 선언한 기능(plugins/registry) — 백엔드는 플러그인 객체의 features 로 같은 키를 등록한다
  ...pluginFeatures,
]

/** 명시 허용형 기능 key — 권한 관리 화면에서 미설정 상태를 '역할 미허용'으로 보여 준다. */
export const DEFAULT_DENY_FEATURE_KEYS: ReadonlySet<string> = new Set(
  FEATURE_REGISTRY.filter((f) => f.defaultDeny).map((f) => f.key),
)
