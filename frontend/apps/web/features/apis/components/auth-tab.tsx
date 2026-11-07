"use client"

import { useState } from "react"
import { Eye, EyeOff, Loader2, Trash2, X } from "lucide-react"
import { notifyError, toast } from "@/lib/notify"
import { useConfirm } from "@/components/confirm-provider"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@workspace/ui/components/select"
import { cn } from "@workspace/ui/lib/utils"

import { type ApiCredential, type ApiDetail, type ApiSecurityScheme, createApiCredential, deleteApiCredential } from "../api"
import { JsonBlock, TabEmpty, TabError, fmtDateTime } from "./apis-detail-shared"
import type { CredentialsAccess } from "./apis-detail"

// ---------------------------------------------------------------------------
// 인증 탭 — 보안 스킴 + 저장된 자격증명(암호화 보관) 관리
// ---------------------------------------------------------------------------
const CRED_TYPES = ["apiKey", "bearer", "basic", "oauth2"]
// 저장값은 그대로(apiKey 등) 두고 화면 표시만 친화적 라벨로.
const CRED_TYPE_LABEL: Record<string, string> = { apiKey: "API Key", bearer: "Bearer 토큰", basic: "Basic 인증", oauth2: "OAuth2 토큰" }
const CRED_TYPE_INPUT: Record<string, string> = { apiKey: "API 키 값", bearer: "토큰", basic: "사용자명·비밀번호", oauth2: "액세스 토큰" }
const CRED_TYPE_HELP: Record<string, string> = {
  apiKey: "키 값 1개를 입력합니다. 보안 스킴을 연결하면 스킴 정의(in/name)대로 헤더·쿼리·쿠키에 주입되고, 미연결 시 X-API-Key 헤더로 주입됩니다.",
  bearer: "토큰을 입력하면 Authorization: Bearer <토큰> 헤더로 주입됩니다.",
  basic: "사용자명·비밀번호를 입력하면 Authorization: Basic base64(user:pw) 로 주입됩니다.",
  oauth2: "액세스 토큰을 입력하면 Authorization: Bearer <토큰> 로 주입됩니다.",
}

/** 보안 스킴 → 맞는 인증 방식(모르면 null). OAS3 http(bearer/basic)·apiKey·oauth2/openIdConnect, Swagger2 basic. */
function credTypeForScheme(s: ApiSecurityScheme | undefined): string | null {
  if (!s) return null
  const t = (s.type ?? "").toLowerCase()
  const scheme = String(s.config?.scheme ?? "").toLowerCase()
  if (t === "apikey") return "apiKey"
  if (t === "basic") return "basic"
  if (t === "http") return scheme === "basic" ? "basic" : scheme === "bearer" ? "bearer" : null
  if (t === "oauth2" || t === "openidconnect") return "oauth2"
  return null
}

// 시크릿 입력 — 기본 마스킹(*), eye 아이콘으로 평문 토글.
function SecretInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <Input className="h-8 pr-8 text-sm" type={show ? "text" : "password"} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => setShow((s) => !s)} title={show ? "숨기기" : "표시"} aria-label={show ? "숨기기" : "표시"} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}

