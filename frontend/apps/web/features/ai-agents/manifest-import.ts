/**
 * 정의 파일 가져오기 — 표 형식 파일(.csv) 변환·결과 병합 (화면과 분리한 순수 로직).
 * 설계: design/ai-agent-bulk-registration-design.md §4.5
 */

import Papa from "papaparse"

import type { ApplyAction, ApplyResult } from "./api"

export const CSV_COLUMNS = [
  "name", "display_name", "description", "owner", "department", "category",
  "status", "version", "framework", "base_model", "endpoint", "tags",
] as const

const REQUIRED_COLUMNS = ["name", "owner", "department", "category"]

/** 표 형식 파일 한 행 → 정의 문서. 빈 칸은 넣지 않는다(서버가 필수 누락을 그 행의 실패로 알려 준다). */
export function rowToDoc(row: Record<string, string | undefined>): Record<string, unknown> {
  const v = (k: string) => (row[k] ?? "").trim() || undefined
  const tags = v("tags")?.split(";").map((t) => t.trim()).filter(Boolean)
  const spec: Record<string, unknown> = {}
  if (v("description")) spec.description = v("description")
  if (v("status")) spec.status = v("status")
  if (v("version")) spec.version = v("version")
  if (v("base_model")) spec.model = { name: v("base_model") }
  if (v("framework")) spec.runtime = { framework: v("framework") }
  if (v("endpoint")) spec.interface = { endpoint: v("endpoint") }
  return {
    apiVersion: "argus.catalog/v1",
    kind: "Agent",
    metadata: {
      name: v("name"),
      ...(v("display_name") ? { displayName: v("display_name") } : {}),
      owner: v("owner"),
      department: v("department"),
      category: v("category"),
      ...(tags?.length ? { tags } : {}),
    },
    spec,
  }
}

/** 표 형식 파일 원문 → 정의 문서 목록. 필수 열이 없으면 오류 문구를 돌려준다. */
export function csvToDocs(text: string): { docs: Record<string, unknown>[]; error?: string } {
  const parsed = Papa.parse<Record<string, string>>(text.replace(/^\uFEFF/, ""), {
    header: true,
    skipEmptyLines: true,
    transformHeader: (h) => h.trim(),
  })
  const missing = REQUIRED_COLUMNS.filter((c) => !parsed.meta.fields?.includes(c))
  if (missing.length) return { docs: [], error: `필수 열 없음(${missing.join(", ")})` }
  return { docs: parsed.data.map(rowToDoc) }
}

/** 여러 요청(정의 파일 1회 + 표 형식 200개씩)의 결과를 하나로. */
export function mergeResults(parts: ApplyResult[], dryRun: boolean): ApplyResult {
  const summary: Record<ApplyAction, number> = { created: 0, updated: 0, unchanged: 0, failed: 0 }
  const results: ApplyResult["results"] = []
  for (const p of parts) {
    for (const k of Object.keys(summary) as ApplyAction[]) summary[k] += p.summary[k] ?? 0
    results.push(...p.results)
  }
  return { dry_run: dryRun, summary, results }
}

/** 양식 내려받기용 표 형식 예시(머리글 + 1행). */
export function csvTemplate(): string {
  return [
    CSV_COLUMNS.join(","),
    "cs.refund-helper,환불 도우미,환불 문의 처리,hong@corp.example,고객지원팀,customer-service,active,1.0.0,langgraph,gpt-4o,https://agents.corp.example/refund,환불;결제",
  ].join("\n")
}
