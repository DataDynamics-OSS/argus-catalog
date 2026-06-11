"use client"

import { useState } from "react"
import { Bug, Lightbulb, MessageSquare, MoreHorizontal, Sparkles, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"

import type { CommentData } from "@/features/comments/api"
import { CommentEditor } from "./comment-editor"
import { RichTextViewer } from "./rich-text-viewer"

type CommentItemProps = {
  comment: CommentData
  currentUser: string
  entityType: string
  entityId: string
  onReply: (parentId: number, content: string, contentPlain: string, category: string) => Promise<void>
  onDelete: (commentId: number) => Promise<void>
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
  entityType,
  entityId,
  onReply,
  onDelete,
}: CommentItemProps) {
  const [showReplyEditor, setShowReplyEditor] = useState(false)

  // 댓글의 author_name 은 새 형식 "성이름(username)" 또는 구 형식 "username".
  // 표시명(괄호 앞)과 username(괄호 안)을 분리해 두면 placeholder·ownership 비교가 깔끔하다.
  const authorMatch = comment.author_name.match(/^(.+?)\(([^()]+)\)\s*$/)
  const authorDisplayName = authorMatch?.[1] ?? comment.author_name
  const authorUsername = authorMatch?.[2] ?? comment.author_name

  const handleReply = async (content: string, contentPlain: string, category: string) => {
    await onReply(comment.id, content, contentPlain, category)
    setShowReplyEditor(false)
  }

  // 구 형식(username 만 저장)과 새 형식("이름(username)") 모두 ownership 인식
  const isOwner = authorUsername === currentUser || comment.author_name === currentUser
  const maxDepthIndent = Math.min(comment.depth, 4) // Cap indent at 4 levels

  return (
    <div style={{ marginLeft: `${maxDepthIndent * 24}px` }}>
      {comment.is_deleted ? (
        /* Deleted comment placeholder — still shows so child replies remain visible */
        <div className="border border-dashed rounded-lg p-3 mb-2 opacity-50">
          <p className="text-sm text-muted-foreground italic">삭제된 댓글입니다.</p>
        </div>
      ) : (
      <div className="border rounded-lg p-3 mb-2">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            {/* Avatar placeholder */}
            <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center text-xs font-medium text-primary">
              {authorDisplayName.charAt(0).toUpperCase()}
            </div>
            <span className="text-sm font-medium">{comment.author_name}</span>
            <span className="text-xs text-muted-foreground">{timeAgo(comment.created_at)}</span>
            {comment.category === "suggestion" && (
              <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
                <Lightbulb className="h-3 w-3" />
                제안
              </span>
            )}
            {comment.category === "feature" && (
              <span className="inline-flex items-center gap-1 text-xs text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-full">
                <Sparkles className="h-3 w-3" />
                기능 요청
              </span>
            )}
            {comment.category === "bug" && (
              <span className="inline-flex items-center gap-1 text-xs text-red-600 bg-red-50 px-1.5 py-0.5 rounded-full">
                <Bug className="h-3 w-3" />
                버그
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => setShowReplyEditor(!showReplyEditor)}
            >
              <MessageSquare className="h-3.5 w-3.5 mr-1" />
              댓글
            </Button>
            {isOwner && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                    <MoreHorizontal className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    className="text-destructive"
                    onClick={() => onDelete(comment.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                    삭제
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {/* Content — RichTextViewer 가 prose 스타일 + highlight.js 적용을 모두 담당. */}
        <RichTextViewer html={comment.content} />
      </div>
      )}

      {/* Reply editor */}
      {showReplyEditor && (
        <div className="mb-2 ml-6">
          <CommentEditor
            onSubmit={handleReply}
            placeholder={`${authorDisplayName} 님에게 댓글...`}
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
          entityType={entityType}
          entityId={entityId}
          onReply={onReply}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}
