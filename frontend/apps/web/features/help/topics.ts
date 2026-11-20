// 화면별 도움말 주제 — 화면(경로·탭·다이얼로그) → 사용자 매뉴얼 쪽·절.
//
// 매뉴얼은 docs/ 의 Antora 사이트를 웹 빌드에 넣어 `/manual/` 로 서빙한다(docs/tools/build-web-manual.sh).
// - route: 이 주제를 고를 대시보드 경로. `[id]` 는 한 단계, `[...slug]` 는 여러 단계 와일드카드.
//   route 가 없는 주제는 화면이 `useHelpTopic(id)` 로 직접 지정한다(탭·다이얼로그).
// - page: 매뉴얼 쪽(docs/modules/ROOT/pages/<page>.adoc).
// - anchor: 그 쪽 안의 절. 없으면 쪽 처음. 매뉴얼에 `[[help-…]]` 로 고정해 둔 id 를 쓴다
//   (제목을 고쳐도 바뀌지 않게). topics.test.ts 가 모든 경로·쪽·앵커가 있는지 검사한다.
// 설계: design/help-system-design.md

export interface HelpTopic {
  id: string
  /** 툴팁·안내에 보일 주제 이름 */
  title: string
  route?: string
  page: string
  anchor?: string
}

/** 웹에서 매뉴얼 컴포넌트(argus-catalog)가 놓이는 경로 */
export const MANUAL_BASE = "/manual/argus-catalog"

export const HELP_TOPICS: HelpTopic[] = [
  // 데이터 카탈로그
  { id: "dashboard", title: "대시보드", route: "/dashboard", page: "catalog/dashboard" },
  { id: "datasets", title: "데이터셋", route: "/dashboard/datasets", page: "catalog/datasets" },
  { id: "dataset-detail", title: "데이터셋 상세", route: "/dashboard/datasets/[id]", page: "catalog/datasets", anchor: "help-dataset-detail" },
  { id: "dataset-metadata", title: "데이터셋 상세 — 메타데이터 탭", page: "catalog/datasets", anchor: "help-dataset-metadata" },
  { id: "dataset-ddl", title: "데이터셋 상세 — DDL 탭", page: "catalog/datasets", anchor: "help-dataset-ddl" },
  { id: "dataset-sample", title: "데이터셋 상세 — 샘플 탭", page: "catalog/datasets", anchor: "help-dataset-sample" },
  { id: "dataset-comments", title: "데이터셋 상세 — 댓글 탭", page: "catalog/datasets", anchor: "help-dataset-comments" },
  { id: "dataset-changes", title: "데이터셋 상세 — 변경 요청 탭", page: "catalog/datasets", anchor: "help-dataset-changes" },
  { id: "dataset-code", title: "데이터셋 상세 — 적재 코드 탭", page: "catalog/datasets", anchor: "_적재_코드_pyspark_kestra_airflow" },
  { id: "dataset-schema", title: "데이터셋 상세 — 스키마 탭", page: "catalog/schema", anchor: "help-dataset-schema" },
  { id: "dataset-history", title: "데이터셋 상세 — 변경 이력 탭", page: "catalog/schema", anchor: "help-dataset-history" },
  { id: "dataset-avro", title: "데이터셋 상세 — Avro 탭", page: "catalog/schema", anchor: "help-dataset-avro" },
  { id: "dataset-terms", title: "데이터셋 상세 — 용어 탭", page: "governance/glossary", anchor: "help-dataset-terms" },
  { id: "dataset-quality", title: "데이터셋 상세 — 품질 탭", page: "catalog/quality", anchor: "help-dataset-quality" },
  { id: "dataset-ai-ready", title: "데이터셋 상세 — AI-Ready 탭", page: "catalog/datasets", anchor: "help-dataset-ai-ready-check" },
  { id: "dataset-lineage", title: "데이터셋 상세 — 리니지 탭", page: "catalog/lineage", anchor: "help-dataset-lineage" },
  { id: "dataset-relationships", title: "데이터셋 상세 — 관계 탭", page: "catalog/lineage", anchor: "help-dataset-relationships" },
  { id: "dataset-erd", title: "데이터셋 상세 — ER 다이어그램 탭", page: "catalog/lineage", anchor: "help-dataset-erd" },
  { id: "catalogs", title: "카탈로그", route: "/dashboard/catalogs", page: "catalog/catalogs" },
  { id: "datasources", title: "데이터 소스", route: "/dashboard/datasources", page: "catalog/platforms" },
  { id: "sync-runs", title: "메타데이터 수집", route: "/dashboard/sync-runs", page: "catalog/platforms", anchor: "help-sync-runs" },
  { id: "quality-runs", title: "데이터 품질", route: "/dashboard/quality-runs", page: "catalog/quality", anchor: "help-quality-runs" },
  { id: "taxonomies", title: "분류 체계", route: "/dashboard/taxonomies", page: "catalog/taxonomies" },

  // 변경 관리
  { id: "changes", title: "변경 관리", route: "/dashboard/changes", page: "governance/change-management" },
  { id: "change-detail", title: "변경 요청 상세", route: "/dashboard/changes/[id]", page: "governance/change-management" },
  { id: "change-new", title: "변경 요청 만들기", route: "/dashboard/changes/new", page: "governance/change-management", anchor: "help-change-new" },
  { id: "my-approvals", title: "내 결재함", route: "/dashboard/my-approvals", page: "governance/change-management", anchor: "help-my-approvals" },

  // 페더레이션
  { id: "federation", title: "페더레이션", route: "/dashboard/federation", page: "federation/index" },
  { id: "federation-datasets", title: "페더레이션 데이터셋", route: "/dashboard/federation/datasets", page: "federation/browse-search" },

  // AI · 모델 · API 카탈로그
  { id: "ai-agents", title: "AI Agent", route: "/dashboard/ai-agents", page: "governance/ai-agent-catalog" },
  { id: "mcp-servers", title: "MCP Server", route: "/dashboard/mcp-servers", page: "governance/mcp-server-catalog" },
  { id: "models", title: "MLflow 모델", route: "/dashboard/models", page: "models/registry" },
  { id: "model-detail", title: "MLflow 모델 상세", route: "/dashboard/models/[name]", page: "models/registry" },
  { id: "mlflow-files", title: "MLflow 모델 파일", route: "/dashboard/mlflow-files", page: "models/registry" },
  { id: "oci-hub", title: "OCI 모델 허브", route: "/dashboard/oci-hub", page: "models/oci-hub" },
  { id: "oci-hub-detail", title: "OCI 모델 상세", route: "/dashboard/oci-hub/[name]", page: "models/oci-hub" },
  { id: "oci-files", title: "OCI 모델 파일", route: "/dashboard/oci-files", page: "models/oci-hub" },
  { id: "apis", title: "API", route: "/dashboard/apis", page: "governance/api-catalog" },
  { id: "api-favorites", title: "API 즐겨찾기", route: "/dashboard/apis/favorites", page: "governance/api-catalog" },

  // 거버넌스
  { id: "standards", title: "데이터 표준", route: "/dashboard/standards", page: "governance/standards" },
  { id: "glossary", title: "용어집", route: "/dashboard/glossary", page: "governance/glossary" },
  { id: "tags", title: "태그", route: "/dashboard/tags", page: "governance/tags" },
  { id: "ai-ready", title: "AI-Ready 점검", route: "/dashboard/ai-ready", page: "governance/ai-ready" },
  { id: "data-reports", title: "오류 신고 처리", route: "/dashboard/data-reports", page: "governance/data-reports" },
  { id: "alerts", title: "알림", route: "/dashboard/alerts", page: "governance/alerts" },

  // 운영
  { id: "search", title: "검색", route: "/dashboard/search", page: "ops/search" },
  { id: "users", title: "사용자 관리", route: "/dashboard/users", page: "ops/users" },
  { id: "settings", title: "설정", route: "/dashboard/settings", page: "ops/settings" },
  { id: "permissions", title: "권한 관리", route: "/dashboard/permissions", page: "ops/permissions" },
  { id: "audit", title: "감사 로그", route: "/dashboard/audit", page: "ops/audit" },

  // 플러그인 화면 — 플러그인이 PluginScreen.help 를 주지 않았을 때
  { id: "plugin", title: "플러그인 화면", route: "/dashboard/x/[...slug]", page: "plugin/usage" },
]

