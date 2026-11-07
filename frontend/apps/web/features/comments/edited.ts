import type { CommentData } from "./api"

// 수정된 댓글 표시 — updated_at 이 작성 시각보다 1초 넘게 뒤면 "(수정됨)".
// (작성 직후 같은 트랜잭션의 미세한 시각 차이는 무시)
export function isCommentEdited(comment: Pick<CommentData, "created_at" | "updated_at">): boolean {
  const created = new Date(comment.created_at).getTime()
  const updated = new Date(comment.updated_at).getTime()
  return Number.isFinite(created) && Number.isFinite(updated) && updated - created > 1000
}
