"use client"

import { useCallback, useEffect, useState } from "react"
import { Loader2, X } from "lucide-react"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"

import { fetchUsers } from "@/features/users/api"
import { type User } from "@/features/users/data/schema"
import { createApi } from "../api"
import { useApis } from "./apis-provider"

const PROTOCOLS = ["REST", "GraphQL", "gRPC", "SOAP", "Webhook", "기타"]
const userDisplayName = (u: User) => `${u.lastName ?? ""}${u.firstName ?? ""}`.trim() || u.username

// 태그 배지 입력 — Enter 로 추가, X 로 삭제. 저장 포맷은 콤마 구분 문자열.
function TagsBadgeInput({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const [text, setText] = useState("")
  const tags = value.split(",").map((t) => t.trim()).filter(Boolean)
  const commit = (raw: string) => {
    const incoming = raw.split(",").map((t) => t.trim()).filter(Boolean)
    if (incoming.length === 0) return
    const next = [...tags]
    for (const t of incoming) if (!next.includes(t)) next.push(t)
    onChange(next.join(", "))
    setText("")
  }
  const remove = (t: string) => onChange(tags.filter((x) => x !== t).join(", "))
  return (
    <div className="flex w-full flex-wrap items-center gap-1 rounded-md border px-2 py-1">
      {tags.map((t) => (
        <Badge key={t} variant="secondary" className="gap-1 text-xs">
          {t}
          <button type="button" className="hover:opacity-70" onClick={() => remove(t)} aria-label={`${t} 제거`}><X className="h-3 w-3" /></button>
        </Badge>
      ))}
      <input
        className="h-7 min-w-[100px] flex-1 bg-transparent text-sm outline-none"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(text) }
          else if (e.key === "Backspace" && text === "" && tags.length > 0) remove(tags[tags.length - 1]!)
        }}
        onBlur={() => { if (text.trim()) commit(text) }}
        placeholder="입력 후 Enter"
      />
    </div>
  )
}

