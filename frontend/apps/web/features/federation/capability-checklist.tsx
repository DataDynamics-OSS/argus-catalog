"use client"

import { useMemo } from "react"

import { Checkbox } from "@workspace/ui/components/checkbox"

import type { CapabilityGroup, CapabilityItem } from "./api"

/** capability 키 그룹별 체크리스트. 노출자 정책·소비자 선택 양쪽에서 재사용.
 *
 * - `available` 가 주어지면(소비자 측) 그 키만 선택 가능, 나머지는 비활성(노출자가 안 줌).
 * - `schema.*` 하위 필드는 마스터 `schema` 가 꺼지면 자동 비활성.
 */
export function CapabilityChecklist({
  items,
  groups,
  value,
  onChange,
  available,
  disabled,
}: {
  items: CapabilityItem[]
  groups: CapabilityGroup[]
  value: string[]
  onChange: (keys: string[]) => void
  available?: string[] | null
  disabled?: boolean
}) {
  const selected = useMemo(() => new Set(value), [value])
  const availableSet = useMemo(
    () => (available ? new Set(available) : null),
    [available]
  )
  const schemaOn = selected.has("schema")

  const itemsByGroup = useMemo(() => {
    const m = new Map<string, CapabilityItem[]>()
    for (const it of items) {
      const arr = m.get(it.group) ?? []
      arr.push(it)
      m.set(it.group, arr)
    }
    return m
  }, [items])

  const isAvailable = (key: string) => !availableSet || availableSet.has(key)

  const toggle = (key: string, checked: boolean) => {
    const next = new Set(selected)
    if (checked) {
      next.add(key)
    } else {
      next.delete(key)
      // 마스터 schema 를 끄면 하위 필드도 함께 제거
      if (key === "schema") {
        for (const it of items) if (it.key.startsWith("schema.")) next.delete(it.key)
      }
    }
    onChange([...next])
  }

  const toggleGroup = (group: string, on: boolean) => {
    const next = new Set(selected)
    for (const it of itemsByGroup.get(group) ?? []) {
      if (!isAvailable(it.key)) continue
      if (on) next.add(it.key)
      else next.delete(it.key)
    }
    onChange([...next])
  }

  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => {
        const groupItems = itemsByGroup.get(g.group) ?? []
        const selectable = groupItems.filter((it) => isAvailable(it.key))
        const allOn =
          selectable.length > 0 && selectable.every((it) => selected.has(it.key))
        return (
          <div key={g.group} className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium">{g.label}</h4>
              {!disabled && selectable.length > 0 && (
                <button
                  type="button"
                  className="text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => toggleGroup(g.group, !allOn)}
                >
                  {allOn ? "전체 해제" : "전체 선택"}
                </button>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
              {groupItems.map((it) => {
                const isSub = it.key.startsWith("schema.")
                const unavailable = !isAvailable(it.key)
                const itemDisabled =
                  disabled || unavailable || (isSub && !schemaOn)
                return (
                  <label
                    key={it.key}
                    className={`flex items-center gap-2 text-sm ${
                      itemDisabled
                        ? "cursor-not-allowed text-muted-foreground/60"
                        : "cursor-pointer"
                    } ${isSub ? "pl-4" : ""}`}
                  >
                    <Checkbox
                      checked={selected.has(it.key)}
                      disabled={itemDisabled}
                      onCheckedChange={(c) => toggle(it.key, c === true)}
                    />
                    <span className="truncate">
                      {it.label}
                      {unavailable && (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          (노출 안 함)
                        </span>
                      )}
                    </span>
                  </label>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
