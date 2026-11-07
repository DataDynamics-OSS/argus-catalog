"use client"

/**
 * 설정 값 출처 패널 — 각 설정 탭 아래에 붙는다.
 *
 * GET /api/v1/settings/sources (관리자 전용) 의 카테고리 하나를 보여 준다. 설정 파일(config.yml·properties)은
 * 최초 기동 때 DB 에 없는 항목만 채우고 이후에는 DB(이 화면) 값이 우선이라, "설정 파일을 고쳤는데 왜
 * 안 바뀌지?"를 바로 알 수 있게 항목별 출처와 설정 파일 값과의 차이를 표시한다.
 */

import { useCallback, useEffect, useState } from "react"
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { authFetch } from "@/features/auth/auth-fetch"
import { errorMessage } from "@/lib/notify"

export type SourceKind = "default" | "config" | "db"

export type SourceInfo = {
  source: SourceKind
  description: string
  /** 비밀값 — 값 자체를 보여 주지 않는다 */
  secret?: boolean
  differs_from_file?: boolean
  file_value?: string
  override?: string
}

export const SOURCE_LABEL: Record<SourceKind, string> = {
  default: "기본값",
  config: "설정 파일",
  db: "화면에서 변경",
}

const SOURCE_CLASS: Record<SourceKind, string> = {
  default: "border-border text-muted-foreground",
  config: "border-sky-500/40 text-sky-700 dark:text-sky-400",
  db: "border-emerald-500/40 text-emerald-700 dark:text-emerald-400",
}

/** 출처별 개수와 설정 파일과 어긋난 항목 수 */
export function summarize(items: Record<string, SourceInfo>) {
  const counts: Record<SourceKind, number> = { default: 0, config: 0, db: 0 }
  let differs = 0
  let overrides = 0
  for (const info of Object.values(items)) {
    counts[info.source] = (counts[info.source] ?? 0) + 1
    if (info.differs_from_file) differs++
    if (info.override) overrides++
  }
  return { counts, differs, overrides }
}

/** 비고 칸 문구 */
export function sourceNote(info: SourceInfo): string {
  const notes: string[] = []
  if (info.override) notes.push(info.override)
  if (info.differs_from_file) {
    notes.push(
      info.secret || info.file_value === undefined
        ? "설정 파일 값과 다름 — 이 화면 값이 쓰임"
        : `설정 파일 값 "${info.file_value || "(빈 값)"}" 과 다름 — 이 화면 값이 쓰임`,
    )
  }
  return notes.join(" · ")
}

function SourceBadge({ kind }: { kind: SourceKind }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-px text-[11px] ${SOURCE_CLASS[kind]}`}>
      {SOURCE_LABEL[kind]}
    </span>
  )
}

export function SettingSources({ category }: { category: string }) {
  const [items, setItems] = useState<Record<string, SourceInfo> | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await authFetch("/api/v1/settings/sources")
      if (!res.ok) throw new Error(`값 출처를 불러오지 못했습니다: ${res.status}`)
      const body = (await res.json()) as Record<string, Record<string, SourceInfo>>
      setItems(body[category] ?? {})
    } catch (e) {
      setError(errorMessage(e) ?? "값 출처를 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [category])

  useEffect(() => {
    load()
  }, [load])

  if (error) return <p className="mt-4 max-w-2xl text-xs text-destructive">{error}</p>
  if (!items || Object.keys(items).length === 0) return null

  const { counts, differs, overrides } = summarize(items)
  const keys = Object.keys(items).sort()

  return (
    <div className="mt-4 max-w-2xl rounded-lg border bg-muted/20 text-sm" data-testid="setting-sources">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="inline-flex items-center gap-1 font-medium"
          onClick={() => {
            if (!open) load() // 저장 직후 펼칠 때 최신 값으로
            setOpen(!open)
          }}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          값 출처
        </button>
        <span className="text-xs text-muted-foreground">
          {(Object.keys(SOURCE_LABEL) as SourceKind[])
            .filter((k) => counts[k] > 0)
            .map((k) => `${SOURCE_LABEL[k]} ${counts[k]}`)
            .join(" · ")}
        </span>
        {differs > 0 && (
          <span className="rounded border border-amber-500/40 px-1.5 py-px text-[11px] text-amber-700 dark:text-amber-400">
            설정 파일과 다름 {differs}
          </span>
        )}
        {overrides > 0 && (
          <span className="rounded border border-amber-500/40 px-1.5 py-px text-[11px] text-amber-700 dark:text-amber-400">
            환경변수 강제 {overrides}
          </span>
        )}
        <Button variant="ghost" size="sm" className="ml-auto h-7" onClick={() => load()} aria-label="값 출처 새로 고침">
          <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>
      {open && (
        <div className="border-t px-3 py-2">
          <p className="mb-2 text-xs text-muted-foreground">
            설정 파일(config.yml·config.properties)은 처음 기동할 때 DB 에 없는 항목만 채웁니다. 그 뒤에는 이 화면에서
            저장한 DB 값이 우선이라, 설정 파일을 고쳐도 아래 &ldquo;설정 파일과 다름&rdquo; 항목은 바뀌지 않습니다.
            비밀값은 값을 보여 주지 않습니다.
          </p>
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 text-left font-medium">항목</th>
                <th className="py-1 pr-2 text-left font-medium">출처</th>
                <th className="py-1 text-left font-medium">비고</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const info = items[k]!
                return (
                  <tr key={k} className="border-t border-border/60 align-top">
                    <td className="py-1 pr-2">
                      <div className="font-mono">{k}</div>
                      {info.description && <div className="text-muted-foreground">{info.description}</div>}
                    </td>
                    <td className="py-1 pr-2"><SourceBadge kind={info.source} /></td>
                    <td className="py-1 text-amber-700 dark:text-amber-400">{sourceNote(info)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
