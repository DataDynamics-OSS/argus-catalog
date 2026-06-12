import type { GlossaryTerm } from "@/features/datasets/data/schema"
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/catalog"

export async function fetchGlossaryTerms(): Promise<GlossaryTerm[]> {
  const res = await authFetch(`${BASE}/glossary`)
  if (!res.ok) throw new Error(`용어 조회 실패: ${res.status}`)
  return res.json()
}

export async function createGlossaryTerm(payload: {
  name: string
  description?: string
  parent_id?: number
  term_type?: string
}): Promise<GlossaryTerm> {
  const res = await authFetch(`${BASE}/glossary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `용어 생성 실패: ${res.status}`)
  }
  return res.json()
}

export async function updateGlossaryTerm(
  termId: number,
  payload: { name?: string; description?: string; parent_id?: number | null; term_type?: string },
): Promise<GlossaryTerm> {
  const res = await authFetch(`${BASE}/glossary/${termId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `용어 수정 실패: ${res.status}`)
  }
  return res.json()
}

export async function deleteGlossaryTerm(termId: number): Promise<void> {
  const res = await authFetch(`${BASE}/glossary/${termId}`, { method: "DELETE" })
  if (!res.ok) {
    // 차단형 삭제(하위 용어 존재 등)의 백엔드 친절 메시지(409 detail)를 그대로 전달.
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `용어 삭제 실패: ${res.status}`)
  }
}
