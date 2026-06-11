"use client"

// Dataset 상세 페이지의 "품질" 탭 — 규칙 정의, 검증 실행 결과, 데이터 프로파일을 한 화면에서 관리한다.

import { Fragment, useCallback, useEffect, useState } from "react"
import { Card, CardContent } from "@workspace/ui/components/card"
import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import { Input } from "@workspace/ui/components/input"
import dynamic from "next/dynamic"

// 검증 SQL 편집기 — CodeViewer 와 동일하게 SSR 비활성 동적 로드 (편집 모드)
const MonacoEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="h-40 rounded-md border bg-muted/30 animate-pulse" />,
})
import { Label } from "@workspace/ui/components/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@workspace/ui/components/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@workspace/ui/components/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@workspace/ui/components/dialog"
import { Separator } from "@workspace/ui/components/separator"
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight, HelpCircle, Pencil, Play, Plus, Shield, Sparkles, Trash2, XCircle } from "lucide-react"
import { authFetch } from "@/features/auth/auth-fetch"

const BASE = "/api/v1/quality"

// 검증 유형별 한글 라벨·설명·기대값 힌트 — 규칙 추가 다이얼로그에서 사용.
// 평가 시맨틱은 서버(app/quality/service.py:_evaluate_rule) 기준.
const CHECK_TYPE_META: Record<string, { label: string; desc: string; expectedHint: string | null; usesThreshold: boolean }> = {
  NOT_NULL: {
    label: "NULL 없음",
    desc: "컬럼의 비-NULL 비율이 임계값(%) 이상이어야 통과합니다 (100 = NULL 1건도 불허). 기대값은 사용하지 않습니다.",
    expectedHint: null,
    usesThreshold: true,
  },
  UNIQUE: {
    label: "고유값",
    desc: "컬럼의 고유값 비율이 임계값(%) 이상이어야 통과합니다 (100 = 중복 없음). 기대값은 사용하지 않습니다.",
    expectedHint: null,
    usesThreshold: true,
  },
  MIN_VALUE: {
    label: "최소값 하한",
    desc: "컬럼의 최소값이 기대값 이상이어야 통과합니다 (예: 금액 ≥ 0). 임계값은 사용하지 않습니다.",
    expectedHint: "예: 0",
    usesThreshold: false,
  },
  MAX_VALUE: {
    label: "최대값 상한",
    desc: "컬럼의 최대값이 기대값 이하여야 통과합니다. 임계값은 사용하지 않습니다.",
    expectedHint: "예: 100",
    usesThreshold: false,
  },
  ROW_COUNT: {
    label: "행 수 하한",
    desc: "전체 행 수가 기대값 이상이어야 통과합니다. 테이블 수준 검사라 컬럼·임계값은 사용하지 않습니다.",
    expectedHint: "예: 1000",
    usesThreshold: false,
  },
  FRESHNESS: {
    label: "최신성",
    desc: "마지막 프로파일 이후 경과 시간이 기대값(시간) 이내여야 통과합니다. 컬럼·임계값은 사용하지 않습니다.",
    expectedHint: "예: 24 (시간)",
    usesThreshold: false,
  },
  ACCEPTED_VALUES: {
    label: "허용 값 목록",
    desc: "컬럼 값이 쉼표로 나열한 허용 목록 안에만 있어야 합니다 (임계값 미사용). 서버 검증은 제한적이며 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다.",
    expectedHint: "예: G,PG,PG-13,R,NC-17",
    usesThreshold: false,
  },
  REGEX: {
    label: "정규식 패턴",
    desc: "컬럼 값이 정규식과 일치해야 합니다 (임계값 미사용). 서버 검증은 제한적이며 품질 배치(quality/*.py)에서 전체 데이터로 평가됩니다.",
    expectedHint: "예: ^[^@]+@[^@]+$",
    usesThreshold: false,
  },
  CUSTOM_SQL: {
    label: "커스텀 SQL",
    desc: "기대값의 SELECT 쿼리를 원본 DB에서 실행해 평가합니다 — 첫 행 첫 컬럼이 위반 건수(0이면 통과)여야 합니다. 조인·집계 등 복잡한 정합성 검증용이며, 보안상 품질 배치(quality/*.py)에서만 실행됩니다 (SELECT 전용, read-only 계정 권장).",
    expectedHint: "예: SELECT count(*) FROM rental r LEFT JOIN inventory i ON i.inventory_id = r.inventory_id WHERE i.inventory_id IS NULL",
    usesThreshold: false,
  },
  CUSTOM_PYTHON: {
    label: "커스텀 Python",
    desc: "quality/custom_checks/ 의 플러그인 함수로 평가합니다 — SQL로 표현하기 어려운 통계 검증·외부 대조 로직용. 기대값에 {\"module\", \"fn\", \"params\"} JSON을 입력하며, 함수는 (passed, actual, detail)을 반환해야 합니다. 품질 배치(quality/*.py)에서만 실행됩니다.",
    expectedHint: "예: {\"module\": \"rental_checks\", \"fn\": \"rental_duration_outlier\", \"params\": {\"max_outlier_pct\": 1.0}}",
    usesThreshold: false,
  },
}

