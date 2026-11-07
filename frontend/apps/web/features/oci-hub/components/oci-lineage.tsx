"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Loader2, Plus, Trash2 } from "lucide-react"
import { errorMessage, notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"

import {
  addLineage, removeLineage,
  LINEAGE_RELATIONS, LINEAGE_RELATION_LABELS, LINEAGE_SOURCE_LABELS, LINEAGE_SOURCE_TYPES,
  type OciLineage,
} from "@/features/oci-hub/api"
import { LineageFlow, type FlowKind, type FlowNode } from "@/components/model-hub/lineage-flow"

/** 카탈로그 안에서 열 수 있는 리니지 대상이면 그 주소 (MLflow 모델 → MLflow 상세). */
export function lineageHref(l: { source_type: string; source_id: string }): string | null {
  if (l.source_type === "mlflow") return `/dashboard/models/${encodeURIComponent(l.source_id)}`
  return null
}

function toFlow(l: OciLineage): FlowNode {
  return {
    id: String(l.id),
    label: l.source_name || l.source_id,
    kind: (["dataset", "mlflow", "model", "external"].includes(l.source_type) ? l.source_type : "external") as FlowKind,
    relation: LINEAGE_RELATION_LABELS[l.relation_type] ?? l.relation_type,
    description: l.description,
    href: lineageHref(l),
  }
}

function LineageGraph({ modelName, lineage }: { modelName: string; lineage: OciLineage[] }) {
  const center = useMemo(() => ({ label: modelName, kind: "oci" as const }), [modelName])
  const inputs = useMemo(() => lineage.filter((l) => l.relation_type !== "produces").map(toFlow), [lineage])
  const outputs = useMemo(() => lineage.filter((l) => l.relation_type === "produces").map(toFlow), [lineage])
  return <LineageFlow center={center} inputs={inputs} outputs={outputs} />
}

function AddLineageDialog({
  modelName, open, onOpenChange, onAdded,
}: { modelName: string; open: boolean; onOpenChange: (o: boolean) => void; onAdded: () => void }) {
  const [sourceType, setSourceType] = useState<string>("dataset")
  const [relation, setRelation] = useState<string>("trained_on")
  const [sourceId, setSourceId] = useState("")
  const [sourceName, setSourceName] = useState("")
  const [description, setDescription] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setSourceType("dataset"); setRelation("trained_on"); setSourceId(""); setSourceName(""); setDescription(""); setError(null)
  }

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      await addLineage(modelName, {
        source_type: sourceType,
        source_id: sourceId.trim(),
        source_name: sourceName.trim() || undefined,
        relation_type: relation,
        description: description.trim() || undefined,
      })
      toast.success("리니지를 추가했습니다.")
      onAdded()
      reset()
      onOpenChange(false)
    } catch (e) {
      setError(errorMessage(e) ?? "추가하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!saving) { if (!o) reset(); onOpenChange(o) } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>리니지 추가</DialogTitle>
          <DialogDescription>
            이 모델의 학습 데이터·원본 모델(입력) 또는 산출물(출력)을 연결합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label>관계</Label>
              <Select value={relation} onValueChange={setRelation} disabled={saving}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LINEAGE_RELATIONS.map((r) => (
                    <SelectItem key={r} value={r}>{LINEAGE_RELATION_LABELS[r]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>대상 종류</Label>
              <Select value={sourceType} onValueChange={setSourceType} disabled={saving}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LINEAGE_SOURCE_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>{LINEAGE_SOURCE_LABELS[t]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="lin-id">대상 식별자 *</Label>
            <Input
              id="lin-id" value={sourceId} onChange={(e) => setSourceId(e.target.value)} disabled={saving}
              placeholder={sourceType === "dataset" ? "데이터셋 URN 또는 이름" : sourceType === "mlflow" ? "MLflow 모델 이름 (예: argus.ml.film_recommender)" : sourceType === "model" ? "모델 이름 (예: bert-base-uncased)" : "URL 등"}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="lin-name">표시 이름</Label>
            <Input id="lin-name" value={sourceName} onChange={(e) => setSourceName(e.target.value)} disabled={saving} placeholder="그래프에 표시할 이름 (비우면 식별자)" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="lin-desc">설명</Label>
            <Input id="lin-desc" value={description} onChange={(e) => setDescription(e.target.value)} disabled={saving} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={saving || !sourceId.trim()}>
            {saving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 리니지 탭 — 그래프 + 목록(편집 권한이 있으면 추가·삭제). */
export function OciLineageTab({
  modelName, lineage, canEdit, onChanged,
}: { modelName: string; lineage: OciLineage[]; canEdit: boolean; onChanged: () => void }) {
  const [addOpen, setAddOpen] = useState(false)
  const [deleting, setDeleting] = useState<number | null>(null)
  const askConfirm = useConfirm()
  // 리니지가 바뀌면 그래프를 새로 그린다(useNodesState 는 초기값만 쓰므로 key 로 재마운트).
  const graphKey = lineage.map((l) => l.id).join(",")

  async function remove(l: OciLineage) {
    if (!(await askConfirm({
      title: "리니지 삭제",
      description: `'${l.source_name || l.source_id}' 리니지를 삭제할까요?`,
      confirmText: "삭제",
      destructive: true,
    }))) return
    setDeleting(l.id)
    try {
      await removeLineage(modelName, l.id)
      toast.success("리니지를 삭제했습니다.")
      onChanged()
    } catch (e) {
      notifyError(e, "리니지를 삭제하지 못했습니다.")
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="space-y-3">
      {canEdit && (
        <div className="flex justify-end">
          <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4 mr-1.5" />리니지 추가
          </Button>
        </div>
      )}
      {lineage.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-12">
          등록된 리니지가 없습니다.{canEdit && " '리니지 추가' 로 학습 데이터나 원본 모델을 연결하세요."}
        </p>
      ) : (
        <>
          <LineageGraph key={graphKey} modelName={modelName} lineage={lineage} />
          <div className="border rounded-md overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/60">
                <tr>
                  <th className="px-3 py-2 text-left font-medium w-32">관계</th>
                  <th className="px-3 py-2 text-left font-medium w-28">종류</th>
                  <th className="px-3 py-2 text-left font-medium">대상</th>
                  <th className="px-3 py-2 text-left font-medium">설명</th>
                  {canEdit && <th className="px-3 py-2 w-16" />}
                </tr>
              </thead>
              <tbody className="divide-y">
                {lineage.map((l) => (
                  <tr key={l.id}>
                    <td className="px-3 py-2">{LINEAGE_RELATION_LABELS[l.relation_type] ?? l.relation_type}</td>
                    <td className="px-3 py-2">{LINEAGE_SOURCE_LABELS[l.source_type] ?? l.source_type}</td>
                    <td className="px-3 py-2">
                      {lineageHref(l) ? (
                        <Link href={lineageHref(l)!} className="font-medium text-primary hover:underline">{l.source_name || l.source_id}</Link>
                      ) : (
                        <span className="font-medium">{l.source_name || l.source_id}</span>
                      )}
                      {l.source_name && <span className="ml-2 font-mono text-xs text-muted-foreground">{l.source_id}</span>}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{l.description || "-"}</td>
                    {canEdit && (
                      <td className="px-3 py-2 text-right">
                        <Button
                          variant="ghost" size="icon-sm" aria-label="리니지 삭제"
                          disabled={deleting === l.id} onClick={() => remove(l)}
                        >
                          {deleting === l.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <AddLineageDialog modelName={modelName} open={addOpen} onOpenChange={setAddOpen} onAdded={onChanged} />
    </div>
  )
}
