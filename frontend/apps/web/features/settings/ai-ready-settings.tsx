"use client"

import { CodeEditor } from "@/components/code-editor"
import { useCallback, useEffect, useState } from "react"
import { Loader2, Save, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Input } from "@workspace/ui/components/input"
import { Label } from "@workspace/ui/components/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@workspace/ui/components/select"

import { fetchAiReadyDefaults, type AiReadyCode } from "@/features/datasets/api"
import { notifyError, notifySuccess } from "@/lib/notify"

import {
  fetchAiReadyConfig,
  fetchPublicUrlPreview,
  updateAiReadyConfig,
  type AiReadyConfig,
  type PublicUrlMode,
  type PublicUrlPreview,
} from "./api"
import { TagInput } from "@/components/tag-input"
import { joinCsv, splitCsv } from "@/lib/text-list"

const NONE = "__none__"
const EMPTY: AiReadyConfig = {
  publisher: "", language: "ko", license: "", license_url: "", contact_email: "", max_missing_pct: 20, max_missed_cycles: 2,
  report_due_days: 10, laws: "", public_mode: "REQUEST", public_scheme: "http", public_port: null, public_url: "",
}

const PUBLIC_MODES: { value: PublicUrlMode; label: string; note: string }[] = [
  { value: "REQUEST", label: "요청 주소", note: "화면이나 API 를 부른 주소 그대로 — 웹 서버(프록시)를 거치면 브라우저가 연 주소" },
  { value: "FQDN", label: "FQDN (호스트 이름)", note: "이 서버의 정규 호스트 이름 — DNS 에 등록돼 있어야 다른 시스템이 찾음" },
  { value: "IP", label: "바인딩 IP", note: "백엔드가 바인딩한 IP. 모든 인터페이스(0.0.0.0)면 기본 경로의 IP" },
  { value: "CUSTOM", label: "직접 입력", note: "도메인 · 로드밸런서 · 게이트웨이 주소처럼 위에 없는 주소" },
]

const LAWS_EXAMPLE = `개인정보 보호법 | 이름 · 연락처 · 주소처럼 개인을 알아볼 수 있는 정보가 있을 때
공공데이터의 제공 및 이용 활성화에 관한 법률 | 공공데이터포털 등으로 개방하는 데이터
통계법 | 승인통계를 작성하거나 그 원자료일 때`

/**
 * AI-Ready 기관 기본값 — 데이터셋 메타데이터 'AI 활용' 묶음에서 비어 있는 항목에 쓰는 값.
 * 데이터셋에 써 넣지 않으므로 바꾸면 값이 빈 데이터셋 전체에 바로 반영된다.
 */
