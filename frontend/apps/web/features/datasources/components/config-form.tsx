"use client"

import { Tip } from "@/components/tip"
import { CodeEditor } from "@/components/code-editor"
import { useId, useState } from "react"
import { Eye, EyeOff } from "lucide-react"

import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Switch } from "@workspace/ui/components/switch"
import type { DatasourceFieldDef } from "@/features/datasources/datasource-configs"
import { TagInput } from "@/components/tag-input"
import { decodeList } from "@/lib/text-list"

// 데이터 소스 유형별 동적 연결 설정 폼
export function ConfigForm({
  fields,
  values,
  onChange,
  storedSecrets = [],
}: {
  fields: DatasourceFieldDef[]
  values: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
  /** 서버에 저장된 비밀값 키 — 값은 내려오지 않으므로 "저장됨" 안내만 표시 */
  storedSecrets?: string[]
}) {
  const [showPasswords, setShowPasswords] = useState<Record<string, boolean>>({})
  const idPrefix = useId()
  const fieldId = (key: string) => `${idPrefix}-${key}`

  const seen = new Set<string>()
  const uniqueFields = fields.filter((f) => {
    if (seen.has(f.key)) return false
    seen.add(f.key)
    return true
  })

  const isVisible = (field: DatasourceFieldDef): boolean => {
    const conditions = fields
      .filter((f) => f.key === field.key && f.showWhen)
      .map((f) => f.showWhen!)
    if (conditions.length === 0) return true
    return conditions.some((cond) => String(values[cond.field] ?? "") === cond.value)
  }

  return (
    <div className="grid gap-3">
      {uniqueFields.map((field) => {
        if (!isVisible(field)) return null
        const id = fieldId(field.key)

        if (field.type === "toggle") {
          return (
            <div key={field.key} className="flex items-center justify-between">
              <Label htmlFor={id} className="text-sm">{field.label}</Label>
              <Switch
                id={id}
                checked={Boolean(values[field.key] ?? field.defaultValue ?? false)}
                onCheckedChange={(v) => onChange(field.key, v)}
              />
            </div>
          )
        }

        if (field.type === "select") {
          return (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={id} className="text-sm">
                {field.label}
                {field.required && <span className="text-destructive ml-0.5">*</span>}
              </Label>
              <Select
                value={String(values[field.key] ?? field.defaultValue ?? "")}
                onValueChange={(v) => onChange(field.key, v)}
              >
                <SelectTrigger id={id} className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {field.options?.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-sm">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        }

        if (field.type === "list") {
          // 값 목록 — 배지 입력, 문자열 배열로 저장(예전 쉼표 문자열도 읽음, TODO-144)
          const raw = values[field.key]
          return (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={id} className="text-sm">
                {field.label}
                {field.required && <span className="text-destructive ml-0.5">*</span>}
              </Label>
              <TagInput
                id={id}
                mono
                commaSeparates
                placeholder={field.placeholder}
                value={Array.isArray(raw) ? decodeList(raw.map(String)) : decodeList(typeof raw === "string" ? raw : null)}
                onChange={(v) => onChange(field.key, v)}
              />
            </div>
          )
        }

        if (field.type === "textarea") {
          // 여러 줄 값(k=v 목록 등) — 한 줄 입력란은 줄바꿈을 지워 값이 붙어 버린다
          return (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={id} className="text-sm">
                {field.label}
                {field.required && <span className="text-destructive ml-0.5">*</span>}
              </Label>
              <CodeEditor
                id={id}
                placeholder={field.placeholder}
                value={String(values[field.key] ?? "")}
                onChange={(v) => onChange(field.key, v)}
                minHeight={72}
                maxHeight={240}
              />
            </div>
          )
        }

        const isStoredSecret = storedSecrets.includes(field.key)
        const isPassword = field.type === "password" || isStoredSecret
        const showPw = showPasswords[field.key] ?? false

        return (
          <div key={field.key} className="grid gap-1.5">
            <Label htmlFor={id} className="text-sm">
              {field.label}
              {field.required && <span className="text-destructive ml-0.5">*</span>}
            </Label>
            <div className="relative">
              <Input
                id={id}
                type={isPassword && !showPw ? "password" : field.type === "number" ? "number" : "text"}
                placeholder={
                  isStoredSecret ? "저장됨 — 변경할 때만 입력" : field.placeholder
                }
                value={String(values[field.key] ?? "")}
                onChange={(e) =>
                  onChange(
                    field.key,
                    field.type === "number" ? Number(e.target.value) || "" : e.target.value,
                  )
                }
                className={`h-9 text-sm ${isPassword ? "pr-9" : ""}`}
              />
              {isPassword && (
                <Tip label={showPw ? "값 숨기기" : "값 보기"}><button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowPasswords((p) => ({ ...p, [field.key]: !showPw }))}
                  aria-label={showPw ? "값 숨기기" : "값 보기"}
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button></Tip>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
