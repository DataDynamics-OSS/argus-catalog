"use client"

import { Tip } from "@/components/tip"
import { useState } from "react"
import { Bug, ClipboardCheck, Lightbulb, MessageSquare, MoreHorizontal, Pencil, Sparkles, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { StatusBadge } from "@workspace/ui/components/status-badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"

import type { CommentData } from "@/features/comments/api"
import { isCommentEdited } from "@/features/comments/edited"
import { notifyError } from "@/lib/notify"
import { CommentEditor } from "./comment-editor"
import { DueText, ReportStatusBadge, ReportStatusDialog } from "./report-status"
import { RichTextViewer } from "./rich-text-viewer"

/** 댓글 분류 — 일반 댓글(general)은 표시하지 않는다 */
const COMMENT_CATEGORY: Record<string, { label: string; icon: typeof Bug }> = {
  suggestion: { label: "개선 요청", icon: Lightbulb },
  feature: { label: "기능 요청", icon: Sparkles },
  bug: { label: "오류 신고", icon: Bug },
}

type CommentItemProps = {
  comment: CommentData
  currentUser: string
  /** 관리자는 타인 댓글도 삭제 가능(서버와 동일 기준: 작성자 또는 관리자). */
  isAdmin?: boolean
  entityType: string
  entityId: string
  onReply: (parentId: number, content: string, contentPlain: string, category: string) => Promise<void>
  onDelete: (commentId: number) => Promise<void>
  /** 본문·분류 수정 — 작성자 또는 관리자(서버 `PUT /comments/{id}` 와 같은 기준). */
  onEdit: (commentId: number, content: string, contentPlain: string, category: string) => Promise<void>
  /** 오류 신고·개선 요청의 처리 상태를 바꿀 수 있는지 — 데이터셋 소유자(생성자)·관리자(TODO-096 4단계). */
  canHandleReports?: boolean
  /** 댓글 쓰기 · 답글 · 편집 가능 여부 — 역할의 기능 권한 comments.write(서버와 같은 기준, TODO-122). 삭제는 그대로. */
  canWrite?: boolean
  /** 처리 상태를 바꾼 뒤 목록을 다시 읽는다. */
  onReportChanged?: () => void
}


function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return "방금 전"
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}일 전`
  const months = Math.floor(days / 30)
  return `${months}개월 전`
}

export function CommentItem({
  comment,
  currentUser,
  isAdmin = false,
  entityType,
  entityId,
  onReply,
  onDelete,
  onEdit,
  canHandleReports = false,
  canWrite = true,
  onReportChanged,
}: CommentItemProps) {
  const [showReplyEditor, setShowReplyEditor] = useState(false)
  const [editing, setEditing] = useState(false)
  // 담당자가 손댄 신고(처리 중 · 해결 · 소명)는 분류를 바꾸면 처리 기록이 사라지므로 서버가 막는다(DEF-141 · DEF-165)
  const reportLocked = comment.status === "IN_PROGRESS" || comment.status === "RESOLVED" || comment.status === "REJECTED"
  const [handling, setHandling] = useState(false)

  // 댓글의 author_name 은 새 형식 "성이름(username)" 또는 구 형식 "username".
  // 표시명(괄호 앞)과 username(괄호 안)을 분리해 두면 placeholder·ownership 비교가 깔끔하다.
  const authorMatch = comment.author_name.match(/^(.+?)\(([^()]+)\)\s*$/)
  const authorDisplayName = authorMatch?.[1] ?? comment.author_name
  const authorUsername = authorMatch?.[2] ?? comment.author_name

  const handleReply = async (content: string, contentPlain: string, category: string) => {
    await onReply(comment.id, content, contentPlain, category)
    setShowReplyEditor(false)
  }

  const handleEdit = async (content: string, contentPlain: string, category: string) => {
    try {
      await onEdit(comment.id, content, contentPlain, category)
      setEditing(false)
    } catch (err) {
      notifyError(err, "댓글을 수정하지 못했습니다.")
      throw err // 에디터가 입력 내용을 지우지 않도록 실패를 전달
    }
  }

  // 구 형식(username 만 저장)과 새 형식("이름(username)") 모두 ownership 인식
  const isOwner = authorUsername === currentUser || comment.author_name === currentUser || isAdmin
  const maxDepthIndent = Math.min(comment.depth, 4) // Cap indent at 4 levels

  return (
    <div style={{ marginLeft: `${maxDepthIndent * 24}px` }}>
      {comment.is_deleted ? (
        /* Deleted comment placeholder — still shows so child replies remain visible */
        <div data-comment-id={comment.id} className="border border-dashed rounded-lg p-3 mb-2 opacity-50">
          <p className="text-sm italic">삭제된 댓글입니다.</p>
        </div>
      ) : (
      <div data-comment-id={comment.id} className="border rounded-lg p-3 mb-2">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            {/* Avatar placeholder */}
            <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center text-xs font-medium text-primary">
              {authorDisplayName.charAt(0).toUpperCase()}
            </div>
            <span className="text-sm font-medium">{comment.author_name}</span>
            <span className="text-xs">{timeAgo(comment.created_at)}</span>
            {isCommentEdited(comment) && (
              <span className="text-xs" title={new Date(comment.updated_at).toLocaleString()}>
                (수정됨)
              </span>
            )}
            {/* 분류는 무채색 테두리 + 아이콘(TODO-107) — 처리 상태는 옆의 ReportStatusBadge 가 색으로 */}
            {COMMENT_CATEGORY[comment.category] && (
              <StatusBadge
                tone="neutral"
                icon={COMMENT_CATEGORY[comment.category]!.icon}
                label={COMMENT_CATEGORY[comment.category]!.label}
              />
            )}
            {comment.status && (
              <>
                <ReportStatusBadge status={comment.status} />
                <DueText status={comment.status} dueAt={comment.due_at} resolvedAt={comment.resolved_at} overdue={comment.overdue} />
              </>
            )}
          </div>
          <div className="flex items-center gap-1">
            {comment.status && canHandleReports && (
              <Button variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground" onClick={() => setHandling(true)}>
                <ClipboardCheck className="h-3.5 w-3.5 mr-1" />
                처리
              </Button>
            )}
            {canWrite && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-muted-foreground"
                onClick={() => setShowReplyEditor(!showReplyEditor)}
              >
                <MessageSquare className="h-3.5 w-3.5 mr-1" />
                댓글
              </Button>
            )}
            {isOwner && (
              <DropdownMenu>
                <Tip label="댓글 메뉴"><DropdownMenuTrigger asChild>
                  <Button aria-label="댓글 메뉴" variant="ghost" size="sm" className="h-7 w-7 p-0">
                    <MoreHorizontal className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger></Tip>
                <DropdownMenuContent align="end">
                  {canWrite && (<>
                  <DropdownMenuItem onClick={() => setEditing(true)}>
                    <Pencil />
                    편집
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  </>)}
                  <DropdownMenuItem
                    variant="destructive"
                    onClick={() => onDelete(comment.id)}
                  >
                    <Trash2 />
                    삭제
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {/* Content — RichTextViewer 가 prose 스타일 + highlight.js 적용을 모두 담당. 수정 중이면 에디터. */}
        {editing ? (
          <CommentEditor
            onSubmit={handleEdit}
            initialContent={comment.content}
            initialCategory={comment.category}
            // 처리 중 · 해결 · 소명된 신고는 분류 선택을 숨긴다(DEF-141 · DEF-165)
            showCategory={!reportLocked}
            categoryNote={reportLocked
              ? (comment.status === "IN_PROGRESS" ? "처리 중인 신고는 분류를 바꿀 수 없습니다" : "처리된 신고는 분류를 바꿀 수 없습니다")
              : undefined}
            submitLabel="저장"
            autoFocus
            onCancel={() => setEditing(false)}
          />
        ) : (
          <RichTextViewer html={comment.content} />
        )}
        {comment.status && comment.resolution && (
          <div className="mt-2 rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-2 text-sm">
            <div className="mb-0.5 text-xs">
              {comment.status === "REJECTED" ? "소명" : "처리 결과"}
              {comment.handled_by ? ` · ${comment.handled_by}` : ""}
            </div>
            <div className="whitespace-pre-wrap break-words">{comment.resolution}</div>
          </div>
        )}
      </div>
      )}

      {canHandleReports && (
        <ReportStatusDialog
          report={handling ? comment : null}
          onClose={() => setHandling(false)}
          onSaved={() => { setHandling(false); onReportChanged?.() }}
        />
      )}

      {/* Reply editor */}
      {canWrite && showReplyEditor && (
        <div className="mb-2 ml-6">
          <CommentEditor
            onSubmit={handleReply}
            placeholder={`${authorDisplayName} 님에게 댓글…`}
            submitLabel="댓글"
            autoFocus
            onCancel={() => setShowReplyEditor(false)}
          />
        </div>
      )}

      {/* Nested replies */}
      {comment.replies?.map((reply) => (
        <CommentItem
          key={reply.id}
          comment={reply}
          currentUser={currentUser}
          isAdmin={isAdmin}
          entityType={entityType}
          entityId={entityId}
          onReply={onReply}
          onDelete={onDelete}
          onEdit={onEdit}
          canHandleReports={canHandleReports}
          canWrite={canWrite}
          onReportChanged={onReportChanged}
        />
      ))}
    </div>
  )
}
