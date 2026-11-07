"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { FolderOpen } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { LocalFilesystemBrowser } from "@/components/local-filesystem-browser"
import { createFilesystemDataSource } from "@/features/filesystem/api"
import { useAuth } from "@/features/auth"

import { filesHref } from "./shared"

/**
 * 모델(버전) 파일 열기 — 관리자는 전체 파일 화면(MLflow 모델 파일 메뉴)으로, 그 밖의 사용자는 이 모델 폴더에
 * 고정된 읽기 전용 대화상자로 연다(서버도 등록 모델 폴더 안만 허용 — OCI 파일 탭과 같은 규칙).
 */
export function ModelFilesLink({
  path, title, variant = "link",
}: {
  /** 파일 브라우저 기준 경로(예: /argus.ml.iris/versions/1). */
  path: string
  /** 대화상자 제목에 쓸 모델·버전 이름. */
  title: string
  /** "button" = 개요 카드의 전체 폭 버튼, "link" = 버전 행의 작은 링크. */
  variant?: "button" | "link"
}) {
  const isAdmin = !!useAuth().user?.is_admin
  const [open, setOpen] = useState(false)
  const label = variant === "button" ? "파일 보기" : "파일"
  const icon = <FolderOpen className={variant === "button" ? "mr-1.5 h-3.5 w-3.5" : "h-3 w-3"} />

  if (isAdmin) {
    return variant === "button" ? (
      <Button variant="outline" size="sm" className="mt-3 w-full" asChild>
        <Link href={filesHref(path)}>{icon}{label}</Link>
      </Button>
    ) : (
      <Link href={filesHref(path)} className="inline-flex items-center gap-0.5 text-primary hover:underline">
        {icon}{label}
      </Link>
    )
  }

  return (
    <>
      {variant === "button" ? (
        <Button variant="outline" size="sm" className="mt-3 w-full" onClick={() => setOpen(true)}>
          {icon}{label}
        </Button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex items-center gap-0.5 text-primary hover:underline"
        >
          {icon}{label}
        </button>
      )}
      {open && <ModelFilesDialog path={path} title={title} onOpenChange={setOpen} />}
    </>
  )
}

function ModelFilesDialog({
  path, title, onOpenChange,
}: { path: string; title: string; onOpenChange: (open: boolean) => void }) {
  const dataSource = useMemo(() => createFilesystemDataSource("model-artifacts"), [])
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title} 파일</DialogTitle>
          <DialogDescription>
            파일을 보거나 내려받을 수 있습니다(읽기 전용). 전체 파일 화면은 관리자만 쓸 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <LocalFilesystemBrowser
          initialPath={path}
          rootPath={path}
          dataSource={dataSource}
          readOnly
        />
      </DialogContent>
    </Dialog>
  )
}
