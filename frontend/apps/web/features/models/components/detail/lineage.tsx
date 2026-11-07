"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { Link2, Plus, Search, Trash2 } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { EmptyState } from "@/components/model-hub/detail-layout"
import { LineageFlow, type FlowNode } from "@/components/model-hub/lineage-flow"
import { authFetch } from "@/features/auth/auth-fetch"
import { type LinkedOciModel } from "@/features/oci-hub/api"

import {
  addModelLineage, deleteModelLineage,
  type LineageRelation, type ModelLineage,
} from "../../api"
import { ReadOnlyNote } from "./shared"

export const RELATION_LABEL: Record<LineageRelation, string> = {
  TRAINING_DATA: "학습 데이터",
  EVALUATION_DATA: "평가 데이터",
  FEATURE_SOURCE: "피처 소스",
}

// OCI 쪽 관계("~에서 변환됨")를 이 모델에서 바라본 방향("~로 변환")으로 읽는다.
const OUTPUT_RELATION: Record<string, string> = {
  converted_from: "변환",
  fine_tuned_from: "파인튜닝",
  distilled_from: "증류",
  derived_from: "파생",
}

type DatasetHit = { id: number; name: string; datasource_name: string | null; datasource_type: string | null }

function AddLineageDialog({
  open, onOpenChange, modelName, versions, onAdded,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  modelName: string
  versions: number[]
  onAdded: () => void
}) {
  const [query, setQuery] = useState("")
  const [hits, setHits] = useState<DatasetHit[]>([])
  const [searching, setSearching] = useState(false)
  const [picked, setPicked] = useState<DatasetHit | null>(null)
  const [version, setVersion] = useState("all")
  const [relation, setRelation] = useState<LineageRelation>("TRAINING_DATA")
  const [description, setDescription] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setQuery(""); setHits([]); setPicked(null); setVersion("all"); setRelation("TRAINING_DATA"); setDescription("")
  }, [open])

  // 데이터셋 검색 (입력 300ms 후)
  useEffect(() => {
    if (!open) return
    const q = query.trim()
    if (!q) { setHits([]); return }
    const t = setTimeout(() => {
      setSearching(true)
      authFetch(`/api/v1/catalog/datasets?search=${encodeURIComponent(q)}&page_size=10`)
        .then((r) => (r.ok ? r.json() : { items: [] }))
        .then((d: { items?: DatasetHit[] }) => setHits(d.items ?? []))
        .catch(() => setHits([]))
        .finally(() => setSearching(false))
    }, 300)
    return () => clearTimeout(t)
  }, [query, open])

  const submit = async () => {
    if (!picked) return
    setSaving(true)
    try {
      await addModelLineage(modelName, {
        dataset_id: picked.id,
        model_version: version === "all" ? null : Number(version),
        relation_type: relation,
        description: description.trim() || undefined,
      })
      toast.success(`데이터셋 '${picked.name}' 을(를) 연결했습니다.`)
      onOpenChange(false)
      onAdded()
    } catch (e) {
      notifyError(e, "데이터셋을 연결하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>데이터셋 연결</DialogTitle>
          <DialogDescription>이 모델의 학습·평가에 사용한 데이터셋을 연결합니다.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>데이터셋</Label>
            {picked ? (
              <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                <span>
                  <span className="font-medium">{picked.name}</span>
                  {picked.datasource_name && <span className="ml-2 text-muted-foreground">{picked.datasource_name}</span>}
                </span>
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setPicked(null)}>변경</Button>
              </div>
            ) : (
              <>
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
                    placeholder="데이터셋 이름으로 검색" className="h-9 pl-8"
                  />
                </div>
                {query.trim() && (
                  <div className="max-h-52 overflow-auto rounded-md border">
                    {searching ? (
                      <p className="px-3 py-2 text-sm text-muted-foreground">검색 중...</p>
                    ) : hits.length === 0 ? (
                      <p className="px-3 py-2 text-sm text-muted-foreground">일치하는 데이터셋이 없습니다.</p>
                    ) : hits.map((h) => (
                      <button
                        key={h.id} type="button" onClick={() => setPicked(h)}
                        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-muted"
                      >
                        <span className="font-medium">{h.name}</span>
                        <span className="text-xs text-muted-foreground">{h.datasource_name ?? h.datasource_type}</span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>관계</Label>
              <Select value={relation} onValueChange={(v) => setRelation(v as LineageRelation)}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(RELATION_LABEL) as LineageRelation[]).map((r) => (
                    <SelectItem key={r} value={r}>{RELATION_LABEL[r]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>모델 버전</Label>
              <Select value={version} onValueChange={setVersion}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">전체 버전</SelectItem>
                  {versions.map((v) => <SelectItem key={v} value={String(v)}>v{v}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>설명 (선택)</Label>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="예: 2024년 대여 이력 전체" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={!picked || saving}>{saving ? "연결 중..." : "연결"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 리니지 탭 — 데이터셋(입력) → 이 모델 → OCI 배포 아티팩트(출력) 그래프 + 데이터셋 연결 목록. */
export function LineageTab({
  modelName, lineage, linked, versions, canEdit, onChanged,
}: {
  modelName: string
  lineage: ModelLineage[]
  linked: LinkedOciModel[]
  versions: number[]
  canEdit: boolean
  onChanged: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<ModelLineage | null>(null)
  const [busy, setBusy] = useState(false)

  const center = useMemo(() => ({ label: modelName, kind: "mlflow" as const }), [modelName])
  const inputs = useMemo<FlowNode[]>(() => lineage.map((l) => ({
    id: String(l.id),
    label: l.dataset_name || `데이터셋 ${l.dataset_id}`,
    kind: "dataset",
    relation: RELATION_LABEL[l.relation_type] ?? l.relation_type,
    description: l.model_version ? `v${l.model_version}` : null,
    href: `/dashboard/datasets/${l.dataset_id}`,
  })), [lineage])
  const outputs = useMemo<FlowNode[]>(() => linked.map((o) => ({
    id: o.name,
    label: o.display_name || o.name,
    kind: "oci",
    relation: OUTPUT_RELATION[o.relation_type] ?? "파생",
    description: o.framework,
    href: `/dashboard/oci-hub/${encodeURIComponent(o.name)}`,
  })), [linked])
  // 리니지가 바뀌면 그래프를 다시 그린다 (useNodesState 는 초기값만 쓰므로 key 로 재마운트).
  const graphKey = [...lineage.map((l) => l.id), ...linked.map((o) => o.name)].join(",")

  const remove = async () => {
    if (!removing) return
    setBusy(true)
    try {
      await deleteModelLineage(modelName, removing.id)
      toast.success("연결을 삭제했습니다.")
      setRemoving(null)
      onChanged()
    } catch (e) {
      notifyError(e, "연결을 삭제하지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        {canEdit ? <span /> : <ReadOnlyNote what="데이터셋 연결" />}
        {canEdit && (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus className="mr-1 h-4 w-4" />데이터셋 연결
          </Button>
        )}
      </div>

      {lineage.length === 0 && linked.length === 0 ? (
        <EmptyState
          icon={Link2}
          text={<>연결된 데이터셋이나 배포 아티팩트가 없습니다.{canEdit && <><br />&apos;데이터셋 연결&apos; 로 학습·평가 데이터셋을 연결하세요.</>}</>}
        />
      ) : (
        <LineageFlow key={graphKey} center={center} inputs={inputs} outputs={outputs} height={Math.max(260, Math.max(inputs.length, outputs.length) * 120 + 60)} />
      )}

      {lineage.length > 0 && (
        <div className="overflow-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-3 py-2 text-left font-medium">데이터셋</th>
                <th className="w-28 px-3 py-2 text-left font-medium">관계</th>
                <th className="w-24 px-3 py-2 text-left font-medium">모델 버전</th>
                <th className="px-3 py-2 text-left font-medium">설명</th>
                {canEdit && <th className="w-12 px-3 py-2" />}
              </tr>
            </thead>
            <tbody className="divide-y">
              {lineage.map((l) => (
                <tr key={l.id}>
                  <td className="px-3 py-2">
                    <Link href={`/dashboard/datasets/${l.dataset_id}`} className="font-medium text-primary hover:underline">
                      {l.dataset_name || `데이터셋 ${l.dataset_id}`}
                    </Link>
                    {l.datasource_type && <Badge variant="outline" className="ml-2 font-normal">{l.datasource_type}</Badge>}
                  </td>
                  <td className="px-3 py-2">{RELATION_LABEL[l.relation_type] ?? l.relation_type}</td>
                  <td className="px-3 py-2">{l.model_version ? `v${l.model_version}` : "전체"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{l.description || "—"}</td>
                  {canEdit && (
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive"
                        onClick={() => setRemoving(l)} aria-label="연결 삭제"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {linked.length > 0 && (
        <p className="text-xs text-muted-foreground">
          배포 아티팩트는 OCI 모델 허브에서 이 모델을 원본으로 연결한 모델입니다. 연결은 OCI 모델의 리니지 탭에서 관리합니다.
        </p>
      )}

      <AddLineageDialog open={adding} onOpenChange={setAdding} modelName={modelName} versions={versions} onAdded={onChanged} />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && !busy && setRemoving(null)}
        title="데이터셋 연결 삭제"
        desc={removing ? `'${removing.dataset_name ?? removing.dataset_id}' 와(과)의 연결을 삭제합니다.` : ""}
        confirmText="삭제"
        destructive
        isLoading={busy}
        handleConfirm={remove}
      />
    </div>
  )
}
