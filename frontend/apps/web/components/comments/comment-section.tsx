"use client"

import { useCallback, useEffect, useState } from "react"
import { MessageSquare } from "lucide-react"

import { Button } from "@workspace/ui/components/button"

import {
  fetchComments,
  createComment,
  deleteComment,
  updateComment,
  type CommentData,
  type PaginatedComments,
} from "@/features/comments/api"
import { useAuth } from "@/features/auth"
import { errorMessage, notifyError } from "@/lib/notify"
import { CommentEditor } from "./comment-editor"
import { CommentItem } from "./comment-item"

type CommentSectionProps = {
  /** Entity type (e.g. "dataset", "model", "glossary"). */
  entityType: string
  /** Entity identifier (e.g. dataset ID, model name). */
  entityId: string
  /** Number of top-level comments per page (default: 10). */
  pageSize?: number
}

export function CommentSection({
  entityType,
  entityId,
  pageSize = 5,
}: CommentSectionProps) {
  const { user } = useAuth()
  const currentUser = user?.username ?? "anonymous"
  // 새 댓글에는 "성이름(username)" 형식으로 저장해 표시도 자연스럽고 (서버가 인증 사용자로 같은 형식을
  // 다시 만들어 저장한다 — 본문의 author_name 은 하위 호환용으로만 보낸다)
  // ownership 비교(comment-item.tsx) 에서 username 만 매칭하기 좋게 둔다.
  const authorName = user
    ? `${(user.last_name ?? "")}${(user.first_name ?? "")}(${user.username})`.trim() || user.username
    : "anonymous"
  const [comments, setComments] = useState<CommentData[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const totalPages = Math.ceil(total / pageSize)

  const load = useCallback(async (p: number) => {
    try {
      setLoading(true)
      setLoadError(null)
      const data: PaginatedComments = await fetchComments(entityType, entityId, p, pageSize)
      setComments(data.items)
      setTotal(data.total)
      setPage(p)
    } catch (err) {
      console.error("Failed to load comments:", err)
      setLoadError(errorMessage(err) ?? "")
    } finally {
      setLoading(false)
    }
  }, [entityType, entityId, pageSize])

  useEffect(() => {
    load(1)
  }, [load])

  const handlePost = useCallback(async (
    content: string, contentPlain: string, category: string,
  ) => {
    try {
      await createComment({
        entity_type: entityType,
        entity_id: entityId,
        content,
        content_plain: contentPlain,
        category,
        author_name: authorName,
      })
    } catch (err) {
      notifyError(err, "댓글을 작성하지 못했습니다.")
      throw err // 에디터가 입력 내용을 유지하도록
    }
    await load(1)
  }, [entityType, entityId, authorName, load])

  const handleReply = useCallback(async (
    parentId: number, content: string, contentPlain: string, category: string,
  ) => {
    try {
      await createComment({
        entity_type: entityType,
        entity_id: entityId,
        parent_id: parentId,
        content,
        content_plain: contentPlain,
        category,
        author_name: authorName,
      })
    } catch (err) {
      notifyError(err, "댓글을 작성하지 못했습니다.")
      throw err
    }
    // Reload current page to show new reply
    await load(page)
  }, [entityType, entityId, authorName, load, page])

  const handleEdit = useCallback(async (
    commentId: number, content: string, contentPlain: string, category: string,
  ) => {
    await updateComment(commentId, { content, content_plain: contentPlain, category })
    await load(page)
  }, [load, page])

  const handleDelete = useCallback(async (commentId: number) => {
    await deleteComment(commentId)
    await load(page)
  }, [load, page])

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <MessageSquare className="h-5 w-5 text-muted-foreground" />
        <h3 className="text-base font-medium">
          댓글 {total > 0 && <span className="text-muted-foreground">({total})</span>}
        </h3>
      </div>

      {/* Editor */}
      <CommentEditor onSubmit={handlePost} />

      {/* Comment list */}
      {loading ? (
        <p className="text-sm text-muted-foreground text-center py-8">댓글을 불러오는 중...</p>
      ) : loadError !== null ? (
        <div className="py-8 text-center text-sm">
          <p className="text-destructive">댓글을 불러오지 못했습니다.</p>
          {loadError && <p className="mt-1 text-xs text-muted-foreground">{loadError}</p>}
        </div>
      ) : comments.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">아직 댓글이 없습니다. 첫 댓글을 남겨보세요!</p>
      ) : (
        <div className="space-y-1">
          {comments.map((c) => (
            <CommentItem
              key={c.id}
              comment={c}
              currentUser={currentUser}
              isAdmin={!!user?.is_admin}
              entityType={entityType}
              entityId={entityId}
              onReply={handleReply}
              onDelete={handleDelete}
              onEdit={handleEdit}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-1 pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => load(page - 1)}
          >
            이전
          </Button>
          {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
            <Button
              key={p}
              variant={p === page ? "default" : "outline"}
              size="sm"
              className="w-8 h-8 p-0"
              onClick={() => load(p)}
            >
              {p}
            </Button>
          ))}
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => load(page + 1)}
          >
            다음
          </Button>
        </div>
      )}
    </div>
  )
}