export function AiReadySettings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [cfg, setCfg] = useState<AiReadyConfig>(EMPTY)
  const [licenses, setLicenses] = useState<AiReadyCode[]>([])

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      setLoadError(null)
      const [c, d] = await Promise.all([fetchAiReadyConfig(), fetchAiReadyDefaults()])
      setCfg({ ...EMPTY, ...c })
      setLicenses(d.licenses)
    } catch {
      setLoadError("AI-Ready 설정을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  // 방식별 실제 주소 — 스킴 · 포트를 바꾸면 다시 계산(입력 중에는 잠시 기다림)
  const [preview, setPreview] = useState<PublicUrlPreview | null>(null)
  useEffect(() => {
    if (loading) return
    const t = setTimeout(() => {
      fetchPublicUrlPreview(cfg.public_scheme, cfg.public_port).then(setPreview).catch(() => setPreview(null))
    }, 300)
    return () => clearTimeout(t)
  }, [loading, cfg.public_scheme, cfg.public_port])

  const set = <K extends keyof AiReadyConfig>(key: K, value: AiReadyConfig[K]) =>
    setCfg((c) => ({ ...c, [key]: value }))

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateAiReadyConfig(cfg)
      notifySuccess("AI-Ready 설정을 저장했습니다.")
      await loadConfig() // 서버가 다듬은 값(언어 소문자 등)으로 맞춘다
    } catch (e) {
      notifyError(e, "AI-Ready 설정을 저장하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {loadError && (
        <div className="flex items-center gap-2 rounded-md px-3 py-2 text-sm bg-red-50 text-red-700 border border-red-200">
          <X className="h-4 w-4" />
          {loadError}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">기관 기본값</CardTitle>
          <CardDescription>
            데이터셋 메타데이터의 「AI 활용 (AI-Ready)」 항목 중 기관 공통 값입니다. 데이터셋에 값이 없으면 이 값을 쓰고,
            데이터셋에 값을 넣으면 그 값이 우선합니다. 데이터셋에 써 넣지 않으므로 바꾸면 값이 빈 데이터셋 전체에 바로 반영됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-publisher">제공기관</Label>
            <Input id="ai-ready-publisher" value={cfg.publisher} onChange={(e) => set("publisher", e.target.value)}
              placeholder="예: 서울특별시" />
            <p className="text-xs">데이터셋을 대외적으로 배포하는 기관 (dct:publisher)</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-language">언어</Label>
            <TagInput id="ai-ready-language" commaSeparates mono value={splitCsv(cfg.language)}
              onChange={(v) => set("language", joinCsv(v))} placeholder="예: ko 입력 후 Enter" />
            <p className="text-xs">ISO 639-1 두 글자 코드를 하나씩 넣습니다(예: ko · en). 비우면 ko</p>
          </div>
          <div className="space-y-1.5">
            <Label>라이선스</Label>
            <Select value={cfg.license || NONE} onValueChange={(v) => set("license", v === NONE ? "" : v)}>
              <SelectTrigger className="w-full"><SelectValue placeholder="미지정" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE} className="text-muted-foreground">미지정</SelectItem>
                {licenses.map((l) => <SelectItem key={l.code} value={l.code}>{l.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Input value={cfg.license_url} onChange={(e) => set("license_url", e.target.value)}
              placeholder="라이선스·이용약관 URL (https://…, 기관 이용약관이면 필수)" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-contact">문의 연락처</Label>
            <Input id="ai-ready-contact" value={cfg.contact_email} onChange={(e) => set("contact_email", e.target.value)}
              placeholder="data@example.go.kr" />
            <p className="text-xs">오류 신고·문의를 받는 역할 기준 공용 이메일 (dcat:contactPoint)</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">최소 요건 기준</CardTitle>
          <CardDescription>
            AI-Ready 점검의 「최소 요건」 배지 기준입니다. 가이드라인이 공유 요건의 예로 든 값(결측률 20% 미만, 갱신 주기 2회 이내)이
            기본값이며, 필수 메타데이터와 표준 포맷은 체크리스트 판정을 그대로 씁니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-missing">결측률 상한 (%)</Label>
            <Input id="ai-ready-missing" type="number" min={0} max={100} step="0.1" value={cfg.max_missing_pct}
              onChange={(e) => set("max_missing_pct", Number(e.target.value))} />
            <p className="text-xs">최신 프로파일의 전체 결측률이 이 값 미만이면 충족</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-cycles">갱신 주기 허용 횟수</Label>
            <Input id="ai-ready-cycles" type="number" min={1} max={100} step="1" value={cfg.max_missed_cycles}
              onChange={(e) => set("max_missed_cycles", Math.trunc(Number(e.target.value)))} />
            <p className="text-xs">마지막 적재 뒤 갱신주기(시간·일·주·월)가 이 횟수 이내로 지났으면 충족</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">오류 신고 처리 기한</CardTitle>
          <CardDescription>
            데이터셋 댓글을 ‘오류 신고’·‘개선 요청’으로 남기면 접수되고, 이 일수만큼 처리 기한이 붙습니다. 가이드라인의 예시 기준은
            “신고 후 10일 안에 수정 또는 소명”입니다. 기한을 넘긴 미처리 신고가 있으면 체크리스트 C31 이 미흡이 됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ai-ready-due-days">처리 기한 (일)</Label>
            <Input id="ai-ready-due-days" type="number" min={1} max={365} step="1" value={cfg.report_due_days}
              onChange={(e) => set("report_due_days", Math.trunc(Number(e.target.value)))} />
            <p className="text-xs">바꾸면 새로 접수되는 신고부터 적용됩니다</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">관련법령 목록</CardTitle>
          <CardDescription>
            데이터셋의 ‘관련법령’ AI 초안은 이 목록 안에서만 고릅니다 — AI 가 없는 법령 이름을 지어내지 못하게 하려는 것입니다.
            한 줄에 하나씩 법령명을 적고, <code>|</code> 뒤에 언제 해당하는지를 적으면 AI 가 고를 때 참고합니다. <code>#</code> 으로 시작하는
            줄은 메모입니다. 비워 두면 관련법령 초안은 만들지 않습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5">
          <CodeEditor id="ai-ready-laws" minHeight={128} value={cfg.laws}
            placeholder={LAWS_EXAMPLE} onChange={(v) => set("laws", v)} />
          <p className="text-xs">최대 100개 · 법령명은 200자까지. 저장하면 빈 줄과 겹치는 이름은 정리됩니다.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">외부 공개 주소</CardTitle>
          <CardDescription>
            DCAT · Croissant · 데이터 카드(‘기계 이해 스키마 제공’ 등)에 넣는 이 서버의 주소입니다. 포털 · AI 학습 도구처럼 다른 시스템이
            이 주소로 메타데이터를 받아 가므로, 그쪽에서 닿는 주소를 고릅니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2" role="radiogroup" aria-label="공개 주소 방식">
            {PUBLIC_MODES.map((m) => {
              const example = m.value === "CUSTOM" ? null : preview?.[m.value]
              return (
                <label key={m.value} className="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm has-[:checked]:border-primary">
                  <input type="radio" name="ai-ready-public-mode" className="mt-1" value={m.value}
                    checked={cfg.public_mode === m.value} onChange={() => set("public_mode", m.value)} />
                  <span className="min-w-0 space-y-0.5">
                    <span className="font-medium">{m.label}</span>
                    {example && <code className="ml-2 break-all text-xs">{example}</code>}
                    <span className="block text-xs">{m.note}</span>
                  </span>
                </label>
              )
            })}
          </div>
          {(cfg.public_mode === "FQDN" || cfg.public_mode === "IP") && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>스킴</Label>
                <Select value={cfg.public_scheme} onValueChange={(v) => set("public_scheme", v as AiReadyConfig["public_scheme"])}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="http">http</SelectItem>
                    <SelectItem value="https">https</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ai-ready-public-port">포트</Label>
                <Input id="ai-ready-public-port" type="number" min={1} max={65535} step="1" value={cfg.public_port ?? ""}
                  placeholder="비우면 백엔드 포트"
                  onChange={(e) => set("public_port", e.target.value ? Math.trunc(Number(e.target.value)) : null)} />
                <p className="text-xs">웹 화면 포트(예: 3000)를 넣어도 됩니다 — 같은 API 를 넘겨 줍니다</p>
              </div>
            </div>
          )}
          {cfg.public_mode === "CUSTOM" && (
            <div className="space-y-1.5">
              <Label htmlFor="ai-ready-public-url">공개 주소 URL</Label>
              <Input id="ai-ready-public-url" value={cfg.public_url} onChange={(e) => set("public_url", e.target.value)}
                placeholder="https://catalog.example.go.kr" />
              <p className="text-xs">이 주소 뒤에 <code>api/v1/external/…</code> 가 붙습니다</p>
            </div>
          )}
          {preview && (
            <p className="text-xs">
              저장된 설정으로 지금 쓰는 주소: <code className="break-all">{preview.current}</code>
            </p>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
          저장
        </Button>
      </div>
    </div>
  )
}
