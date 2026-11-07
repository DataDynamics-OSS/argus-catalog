/**
 * 분류체계(Taxonomy) API 클라이언트.
 * 백엔드: /api/v1/catalog/{taxonomies,categories,datasets/{id}/categories}
 */

import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/catalog"

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
  return res.json() as Promise<T>
}

async function okOrThrow(res: Response): Promise<void> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || `요청 실패: ${res.status}`)
  }
}

export interface Taxonomy {
  id: number
  code: string | null
  name: string
  description: string | null
  sort_order: number
}

export interface Category {
  id: number
  taxonomy_id: number
  parent_id: number | null
  code: string | null
  name: string
  description: string | null
  sort_order: number
}

export interface TreeCategory {
  id: number
  name: string
  parent_id: number | null
  dataset_count: number
  children: TreeCategory[]
}

export interface TaxonomyTree {
  taxonomy: Taxonomy
  categories: TreeCategory[]
  uncategorized_count: number
}

export interface DatasetCategoryRef {
  category_id: number
  category_name: string
  taxonomy_id: number
  taxonomy_name: string
  path: string
}

// ---- Taxonomy ----
export async function fetchTaxonomies(): Promise<Taxonomy[]> {
  return jsonOrThrow(await authFetch(`${BASE}/taxonomies`))
}

export async function createTaxonomy(payload: { name: string; description?: string }): Promise<Taxonomy> {
  return jsonOrThrow(await authFetch(`${BASE}/taxonomies`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }))
}

export async function updateTaxonomy(id: number, payload: { name?: string; description?: string | null }): Promise<Taxonomy> {
  return jsonOrThrow(await authFetch(`${BASE}/taxonomies/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }))
}

export async function deleteTaxonomy(id: number): Promise<void> {
  return okOrThrow(await authFetch(`${BASE}/taxonomies/${id}`, { method: "DELETE" }))
}

export async function fetchTaxonomyTree(id: number): Promise<TaxonomyTree> {
  return jsonOrThrow(await authFetch(`${BASE}/taxonomies/${id}/tree`))
}

// ---- Category ----
export async function createCategory(payload: {
  taxonomy_id: number
  name: string
  parent_id?: number | null
  description?: string
}): Promise<Category> {
  return jsonOrThrow(await authFetch(`${BASE}/categories`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }))
}

export async function updateCategory(
  id: number,
  payload: { name?: string; parent_id?: number | null; description?: string; sort_order?: number },
): Promise<Category> {
  return jsonOrThrow(await authFetch(`${BASE}/categories/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }))
}

export async function deleteCategory(id: number): Promise<void> {
  return okOrThrow(await authFetch(`${BASE}/categories/${id}`, { method: "DELETE" }))
}

// ---- Dataset ↔ Category 매핑 ----
export async function fetchDatasetCategories(datasetId: number): Promise<DatasetCategoryRef[]> {
  return jsonOrThrow(await authFetch(`${BASE}/datasets/${datasetId}/categories`))
}

/** 이 분류에 직접 매핑된 데이터셋 id (하위 분류 매핑 제외). 매핑 해제는 직접 매핑만 가능하다. */
export async function fetchDirectDatasetIds(categoryId: number): Promise<number[]> {
  return jsonOrThrow(await authFetch(`${BASE}/categories/${categoryId}/direct-dataset-ids`))
}

export async function addDatasetCategory(datasetId: number, categoryId: number): Promise<void> {
  return okOrThrow(await authFetch(`${BASE}/datasets/${datasetId}/categories/${categoryId}`, { method: "POST" }))
}

export async function removeDatasetCategory(datasetId: number, categoryId: number): Promise<void> {
  return okOrThrow(await authFetch(`${BASE}/datasets/${datasetId}/categories/${categoryId}`, { method: "DELETE" }))
}
