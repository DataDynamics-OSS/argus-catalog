import type { GlossaryTerm } from "@/features/datasets/data/schema"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/catalog"

export async function fetchGlossaryTerms(): Promise<GlossaryTerm[]> {
  const res = await authFetch(`${BASE}/glossary`)
  if (!res.ok) await throwOnError(res, "용어집을 불러오지 못했습니다")
  return res.json()
}

export async function createGlossaryTerm(payload: {
  name: string
  description?: string
  parent_id?: number
  term_type?: "CATEGORY" | "TERM"
}): Promise<GlossaryTerm> {
  const res = await authFetch(`${BASE}/glossary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "용어를 생성하지 못했습니다.")
  return res.json()
}

export async function updateGlossaryTerm(
  termId: number,
  payload: { name?: string; description?: string | null; parent_id?: number | null; term_type?: "CATEGORY" | "TERM" },
): Promise<GlossaryTerm> {
  const res = await authFetch(`${BASE}/glossary/${termId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwOnError(res, "용어를 수정하지 못했습니다.")
  return res.json()
}

export async function deleteGlossaryTerm(termId: number): Promise<void> {
  const res = await authFetch(`${BASE}/glossary/${termId}`, { method: "DELETE" })
  // 차단형 삭제(하위 용어 존재 등)의 백엔드 안내(409 detail)를 그대로 전달.
  if (!res.ok) await throwOnError(res, "용어를 삭제하지 못했습니다.")
}
