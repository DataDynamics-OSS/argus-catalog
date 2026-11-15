import { readdirSync, readFileSync, existsSync, statSync } from "node:fs"
import { join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import { helpWindowFeatures } from "./open-help"
import { resolveHelp } from "./resolve"
import {
  DATASET_TAB_TOPICS, FALLBACK_TOPIC, HELP_TOPICS, MANUAL_BASE, helpUrl, matchRoute, topicById, topicForPath,
} from "./topics"

const WEB = fileURLToPath(new URL("../../", import.meta.url))
const PAGES = join(WEB, "../../../docs/modules/ROOT/pages")

/** app/dashboard/**\/page.tsx → 경로 패턴("/dashboard/datasets/[id]") */
function dashboardRoutes(): string[] {
  const root = join(WEB, "app")
  const out: string[] = []
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) walk(p)
      else if (name === "page.tsx") {
        const rel = relative(root, dir).split(sep).filter((s) => !/^\(.*\)$/.test(s)).join("/")
        out.push(`/${rel}`)
      }
    }
  }
  walk(join(root, "dashboard"))
  return out.sort()
}

describe("도움말 주제 — 화면·매뉴얼 대응", () => {
  it("모든 대시보드 화면에 같은 경로 패턴의 도움말 주제가 있다", () => {
    const routes = dashboardRoutes()
    expect(routes.length).toBeGreaterThan(20)
    const covered = new Set(HELP_TOPICS.map((t) => t.route).filter(Boolean))
    const missing = routes.filter((r) => !covered.has(r))
    expect(missing, "features/help/topics.ts 의 HELP_TOPICS 에 이 화면의 주제를 더하세요").toEqual([])
  })

  it("주제 id 가 겹치지 않는다", () => {
    const ids = HELP_TOPICS.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it("모든 주제의 매뉴얼 쪽과 앵커가 있다", () => {
    const problems: string[] = []
    for (const t of [...HELP_TOPICS, FALLBACK_TOPIC]) {
      const file = join(PAGES, `${t.page}.adoc`)
      if (!existsSync(file)) {
        problems.push(`${t.id}: 쪽 없음 ${t.page}.adoc`)
        continue
      }
      if (!t.anchor) continue
      const src = readFileSync(file, "utf8")
      if (!src.includes(`[[${t.anchor}]]`) && !src.includes(`[#${t.anchor}]`)) {
        problems.push(`${t.id}: ${t.page}.adoc 에 [[${t.anchor}]] 없음`)
      }
    }
    expect(problems).toEqual([])
  })

  it("데이터셋 상세 탭 주제가 모두 있다", () => {
    for (const id of Object.values(DATASET_TAB_TOPICS)) expect(topicById(id), id).toBeDefined()
  })
})

describe("경로 고르기", () => {
  it("고정 세그먼트가 동적 세그먼트보다 우선한다", () => {
    expect(topicForPath("/dashboard/changes/new")?.id).toBe("change-new")
    expect(topicForPath("/dashboard/changes/42")?.id).toBe("change-detail")
    expect(topicForPath("/dashboard/apis/favorites")?.id).toBe("api-favorites")
    expect(topicForPath("/dashboard/datasets")?.id).toBe("datasets")
    expect(topicForPath("/dashboard/datasets/123")?.id).toBe("dataset-detail")
    expect(topicForPath("/dashboard")?.id).toBe("dashboard")
    expect(topicForPath("/dashboard/")?.id).toBe("dashboard")
  })

  it("catch-all 은 한 단계 이상 있어야 맞는다", () => {
    expect(matchRoute("/dashboard/x/[...slug]", "/dashboard/x/acme/billing")).toBeGreaterThan(0)
    expect(matchRoute("/dashboard/x/[...slug]", "/dashboard/x")).toBe(-1)
    expect(matchRoute("/dashboard/datasets/[id]", "/dashboard/datasets/1/extra")).toBe(-1)
  })

  it("맞는 주제가 없으면 매뉴얼 첫 쪽", () => {
    expect(topicForPath("/somewhere")).toBeUndefined()
    expect(resolveHelp("/somewhere", null).url).toBe(`${MANUAL_BASE}/index.html`)
  })

  it("화면이 지정한 주제가 경로보다 우선한다", () => {
    const r = resolveHelp("/dashboard/datasets/7", "dataset-quality")
    expect(r.url).toBe(`${MANUAL_BASE}/catalog/quality.html#help-dataset-quality`)
    expect(resolveHelp("/dashboard/datasets/7", "없는-주제").id).toBe("dataset-detail")
  })

  it("플러그인 화면은 도움말 지정이 없으면 플러그인 사용 안내", () => {
    expect(resolveHelp("/dashboard/x/acme/billing", null).url).toBe(`${MANUAL_BASE}/plugin/usage.html`)
  })

  it("한글 앵커는 인코딩한다", () => {
    expect(helpUrl({ page: "catalog/datasets", anchor: "_적재_코드" })).toBe(
      `${MANUAL_BASE}/catalog/datasets.html#${encodeURIComponent("_적재_코드")}`,
    )
  })
})

describe("도움말 창 크기", () => {
  it("화면 오른쪽에 붙고 너비는 480~960", () => {
    expect(helpWindowFeatures({ availWidth: 2560, availHeight: 1400 })).toBe("popup,width=960,height=1260,left=1600,top=70")
    expect(helpWindowFeatures({ availWidth: 800, availHeight: 600, availLeft: 100 })).toBe("popup,width=480,height=540,left=420,top=30")
  })
})
