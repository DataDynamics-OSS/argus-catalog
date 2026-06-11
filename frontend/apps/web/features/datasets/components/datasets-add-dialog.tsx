"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"
import { Textarea } from "@workspace/ui/components/textarea"
import { createDataset, fetchDatasources } from "../api"
import type { Datasource } from "../data/schema"
import { useDatasets } from "./datasets-provider"
import { isDatasourceTypeImplemented } from "@/features/datasources/datasource-configs"

export function DatasetsAddDialog() {
  const { open, setOpen, refreshDatasets } = useDatasets()
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [datasourceId, setDatasourceId] = useState("")
  const [summary, setSummary] = useState("")
  const [description, setDescription] = useState("")

  useEffect(() => {
    if (open === "add") {
      fetchDatasources().then(setDatasources).catch(() => {})
    }
  }, [open])

  // 지원되는 (sync 어댑터가 있는) 데이터 소스만 선택지로 노출.
  const availableDatasources = useMemo(
    () => datasources.filter((p) => isDatasourceTypeImplemented(p.type)),
    [datasources],
  )

  // 선택한 데이터 소스의 origin 을 그대로 데이터셋 origin 으로 사용 (데이터 소스이 SoT).
  const selectedDatasource = useMemo(
    () => availableDatasources.find((p) => String(p.id) === datasourceId),
    [availableDatasources, datasourceId],
  )
  const effectiveOrigin = selectedDatasource?.origin ?? "DEV"

  // 데이터 소스 카드 legend 와 동일한 색상 매핑: PROD=emerald, STAGING=orange, DEV=sky.
  const originColor = (origin: string | undefined) =>
    origin === "PROD"
      ? "text-emerald-600"
      : origin === "STAGING"
        ? "text-orange-500"
        : "text-sky-500"

  const handleSubmit = useCallback(async () => {
    if (!name.trim() || !datasourceId) return
    setSaving(true)
    setError(null)
    try {
      await createDataset({
        name: name.trim(),
        display_name: displayName.trim() || undefined,
        datasource_id: Number(datasourceId),
        summary: summary.trim() || undefined,
        description: description.trim() || undefined,
        origin: effectiveOrigin,
      })
      setName("")
      setDisplayName("")
      setDatasourceId("")
      setSummary("")
      setDescription("")
      setOpen(null)
      await refreshDatasets()
    } catch (e) {
      setError(e instanceof Error ? e.message : "데이터셋 생성에 실패했습니다.")
    } finally {
      setSaving(false)
    }
  }, [name, displayName, datasourceId, summary, description, effectiveOrigin, setOpen, refreshDatasets])

  return (
    <Dialog open={open === "add"} onOpenChange={() => setOpen(null)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>데이터셋 추가</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="ds-name">
              물리명 <span className="text-xs text-muted-foreground">(테이블명)</span>
            </Label>
            <Input
              id="ds-name"
              placeholder="예: user_events"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-display-name">
              논리명 <span className="text-xs text-muted-foreground">(선택, 표시용)</span>
            </Label>
            <Input
              id="ds-display-name"
              placeholder="예: 사용자 이벤트"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={255}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-datasource">데이터 소스</Label>
            <Select value={datasourceId} onValueChange={setDatasourceId}>
              <SelectTrigger id="ds-datasource">
                <SelectValue placeholder="데이터 소스 선택" />
              </SelectTrigger>
              <SelectContent>
                {availableDatasources.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    <span className="inline-flex items-center gap-2">
                      <span>{p.name}</span>
                      <span className={`text-[10px] font-mono font-semibold ${originColor(p.origin)}`}>
                        {p.origin || "DEV"}
                      </span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* 데이터셋 환경은 데이터 소스의 환경을 그대로 따른다 — 별도 입력 없이 표시만. */}
            {selectedDatasource && (
              <p className="text-xs text-muted-foreground">
                환경은 데이터 소스의 값
                <span className={`ml-1 font-mono font-semibold ${originColor(effectiveOrigin)}`}>
                  {effectiveOrigin}
                </span>
                {" "}을 따릅니다.
              </p>
            )}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-summary">
              요약 <span className="text-xs text-muted-foreground">(한 줄, 최대 200자)</span>
            </Label>
            <Input
              id="ds-summary"
              placeholder="예: 매장 주문 트랜잭션"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ds-desc">상세 설명</Label>
            <Textarea
              id="ds-desc"
              placeholder="이 데이터셋의 내용을 자세히 설명하세요"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => setOpen(null)}
              disabled={saving}
            >
              취소
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={saving || !name.trim() || !datasourceId}
            >
              {saving ? "생성 중..." : "생성"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