export function AuthTab({ api, credentials, access, canAdmin, onChanged }: {
  api: ApiDetail
  credentials: ApiCredential[]
  /** 자격증명 목록 조회 결과 — forbidden(기능 권한 없음)·error 는 0건과 구분해 안내 */
  access: CredentialsAccess
  /** 자격증명 추가·삭제 가능(관리자) */
  canAdmin: boolean
  onChanged: () => Promise<void>
}) {
  const askConfirm = useConfirm()
  const [label, setLabel] = useState("")
  const [type, setType] = useState("apiKey")
  const [schemeName, setSchemeName] = useState<string>("none")
  const [value, setValue] = useState("")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [selectedCredId, setSelectedCredId] = useState<number | null>(null)
  const selectedCred = credentials.find((c) => c.id === selectedCredId) ?? null
  // 연결한 스킴이 요구하는 인증 방식과 다르면 경고(주입 위치가 스펙과 어긋남)
  const schemeType = credTypeForScheme(api.security_schemes.find((s) => s.scheme_name === schemeName))
  const schemeMismatch = schemeType !== null && schemeType !== type

  const pickScheme = (name: string) => {
    setSchemeName(name)
    const t = credTypeForScheme(api.security_schemes.find((s) => s.scheme_name === name))
    if (t) setType(t)
  }

  const add = async () => {
    if (!label.trim()) { toast.error("자격증명 이름을 입력하세요."); return }
    // 빈 시크릿으로 저장하면 호출 시 빈 인증 헤더가 주입된다
    if (type === "basic") {
      if (!username.trim() || !password) { toast.error("사용자명과 비밀번호를 모두 입력하세요."); return }
    } else if (!value.trim()) {
      toast.error(`${CRED_TYPE_INPUT[type] ?? "값"}을 입력하세요.`); return
    }
    const values: Record<string, unknown> =
      type === "basic" ? { username, password }
      : type === "apiKey" ? { value }
      : { token: value }
    setBusy(true)
    try {
      await createApiCredential(api.name, {
        label: label.trim(),
        type,
        scheme_name: schemeName !== "none" ? schemeName : undefined,
        values,
      })
      setLabel(""); setValue(""); setUsername(""); setPassword("")
      await onChanged()
      toast.success("자격증명을 저장했습니다.")
    } catch (e) {
      notifyError(e, "자격증명을 저장하지 못했습니다.")
    } finally { setBusy(false) }
  }

  const remove = async (c: ApiCredential) => {
    if (!(await askConfirm({
      title: "자격증명 삭제",
      description: `'${c.label}' 자격증명을 삭제하시겠습니까? 이 자격증명을 쓰는 호출 설정은 다시 선택해야 합니다.`,
      confirmText: "삭제",
      destructive: true,
    }))) return
    try { await deleteApiCredential(api.name, c.id) }
    catch (e) { notifyError(e, "자격증명을 삭제하지 못했습니다."); return }
    if (selectedCredId === c.id) setSelectedCredId(null)
    toast.success("자격증명을 삭제했습니다.")
    await onChanged()
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        이 API 호출에 사용할 자격증명(시크릿)을 보관·관리하는 화면입니다. 등록한 시크릿은 서버에 암호화 보관되어 화면·응답에 노출되지 않으며,
        엔드포인트 탭에서 행을 펼쳐 “호출(Try-it)” 시 선택하면 서버가 복호화해 요청에 자동 주입합니다. apiKey 는 보안 스킴을 연결하면 스킴 정의(in/name)에 따라
        헤더·쿼리·쿠키 중 올바른 위치로 주입되고, bearer·oauth2 는 Authorization: Bearer, basic 은 Authorization: Basic 으로 주입됩니다.
        자격증명은 이 API 의 서버 주소·Base URL 로 호출할 때만 전송되며, 다른 주소로는 보내지 않습니다.
      </p>
      {/* 저장된 자격증명 — 기능 권한이 없거나 조회에 실패하면 0건 대신 사유를 안내 */}
      {access === "forbidden" ? (
        <Card><CardContent className="space-y-1 pt-4">
          <p className="text-sm font-medium">저장된 자격증명</p>
          <TabEmpty>자격증명을 볼 수 있는 기능 권한(apis.credentials)이 없습니다. 필요하면 관리자에게 요청하세요.</TabEmpty>
        </CardContent></Card>
      ) : access === "error" ? (
        <Card><CardContent className="space-y-2 pt-4">
          <p className="text-sm font-medium">저장된 자격증명</p>
          <TabError message="자격증명 목록을 불러오지 못했습니다." onRetry={() => { void onChanged() }} />
        </CardContent></Card>
      ) : (
      <Card><CardContent className="space-y-3 pt-4">
        <p className="text-sm font-medium">저장된 자격증명 ({credentials.length})</p>
        <p className="text-xs text-muted-foreground">시크릿은 서버에 암호화 보관되며 화면/응답에 노출되지 않습니다. 엔드포인트 탭의 호출(Try-it)에서 선택해 주입합니다.</p>
        {credentials.length > 0 && (
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr><th className="px-3 py-2 text-left">이름</th><th className="px-3 py-2 text-left">유형</th><th className="px-3 py-2 text-left">스킴</th>{canAdmin && <th className="w-10" />}</tr></thead>
              <tbody>
                {credentials.map((c) => (
                  <tr key={c.id} className={cn("border-t", selectedCredId === c.id && "bg-muted/40")}>
                    <td className="px-3 py-2">
                      <button type="button" className="font-medium text-primary hover:underline" onClick={() => setSelectedCredId((id) => id === c.id ? null : c.id)}>{c.label}</button>
                    </td>
                    <td className="px-3 py-2"><Badge variant="outline" className="text-xs">{CRED_TYPE_LABEL[c.type] ?? c.type}</Badge></td>
                    <td className="px-3 py-2 text-muted-foreground">{c.scheme_name || "-"}</td>
                    {canAdmin && (
                      <td className="px-3 py-2"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => remove(c)} title="삭제" aria-label={`${c.label} 삭제`}><Trash2 className="h-4 w-4" /></Button></td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 선택한 자격증명 상세 — 메타데이터만(시크릿은 보안상 미표시) */}
        {selectedCred && (
          <div className="space-y-2 rounded-md border bg-muted/20 p-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">{selectedCred.label}</p>
              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSelectedCredId(null)} title="닫기"><X className="h-4 w-4" /></Button>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div><span className="text-muted-foreground">유형: </span>{CRED_TYPE_LABEL[selectedCred.type] ?? selectedCred.type}</div>
              <div><span className="text-muted-foreground">보안 스킴: </span>{selectedCred.scheme_name || "-"}</div>
              <div><span className="text-muted-foreground">생성자: </span>{selectedCred.created_by || "-"}</div>
              <div><span className="text-muted-foreground">생성일: </span>{fmtDateTime(selectedCred.created_at)}</div>
            </div>
            <p className="text-[11px] text-muted-foreground">🔒 보안상 저장된 시크릿(키/비밀번호)은 화면에 표시되지 않습니다. 호출 시 서버에서만 복호화·주입됩니다.</p>
          </div>
        )}

        {/* 추가 폼 — 관리자 전용(백엔드 AdminUser 가드) */}
        {!canAdmin ? (
          <p className="pt-2 text-xs text-muted-foreground">자격증명 추가·삭제는 관리자만 할 수 있습니다.</p>
        ) : (<>
        <p className="pt-2 text-sm font-medium">자격증명 추가</p>

        {/* 정적 도움말 — 인증 방식별 의미·주입 방식·입력값 */}
        <details className="rounded-md border bg-muted/20 p-3">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">인증 방식 도움말 (펼치기)</summary>
          <div className="mt-2 overflow-hidden rounded-md border bg-background">
            <table className="w-full text-sm">
              <thead className="bg-muted/40"><tr>
                <th className="w-[110px] px-3 py-2 text-left font-medium text-muted-foreground">인증 방식</th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">설명 · 주입 방식</th>
                <th className="w-[150px] px-3 py-2 text-left font-medium text-muted-foreground">입력값</th>
              </tr></thead>
              <tbody>
                {CRED_TYPES.map((t) => (
                  <tr key={t} className="border-t align-top">
                    <td className="px-3 py-2 font-medium">{CRED_TYPE_LABEL[t]}</td>
                    <td className="px-3 py-2 text-muted-foreground">{CRED_TYPE_HELP[t]}</td>
                    <td className="px-3 py-2 text-muted-foreground">{CRED_TYPE_INPUT[t]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">이름</p>
            <Input className="h-8 text-sm" placeholder="예: 운영 키" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">인증 방식</p>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>{CRED_TYPES.map((t) => <SelectItem key={t} value={t}>{CRED_TYPE_LABEL[t]}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">보안 스킴 (선택)</p>
            <Select value={schemeName} onValueChange={pickScheme}>
              <SelectTrigger className="h-8 text-sm"><SelectValue placeholder="스킴 미지정" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">스킴 미지정</SelectItem>
                {api.security_schemes.map((s) => <SelectItem key={s.id} value={s.scheme_name}>{s.scheme_name} ({s.type})</SelectItem>)}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              스펙(OpenAPI)에 선언된 보안 스킴 목록입니다(아래 “스펙 보안 스킴” 참고). API Key 는 연결 시 스킴의 위치(헤더/쿼리/쿠키)·이름을 따릅니다.
              {api.security_schemes.length === 0 ? " 이 스펙엔 선언된 스킴이 없어 '스킴 미지정'만 선택할 수 있습니다." : " 스킴을 고르면 맞는 인증 방식이 자동 선택됩니다. 매핑이 불필요하면 '스킴 미지정'으로 두세요."}
            </p>
            {schemeMismatch && (
              <p className="text-[11px] text-amber-700">
                선택한 스킴은 {CRED_TYPE_LABEL[schemeType!]} 방식입니다. 인증 방식({CRED_TYPE_LABEL[type]})과 달라 스펙과 다른 위치로 주입될 수 있습니다.
              </p>
            )}
          </div>
          <div className="hidden sm:block" />
          {type === "basic" ? (
            <>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">사용자명</p>
                <Input className="h-8 text-sm" placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">비밀번호</p>
                <SecretInput value={password} onChange={setPassword} placeholder="password" />
              </div>
            </>
          ) : (
            <div className="space-y-1 sm:col-span-2">
              <p className="text-xs font-medium text-muted-foreground">{CRED_TYPE_INPUT[type]}</p>
              <SecretInput value={value} onChange={setValue} placeholder={CRED_TYPE_INPUT[type]} />
            </div>
          )}
        </div>

        {/* 동적 안내 — 선택한 인증 방식 설명 */}
        <p className="text-xs text-muted-foreground">※ {CRED_TYPE_HELP[type]}</p>

        <div className="flex justify-end">
          <Button size="sm" onClick={add} disabled={busy}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null} 자격증명 추가</Button>
        </div>
        </>)}
      </CardContent></Card>
      )}

      {/* 스펙 보안 스킴 */}
      <Card><CardContent className="space-y-2 pt-4">
        <p className="text-sm font-medium">스펙 보안 스킴 ({api.security_schemes.length})</p>
        {api.security_schemes.length === 0 ? (
          <p className="text-sm text-muted-foreground">선언된 인증 스킴이 없습니다.</p>
        ) : api.security_schemes.map((s) => (
          <div key={s.id} className="space-y-1">
            <div className="flex items-center gap-2"><span className="font-medium">{s.scheme_name}</span><Badge variant="outline" className="text-xs">{s.type}</Badge></div>
            <JsonBlock value={s.config} />
          </div>
        ))}
      </CardContent></Card>
    </div>
  )
}