type QualityRule = {
  id: number; rule_name: string; check_type: string; column_name: string | null
  expected_value: string | null; threshold: number; severity: string; is_active: string
}

type QualityResult = {
  id: number; rule_id: number; rule_name: string | null; check_type: string | null
  column_name: string | null; passed: string; actual_value: string | null
  detail: string | null; severity: string | null; checked_at: string
  failed_samples: Record<string, string | null>[] | null
}

type QualityScore = { score: number; total_rules: number; passed_rules: number; failed_rules: number; scored_at?: string }
type UpstreamQuality = {
  id: number; name: string; urn: string
  quality_status: string; quality_score: number | null; relation_type: string
}
type RuleRecommendation = {
  rule_name: string; check_type: string; column_name: string | null
  expected_value: string | null; threshold: number; severity: string; reason: string
}
type ScoreHistoryItem = { id: number; score: number; passed_rules: number; failed_rules: number; total_rules: number; scored_at: string }

type ColumnProfile = {
  column_name: string; column_type: string; total_count: number
  null_count: number; null_percent: number; unique_count: number; unique_percent: number
  min_value: string | null; max_value: string | null; mean_value: number | null
  top_values?: { value: string; count: number }[] | null
}

type Props = { datasetId: number
  /** 컬럼 선택 콤보 옵션 — 데이터셋 스키마의 field_path 목록 */
  columns?: string[]
}

