// 데이터셋 권한 — 서버 `app.catalog.dataset_owner`(backend/app/catalog/dataset_owner.py)와 같은 기준(TODO-154).
// - 일반 편집(메타데이터 · 스키마 · 샘플 · 품질 · AI 생성 · AI-Ready · 신고 처리): `isDatasetOwner` — 생성자 · 소유자 목록 · 관리자
// - 데이터셋 삭제 · 소유자 추가 · 삭제: `canManageDataset` — 생성자 · 관리자(목록 사용자가 권한을 넓히지 못하게)
// 화면은 발견성 용도이고 최종 판단은 서버가 한다.

type UserLike = { is_admin?: boolean | null; username?: string | null } | null | undefined

/** 데이터셋 삭제 · 소유자 추가 · 삭제 — 관리자 또는 생성자. 서버 `assert_dataset_creator_or_admin` 과 같다. */
export function canManageDataset(user: UserLike, createdBy: string | null | undefined): boolean {
  if (user?.is_admin) return true
  return !!createdBy && !!user?.username && createdBy === user.username
}

/** 소유자 목록 이름에서 사용자 아이디 — "홍길동 (hong)" → hong, "hong" → hong. 서버 `owner_username` 과 같다. */
export function ownerUsername(ownerName: string | null | undefined): string {
  const name = (ownerName ?? "").trim()
  return name.match(/\(([^()]+)\)\s*$/)?.[1]?.trim() ?? name
}

type DatasetOwnerLike = { created_by?: string | null; owners?: { owner_name: string }[] | null } | null | undefined

// 데이터셋 소유자 — 생성자 또는 소유자 목록에 오른 사용자(관리자 포함). 서버 `assert_dataset_owner_or_admin`
// (backend/app/catalog/dataset_owner.py)과 같은 기준으로, AI-Ready 알림을 받는 사람(DEF-138)이 데이터셋 일반 편집도
// 한다(TODO-154). 삭제 · 소유자 추가 · 삭제는 `canManageDataset`(생성자).
export function isDatasetOwner(user: UserLike, dataset: DatasetOwnerLike): boolean {
  if (user?.is_admin) return true
  const me = user?.username
  if (!me || !dataset) return false
  if (dataset.created_by === me) return true
  return (dataset.owners ?? []).some((o) => ownerUsername(o.owner_name) === me)
}

/** 화면 표시용 소유자 이름 — 소유자 목록에 생성자가 없으면 "아이디 (생성자)" 를 덧붙인다. 서버 `owner_names` 와 같은 기준. */
export function datasetOwnerLabels(dataset: DatasetOwnerLike): string[] {
  const names = (dataset?.owners ?? []).map((o) => o.owner_name)
  const creator = dataset?.created_by
  if (creator && !names.some((n) => ownerUsername(n) === creator)) names.push(`${creator} (생성자)`)
  return names
}
