// 데이터셋 편집 권한 — 서버 `_require_dataset_editor`(backend/app/catalog/router.py)와 같은 기준.
// 관리자이거나 데이터셋 생성자(created_by)면 수정·삭제·메타데이터 편집 가능. 화면은 발견성 용도이고
// 최종 판단은 서버가 한다.

type UserLike = { is_admin?: boolean | null; username?: string | null } | null | undefined

export function canManageDataset(user: UserLike, createdBy: string | null | undefined): boolean {
  if (user?.is_admin) return true
  return !!createdBy && !!user?.username && createdBy === user.username
}