export function QualityTab({ datasetId, columns = [] }: Props) {
  const [rules, setRules] = useState<QualityRule[]>([])
  const [results, setResults] = useState<QualityResult[]>([])
  const [score, setScore] = useState<QualityScore | null>(null)
  const [profile, setProfile] = useState<ColumnProfile[]>([])
  const [scoreHistory, setScoreHistory] = useState<ScoreHistoryItem[]>([])
  const [expandedResultId, setExpandedResultId] = useState<number | null>(null)  // 위반 샘플 펼침 행
  const [schedule, setSchedule] = useState<string | null>(null)
  const [recsOpen, setRecsOpen] = useState(false)
  const [recs, setRecs] = useState<RuleRecommendation[]>([])
  const [selectedRecs, setSelectedRecs] = useState<Set<number>>(new Set())
  const [recsLoading, setRecsLoading] = useState(false)
  const [upstreamWarnings, setUpstreamWarnings] = useState<UpstreamQuality[]>([])
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [profiling, setProfiling] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)

  // Add rule form
  const [ruleName, setRuleName] = useState("")
  const [checkType, setCheckType] = useState("NOT_NULL")
  const [columnName, setColumnName] = useState("")
  const [expectedValue, setExpectedValue] = useState("")
  const [threshold, setThreshold] = useState("100")
  const [severity, setSeverity] = useState("WARNING")

  // 규칙/결과/점수/프로파일을 한 번에 조회. 점수·프로파일은 미생성 상태에서 404 가 정상이므로
  // 개별 .catch(() => null) 로 막아 다른 응답이 함께 무효화되는 것을 방지.
  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [rulesResp, resultsResp, scoreResp, profileResp, historyResp, scheduleResp, upstreamResp] = await Promise.all([
        authFetch(`${BASE}/rules?dataset_id=${datasetId}`),
        authFetch(`${BASE}/datasets/${datasetId}/results`),
        authFetch(`${BASE}/datasets/${datasetId}/score`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/profile`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/score/history`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/schedule`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/upstream-quality`).catch(() => null),
      ])
      if (rulesResp.ok) setRules(await rulesResp.json())
      if (resultsResp.ok) setResults(await resultsResp.json())
      if (scoreResp?.ok) setScore(await scoreResp.json())
      if (historyResp?.ok) setScoreHistory(await historyResp.json())
      if (scheduleResp?.ok) setSchedule((await scheduleResp.json()).schedule ?? null)
      if (upstreamResp?.ok) setUpstreamWarnings(await upstreamResp.json())
      if (profileResp?.ok) {
        const data = await profileResp.json()
        setProfile(data.columns || [])
      }
    } catch (err) {
      console.error("Failed to load quality data", { datasetId, err })
    } finally { setLoading(false) }
  }, [datasetId])

  useEffect(() => { fetchData() }, [fetchData])

  // 정의된 모든 규칙을 서버에서 즉시 실행하고 결과 테이블을 갱신.
  const runCheck = async () => {
    setRunning(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/check`, { method: "POST" })
      if (resp.ok) await fetchData()
    } catch (err) {
      console.error("Failed to run quality check", { datasetId, err })
    } finally { setRunning(false) }
  }

  // 데이터 프로파일링(컬럼별 NULL/고유값/최소·최대 통계) 재계산.
  const runProfile = async () => {
    setProfiling(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/profile`, { method: "POST" })
      if (resp.ok) await fetchData()
    } catch (err) {
      console.error("Failed to run profiling", { datasetId, err })
    } finally { setProfiling(false) }
  }

  // 다이얼로그 입력값으로 규칙 등록/수정 후 폼 리셋. 입력 검증은 disabled 속성으로 처리.
  const saveRule = async () => {
    const payload = {
      rule_name: ruleName,
      check_type: checkType,
      column_name: columnName || null,
      expected_value: expectedValue || null,
      threshold: Number(threshold),
      severity,
    }
    try {
      if (editingRuleId != null) {
        await authFetch(`${BASE}/rules/${editingRuleId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      } else {
        await authFetch(`${BASE}/rules`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dataset_id: datasetId, ...payload }),
        })
      }
    } catch (err) {
      console.error("Failed to save quality rule", { datasetId, ruleName, err })
    }
    setAddOpen(false)
    setEditingRuleId(null)
    setRuleName(""); setColumnName(""); setExpectedValue(""); setThreshold("100")
    fetchData()
  }

  // 기존 규칙을 다이얼로그에 채워 편집 모드로 연다.
  const openEditRule = (rule: QualityRule) => {
    setEditingRuleId(rule.id)
    setRuleName(rule.rule_name)
    setCheckType(rule.check_type)
    setColumnName(rule.column_name || "")
    setExpectedValue(rule.expected_value || "")
    setThreshold(String(rule.threshold))
    setSeverity(rule.severity)
    setAddOpen(true)
  }

  // 프로파일 기반 규칙 추천 — 후보를 받아 선택 생성
  const openRecommendations = async () => {
    setRecsLoading(true)
    setRecsOpen(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/rules/recommendations`)
      if (resp.ok) {
        const data: RuleRecommendation[] = await resp.json()
        setRecs(data)
        setSelectedRecs(new Set(data.map((_, i) => i)))  // 기본 전체 선택
      }
    } catch (err) {
      console.error("Failed to fetch rule recommendations", { datasetId, err })
    } finally { setRecsLoading(false) }
  }

  const createSelectedRecs = async () => {
    const chosen = recs.filter((_, i) => selectedRecs.has(i))
    try {
      for (const r of chosen) {
        await authFetch(`${BASE}/rules`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dataset_id: datasetId, rule_name: r.rule_name, check_type: r.check_type,
            column_name: r.column_name, expected_value: r.expected_value,
            threshold: r.threshold, severity: r.severity,
          }),
        })
      }
    } catch (err) {
      console.error("Failed to create recommended rules", { datasetId, err })
    }
    setRecsOpen(false)
    fetchData()
  }

  // 검증 주기 설정 — 백그라운드 스케줄러(10분 간격 확인)가 자동 실행
  const saveSchedule = async (value: string) => {
    const next = value === "none" ? null : value
    setSchedule(next)
    try {
      await authFetch(`${BASE}/datasets/${datasetId}/schedule`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ schedule: next }),
      })
    } catch (err) {
      console.error("Failed to save quality schedule", { datasetId, err })
    }
  }

  const deleteRule = async (ruleId: number) => {
    try {
      await authFetch(`${BASE}/rules/${ruleId}`, { method: "DELETE" })
    } catch (err) {
      console.error("Failed to delete quality rule", { ruleId, err })
    }
    fetchData()
  }

  const pct = score?.score ?? 0

  return (
    <div className="space-y-4">
      {/* 업스트림 품질 경고 — 원천 데이터 품질 문제의 전파 알림 */}
      {upstreamWarnings.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-700 dark:bg-amber-950/40">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
          <div>
            <p className="font-medium text-amber-900 dark:text-amber-200">업스트림 품질 경고</p>
            <p className="mt-0.5 text-amber-800 dark:text-amber-300">
              이 데이터셋의 원천 중 품질 문제가 있는 데이터셋이 있습니다 — 파생 데이터의 정합성에 영향을 줄 수 있습니다:
            </p>
            <ul className="mt-1 space-y-0.5">
              {upstreamWarnings.map((u) => (
                <li key={u.id} className="text-amber-900 dark:text-amber-200">
                  <a href={`/dashboard/datasets/${u.id}`} className="font-mono text-xs hover:underline">{u.name}</a>
                  <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                    u.quality_status === "BAD" ? "bg-red-100 text-red-700" : "bg-amber-200 text-amber-800"
                  }`}>{u.quality_status}{u.quality_score != null && ` ${u.quality_score}%`}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Score bar + action buttons */}
      <Card>
        <CardContent className="py-3 px-4">
          <div className="flex items-center gap-4">
            <Shield className="h-5 w-5 text-muted-foreground" />
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-1">
                <span className="text-sm font-medium">품질 점수</span>
                <span className="text-lg font-bold">{score ? `${pct}%` : "—"}</span>
              </div>
              <div className="w-full bg-muted rounded-full h-2.5">
                <div className={`h-2.5 rounded-full transition-all ${
                  pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"
                }`} style={{ width: `${pct}%` }} />
              </div>
            </div>
            <Separator orientation="vertical" className="h-8" />
            {score && (
              <div className="flex items-center gap-3 text-sm">
                <span className="flex items-center gap-1"><CheckCircle className="h-3.5 w-3.5 text-green-500" />{score.passed_rules}</span>
                <span className="flex items-center gap-1"><XCircle className="h-3.5 w-3.5 text-red-500" />{score.failed_rules}</span>
                <span className="text-muted-foreground">/ 전체 {score.total_rules}개</span>
              </div>
            )}
            <ScoreTrend history={scoreHistory} />
            <div className="flex flex-col items-end gap-0.5 text-xs text-muted-foreground whitespace-nowrap">
              {score?.scored_at && <span>마지막 검증 {timeAgo(score.scored_at)}</span>}
              <div className="flex items-center gap-1">
                <span>주기</span>
                <Select value={schedule ?? "none"} onValueChange={saveSchedule}>
                  <SelectTrigger className="h-7 w-24 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">없음</SelectItem>
                    <SelectItem value="hourly">매시간</SelectItem>
                    <SelectItem value="daily">매일</SelectItem>
                    <SelectItem value="weekly">매주</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={runProfile} disabled={profiling}>
                {profiling ? "프로파일링..." : "프로파일"}
              </Button>
              <Button variant="outline" size="sm" onClick={runCheck} disabled={running}>
                <Play className="h-3.5 w-3.5 mr-1" />
                {running ? "실행 중..." : "검증 실행"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
                <Plus className="h-3.5 w-3.5 mr-1" />규칙 추가
              </Button>
              <Button variant="outline" size="sm" onClick={openRecommendations}>
                <Sparkles className="h-3.5 w-3.5 mr-1" />규칙 추천
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Results table */}
      {results.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">상태</TableHead>
                  <TableHead>규칙</TableHead>
                  <TableHead className="w-32">검증 유형</TableHead>
                  <TableHead className="w-32">컬럼</TableHead>
                  <TableHead className="w-28">실제 값</TableHead>
                  <TableHead>상세</TableHead>
                  <TableHead className="w-20">심각도</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.map(r => (
                  <Fragment key={r.id}>
                    <TableRow>
                      <TableCell>
                        {r.passed === "true"
                          ? <CheckCircle className="h-4 w-4 text-green-500" />
                          : <XCircle className="h-4 w-4 text-red-500" />}
                      </TableCell>
                      <TableCell className="font-medium text-sm">{r.rule_name}</TableCell>
                      <TableCell><Badge variant="outline" className="text-sm">{r.check_type}</Badge></TableCell>
                      <TableCell className="text-sm text-muted-foreground">{r.column_name || "—"}</TableCell>
                      <TableCell className="text-sm font-mono">{r.actual_value || "—"}</TableCell>
                      <TableCell className="text-sm text-muted-foreground whitespace-normal break-words">
                        {r.detail}
                        {r.failed_samples && r.failed_samples.length > 0 && (
                          <button
                            type="button"
                            onClick={() => setExpandedResultId(expandedResultId === r.id ? null : r.id)}
                            className="ml-2 inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
                          >
                            {expandedResultId === r.id ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                            위반 샘플 {r.failed_samples.length}행
                          </button>
                        )}
                      </TableCell>
                      <TableCell><SeverityBadge severity={r.severity || "INFO"} /></TableCell>
                    </TableRow>
                    {expandedResultId === r.id && r.failed_samples && (
                      <TableRow>
                        <TableCell colSpan={7} className="bg-muted/30 p-3">
                          <div className="overflow-x-auto">
                            <table className="text-xs font-mono">
                              <thead>
                                <tr>
                                  {Object.keys(r.failed_samples[0]!).map(k => (
                                    <th key={k} className={`px-2 py-1 text-left font-semibold border-b ${k === r.column_name ? "text-red-600" : "text-muted-foreground"}`}>{k}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {r.failed_samples.map((row, i) => (
                                  <tr key={i}>
                                    {Object.entries(row).map(([k, v]) => (
                                      <td key={k} className={`px-2 py-1 border-b border-border/50 ${k === r.column_name ? "text-red-600 font-semibold" : ""}`}>
                                        {v ?? <span className="italic text-muted-foreground">NULL</span>}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Rules list */}
      {rules.length > 0 && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium">품질 규칙 ({rules.length})</span>
            </div>
            <div className="space-y-2">
              {rules.map(rule => (
                <div key={rule.id} className="flex items-center justify-between text-sm border rounded px-3 py-2">
                  <div className="flex items-center gap-3">
                    <Badge variant="outline">{rule.check_type}</Badge>
                    <span className="font-medium">{rule.rule_name}</span>
                    {rule.column_name && <span className="text-muted-foreground font-mono">{rule.column_name}</span>}
                    <span className="text-muted-foreground">임계값: {rule.threshold}%</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={rule.severity} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEditRule(rule)} aria-label="규칙 편집">
                      <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => deleteRule(rule.id)} aria-label="규칙 삭제">
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Profile summary */}
      {profile.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="px-4 py-3 border-b">
              <span className="text-sm font-medium">데이터 프로파일</span>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>컬럼</TableHead>
                  <TableHead className="w-20">타입</TableHead>
                  <TableHead className="w-20">NULL</TableHead>
                  <TableHead className="w-20">고유</TableHead>
                  <TableHead className="w-28">최소</TableHead>
                  <TableHead className="w-28">최대</TableHead>
                  <TableHead className="w-20">평균</TableHead>
                  <TableHead>최빈값</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.map(cp => (
                  <TableRow key={cp.column_name}>
                    <TableCell className="font-medium font-mono text-sm">{cp.column_name}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{cp.column_type}</TableCell>
                    <TableCell className={`text-sm ${cp.null_percent > 5 ? "text-red-500 font-medium" : ""}`}>
                      {cp.null_percent}%
                    </TableCell>
                    <TableCell className="text-sm">{cp.unique_percent}%</TableCell>
                    {isBinaryType(cp.column_type) ? (
                      <TableCell colSpan={4} className="text-sm text-muted-foreground italic">
                        바이너리 — 통계 생략
                      </TableCell>
                    ) : (
                      <>
                        <TableCell className="text-sm font-mono">{cp.min_value ?? "—"}</TableCell>
                        <TableCell className="text-sm font-mono">{cp.max_value ?? "—"}</TableCell>
                        <TableCell className="text-sm font-mono">{cp.mean_value?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell className="text-sm">
                          {cp.top_values && cp.top_values.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {cp.top_values.map((tv) => (
                                <span key={tv.value} className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                                  {tv.value}
                                  <span className="ml-1 text-muted-foreground">×{tv.count.toLocaleString()}</span>
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {rules.length === 0 && results.length === 0 && !loading && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 gap-3">
            <Shield className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">정의된 품질 규칙이 없습니다. 규칙을 추가하고 검증을 실행하세요.</p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={runProfile} disabled={profiling}>데이터 프로파일</Button>
              <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
                <Plus className="h-3.5 w-3.5 mr-1" />규칙 추가
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Add Rule Dialog */}
      {/* 도움말 — 프로파일/검증 수행 방법 (접이식) */}
      <Card>
        <CardContent className="p-0">
          <button
            type="button"
            onClick={() => setHelpOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <HelpCircle className="h-4 w-4" />
            도움말 — 프로파일과 검증은 어떻게 동작하나요?
            {helpOpen ? <ChevronDown className="h-4 w-4 ml-auto" /> : <ChevronRight className="h-4 w-4 ml-auto" />}
          </button>
          {helpOpen && (
            <div className="border-t px-5 py-4 text-sm text-muted-foreground space-y-4">
              <div>
                <p className="font-semibold text-foreground mb-1">권장 순서</p>
                <p>
                  ① <b>프로파일</b>로 현재 데이터의 통계를 수집 → ② <b>규칙 추가</b>로 기대 조건을 정의 →
                  ③ <b>검증 실행</b>으로 규칙을 평가합니다. 이후 일상 운영에서는 프로파일·검증 실행을 주기적으로 반복하면 됩니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">프로파일</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li>원본 데이터베이스에 직접 프로파일링 쿼리를 실행해 <b>전체 행 수</b>와 컬럼별
                    <b> NULL 수 · 고유값 수 · 최소/최대 · 평균</b>을 수집합니다.</li>
                  <li>원본 DB 에 접속할 수 없으면 스키마 정보만으로 구성된 프로파일로 대체됩니다
                    (통계값 없음 — 데이터 소스 연결 설정을 확인하세요).</li>
                  <li>결과는 실행 시점의 스냅숏으로 저장되며, 아래 컬럼 프로파일 표에 최신 결과가 표시됩니다.</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">검증 실행</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li>활성 상태의 모든 규칙을 <b>최신 프로파일 통계를 기준으로</b> 평가합니다.
                    프로파일이 없으면 자동으로 프로파일을 먼저 수행합니다.</li>
                  <li>규칙별 통과/실패가 기록되고, <b>품질 점수 = 통과 규칙 ÷ 전체 규칙</b>(%)으로
                    산출되어 데이터셋 헤더의 품질 표시와 연동됩니다.</li>
                  <li>데이터가 바뀐 뒤에는 <b>프로파일을 먼저 갱신</b>해야 검증이 최신 상태를 반영합니다.</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">규칙 유형</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>NOT_NULL</b> — 컬럼에 NULL 이 없어야 함 / <b>UNIQUE</b> — 모든 값이 고유해야 함</li>
                  <li><b>MIN_VALUE / MAX_VALUE</b> — 컬럼 값이 지정 범위 안이어야 함</li>
                  <li><b>ROW_COUNT</b> — 전체 행 수가 기댓값 조건을 만족해야 함</li>
                  <li><b>FRESHNESS</b> — 최신 데이터가 지정 기간 안에 적재되었어야 함</li>
                  <li><b>ACCEPTED_VALUES / REGEX</b> — 허용 값 목록·패턴 검사.
                    프로파일 통계만으로는 평가가 제한되어 원본 DB 접속이 가능할 때 정확합니다.</li>
                  <li><b>CUSTOM_SQL</b> — 직접 작성한 SELECT 쿼리로 평가 (조인·집계 등 교차 검증).
                    첫 행 첫 컬럼 = 위반 건수(0이면 통과) 규약이며, 보안상 품질 배치에서만 실행됩니다.</li>
                  <li><b>CUSTOM_PYTHON</b> — Python 플러그인 함수로 평가 (통계 검정 등 SQL로 불가능한 로직).
                    아래 작성 방법 참고. 역시 품질 배치에서만 실행됩니다.</li>
                </ul>
                <p className="mt-1">
                  심각도는 <b>치명</b>(데이터 사용 불가 수준) · <b>경고</b>(주의 필요) · <b>정보</b>(참고)로
                  구분해 규칙마다 지정합니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">커스텀 Python 체크 작성 방법 (custom_checks)</p>
                <p className="mb-1">
                  저장소의 <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">quality/custom_checks/</code> 디렉터리에
                  체크 함수를 작성하고, 규칙의 플러그인 설정(JSON)으로 연결합니다.
                </p>
                <p className="mb-1">① 체크 함수 — <b>(통과 여부, 실제 값, 상세 설명) 3-튜플</b>을 반환:</p>
                <pre className="rounded-md border bg-muted/40 p-3 text-xs font-mono overflow-x-auto whitespace-pre">{`# quality/custom_checks/rental_checks.py
def rental_duration_outlier(df, params):
    """대여 기간 IQR 이상치 비율 검사."""
    max_pct = float(params.get("max_outlier_pct", 1.0))
    # df 는 실행 엔진의 DataFrame — pandas/PySpark 양쪽을 지원하려면:
    if hasattr(df, "toPandas"):
        df = df.select("rental_date", "return_date").toPandas()
    ...  # 검증 로직
    return passed, f"{pct:.2f}%", f"이상치 {n}건 ({pct:.2f}%, 허용 {max_pct}%)"`}</pre>
                <p className="mt-2 mb-1">② 규칙 등록 — 검증 유형 <b>CUSTOM_PYTHON</b>, 플러그인 설정(JSON):</p>
                <pre className="rounded-md border bg-muted/40 p-3 text-xs font-mono overflow-x-auto whitespace-pre">{`{"module": "rental_checks",
 "fn": "rental_duration_outlier",
 "params": {"max_outlier_pct": 1.0}}`}</pre>
                <ul className="list-disc pl-5 space-y-0.5 mt-2">
                  <li>평가는 품질 배치 실행 시 수행됩니다:
                    <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded ml-1">python quality/python-quality.py --datasource-id ... --username admin ...</code></li>
                  <li>모듈·함수 이름은 영문 식별자만 허용되며, 플러그인 코드는 git 리뷰를 거친
                    신뢰 코드만 배포하세요 (배치가 그대로 실행합니다)</li>
                  <li>서버의 "검증 실행" 버튼은 커스텀 규칙을 실행하지 않고 "평가 제외"로 표시합니다 —
                    점수에는 영향이 없습니다</li>
                  <li>자세한 규약은 <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">quality/README.md</code> 참고</li>
                </ul>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 규칙 추천 다이얼로그 — 프로파일 기반 후보를 선택해 일괄 생성 */}
      <Dialog open={recsOpen} onOpenChange={setRecsOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>규칙 추천 (프로파일 기반)</DialogTitle></DialogHeader>
          {recsLoading ? (
            <p className="py-8 text-center text-sm text-muted-foreground">프로파일 분석 중...</p>
          ) : recs.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              추천할 규칙이 없습니다 — 프로파일을 먼저 수집했는지, 이미 같은 규칙이 등록돼 있지 않은지 확인하세요.
            </p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                최신 프로파일 통계에서 도출한 후보입니다. 현재 데이터가 깨끗한 상태를 기준으로 하므로,
                의도된 예외가 있는 컬럼은 선택을 해제하세요.
              </p>
              <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
                {recs.map((r, i) => (
                  <label key={i} className="flex cursor-pointer items-start gap-2.5 rounded border px-3 py-2 hover:bg-muted/40">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selectedRecs.has(i)}
                      onChange={(e) => {
                        const next = new Set(selectedRecs)
                        if (e.target.checked) next.add(i); else next.delete(i)
                        setSelectedRecs(next)
                      }}
                    />
                    <div className="min-w-0 flex-1 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline">{r.check_type}</Badge>
                        <span className="font-medium">{r.rule_name}</span>
                        {r.column_name && <span className="font-mono text-xs text-muted-foreground">{r.column_name}</span>}
                        <SeverityBadge severity={r.severity} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground">{r.reason}
                        {r.expected_value && <span className="ml-1 font-mono">기대값: {r.expected_value}</span>}
                      </p>
                    </div>
                  </label>
                ))}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{selectedRecs.size} / {recs.length} 선택</span>
                <Button onClick={createSelectedRecs} disabled={selectedRecs.size === 0}>
                  선택한 규칙 {selectedRecs.size}개 생성
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={addOpen} onOpenChange={(o) => {
        setAddOpen(o)
        if (!o) {
          setEditingRuleId(null)
          setRuleName(""); setColumnName(""); setExpectedValue(""); setThreshold("100"); setSeverity("WARNING")
        }
      }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{editingRuleId != null ? "품질 규칙 수정" : "품질 규칙 추가"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label className="text-sm">규칙 이름</Label><Input value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="amount is not null" className="h-9" /></div>
            <div><Label className="text-sm">검증 유형</Label>
              <Select value={checkType} onValueChange={setCheckType}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CHECK_TYPE_META).map(([value, meta]) => (
                    <SelectItem key={value} value={value}>
                      <span className="font-mono">{value}</span>
                      <span className="text-muted-foreground"> — {meta.label}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {CHECK_TYPE_META[checkType] && (
                <p className="mt-1 text-xs text-muted-foreground">{CHECK_TYPE_META[checkType].desc}</p>
              )}
            </div>
            <div>
              <Label className="text-sm">컬럼 (선택)</Label>
              {columns.length > 0 ? (
                <Select
                  value={columnName || "__none__"}
                  onValueChange={(v) => setColumnName(v === "__none__" ? "" : v)}
                >
                  <SelectTrigger className="h-9"><SelectValue placeholder="선택 안 함 (테이블 수준)" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">선택 안 함 (테이블 수준 — ROW_COUNT/FRESHNESS)</SelectItem>
                    {columns.map((c) => (
                      <SelectItem key={c} value={c} className="font-mono text-sm">{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input value={columnName} onChange={e => setColumnName(e.target.value)} placeholder="amount" className="h-9" />
              )}
            </div>
            <div>
              <Label className="text-sm">
                {checkType === "CUSTOM_SQL" ? "검증 SQL" : checkType === "CUSTOM_PYTHON" ? "플러그인 설정 (JSON)" : "기대값"}
              </Label>
              {checkType === "CUSTOM_SQL" || checkType === "CUSTOM_PYTHON" ? (
                <div className="rounded-md border overflow-hidden">
                  <MonacoEditor
                    height={checkType === "CUSTOM_SQL" ? 160 : 110}
                    language={checkType === "CUSTOM_SQL" ? "sql" : "json"}
                    theme="light"
                    value={expectedValue}
                    onChange={(v) => setExpectedValue(v ?? "")}
                    options={{
                      minimap: { enabled: false },
                      lineNumbers: "on",
                      fontSize: 13,
                      fontFamily: "D2Coding, Menlo, Consolas, monospace",
                      scrollBeyondLastLine: false,
                      wordWrap: "on",
                      padding: { top: 8, bottom: 8 },
                      placeholder: checkType === "CUSTOM_SQL"
                        ? "-- 첫 행 첫 컬럼 = 위반 건수 (0 = 통과)\nSELECT count(*) FROM ..."
                        : '{"module": "rental_checks", "fn": "rental_duration_outlier", "params": {}}',
                      tabSize: 2,
                      automaticLayout: true,
                    }}
                  />
                </div>
              ) : (
                <Input
                  value={expectedValue}
                  onChange={e => setExpectedValue(e.target.value)}
                  placeholder={CHECK_TYPE_META[checkType]?.expectedHint ?? "이 유형은 기대값을 사용하지 않습니다"}
                  disabled={CHECK_TYPE_META[checkType]?.expectedHint === null}
                  className="h-9"
                />
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-sm">임계값 (%)</Label>
                <Input
                  type="number"
                  value={threshold}
                  onChange={e => setThreshold(e.target.value)}
                  placeholder={CHECK_TYPE_META[checkType]?.usesThreshold ? "100" : "이 유형은 임계값을 사용하지 않습니다"}
                  disabled={!CHECK_TYPE_META[checkType]?.usesThreshold}
                  className="h-9"
                />
              </div>
              <div><Label className="text-sm">심각도</Label>
                <Select value={severity} onValueChange={setSeverity}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="BREAKING">BREAKING</SelectItem>
                    <SelectItem value="WARNING">WARNING</SelectItem>
                    <SelectItem value="INFO">INFO</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end"><Button onClick={saveRule} disabled={!ruleName.trim()}>{editingRuleId != null ? "저장" : "규칙 추가"}</Button></div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SeverityBadge({ severity }: { severity: string }) {
  if (severity === "BREAKING") return <Badge className="bg-red-500 text-white text-sm px-2 py-0.5 border-0">치명</Badge>
  if (severity === "WARNING") return <Badge className="bg-amber-500 text-white text-sm px-2 py-0.5 border-0">경고</Badge>
  return <Badge variant="outline" className="text-sm px-2 py-0.5">정보</Badge>
}


/** 점수 추세 스파크라인 — 외부 차트 라이브러리 없이 SVG 로 그린다 (최근 20회). */
function ScoreTrend({ history }: { history: ScoreHistoryItem[] }) {
  // 오래된 → 최신 순으로 정렬 후 최근 20개
  const points = [...history]
    .sort((a, b) => new Date(a.scored_at).getTime() - new Date(b.scored_at).getTime())
    .slice(-20)
  if (points.length < 2) return null

  const W = 220, H = 48, PAD = 4
  const xs = (i: number) => PAD + (i * (W - PAD * 2)) / (points.length - 1)
  const ys = (v: number) => PAD + ((100 - v) * (H - PAD * 2)) / 100
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${ys(p.score).toFixed(1)}`).join(" ")
  const last = points[points.length - 1]!
  const prev = points[points.length - 2]!
  const delta = last.score - prev.score

  return (
    <div className="flex items-center gap-3" title="최근 검증 점수 추세 (최대 20회)">
      <svg width={W} height={H} className="shrink-0">
        {/* 기준선 (90% = GOOD 경계) */}
        <line x1={PAD} y1={ys(90)} x2={W - PAD} y2={ys(90)} stroke="currentColor" strokeOpacity={0.15} strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5}
          className={last.score >= 90 ? "text-green-500" : last.score >= 70 ? "text-amber-500" : "text-red-500"} />
        {points.map((p, i) => (
          <circle key={p.id} cx={xs(i)} cy={ys(p.score)} r={i === points.length - 1 ? 3 : 1.5}
            className={p.score >= 90 ? "fill-green-500" : p.score >= 70 ? "fill-amber-500" : "fill-red-500"} />
        ))}
      </svg>
      <div className="text-xs text-muted-foreground whitespace-nowrap">
        <div>최근 {points.length}회</div>
        {delta !== 0 && (
          <div className={delta > 0 ? "text-green-600" : "text-red-600"}>
            {delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%p
          </div>
        )}
      </div>
    </div>
  )
}


/** 바이너리 계열 타입 — min/max/평균 통계가 의미 없는 컬럼 (BLOB·이미지·지오메트리 등).
 *  pandas 의 dtype 'OBJECT' 도 배치가 바이너리 감지 시 BINARY 로 기록하지만,
 *  과거 수집분('OBJECT')과 스키마 타입(BLOB 등)도 함께 가린다. */
function isBinaryType(t: string | null | undefined): boolean {
  if (!t) return false
  const upper = t.toUpperCase()
  return ["BLOB", "BINARY", "VARBINARY", "BYTEA", "BYTES", "IMAGE", "GEOMETRY", "OBJECT", "RAW"]
    .some(k => upper.includes(k))
}

/** 상대 시각 표기 — 알림 페이지와 동일 포맷. */
function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "방금 전"
  if (mins < 60) return `${mins}분 전`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}시간 전`
  return `${Math.floor(hours / 24)}일 전`
}