/** 맞는 주제가 없을 때 — 매뉴얼 첫 쪽 */
export const FALLBACK_TOPIC: HelpTopic = { id: "index", title: "사용자 매뉴얼", page: "index" }

const byId = new Map(HELP_TOPICS.map((t) => [t.id, t]))

export function topicById(id: string): HelpTopic | undefined {
  return byId.get(id)
}

/** 경로 패턴이 pathname 에 맞으면 구체성 점수(고정 세그먼트 수×10 + 동적 세그먼트 수), 아니면 -1. */
export function matchRoute(pattern: string, pathname: string): number {
  const pat = pattern.split("/").filter(Boolean)
  const path = pathname.split("?")[0]!.split("#")[0]!.split("/").filter(Boolean)
  let score = 0
  for (let i = 0; i < pat.length; i++) {
    const seg = pat[i]!
    if (/^\[\.\.\..+\]$/.test(seg)) {
      // catch-all — 마지막 세그먼트여야 하고 한 단계 이상 남아 있어야 한다
      return i === pat.length - 1 && path.length > i ? score + 1 : -1
    }
    if (i >= path.length) return -1
    if (/^\[.+\]$/.test(seg)) score += 1
    else if (seg === path[i]) score += 10
    else return -1
  }
  return path.length === pat.length ? score : -1
}

/** pathname 에 맞는 가장 구체적인 주제. 없으면 undefined. */
export function topicForPath(pathname: string): HelpTopic | undefined {
  let best: HelpTopic | undefined
  let bestScore = -1
  for (const t of HELP_TOPICS) {
    if (!t.route) continue
    const s = matchRoute(t.route, pathname)
    if (s > bestScore) {
      best = t
      bestScore = s
    }
  }
  return best
}

/** 매뉴얼 링크. `url` 로 직접 주소를 가진 주제(플러그인)는 그대로 쓴다. */
export function helpUrl(topic: Pick<HelpTopic, "page" | "anchor"> & { url?: string }): string {
  if (topic.url) return topic.url
  const anchor = topic.anchor ? `#${encodeURIComponent(topic.anchor)}` : ""
  return `${MANUAL_BASE}/${topic.page}.html${anchor}`
}

/** 데이터셋 상세 탭(value) → 도움말 주제 id. 탭 상태가 URL 에 없어 화면이 useHelpTopic 으로 지정한다. */
export const DATASET_TAB_TOPICS: Record<string, string> = {
  overview: "dataset-detail",
  metadata: "dataset-metadata",
  ddl: "dataset-ddl",
  schema: "dataset-schema",
  terms: "dataset-terms",
  quality: "dataset-quality",
  "ai-ready": "dataset-ai-ready",
  sample: "dataset-sample",
  comments: "dataset-comments",
  lineage: "dataset-lineage",
  relationships: "dataset-relationships",
  erd: "dataset-erd",
  avro: "dataset-avro",
  pyspark: "dataset-code",
  kestra: "dataset-code",
  airflow: "dataset-code",
  history: "dataset-history",
  changes: "dataset-changes",
}