export function ApisAddDialog() {
  const { open, setOpen, refreshApis, setSelectedApiName } = useApis()
  const [regMode, setRegMode] = useState<"spec" | "manual">("spec")
  const [mode, setMode] = useState<"text" | "url">("text")
  const [specText, setSpecText] = useState("")
  const [specUrl, setSpecUrl] = useState("")
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [version, setVersion] = useState("")
  const [protocol, setProtocol] = useState("REST")
  const [contractUrl, setContractUrl] = useState("")
  const [contractText, setContractText] = useState("")
  const [ownerEmail, setOwnerEmail] = useState("")
  const [category, setCategory] = useState("")
  const [tags, setTags] = useState("")
  const [users, setUsers] = useState<User[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 다이얼로그가 열리면 사용자 관리 목록을 1회 로드(소유자 콤보용).
  useEffect(() => {
    if (open === "add" && users.length === 0) {
      fetchUsers({ pageSize: 0 }).then((r) => setUsers(r.items)).catch(() => {})
    }
  }, [open, users.length])

  const reset = useCallback(() => {
    setRegMode("spec"); setMode("text"); setSpecText(""); setSpecUrl("")
    setName(""); setDisplayName(""); setDescription(""); setBaseUrl(""); setVersion(""); setProtocol("REST")
    setContractUrl(""); setContractText("")
    setOwnerEmail(""); setCategory(""); setTags(""); setError(null)
  }, [])

  const submit = useCallback(async () => {
    setError(null)
    if (regMode === "spec") {
      if (mode === "text" && !specText.trim()) { setError("OpenAPI 스펙(JSON/YAML)을 붙여넣으세요."); return }
      if (mode === "url" && !specUrl.trim()) { setError("스펙 URL을 입력하세요."); return }
    } else if (!name.trim()) {
      setError("수동 등록 시 API 이름은 필수입니다.")
      return
    }
    setSaving(true)
    try {
      const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean)
      const created = await createApi(
        regMode === "spec"
          ? {
              name: name.trim() || undefined,
              owner_email: ownerEmail.trim() || undefined,
              category: category.trim() || undefined,
              spec_text: mode === "text" ? specText : undefined,
              spec_url: mode === "url" ? specUrl.trim() : undefined,
            }
          : {
              name: name.trim(),
              display_name: displayName.trim() || undefined,
              description: description.trim() || undefined,
              base_url: baseUrl.trim() || undefined,
              version: version.trim() || undefined,
              protocol,
              contract_url: contractUrl.trim() || undefined,
              contract_text: contractText.trim() || undefined,
              owner_email: ownerEmail.trim() || undefined,
              category: category.trim() || undefined,
              tags: tagList.length > 0 ? tagList : undefined,
            },
      )
      reset()
      setOpen(null)
      await refreshApis()
      setSelectedApiName(created.name)
    } catch (e) {
      setError(e instanceof Error ? e.message : "API 등록에 실패했습니다.")
    } finally {
      setSaving(false)
    }
  }, [regMode, mode, specText, specUrl, name, displayName, description, baseUrl, version, protocol, contractUrl, contractText, ownerEmail, category, tags, reset, setOpen, refreshApis, setSelectedApiName])

  return (
    <Dialog open={open === "add"} onOpenChange={(o) => { if (!o) { setOpen(null); reset() } }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>API 등록</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <Tabs value={regMode} onValueChange={(v) => setRegMode(v as "spec" | "manual")}>
            <TabsList>
              <TabsTrigger value="spec">스펙(Swagger/OpenAPI)</TabsTrigger>
              <TabsTrigger value="manual">수동 등록</TabsTrigger>
            </TabsList>
          </Tabs>

          {regMode === "spec" ? (
            <>
              <p className="text-sm text-muted-foreground">
                OpenAPI 2.0(Swagger) / 3.x 스펙을 붙여넣거나 URL로 등록합니다. 이름·버전·엔드포인트는 스펙에서 자동 추출됩니다.
              </p>
              <Tabs value={mode} onValueChange={(v) => setMode(v as "text" | "url")}>
                <TabsList>
                  <TabsTrigger value="text">스펙 붙여넣기</TabsTrigger>
                  <TabsTrigger value="url">URL</TabsTrigger>
                </TabsList>
              </Tabs>
              {mode === "text" ? (
                <Textarea
                  placeholder='{"openapi":"3.0.0","info":{"title":"My API","version":"1.0.0"}, ...}  또는 YAML'
                  value={specText}
                  onChange={(e) => setSpecText(e.target.value)}
                  className="min-h-48 font-mono text-xs"
                />
              ) : (
                <Input placeholder="https://example.com/openapi.json" value={specUrl} onChange={(e) => setSpecUrl(e.target.value)} />
              )}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>API 식별자 (선택, 미지정 시 자동 생성)</Label>
                  <Input placeholder="예: payments-api" value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} />
                  <p className="text-[11px] text-muted-foreground">영문·숫자·하이픈(-)·언더스코어(_)만 사용</p>
                </div>
                <div className="space-y-1.5">
                  <Label>카테고리 (선택)</Label>
                  <Input placeholder="예: 결제" value={category} onChange={(e) => setCategory(e.target.value)} />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>소유자</Label>
                <Select value={ownerEmail || "none"} onValueChange={(v) => setOwnerEmail(v === "none" ? "" : v)}>
                  <SelectTrigger><SelectValue placeholder="소유자 선택" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                    {users.map((u) => <SelectItem key={u.id} value={u.email}>{userDisplayName(u)} ({u.username})</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                스펙 없이 HTTP/REST API를 직접 등록합니다. 기본 메타데이터만 입력해 생성한 뒤, 상세 화면의 엔드포인트 탭에서 엔드포인트를 추가하세요.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>API 식별자 (필수)</Label>
                  <Input placeholder="예: billing-api" value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} />
                  <p className="text-[11px] text-muted-foreground">영문·숫자·하이픈(-)·언더스코어(_)만 사용</p>
                </div>
                <div className="space-y-1.5">
                  <Label>표시명 (선택)</Label>
                  <Input placeholder="예: 결제 API" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>설명 (선택)</Label>
                <Textarea placeholder="API 용도·기능 요약" value={description} onChange={(e) => setDescription(e.target.value)} className="min-h-16 text-sm" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>Base URL (선택)</Label>
                  <Input placeholder="https://api.example.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label>버전</Label>
                  <Input placeholder="예: 1.0.0 (미입력 시 1.0.0)" value={version} onChange={(e) => setVersion(e.target.value)} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>프로토콜</Label>
                  <Select value={protocol} onValueChange={setProtocol}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{PROTOCOLS.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>카테고리 (선택)</Label>
                  <Input placeholder="예: 결제" value={category} onChange={(e) => setCategory(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label>소유자</Label>
                  <Select value={ownerEmail || "none"} onValueChange={(v) => setOwnerEmail(v === "none" ? "" : v)}>
                    <SelectTrigger><SelectValue placeholder="소유자 선택" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none" className="text-muted-foreground">미지정</SelectItem>
                      {users.map((u) => <SelectItem key={u.id} value={u.email}>{userDisplayName(u)} ({u.username})</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>태그 (선택, 입력 후 Enter)</Label>
                <TagsBadgeInput value={tags} onChange={setTags} />
              </div>
              {protocol !== "REST" && (
                <>
                  <div className="space-y-1.5">
                    <Label>계약 문서 URL (선택)</Label>
                    <Input placeholder="SDL/WSDL/.proto/AsyncAPI 문서 URL" value={contractUrl} onChange={(e) => setContractUrl(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>계약 문서 원문 (선택)</Label>
                    <Textarea placeholder="SDL / WSDL / .proto / AsyncAPI 원문을 붙여넣기" value={contractText} onChange={(e) => setContractText(e.target.value)} className="min-h-24 font-mono text-xs" />
                  </div>
                </>
              )}
            </>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(null)} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            등록
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
