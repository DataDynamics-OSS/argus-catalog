"use client"

import { useEffect, useState } from "react"
import { notifyError, toast } from "@/lib/notify"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
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
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { Textarea } from "@workspace/ui/components/textarea"

import {
  createMcpDeployment,
  createMcpServer,
  fetchMcpServers,
  importMcpDocuments,
} from "../api"
import {
  AUTH_METHODS,
  ENVIRONMENTS,
  PROVIDER_LABEL,
  PROVIDER_TYPES,
  TRANSPORTS,
  type McpImportResult,
  type McpServerSummary,
} from "../data/schema"
import { OriginBadge } from "./mcp-badges"
import { useMcpServers } from "./mcp-provider"

const NAME_RE = /^[a-z0-9][a-z0-9._-]*$/

function OLabel({ children, origin }: { children: React.ReactNode; origin: "argus" | "registry" }) {
  return <Label className="flex items-center gap-1.5">{children}<OriginBadge origin={origin} /></Label>
}

export function McpDialogs() {
  return (
    <>
      <AddServerDialog />
      <AddDeploymentDialog />
      <ImportDialog />
    </>
  )
}

// ---------------------------------------------------------------------------
// 서버 정의 등록
// ---------------------------------------------------------------------------

function AddServerDialog() {
  const { open, setOpen, reload, setSelectedServer } = useMcpServers()
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [provider, setProvider] = useState("internal")
  const [framework, setFramework] = useState("generic")
  const [platform, setPlatform] = useState("")
  const [registryName, setRegistryName] = useState("")
  const [repo, setRepo] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open === "add-server") {
      setName(""); setDisplayName(""); setDescription(""); setProvider("internal")
      setFramework("generic"); setPlatform(""); setRegistryName(""); setRepo("")
    }
  }, [open])

  const valid = NAME_RE.test(name.trim())

  const submit = async () => {
    setSaving(true)
    try {
      const s = await createMcpServer({
        name: name.trim(),
        display_name: displayName.trim() || null,
        description: description.trim() || null,
        provider_type: provider,
        framework,
        target_platform: platform.trim() || null,
        registry_name: registryName.trim() || null,
        repository_url: repo.trim() || null,
      })
      toast.success("서버 정의를 등록했습니다.")
      setOpen(null)
      reload()
      setSelectedServer(s.name)
    } catch (e) {
      notifyError(e, "MCP 서버를 등록하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open === "add-server"} onOpenChange={() => setOpen(null)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>MCP 서버 정의 등록</DialogTitle>
        </DialogHeader>
        <div className="grid max-h-[70vh] gap-3 overflow-y-auto py-1">
          <p className="text-xs text-muted-foreground">
            서버 정의는 “어떤 MCP 서버(패키지·제품)인가”를 나타냅니다. 실제 운영 엔드포인트는 등록 후 “배포”로 추가합니다.
            외부·커뮤니티 서버는 MCP Registry server.json 가져오기를 사용하면 표준 메타데이터가 그대로 채워집니다.
          </p>
          <div className="grid gap-1.5">
            <OLabel origin="argus">이름 (고유 식별자)</OLabel>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: github-mcp, hive-mcp" />
            {name && !valid && <p className="text-xs text-destructive">소문자·숫자·. _ - 만 사용할 수 있습니다.</p>}
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="argus">표시명</OLabel>
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <OLabel origin="argus">제공자 유형</OLabel>
              <Select value={provider} onValueChange={setProvider}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PROVIDER_TYPES.map((p) => <SelectItem key={p} value={p}>{PROVIDER_LABEL[p]}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <OLabel origin="argus">프레임워크</OLabel>
              <Select value={framework} onValueChange={setFramework}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="generic">일반 MCP</SelectItem>
                  <SelectItem value="argus-forge">Argus Forge</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="argus">대상 플랫폼</OLabel>
            <Input value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="예: hive, kafka, github, slack" />
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="registry">설명</OLabel>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="registry">Registry 이름</OLabel>
            <Input value={registryName} onChange={(e) => setRegistryName(e.target.value)} placeholder="io.github.org/server-name" />
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="registry">저장소 URL</OLabel>
            <Input value={repo} onChange={(e) => setRepo(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(null)} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={saving || !valid}>{saving ? "등록 중..." : "등록"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// 배포 등록
// ---------------------------------------------------------------------------

function AddDeploymentDialog() {
  const { open, setOpen, reload, addDeploymentServer, setAddDeploymentServer, setSelectedDeployment } = useMcpServers()
  const [servers, setServers] = useState<McpServerSummary[]>([])
  const [server, setServer] = useState("")
  const [name, setName] = useState("")
  const [environment, setEnvironment] = useState("DEV")
  const [transport, setTransport] = useState("streamable_http")
  const [endpoint, setEndpoint] = useState("")
  const [auth, setAuth] = useState("none")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open !== "add-deployment") return
    setServer(addDeploymentServer ?? "")
    setName(addDeploymentServer ? `${addDeploymentServer}-dev` : "")
    setEnvironment("DEV"); setTransport("streamable_http"); setEndpoint(""); setAuth("none")
    fetchMcpServers({ pageSize: 100 })
      .then((d) => setServers(d.items.filter((s) => s.status !== "retired")))
      .catch(() => setServers([]))
  }, [open, addDeploymentServer])

  const close = () => {
    setOpen(null)
    setAddDeploymentServer(null)
  }

  const valid = NAME_RE.test(name.trim()) && !!server

  const submit = async () => {
    setSaving(true)
    try {
      const d = await createMcpDeployment({
        name: name.trim(),
        server_name: server,
        environment,
        transport,
        endpoint: endpoint.trim() || null,
        auth_method: auth,
      })
      toast.success("배포를 등록했습니다. 자격증명이 필요하면 상세의 “자격증명” 탭에서 입력한 뒤 “수집”을 실행하세요.")
      close()
      reload()
      setSelectedDeployment(d.name)
    } catch (e) {
      notifyError(e, "MCP 배포를 등록하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open === "add-deployment"} onOpenChange={close}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>MCP 배포 등록</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3 py-1">
          <p className="text-xs text-muted-foreground">
            배포는 서버 정의의 실제 운영 인스턴스(엔드포인트·환경)입니다. 같은 서버를 여러 환경·대상 시스템에 배포할 수 있습니다.
          </p>
          <div className="grid gap-1.5">
            <Label>서버 정의</Label>
            <Select value={server} onValueChange={(v) => { setServer(v); if (!name) setName(`${v}-${environment.toLowerCase()}`) }}>
              <SelectTrigger><SelectValue placeholder="서버 정의 선택" /></SelectTrigger>
              <SelectContent>
                {servers.map((s) => (
                  <SelectItem key={s.name} value={s.name}>{s.display_name || s.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="argus">배포 이름</OLabel>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: hive-mcp-prod-a" />
            {name && !NAME_RE.test(name.trim()) && <p className="text-xs text-destructive">소문자·숫자·. _ - 만 사용할 수 있습니다.</p>}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <OLabel origin="argus">환경</OLabel>
              <Select value={environment} onValueChange={setEnvironment}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{ENVIRONMENTS.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <OLabel origin="argus">전송</OLabel>
              <Select value={transport} onValueChange={setTransport}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{TRANSPORTS.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="argus">MCP 엔드포인트</OLabel>
            <Input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://host:port/mcp" disabled={transport === "stdio"} />
            {transport === "stdio" && (
              <p className="text-xs text-muted-foreground">stdio 서버는 Argus 가 실행하지 않으므로 수동 등록·가져오기로만 도구를 관리합니다.</p>
            )}
          </div>
          <div className="grid gap-1.5">
            <OLabel origin="argus">인증 방식</OLabel>
            <Select value={auth} onValueChange={setAuth}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{AUTH_METHODS.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={saving}>취소</Button>
          <Button onClick={submit} disabled={saving || !valid}>{saving ? "등록 중..." : "등록"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// server.json / mcp.json 가져오기
// ---------------------------------------------------------------------------

const ACTION_STYLE: Record<string, string> = {
  created: "text-emerald-700 dark:text-emerald-400",
  updated: "text-sky-700 dark:text-sky-400",
  exists: "text-muted-foreground",
  error: "text-destructive",
}
const ACTION_LABEL: Record<string, string> = {
  created: "생성", updated: "갱신", exists: "이미 있음", error: "오류",
}

function ImportDialog() {
  const { open, setOpen, reload } = useMcpServers()
  const [format, setFormat] = useState<"server_json" | "mcp_json">("server_json")
  const [content, setContent] = useState("")
  const [environment, setEnvironment] = useState("DEV")
  const [preview, setPreview] = useState<McpImportResult | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open === "import") {
      setContent(""); setPreview(null); setParseError(null)
    }
  }, [open])

  const run = async (dryRun: boolean) => {
    let doc: unknown
    try {
      doc = JSON.parse(content)
      setParseError(null)
    } catch (e) {
      setParseError(e instanceof Error ? `JSON 파싱 오류: ${e.message}` : "JSON 파싱 오류")
      return
    }
    setBusy(true)
    try {
      const r = await importMcpDocuments({ format, content: doc, dry_run: dryRun, environment })
      if (dryRun) setPreview(r)
      else {
        const created = r.items.filter((i) => i.action === "created" || i.action === "updated").length
        toast.success(`MCP 문서를 가져왔습니다: ${created}건 반영`)
        setOpen(null)
        reload()
      }
    } catch (e) {
      notifyError(e, "MCP 문서를 가져오지 못했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open === "import"} onOpenChange={() => setOpen(null)}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>MCP 서버 가져오기</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <Tabs value={format} onValueChange={(v) => { setFormat(v as typeof format); setPreview(null) }}>
            <TabsList>
              <TabsTrigger value="server_json">MCP Registry server.json</TabsTrigger>
              <TabsTrigger value="mcp_json">클라이언트 mcp.json</TabsTrigger>
            </TabsList>
          </Tabs>
          {format === "server_json" ? (
            <p className="text-xs text-muted-foreground">
              공식 MCP Registry 형식(name·description·version·packages·remotes)을 서버 정의로 가져옵니다(remotes 는 서버 정의에 기록되며, 운영 배포는 별도로 등록합니다).
              Argus 에서 내보낸 파일의 <code>_meta</code> 에 담긴 Argus 전용 정보 <OriginBadge origin="argus" className="align-middle" /> 도 복원됩니다.
              하나의 객체 또는 배열을 붙여넣을 수 있습니다.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Claude Desktop·Cursor·VS Code 등의 <code>mcpServers</code>/<code>servers</code> 설정을 서버 정의 + 배포로 가져옵니다.
              환경변수·헤더는 <b>키 이름만</b> 기록하고 값(토큰 등)은 저장하지 않습니다. Argus Forge 서버는 자동 인식합니다.
            </p>
          )}
          <Textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setPreview(null) }}
            rows={12}
            className="font-mono text-xs"
            placeholder={format === "server_json"
              ? '{\n  "name": "io.github.org/server",\n  "description": "...",\n  "version": "1.0.0",\n  "remotes": [{ "type": "streamable-http", "url": "https://..." }]\n}'
              : '{\n  "mcpServers": {\n    "github": { "url": "https://api.githubcopilot.com/mcp/", "headers": { "Authorization": "Bearer ..." } }\n  }\n}'}
          />
          <div className="flex items-center gap-2">
            <Label className="text-xs">배포 환경</Label>
            <Select value={environment} onValueChange={setEnvironment}>
              <SelectTrigger size="sm" className="w-[100px]"><SelectValue /></SelectTrigger>
              <SelectContent>{ENVIRONMENTS.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {parseError && <p className="text-xs text-destructive">{parseError}</p>}
          {preview && (
            <div className="max-h-56 overflow-y-auto rounded-md border p-2">
              {preview.items.length === 0 ? (
                <p className="text-xs text-muted-foreground">가져올 항목이 없습니다.</p>
              ) : (
                <ul className="space-y-1">
                  {preview.items.map((i, idx) => (
                    <li key={`${i.kind}-${i.name}-${idx}`} className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge variant="outline" className="text-[10px]">{i.kind === "server" ? "서버" : "배포"}</Badge>
                      <span className="font-mono">{i.name}</span>
                      <span className={ACTION_STYLE[i.action]}>{ACTION_LABEL[i.action] ?? i.action}</span>
                      {i.message && <span className="text-muted-foreground">— {i.message}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(null)} disabled={busy}>닫기</Button>
          <Button variant="outline" onClick={() => run(true)} disabled={busy || !content.trim()}>미리보기</Button>
          <Button onClick={() => run(false)} disabled={busy || !preview}>가져오기</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
