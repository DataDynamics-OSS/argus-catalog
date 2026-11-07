"use client"

import { useRef } from "react"
import { Upload } from "lucide-react"

import { Button } from "@workspace/ui/components/button"

// 등록 다이얼로그·상세(스펙 새 버전·계약 문서·개요 편집)가 함께 쓰는 입력 도우미.

/** http(s) 주소 형식 확인 — 비어 있으면 통과(선택 항목). */
export function urlError(value: string): string | null {
  const v = value.trim()
  if (!v) return null
  try {
    const u = new URL(v)
    if (u.protocol === "http:" || u.protocol === "https:") return null
  } catch { /* 아래 메시지 */ }
  return "http:// 또는 https:// 로 시작하는 주소를 입력하세요."
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// 스펙·계약 문서 파일 불러오기 — 브라우저에서 텍스트로 읽어 입력란에 채운다(업로드 API 불필요).
export const TEXT_FILE_MAX = 5 * 1024 * 1024
// 이보다 큰 스펙 파일은 입력란에 펼치지 않고 파일 정보만 표시(수 MB 텍스트 입력란은 화면이 느려짐)
export const LARGE_SPEC_BYTES = 200 * 1024
export const SPEC_FILE_ACCEPT = ".json,.yaml,.yml"
export const CONTRACT_FILE_ACCEPT = ".graphql,.gql,.wsdl,.xml,.proto,.json,.yaml,.yml,.txt"

export function FileLoadButton({ accept, onLoad, onError, disabled }: {
  accept: string
  onLoad: (text: string, file: File) => void
  onError: (message: string) => void
  disabled?: boolean
}) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <>
      <input
        ref={ref}
        type="file"
        accept={accept}
        className="hidden"
        onChange={async (e) => {
          const f = e.target.files?.[0]
          e.target.value = "" // 같은 파일을 다시 골라도 onChange 가 발생하도록
          if (!f) return
          if (f.size > TEXT_FILE_MAX) { onError(`파일이 너무 큽니다(최대 5MB): ${f.name}`); return }
          try { onLoad(await f.text(), f) } catch { onError(`파일을 읽지 못했습니다: ${f.name}`) }
        }}
      />
      <Button type="button" variant="outline" size="sm" className="h-8" disabled={disabled} onClick={() => ref.current?.click()}>
        <Upload className="mr-1 h-3.5 w-3.5" /> 파일 불러오기
      </Button>
    </>
  )
}
