"use client"

// Dataset 상세 페이지의 "품질" 탭 — 품질 진단(규칙 정의·진단·결과)과 데이터 프로파일(컬럼 통계) 두 구역.

import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react"
import { Badge } from "@workspace/ui/components/badge"
import { StatusBadge, statusMeta } from "@workspace/ui/components/status-badge"
import { Button } from "@workspace/ui/components/button"
import { DisabledReason, Tip } from "@/components/tip"
import { Input } from "@workspace/ui/components/input"
import dynamic from "next/dynamic"

// 진단 SQL 편집기 — CodeViewer 와 동일하게 SSR 비활성 동적 로드 (편집 모드)
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
import { cn } from "@workspace/ui/lib/utils"
import { Tooltip, TooltipContent, TooltipTrigger } from "@workspace/ui/components/tooltip"
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight, Code2, Columns3, HelpCircle, Link2, Loader2, Pencil, Play, Plus, Shield, Sparkles, Square, Trash2, XCircle } from "lucide-react"
import { notifyError, notifySuccess } from "@/lib/notify"
import { SeverityBadge } from "@/components/severity-badge"
import { datasetQualityStatus } from "@/features/datasets/status"
import { useConfirm } from "@/components/confirm-provider"
import { authFetch, throwOnError } from "@/features/auth/auth-fetch"
import { usePermissions } from "@/features/permissions/use-permissions"
import { MONACO_FONT_SIZE } from "@/lib/monaco"
import {
  DATE_FORMAT_CHOICES, isBinaryColumnType, listedDateFormat, ruleListMeta,
} from "@/features/datasets/quality-rules"
import {
  categoryOfRule, checkTypeLabel, domainForCategory, pathSkipReason, ruleCategories, skippedOnPath,
} from "@/features/quality/check-types"
import { QualityCategorySelect, QualityCheckTypeSelect } from "@/features/quality/components/quality-category-select"
import { typeForCategory } from "@/features/quality/definitions"
import { loadQualityIndicators, type QualityIndicator } from "@/features/quality/api"
import { QualityDatasetAssignDialog } from "@/features/quality/components/quality-dataset-assign-dialog"
import { QualitySqlPreviewDialog } from "@/features/quality/components/quality-sql-preview"
import { datasetHref } from "@/features/datasets/links"
import { QUALITY_RUN_STATUS } from "@/features/quality/format"
import { TagInput } from "@/components/tag-input"
import { decodeList, encodeList } from "@/lib/text-list"

const BASE = "/api/v1/quality"

// 진단 유형별 한글 라벨·설명·기대값 힌트 — 규칙 추가 다이얼로그에서 사용.
// 평가 시맨틱은 서버(app/quality/service.py:_evaluate_rule) 기준.
/** 기대값이 값 목록인 유형 — 배지 입력(TODO-143) */
const LIST_CHECK_TYPES = new Set(["ACCEPTED_VALUES", "DUMMY_VALUE"])

const CHECK_TYPE_META: Record<string, { desc: string; expectedHint: string | null; usesThreshold: boolean }> = {
  NOT_NULL: {
    desc: "컬럼의 비-NULL 비율이 임계값(%) 이상이어야 통과합니다 (100 = NULL 1건도 불허). 기대값은 사용하지 않습니다.",
    expectedHint: null,
    usesThreshold: true,
  },
  UNIQUE: {
    desc: "컬럼의 고유값 비율이 임계값(%) 이상이어야 통과합니다 (100 = 중복 없음). 기대값은 사용하지 않습니다.",
    expectedHint: null,
    usesThreshold: true,
  },
  MIN_VALUE: {
    desc: "컬럼의 최소값이 기대값 이상이어야 통과합니다 (예: 금액 ≥ 0). 임계값은 사용하지 않습니다.",
    expectedHint: "예: 0",
    usesThreshold: false,
  },
  MAX_VALUE: {
    desc: "컬럼의 최대값이 기대값 이하여야 통과합니다. 임계값은 사용하지 않습니다.",
    expectedHint: "예: 100",
    usesThreshold: false,
  },
  ROW_COUNT: {
    desc: "전체 행 수가 기대값 이상이어야 통과합니다. 테이블 수준 검사라 컬럼·임계값은 사용하지 않습니다.",
    expectedHint: "예: 1000",
    usesThreshold: false,
  },
  FRESHNESS: {
    desc: "적재·수정 시각 컬럼(필수)의 최댓값 이후 경과 시간이 기대값(시간, 비우면 24) 이내여야 통과합니다. 정밀 진단이 전체 데이터에서 최댓값을 구하고, 시간대 없는 시각은 설정의 원천 데이터 시간대(기본 Asia/Seoul)로 해석합니다. 임계값은 사용하지 않습니다.",
    expectedHint: "예: 24 (시간)",
    usesThreshold: false,
  },
  ACCEPTED_VALUES: {
    desc: "컬럼 값이 허용 값 목록 안에만 있어야 합니다 (임계값 미사용). 정밀 진단이 전체 데이터로 평가합니다.",
    expectedHint: "예: PG-13 입력 후 Enter",
    usesThreshold: false,
  },
  REGEX: {
    desc: "컬럼 값이 정규식과 일치해야 합니다 (임계값 미사용). 정밀 진단이 전체 데이터로 평가합니다.",
    expectedHint: "예: ^[^@]+@[^@]+$",
    usesThreshold: false,
  },
  CUSTOM_SQL: {
    desc: "기대값의 SELECT 쿼리를 Trino 에서 실행해 평가합니다 — 첫 행 첫 컬럼이 위반 건수(0이면 통과)여야 합니다. 조인·집계 등 복잡한 정합성 검증용입니다. 테이블 이름만 적으면 이 데이터셋의 Trino 카탈로그·스키마에서 찾습니다. SELECT·WITH 로 시작하는 문장 하나만 받습니다. Trino 가 연결된 데이터셋의 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: SELECT count(*) FROM rental r LEFT JOIN inventory i ON i.inventory_id = r.inventory_id WHERE i.inventory_id IS NULL",
    usesThreshold: false,
  },
  CUSTOM_PYTHON: {
    desc: "품질 워커의 플러그인 함수로 평가합니다 — SQL로 표현하기 어려운 통계 검증·외부 대조 로직용. 기대값에 {\"module\", \"fn\", \"params\"} JSON을 입력하며, 함수는 테이블 전체의 pandas DataFrame 을 받아 (passed, actual, detail)을 반환해야 합니다. 플러그인은 워커의 custom_checks 디렉터리에 둡니다. 정밀 진단에서 품질 워커가 원본 DB(PostgreSQL·MySQL·MariaDB·Oracle·SQL Server)를 직접 읽어 평가합니다 — Trino 로 진단하는 데이터셋도 원본이 이 유형이면 함께 평가합니다.",
    expectedHint: "예: {\"module\": \"rental_checks\", \"fn\": \"rental_duration_outlier\", \"params\": {\"max_outlier_pct\": 1.0}}",
    usesThreshold: false,
  },
  DATE_FORMAT: {
    desc: "문자·숫자로 저장한 날짜가 정한 형식이고 달력에 있는 날짜여야 합니다(2월 30일·13월은 오류, NULL 은 세지 않음). 형식은 목록에서 고르거나 직접 적습니다. 날짜·시각 타입 컬럼은 항상 통과합니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: YYYY/MM/DD",
    usesThreshold: false,
  },
  COLUMN_COMPARE: {
    desc: "이 컬럼과 다른 컬럼의 순서·크기 관계를 검사합니다 (예: 시작일 ≤ 종료일). 기대값에 연산자(<, <=, =, >=, >, <>)와 상대 컬럼을 적습니다. 한쪽이 NULL 인 행은 세지 않습니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: <= end_ymd",
    usesThreshold: false,
  },
  BROKEN_TEXT: {
    desc: "값에 깨진 글자(대체 문자 U+FFFD)나 완성형이 아닌 한글(낱자만 있는 ㄱ·ㅏ 등)이 없어야 합니다 (NULL 은 세지 않음). 기대값·임계값은 사용하지 않습니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: null,
    usesThreshold: false,
  },
  WHITESPACE: {
    desc: "값 앞뒤의 공백(전각 공백 포함)과 값 안의 제어 문자(탭·줄바꿈)가 없어야 합니다. 기대값에 쓰면 안 되는 문자를 이어 적으면 그 문자도 오류로 셉니다(비워도 됩니다). 정밀 진단에서만 평가됩니다.",
    expectedHint: "(선택) 금지할 문자 — 예: #$%",
    usesThreshold: false,
  },
  LOGICAL: {
    desc: "조건이 참인 행은 결론도 참이어야 합니다. 기대값에 'IF <조건> THEN <조건>' 을 적습니다. 조건은 '컬럼 연산자 값(또는 컬럼)', '컬럼 IS [NOT] NULL', '컬럼 [NOT] IN (값, …)' 을 AND·OR 로 잇습니다(괄호는 쓰지 않음). 날짜 · 시각 컬럼과 비교할 값은 DATE '2024-01-31' · TIMESTAMP '2024-01-31 09:00:00' 처럼 적습니다. 컬럼은 선택하지 않습니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: IF cancel_yn = 'Y' THEN cancel_ymd IS NOT NULL",
    usesThreshold: false,
  },
  FORMULA: {
    desc: "이 컬럼의 값이 계산식의 결과와 같아야 합니다. 기대값에 컬럼 이름·숫자·사칙연산·괄호로 식을 적고, 끝에 '~ 0.01' 처럼 허용 오차를 붙일 수 있습니다. 어느 한쪽이 NULL 인 행은 세지 않습니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: supply_amt + vat_amt",
    usesThreshold: false,
  },
  FUNCTION: {
    desc: "Trino 에 미리 등록한 함수로 컬럼 값을 검증합니다. 기대값에 함수 이름만 적습니다(카탈로그.스키마.함수 — 괄호·인자 없이). 함수는 컬럼 값을 인자 하나로 받아 boolean 을 돌려줘야 하며, true 가 아니면 오류로 셉니다(NULL 값은 세지 않음). 정규식으로 안 되는 검증(검증 숫자 계산 등)에 씁니다. Trino 가 연결된 데이터셋의 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: memory.default.is_valid_brno",
    usesThreshold: false,
  },
  DUMMY_VALUE: {
    desc: "999999 · -999 · N/A 처럼 값이 없음을 대신 적은 자리채움(더미) 값이 얼마나 섞여 있는지 봅니다 — AI-Ready 품질 지표 '통계적 타당성'. 기대값에 더미 값을 하나씩 넣고(비우면 흔한 값 목록), 더미 값이 아닌 값의 비율이 임계값(%) 이상이면 통과합니다(100 = 하나도 없어야 함). 숫자 컬럼은 숫자 더미 값만 비교하고 NULL 은 세지 않습니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "(선택) 예: 999999 입력 후 Enter",
    usesThreshold: true,
  },
  CONTINUITY: {
    desc: "날짜·시각 컬럼의 가장 이른 기간부터 가장 늦은 기간까지 값이 하나도 없는 기간(시계열 공백)이 있는지 봅니다 — AI-Ready 품질 지표 '적시성 › 데이터 연속성'. 기대값에 단위(HOUR · DAY · MONTH · YEAR)를 적고, 문자열로 저장한 날짜면 뒤에 형식을 붙입니다(예: DAY YYYYMMDD). 있는 기간의 비율이 임계값(%) 이상이면 통과합니다(100 = 빠진 기간 없음). 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: DAY · MONTH · DAY YYYYMMDD",
    usesThreshold: true,
  },
  REFERENTIAL: {
    desc: "이 컬럼의 값이 참조 데이터셋의 컬럼에 있어야 합니다. 기대값에 '참조 데이터셋 URN(또는 ID)#컬럼'을 적습니다. Trino 로 진단하면 참조 데이터셋도 같은 Trino 에 연결돼 있어야 하고(원본 DB 는 달라도 됨), 원천 직접 진단이면 참조 데이터셋이 같은 원본 데이터 소스에 있어야 합니다. 정밀 진단에서만 평가됩니다.",
    expectedHint: "예: commerce.commerce.customers.dataset#customer_id",
    usesThreshold: false,
  },
}

type QualityRule = {
  id: number; rule_name: string; check_type: string; column_name: string | null
  expected_value: string | null; threshold: number; severity: string; is_active: string
  /** 적어 둔 도메인(null = 자동)과 그것으로 정해진 품질 지표 키 */
  domain?: string | null; indicator?: string | null
  /** 할당된 품질 지표(없으면 이 데이터셋에서 직접 정한 규칙) — 유형·기준값·심각도는 지표를 따른다 */
  definition_id?: number | null; definition_name?: string | null
}

// passed=null → 미평가(서버가 평가할 수 없는 유형인데 이전 배치 결과도 없음, 점수 제외 — id·checked_at 도 null)
// carried_forward=true → 이번 실행에서 새로 평가하지 않고 직전(배치) 결과를 이어받음
type QualityResult = {
  id: number | null; rule_id: number; rule_name: string | null; check_type: string | null
  column_name: string | null; passed: string | null; actual_value: string | null
  detail: string | null; severity: string | null; checked_at: string | null
  failed_samples: Record<string, string | null>[] | null
  carried_forward?: boolean
  checked_count?: number | null; error_count?: number | null
}

// 정밀 진단 — 실행은 품질 워커가 하고 화면은 실행 행을 폴링해 상태를 보여 준다
type EngineStatus = {
  available: boolean; reason: string | null; engine: string
  /** 이 경로가 평가하는 규칙 유형 */
  check_types: string[]
  engine_datasource_name: string | null; engine_table: string | null; workers_alive: number
}
type QualityRun = {
  id: number; kind?: string; status: string; unresponsive: boolean; current_target: string | null
  progress_done: number; progress_total: number | null
  rules_total: number; rules_evaluated: number
  checked_count: number | null; error_count: number | null; error_rate: number | null
  error_message: string | null; requested_by: string | null; cancel_requested_by?: string | null
  queued_at: string | null; started_at: string | null; finished_at: string | null
}
const isActiveRun = (run: QualityRun | null) => run?.status === "QUEUED" || run?.status === "RUNNING"

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
  /** 데이터셋 소유자(생성자) 또는 관리자 — 진단 · 규칙 · 주기 · 프로파일을 바꿀 수 있다. 아니면 보기만 */
  canManage?: boolean
}

// 규칙을 만드는 버튼의 설명 — 툴팁 제목과 본문
const ASSIGN_TIP = {
  title: "지표 할당 — 미리 정해 둔 품질 지표를 컬럼에 연결",
  lines: [
    "품질 지표 메뉴에 등록된 지표(진단 유형 · 기준 · 임계값 · 관리지침 진단 기준)를 골라 이 데이터셋의 컬럼에 할당합니다. 할당한 컬럼마다 규칙이 하나씩 만들어집니다.",
    "할당한 규칙의 기준은 지표를 따릅니다 — 지표를 바꾸면 이 규칙에도 반영됩니다. 여러 데이터셋에 같은 기준을 적용할 때 씁니다.",
  ],
}
const ADD_TIP = {
  title: "규칙 추가 — 이 데이터셋만의 규칙을 직접 정의",
  lines: [
    "진단 유형(NULL · 고유값 · 범위 · 허용 값 · 정규식 · 날짜 형식 · 참조관계 · 사용자 SQL 등)과 대상 컬럼 · 기준값 · 임계값 · 심각도 · 진단 기준을 직접 정해 규칙 하나를 만듭니다.",
    "지표와 연결되지 않은 규칙이라 기준은 이 데이터셋에서만 바꿉니다.",
  ],
}
const RECOMMEND_TIP = {
  title: "규칙 추천 — 데이터 프로파일로 규칙 후보를 제안",
  lines: [
    "최신 데이터 프로파일 통계를 보고 지금 데이터에 맞는 규칙 후보를 보여 줍니다 — NULL 이 없는 컬럼은 NOT NULL, 값이 모두 다른 컬럼은 고유값, 코드성 컬럼은 허용 값, 음수가 없는 숫자 컬럼은 최소값 0, 테이블은 행 수 하한(현재의 90%).",
    "후보를 골라 한 번에 만듭니다. 바로 만들지 않으며, 같은 유형 · 컬럼의 규칙이 이미 있으면 후보에서 뺍니다. 프로파일이 없으면 먼저 프로파일을 실행하세요.",
  ],
}

/** 실행 중 경과 시간 — "12초", "3분 5초", "1시간 2분". */
function elapsedText(since: string): string {
  const sec = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 1000))
  if (sec < 60) return `${sec}초`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}분 ${sec % 60}초`
  return `${Math.floor(min / 60)}시간 ${min % 60}분`
}

/**
 * 정밀 진단 · 프로파일 실행 상태 — 버튼을 누른 구역 바로 아래에 보인다(정밀 진단은 품질 진단 구역, 프로파일은 데이터 프로파일 구역).
 * 실행 중이면 진행 막대 · 경과 시간 · 중지 버튼, 끝났으면 결과 요약.
 */
function RunStatusCard({ run, engine, canCancel, onCancel, inline = false }: {
  run: QualityRun; engine: EngineStatus | null; canCancel: boolean; onCancel: () => void
  /** 다른 박스 안에 넣을 때 — 테두리 없이 */
  inline?: boolean
}) {
  const active = isActiveRun(run)
  const what = run.kind === "PROFILE" ? "프로파일" : "정밀 진단"
  const cancelling = active && !!run.cancel_requested_by
  const running = run.status === "RUNNING"
  const pct = running && run.progress_total
    ? Math.min(100, Math.round((run.progress_done / run.progress_total) * 100)) : null
  const since = running ? run.started_at : run.queued_at
  const when = run.finished_at || run.started_at || run.queued_at
  return (
    <div className={cn("grid gap-2 px-4 py-2.5 text-sm", !inline && "rounded-md border", !inline && active && "border-primary/40")} aria-live="polite">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex items-center gap-1.5 font-medium">
            {active && <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />}
            {what}
          </span>
          {/* 실행 상태 톤 — 품질 진단 이력과 같은 맵(DEF-116) */}
          {cancelling ? <StatusBadge tone="neutral" label="중지 중" />
            : run.unresponsive ? <StatusBadge tone="danger" label="응답 없음" />
            : <StatusBadge {...statusMeta(QUALITY_RUN_STATUS, run.status)} />}
          {running && run.progress_total != null && (
            <span >
              쿼리 {run.progress_done}/{run.progress_total}
              {run.current_target ? ` · ${run.current_target}` : ""}
            </span>
          )}
          {run.status === "QUEUED" && !cancelling && (
            <span className={engine && engine.workers_alive === 0 ? "text-amber-600" : "text-muted-foreground"}>
              {engine && engine.workers_alive === 0
                ? "실행 중인 품질 워커가 없습니다 — 워커를 띄우면 시작합니다."
                : "품질 워커가 곧 시작합니다."}
            </span>
          )}
          {run.checked_count != null && (
            <span >
              규칙 {run.rules_evaluated}/{run.rules_total} · 진단 {run.checked_count.toLocaleString()}건 ·
              오류 {(run.error_count ?? 0).toLocaleString()}건
              {run.error_rate != null ? ` · 오류율 ${run.error_rate}%` : ""}
            </span>
          )}
          {run.error_message && (
            <span className={run.status === "FAILED" ? "text-red-600" : "text-amber-600"}>{run.error_message}</span>
          )}
          <span className="ml-auto flex items-center gap-2 text-xs">
            {active && since ? `${running ? "실행" : "대기"} ${elapsedText(since)}` : when ? timeAgo(when) : ""}
            {run.requested_by ? ` · ${run.requested_by}` : ""}
          </span>
          {canCancel && active && (
            <Button variant="outline" size="sm" className="h-7" onClick={onCancel} disabled={cancelling}>
              <Square className="mr-1 h-3 w-3" />{cancelling ? "중지 중…" : `${what} 중지`}
            </Button>
          )}
        </div>
        {active && (
          <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar"
            aria-label={`${what} 진행`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
            {pct != null
              ? <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.max(pct, 3)}%` }} />
              : <div className="h-full w-1/3 animate-pulse rounded-full bg-primary/40" />}
          </div>
        )}
        {cancelling && (
          <p className="text-xs">
            {run.cancel_requested_by} 이(가) 중지를 요청했습니다. 품질 워커가 다음 확인 때 실행 중인 쿼리를 멈추고 실행을 끝냅니다
            (원천 DB 를 직접 읽는 중이면 읽던 묶음을 마친 뒤).
          </p>
        )}
    </div>
  )
}

/** 진단 버튼의 설명 — 무엇으로 평가하는지 · 무엇을 못 하는지 · 왜 지금 누를 수 없는지. */
function ActionTip({ title, lines, blocked, children }: {
  title: string; lines: string[]; blocked?: string | null; children: ReactNode
}) {
  return (
    <Tooltip>
      {/* 누를 수 없는 단추에도 설명이 뜨도록 감싼 span 이 마우스·포커스를 받는다 */}
      <TooltipTrigger asChild>
        <span tabIndex={blocked ? 0 : -1} className="inline-flex">{children}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="block max-w-sm space-y-1 text-left leading-relaxed">
        <p className="font-semibold">{title}</p>
        {lines.map((l) => <p key={l}>{l}</p>)}
        {blocked && <p className="border-t border-background/30 pt-1 font-medium">지금은 실행할 수 없습니다 — {blocked}</p>}
      </TooltipContent>
    </Tooltip>
  )
}

export function QualityTab({ datasetId, columns = [], canManage = false }: Props) {
  const askConfirm = useConfirm()
  // 기능 권한(권한 관리 › 기능 권한)에 더해, 진단 · 변경은 데이터셋 소유자 또는 관리자만 — 서버도 같은 기준으로 403
  const { canUseFeature } = usePermissions()
  const runAllowed = canUseFeature("quality.run")
  const canManageRules = canManage && canUseFeature("quality.rules-manage")
  const canRun = canManage && runAllowed
  // 진단 버튼을 누를 수 없는 이유 — 버튼은 흐리게 두고 툴팁으로 알린다
  const runBlocked = !canManage
    ? "데이터셋의 소유자 또는 관리자만 진단할 수 있습니다."
    : !runAllowed ? "품질 진단 실행 기능 권한이 없습니다." : null
  const [rules, setRules] = useState<QualityRule[]>([])
  const [results, setResults] = useState<QualityResult[]>([])
  const [score, setScore] = useState<QualityScore | null>(null)
  const [profile, setProfile] = useState<ColumnProfile[]>([])
  // 프로파일을 언제 떴고 몇 행이었는지 — 데이터 프로파일 구역 머리에 보인다
  const [profileMeta, setProfileMeta] = useState<{ row_count: number; profiled_at: string } | null>(null)
  const [scoreHistory, setScoreHistory] = useState<ScoreHistoryItem[]>([])
  const [expandedResultId, setExpandedResultId] = useState<number | null>(null)  // 위반 샘플 펼침 행
  const [recsOpen, setRecsOpen] = useState(false)
  const [recs, setRecs] = useState<RuleRecommendation[]>([])
  const [selectedRecs, setSelectedRecs] = useState<Set<number>>(new Set())
  const [recsLoading, setRecsLoading] = useState(false)
  const [upstreamWarnings, setUpstreamWarnings] = useState<UpstreamQuality[]>([])
  const [loading, setLoading] = useState(false)
  const [profiling, setProfiling] = useState(false)
  const [engine, setEngine] = useState<EngineStatus | null>(null)
  const [latestRun, setLatestRun] = useState<QualityRun | null>(null)
  const [requestingRun, setRequestingRun] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null)
  const [assignOpen, setAssignOpen] = useState(false)      // 품질 지표 할당 대화상자
  const [sqlRule, setSqlRule] = useState<QualityRule | null>(null)  // 진단 쿼리를 볼 규칙
  // 편집 중인 규칙이 품질 지표를 할당한 것이면 그 규칙 — 유형·기준값·심각도 입력을 잠근다
  const editingLinked = rules.find((r) => r.id === editingRuleId && r.definition_id != null) ?? null
  const [helpOpen, setHelpOpen] = useState(false)

  // Add rule form
  const [ruleName, setRuleName] = useState("")
  const [checkType, setCheckType] = useState("NOT_NULL")
  const [columnName, setColumnName] = useState("")
  const [expectedValue, setExpectedValue] = useState("")
  const [threshold, setThreshold] = useState("100")
  const [severity, setSeverity] = useState("WARNING")
  // 분류(관리지침 진단 기준) — 데이터 품질 메뉴의 품질 지표와 같이 분류를 먼저 고르고, 그 안에서 기준 방식(규칙 유형)을 고른다
  const [category, setCategory] = useState("REQUIRED")
  // 날짜 형식을 목록에서 고르지 않고 직접 적는 중인지(목록에 없는 값이 들어 있으면 켜지 않아도 직접 입력으로 본다)
  const [dateCustom, setDateCustom] = useState(false)
  const [indicators, setIndicators] = useState<QualityIndicator[]>([])
  useEffect(() => {
    // 지표 이름·도메인 선택지 — 못 읽어도 규칙 편집은 된다(도메인 선택만 안 보임)
    loadQualityIndicators().then(setIndicators).catch((err) => console.error("Failed to load quality indicators", err))
  }, [])
  const indicatorName = (key?: string | null) => indicators.find((i) => i.key === key)?.name

  // 규칙/결과/점수/프로파일을 한 번에 조회. 점수·프로파일은 미생성 상태에서 404 가 정상이므로
  // 개별 .catch(() => null) 로 막아 다른 응답이 함께 무효화되는 것을 방지.
  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [rulesResp, resultsResp, scoreResp, profileResp, historyResp, upstreamResp, engineResp, runsResp] = await Promise.all([
        authFetch(`${BASE}/rules?dataset_id=${datasetId}`),
        authFetch(`${BASE}/datasets/${datasetId}/results`),
        authFetch(`${BASE}/datasets/${datasetId}/score`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/profile`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/score/history`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/upstream-quality`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/engine`).catch(() => null),
        authFetch(`${BASE}/datasets/${datasetId}/runs?limit=1`).catch(() => null),
      ])
      if (rulesResp.ok) setRules(await rulesResp.json())
      if (resultsResp.ok) setResults(await resultsResp.json())
      if (scoreResp?.ok) setScore(await scoreResp.json())
      if (historyResp?.ok) setScoreHistory(await historyResp.json())
      if (upstreamResp?.ok) setUpstreamWarnings(await upstreamResp.json())
      if (engineResp?.ok) setEngine(await engineResp.json())
      if (runsResp?.ok) setLatestRun(((await runsResp.json()) as QualityRun[])[0] ?? null)
      if (profileResp?.ok) {
        const data = await profileResp.json()
        setProfile(data.columns || [])
        setProfileMeta(data.profiled_at ? { row_count: data.row_count ?? 0, profiled_at: data.profiled_at } : null)
      }
      // 규칙·결과는 필수 응답 — 실패하면 빈 화면으로 조용히 두지 않고 알린다
      const failed = [rulesResp, resultsResp].find((r) => !r.ok)
      if (failed) await throwOnError(failed, "품질 정보를 불러오지 못했습니다.")
    } catch (err) {
      console.error("Failed to load quality data", { datasetId, err })
      notifyError(err, "품질 정보를 불러오지 못했습니다.")
    } finally { setLoading(false) }
  }, [datasetId])

  useEffect(() => { fetchData() }, [fetchData])

  // 정밀 진단을 대기열에 넣는다 — 실행은 품질 워커가 하고, 아래 폴링이 상태를 따라간다.
  const requestRun = async () => {
    setRequestingRun(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/runs`, { method: "POST" })
      if (!resp.ok) await throwOnError(resp, "정밀 진단을 요청하지 못했습니다.")
      setLatestRun(await resp.json())
    } catch (err) {
      console.error("Failed to request quality run", { datasetId, err })
      notifyError(err, "정밀 진단을 요청하지 못했습니다.")
    } finally { setRequestingRun(false) }
  }

  const cancelRun = async (run: QualityRun) => {
    const what = run.kind === "PROFILE" ? "프로파일" : "정밀 진단"
    if (run.status === "RUNNING" && !(await askConfirm({
      title: `${what} 중지`,
      description: `실행 중인 ${what}을(를) 멈출까요? 품질 워커가 진행 중인 쿼리를 멈춥니다. 이번 실행의 결과는 남지 않고, 직전 결과가 그대로 유지됩니다.`,
      confirmText: "중지",
      cancelText: "계속 실행",
    }))) return
    const runId = run.id
    try {
      const resp = await authFetch(`${BASE}/runs/${runId}/cancel`, { method: "POST" })
      if (!resp.ok) await throwOnError(resp, "진단을 중지하지 못했습니다.")
      setLatestRun(await resp.json())
    } catch (err) {
      console.error("Failed to cancel quality run", { runId, err })
      notifyError(err, "진단을 중지하지 못했습니다.")
    }
  }

  // 대기·실행 중인 진단이 있으면 3초마다 상태를 읽고, 끝나면 결과·점수를 다시 불러온다.
  const activeRunId = isActiveRun(latestRun) ? latestRun!.id : null
  useEffect(() => {
    if (activeRunId === null) return
    const timer = setInterval(async () => {
      try {
        const resp = await authFetch(`${BASE}/runs/${activeRunId}`)
        if (!resp.ok) return
        const run: QualityRun = await resp.json()
        setLatestRun(run)
        if (!isActiveRun(run)) await fetchData()
      } catch (err) {
        console.error("Failed to poll quality run", { activeRunId, err })
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [activeRunId, fetchData])

  // 데이터 프로파일링(컬럼별 NULL/고유값/최소·최대 통계) — 정밀 진단과 같은 대기열에 넣고 품질 워커가 수집한다.
  // 끝나면 위 폴링이 프로파일 · 행 수를 다시 불러온다.
  const runProfile = async () => {
    setProfiling(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/runs`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: "PROFILE" }),
      })
      if (!resp.ok) await throwOnError(resp, "프로파일을 요청하지 못했습니다.")
      setLatestRun(await resp.json())
    } catch (err) {
      console.error("Failed to request profiling", { datasetId, err })
      notifyError(err, "프로파일을 요청하지 못했습니다.")
    } finally { setProfiling(false) }
  }
  const runActive = isActiveRun(latestRun)
  const profileRun = latestRun?.kind === "PROFILE"

  // 규칙 대화상자의 분류 — 서버 분류 전체와 "분류 없음"(정밀 진단 대상이 아닌 유형)
  const categories = ruleCategories(indicators)
  const currentCategory = categories.find((c) => c.key === category)
  // 분류를 바꾸면 그 분류에 지금 기준 방식이 있으면 그대로, 없으면 첫 방식 — 방식이 바뀌면 앞 방식의 기대값은 뜻이 달라지므로 비운다
  const pickCategory = (key: string) => {
    const next = typeForCategory(categories.find((c) => c.key === key), checkType)
    setCategory(key)
    pickCheckType(next)
  }
  const pickCheckType = (next: string) => {
    if (next !== checkType) {
      setExpectedValue("")
      setDateCustom(false)
    }
    setCheckType(next)
  }

  // 다이얼로그 입력값으로 규칙 등록/수정 후 폼 리셋. 입력 검증은 disabled 속성으로 처리.
  const saveRule = async () => {
    // 품질 지표에 연결된 규칙은 유형·기준값·심각도·분류를 지표가 정한다 — 이름·컬럼·임계값만 보낸다
    const payload = editingLinked
      ? { rule_name: ruleName, column_name: columnName || null, threshold: Number(threshold) }
      : {
        rule_name: ruleName,
        check_type: checkType,
        column_name: columnName || null,
        expected_value: expectedValue || null,
        threshold: Number(threshold),
        severity,
        domain: domainForCategory(categories.find((c) => c.key === category), checkType, indicators),
      }
    try {
      const resp = editingRuleId != null
        ? await authFetch(`${BASE}/rules/${editingRuleId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
        : await authFetch(`${BASE}/rules`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dataset_id: datasetId, ...payload }),
        })
      if (!resp.ok) await throwOnError(resp, "규칙을 저장하지 못했습니다.")
    } catch (err) {
      // 실패 시 다이얼로그·입력값을 유지해 수정 후 다시 저장할 수 있게 한다
      console.error("Failed to save quality rule", { datasetId, ruleName, err })
      notifyError(err, "규칙을 저장하지 못했습니다.")
      return
    }
    notifySuccess(editingRuleId != null ? `규칙 '${ruleName}' 을(를) 수정했습니다.` : `규칙 '${ruleName}' 을(를) 추가했습니다.`)
    setAddOpen(false)
    setEditingRuleId(null)
    setRuleName(""); setColumnName(""); setExpectedValue(""); setThreshold("100")
    setCategory("REQUIRED"); setCheckType("NOT_NULL")
    fetchData()
  }

  // 품질 지표와의 연결을 끊는다 — 규칙은 지금 기준 그대로 이 데이터셋에서 직접 정한 규칙이 된다.
  const unlinkRule = async (rule: QualityRule) => {
    if (!(await askConfirm({
      title: "지표 연결 끊기",
      description: `'${rule.rule_name}' 규칙을 품질 지표 '${rule.definition_name}' 에서 떼어 낼까요? 규칙과 진단 결과는 그대로 남고, 앞으로 지표를 바꿔도 이 규칙은 바뀌지 않습니다.`,
      confirmText: "연결 끊기",
    }))) return
    try {
      const resp = await authFetch(`${BASE}/rules/${rule.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definition_id: null }),
      })
      if (!resp.ok) await throwOnError(resp, "연결을 끊지 못했습니다.")
      notifySuccess("품질 지표와의 연결을 끊었습니다.")
      setAddOpen(false)
      setEditingRuleId(null)
      fetchData()
    } catch (err) {
      console.error("Failed to unlink quality rule", { ruleId: rule.id, err })
      notifyError(err, "연결을 끊지 못했습니다.")
    }
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
    setCategory(categoryOfRule(rule, categories))
    setDateCustom(false)
    setAddOpen(true)
  }

  // 프로파일 기반 규칙 추천 — 후보를 받아 선택 생성
  const openRecommendations = async () => {
    setRecsLoading(true)
    setRecsOpen(true)
    try {
      const resp = await authFetch(`${BASE}/datasets/${datasetId}/rules/recommendations`)
      if (!resp.ok) await throwOnError(resp, "추천 규칙을 불러오지 못했습니다.")
      const data: RuleRecommendation[] = await resp.json()
      setRecs(data)
      setSelectedRecs(new Set(data.map((_, i) => i)))  // 기본 전체 선택
    } catch (err) {
      console.error("Failed to fetch rule recommendations", { datasetId, err })
      notifyError(err, "추천 규칙을 불러오지 못했습니다.")
    } finally { setRecsLoading(false) }
  }

  const createSelectedRecs = async () => {
    const chosen = recs.filter((_, i) => selectedRecs.has(i))
    let created = 0
    try {
      for (const r of chosen) {
        const resp = await authFetch(`${BASE}/rules`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dataset_id: datasetId, rule_name: r.rule_name, check_type: r.check_type,
            column_name: r.column_name, expected_value: r.expected_value,
            threshold: r.threshold, severity: r.severity,
          }),
        })
        if (!resp.ok) await throwOnError(resp, `추천 규칙(${r.rule_name})을 생성하지 못했습니다.`)
        created += 1
      }
      if (created > 0) notifySuccess(`추천 규칙 ${created}건을 추가했습니다.`)
    } catch (err) {
      console.error("Failed to create recommended rules", { datasetId, err })
      // 일부만 만들어졌으면 몇 건이 추가됐는지도 알린다
      notifyError(err, created > 0
        ? `추천 규칙 ${created}건을 추가한 뒤 나머지를 생성하지 못했습니다.`
        : "추천 규칙을 생성하지 못했습니다.")
    }
    setRecsOpen(false)
    fetchData()
  }

  const deleteRule = async (rule: QualityRule) => {
    if (
      !(await askConfirm({
        title: "규칙 삭제",
        description: `'${rule.rule_name}' 규칙을 삭제할까요? 이 규칙의 진단 결과 이력도 함께 삭제됩니다.`,
        confirmText: "삭제",
        destructive: true,
      }))
    ) return
    try {
      const resp = await authFetch(`${BASE}/rules/${rule.id}`, { method: "DELETE" })
      if (!resp.ok) await throwOnError(resp, "규칙을 삭제하지 못했습니다.")
      notifySuccess(`규칙 '${rule.rule_name}' 을(를) 삭제했습니다.`)
    } catch (err) {
      console.error("Failed to delete quality rule", { ruleId: rule.id, err })
      notifyError(err, "규칙을 삭제하지 못했습니다.")
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
                  <a href={datasetHref(u.id, "quality")} className="font-mono text-xs hover:underline">{u.name}</a>
                  <StatusBadge tone={datasetQualityStatus(u.quality_status).tone} size="sm" className="ml-2">
                    {datasetQualityStatus(u.quality_status).label}{u.quality_score != null && ` ${u.quality_score}%`}
                  </StatusBadge>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* ── 품질 진단 — 규칙으로 판정한다. 아래 데이터 프로파일(컬럼 통계)과는 다른 일이라 구역을 나눈다 ── */}
      <SectionHeading title="품질 진단"
        description="규칙으로 데이터가 기준을 지키는지 판정합니다. 결과가 품질 점수가 됩니다." />

      {/* Score bar + action buttons */}
      <div className="py-1">
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
                <span >/ 전체 {score.total_rules}개</span>
              </div>
            )}
            <ScoreTrend history={scoreHistory} />
            <div className="flex flex-col items-end gap-0.5 text-xs whitespace-nowrap">
              {score?.scored_at && <span>마지막 진단 {timeAgo(score.scored_at)}</span>}
              {/* 주기 진단은 관리 › 데이터 품질 › 품질 진단 작업에서 — 데이터셋 하나만 고를 수도 있다(예전 데이터셋 주기는 작업으로 옮겼다) */}
              <a href="/dashboard/quality-runs?tab=jobs" className="hover:text-foreground hover:underline"
                title="정해진 주기로 진단하려면 관리 › 데이터 품질 › 품질 진단 작업에 이 데이터셋(또는 카탈로그 · 스키마)을 대상으로 등록합니다.">
                주기 진단 설정 →
              </a>
            </div>
            <div className="flex items-center gap-2">
              <ActionTip title={engine?.engine === "DIRECT" ? "정밀 진단 — 원천 DB 를 직접 읽어 전체 데이터를 평가" : "정밀 진단 — 원본 전체 데이터를 Trino 로 평가"}
                blocked={runBlocked ?? (engine == null ? "정밀 진단 경로를 확인하는 중입니다."
                  : !engine.available ? (engine.reason ?? "이 데이터셋은 정밀 진단할 수 없습니다.") : null)}
                lines={[
                  engine?.engine === "DIRECT"
                    ? "카탈로그에 Trino 가 연결돼 있지 않아 품질 워커가 원천 DB 를 직접 읽어(읽기 전용) 규칙마다 진단 건수 · 오류 건수 · 오류율을 남깁니다. Trino 함수 · 커스텀 SQL 규칙은 Trino 연결이 필요해 건너뜁니다."
                    : "테이블의 모든 행을 Trino 로 집계해 규칙마다 진단 건수 · 오류 건수 · 오류율을 남깁니다. 커스텀 Python 규칙은 원본 DB 를 직접 읽을 수 있으면 함께 평가하고, 아니면 건너뜁니다.",
                  "실행은 품질 워커가 대기열에서 가져가 하며, 테이블 크기에 따라 수 분이 걸릴 수 있습니다. 진행 상황은 아래 상태 카드에 나옵니다.",
                  ...(engine?.available && engine.engine_table
                    ? [`대상: ${engine.engine_table} (${engine.engine === "DIRECT" ? "원천 직접" : "Trino"}: ${engine.engine_datasource_name ?? "-"})`] : []),
                ]}>
                <Button variant="outline" size="sm" onClick={requestRun}
                  disabled={!canRun || !engine?.available || requestingRun || runActive}>
                  <Play className="h-3.5 w-3.5 mr-1" />
                  {runActive ? (profileRun ? "프로파일 중…" : "진단 중…") : "정밀 진단"}
                </Button>
              </ActionTip>
              {canManageRules && (
                <>
                  <ActionTip {...ASSIGN_TIP}>
                    <Button variant="outline" size="sm" onClick={() => setAssignOpen(true)}>
                      <Columns3 className="h-3.5 w-3.5 mr-1" />지표 할당
                    </Button>
                  </ActionTip>
                  <ActionTip {...ADD_TIP}>
                    <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
                      <Plus className="h-3.5 w-3.5 mr-1" />규칙 추가
                    </Button>
                  </ActionTip>
                  <ActionTip {...RECOMMEND_TIP}>
                    <Button variant="outline" size="sm" onClick={openRecommendations}>
                      <Sparkles className="h-3.5 w-3.5 mr-1" />규칙 추천
                    </Button>
                  </ActionTip>
                </>
              )}
            </div>
          </div>
      </div>

      {/* 정밀 진단 실행 상태 — 프로파일 실행은 아래 데이터 프로파일 구역에 보인다(둘은 같은 대기열에서 돈다) */}
      {latestRun && !profileRun && (
        <RunStatusCard run={latestRun} engine={engine} canCancel={canRun} onCancel={() => void cancelRun(latestRun)} />
      )}

      {/* Results table */}
      {results.length > 0 && (
        <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">상태</TableHead>
                  <TableHead>규칙</TableHead>
                  <TableHead className="w-32">기준 방식</TableHead>
                  <TableHead className="w-32">컬럼</TableHead>
                  <TableHead className="w-28">실제 값</TableHead>
                  <TableHead>상세</TableHead>
                  <TableHead className="w-20">심각도</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.map(r => (
                  <Fragment key={r.rule_id}>
                    <TableRow>
                      <TableCell>
                        {r.passed === null
                          ? <span title="미평가 — 점수에서 제외"><HelpCircle className="h-4 w-4 text-muted-foreground" /></span>
                          : r.passed === "true"
                            ? <CheckCircle className="h-4 w-4 text-green-500" />
                            : <XCircle className="h-4 w-4 text-red-500" />}
                      </TableCell>
                      <TableCell className="font-medium text-sm">{r.rule_name}</TableCell>
                      <TableCell><Badge variant="outline" title={r.check_type ?? undefined}>{checkTypeLabel(r.check_type)}</Badge></TableCell>
                      <TableCell className="text-sm">{r.column_name || "—"}</TableCell>
                      <TableCell className="text-sm font-mono">{r.actual_value || "—"}</TableCell>
                      <TableCell className="text-sm whitespace-normal break-words">
                        {r.detail}
                        {r.failed_samples && r.failed_samples.length > 0 && (
                          <button
                            type="button"
                            onClick={() => setExpandedResultId(expandedResultId === r.rule_id ? null : r.rule_id)}
                            className="ml-2 inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
                          >
                            {expandedResultId === r.rule_id ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                            위반 샘플 {r.failed_samples.length}행
                          </button>
                        )}
                      </TableCell>
                      <TableCell><SeverityBadge severity={r.severity || "INFO"} /></TableCell>
                    </TableRow>
                    {expandedResultId === r.rule_id && r.failed_samples && (
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
                                        {v ?? <span className="italic">NULL</span>}
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
        </div>
      )}

      {/* Rules list */}
      {rules.length > 0 && (
        <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium">규칙 ({rules.length})</span>
            </div>
            <div className="space-y-2">
              {rules.map(rule => {
                const meta = ruleListMeta(rule)
                return (
                <div key={rule.id} className={`flex items-center justify-between text-sm border rounded px-3 py-2 ${meta.inactive ? "bg-muted/40" : ""}`}>
                  <div className={`flex items-center gap-3 ${meta.inactive ? "opacity-60" : ""}`}>
                    {/* 분류 → 기준 방식 — 데이터 품질 메뉴의 품질 지표와 같은 순서 */}
                    {indicatorName(rule.indicator) ? (
                      <Badge variant="secondary" title="이 규칙의 진단 기준(공공데이터 관리지침) — 품질 진단 작업·리포트가 이 이름으로 묶습니다">
                        {indicatorName(rule.indicator)}
                      </Badge>
                    ) : (
                      <Badge variant="secondary" title="관리지침 진단 기준 밖의 규칙 — 진단 기준을 고른 진단 · 품질 진단 작업의 진단 기준별 집계 · 리포트에는 들어가지 않습니다(진단 기준을 고르지 않은 정밀 진단은 평가)">
                        진단 기준 없음
                      </Badge>
                    )}
                    <Badge variant="outline" title={rule.check_type}>{checkTypeLabel(rule.check_type)}</Badge>
                    <span className="font-medium">{rule.rule_name}</span>
                    {rule.definition_id != null ? (
                      <Badge variant="outline" className="gap-1" title={`품질 지표 '${rule.definition_name}' 를 할당한 규칙 — 기준은 지표에서 바꿉니다`}>
                        <Link2 className="h-3 w-3" />{rule.definition_name}
                      </Badge>
                    ) : (
                      <Badge variant="outline" 
                        title="이 데이터셋에서 직접 정의한 규칙 — 품질 지표와 연결돼 있지 않아 기준은 이 규칙에서 바꿉니다">
                        직접 정의
                      </Badge>
                    )}
                    {rule.column_name && <span className="font-mono">{rule.column_name}</span>}
                    {meta.threshold != null && <span >임계값: {meta.threshold}%</span>}
                    {meta.inactive && (
                      <Badge variant="secondary" title="비활성 규칙 — 진단·결과·점수에서 제외됩니다">비활성</Badge>
                    )}
                    {!meta.inactive && engine?.available && skippedOnPath(rule.check_type, engine.check_types) && (
                      <StatusBadge tone="warning" title={pathSkipReason(engine.engine, rule.check_type)}>정밀 진단 제외</StatusBadge>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={rule.severity} />
                    <Tip label="진단 쿼리 보기"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setSqlRule(rule)}
                      aria-label="진단 쿼리 보기">
                      <Code2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button></Tip>
                    {canManageRules && (
                      <>
                        <Tip label="규칙 편집"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEditRule(rule)} aria-label="규칙 편집">
                          <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                        </Button></Tip>
                        <Tip label="규칙 삭제"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => deleteRule(rule)} aria-label="규칙 삭제">
                          <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                        </Button></Tip>
                      </>
                    )}
                  </div>
                </div>
                )
              })}
            </div>
        </div>
      )}

      {/* Empty state */}
      {rules.length === 0 && results.length === 0 && !loading && (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
            <Shield className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm">
              {canManage
                ? "정의된 규칙이 없습니다. 품질 지표를 할당하거나 규칙을 추가한 뒤 진단을 실행하세요."
                : "정의된 규칙이 없습니다. 규칙 추가와 진단은 데이터셋의 소유자 또는 관리자가 합니다."}
            </p>
            <div className="flex gap-2">
              {canManageRules && (
                <>
                  <ActionTip {...ASSIGN_TIP}>
                    <Button variant="outline" size="sm" onClick={() => setAssignOpen(true)}>
                      <Columns3 className="h-3.5 w-3.5 mr-1" />지표 할당
                    </Button>
                  </ActionTip>
                  <ActionTip {...ADD_TIP}>
                    <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
                      <Plus className="h-3.5 w-3.5 mr-1" />규칙 추가
                    </Button>
                  </ActionTip>
                </>
              )}
            </div>
        </div>
      )}

      {/* ── 데이터 프로파일 — 컬럼 통계. 규칙 판정이 아니다 ── */}
      <SectionHeading title="데이터 프로파일"
        description="컬럼별 통계(NULL · 고유값 · 최소/최대 · 최빈값)로 데이터의 분포를 봅니다. 규칙 판정이 아니며 품질 점수를 바꾸지 않습니다. 규칙 추천이 이 통계를 씁니다." />
      <div className="overflow-hidden rounded-md border">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
            {profileMeta ? (
              <span >
                {profileMeta.row_count.toLocaleString()}행 · {timeAgo(profileMeta.profiled_at)} 프로파일
              </span>
            ) : (
              <span className="text-muted-foreground">{loading ? "불러오는 중…" : "아직 프로파일이 없습니다."}</span>
            )}
            {canRun && (
              <DisabledReason className="ml-auto" reason={!engine?.available ? (engine?.reason ?? "이 데이터셋은 품질 워커로 프로파일할 수 없습니다.") : runActive ? "진행 중인 정밀 진단 · 프로파일이 끝나면 실행할 수 있습니다." : undefined}><Tip label={engine?.available ? `품질 워커가 ${engine.engine === "DIRECT" ? "원천 DB 를 직접" : "Trino 로"} 컬럼 통계를 수집하고 데이터셋의 행 수를 갱신합니다.` : undefined}><Button variant="outline" size="sm" className="ml-auto" onClick={runProfile}
                disabled={profiling || runActive || !engine?.available}
               >
                {profiling || (runActive && profileRun) ? "프로파일링…" : profileMeta ? "프로파일 갱신" : "프로파일 실행"}
              </Button></Tip></DisabledReason>
            )}
          </div>
          {latestRun && profileRun && (
            <div className="border-t">
              <RunStatusCard inline run={latestRun} engine={engine} canCancel={canRun} onCancel={() => void cancelRun(latestRun)} />
            </div>
          )}
          {profile.length > 0 && (
            <Table className="border-t">
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
                    <TableCell className="text-sm">{cp.column_type}</TableCell>
                    <TableCell className={`text-sm ${cp.null_percent > 5 ? "text-red-500 font-medium" : ""}`}>
                      {cp.null_percent}%
                    </TableCell>
                    <TableCell className="text-sm">{cp.unique_percent}%</TableCell>
                    {isBinaryColumnType(cp.column_type) ? (
                      <TableCell colSpan={4} className="text-sm italic">
                        바이너리 — 통계 생략
                      </TableCell>
                    ) : (
                      <>
                        {/* 긴 문자열 최소/최대(예: 영화 설명)가 표를 넓혀 페이지 전체가 가로로 넘치지 않도록 말줄임 — 전체 값은 title */}
                        <TableCell className="max-w-56 truncate text-sm font-mono" title={cp.min_value ?? undefined}>{cp.min_value ?? "—"}</TableCell>
                        <TableCell className="max-w-56 truncate text-sm font-mono" title={cp.max_value ?? undefined}>{cp.max_value ?? "—"}</TableCell>
                        <TableCell className="text-sm font-mono">{cp.mean_value?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell className="text-sm">
                          {cp.top_values && cp.top_values.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {cp.top_values.map((tv) => (
                                <span key={tv.value} className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                                  {tv.value}
                                  <span className="ml-1">×{tv.count.toLocaleString()}</span>
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
          )}
      </div>

      {/* Add Rule Dialog */}
      {/* 도움말 — 진단·프로파일 수행 방법 (접이식) */}
      <div className="rounded-md border">
          <button
            type="button"
            onClick={() => setHelpOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <HelpCircle className="h-4 w-4" />
            도움말 — 품질 진단과 데이터 프로파일은 어떻게 동작하나요?
            {helpOpen ? <ChevronDown className="h-4 w-4 ml-auto" /> : <ChevronRight className="h-4 w-4 ml-auto" />}
          </button>
          {helpOpen && (
            <div className="border-t px-5 py-4 text-sm space-y-4">
              <div>
                <p className="font-semibold text-foreground mb-1">권장 순서</p>
                <p>
                  ① 아래 <b>데이터 프로파일</b> 구역에서 프로파일을 실행해 현재 데이터의 통계를 봅니다 → ② <b>규칙 추가</b>(또는 규칙 추천)로
                  기대 조건을 정의합니다 → ③ <b>정밀 진단</b>으로 규칙을 평가합니다. 주기적으로 돌리려면
                  관리 › 데이터 품질의 품질 진단 작업에 등록합니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">데이터 프로파일</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li>정밀 진단처럼 <b>품질 워커</b>가 실행합니다 — Trino 가 연결돼 있으면 Trino 로, 없으면 원천 DB 에 직접 집계 쿼리를 내
                    <b> 전체 행 수</b>와 컬럼별 <b>NULL 수 · 고유값 수 · 최소/최대 · 평균 · 최빈값</b>(고유값 20개 이하 컬럼)을 수집합니다.</li>
                  <li>끝나면 데이터셋의 <b>행 수</b>도 갱신합니다(정밀 진단도 전체 행 수로 갱신합니다). 통계를 내지 못한 컬럼은 이름 · 타입만 남고
                    실행 상태에 &quot;일부 실패&quot; 로 나옵니다.</li>
                  <li>결과는 실행 시점의 스냅숏으로 저장되며, 데이터 프로파일 구역의 표에 최신 결과가 표시됩니다.</li>
                  <li>프로파일은 통계일 뿐 규칙 판정이 아닙니다 — 실행해도 품질 점수는 바뀌지 않습니다.</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">정밀 진단</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li>규칙별 통과/실패가 기록되고, <b>품질 점수 = 통과 규칙 ÷ 전체 규칙</b>(%)으로
                    산출되어 데이터셋 헤더의 품질 표시와 연동됩니다. 이번에 평가하지 않은 규칙은 직전 결과를 이어받습니다.</li>
                  <li>프로파일을 쓰지 않고 원본 전체를 평가해 진단 건수·오류 건수를 냅니다. 카탈로그에 Trino 가
                    연결돼 있으면 Trino 로 집계하고, 없으면 품질 워커가 원천 DB(PostgreSQL·MySQL·MariaDB·Oracle·SQL Server)를 직접 읽습니다.
                    Trino 함수·커스텀 SQL 은 Trino 경로에서만 평가하고, 커스텀 Python 은 원천 DB 를 직접 읽을 수 있을 때(Trino 경로라도) 평가하며, 경로에서 빠지는
                    규칙에는 <b>정밀 진단 제외</b> 표시가 붙습니다.</li>
                  <li>주기적으로 진단하려면 관리 › 데이터 품질 › <b>품질 진단 작업</b>에 이 데이터셋(또는 카탈로그 · 스키마)을 대상으로 등록합니다.</li>
                  <li>진단 · 프로파일 · 규칙을 바꾸는 버튼은 <b>데이터셋의 소유자 또는 관리자</b>만 씁니다. 점수 · 추세 · 결과는 누구나 봅니다.</li>
                </ul>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">규칙 유형</p>
                <ul className="list-disc pl-5 space-y-0.5">
                  <li><b>NOT_NULL</b> — 컬럼에 NULL 이 없어야 함 / <b>UNIQUE</b> — 모든 값이 고유해야 함</li>
                  <li><b>MIN_VALUE / MAX_VALUE</b> — 컬럼 값이 지정 범위 안이어야 함</li>
                  <li><b>ROW_COUNT</b> — 전체 행 수가 기댓값 조건을 만족해야 함</li>
                  <li><b>BROKEN_TEXT / WHITESPACE</b> — 글자 깨짐, 값 앞뒤 공백·제어 문자·금지 문자 / <b>LOGICAL / FORMULA</b> —
                    논리관계(IF … THEN …), 계산식(합계 = 공급가 + 부가세). 정밀 진단으로 평가합니다.</li>
                  <li><b>FUNCTION</b> — Trino 에 미리 등록한 함수(UDF)로 값 검증. 기대값은 함수 이름. 정밀 진단으로 평가합니다.</li>
                  <li><b>DUMMY_VALUE / CONTINUITY</b> — AI-Ready 품질 지표: 더미 값(999999 · -999 · N/A) 비율, 날짜 컬럼의 빠진 기간(일 · 월 단위
                    시계열 공백). 정밀 진단으로 평가합니다.</li>
                  <li><b>DATE_FORMAT / COLUMN_COMPARE / REFERENTIAL</b> — 날짜 형식, 컬럼 간 순서(시작일 ≤ 종료일), 참조관계.
                    <b>정밀 진단</b>으로 평가합니다(참조관계는 Trino 경로면 같은 Trino, 원천 직접 경로면 같은 원본 데이터 소스의 데이터셋만).</li>
                  <li><b>FRESHNESS</b> — 최신 데이터가 지정 기간 안에 적재되었어야 함 (적재·수정 시각 컬럼의 최댓값 기준 — 컬럼 필수)</li>
                  <li><b>ACCEPTED_VALUES / REGEX</b> — 허용 값 목록·패턴 검사.</li>
                  <li><b>CUSTOM_SQL</b> — 직접 작성한 SELECT 쿼리로 평가 (조인·집계 등 교차 검증).
                    첫 행 첫 컬럼 = 위반 건수(0이면 통과) 규약이며, Trino 가 연결된 데이터셋의 정밀 진단에서 Trino 로 실행합니다.</li>
                  <li><b>CUSTOM_PYTHON</b> — Python 플러그인 함수로 평가 (통계 검정 등 SQL로 불가능한 로직).
                    아래 작성 방법 참고. 정밀 진단에서 원천 DB 를 직접 읽을 수 있으면(Trino 경로라도) 품질 워커가 실행합니다.</li>
                </ul>
                <p className="mt-1">
                  심각도는 <b>치명</b>(데이터 사용 불가 수준) · <b>경고</b>(주의 필요) · <b>정보</b>(참고)로
                  구분해 규칙마다 지정합니다.
                </p>
              </div>

              <div>
                <p className="font-semibold text-foreground mb-1">커스텀 Python 체크 작성 방법 (custom_checks)</p>
                <p className="mb-1">
                  품질 워커의 <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">custom_checks/</code> 디렉터리(설정
                  <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">direct.checks_dir</code>)에 체크 함수를 작성하고,
                  규칙의 플러그인 설정(JSON)으로 연결합니다.
                </p>
                <p className="mb-1">① 체크 함수 — <b>(통과 여부, 실제 값, 상세 설명) 3-튜플</b>을 반환:</p>
                <pre className="rounded-md border bg-muted/40 p-3 text-xs font-mono overflow-x-auto whitespace-pre">{`# custom_checks/rental_checks.py (품질 워커 설치 디렉터리)
def rental_duration_outlier(df, params):
    """대여 기간 IQR 이상치 비율 검사."""
    max_pct = float(params.get("max_outlier_pct", 1.0))
    # df 는 테이블 전체(스키마의 모든 컬럼)의 pandas DataFrame
    ...  # 진단 로직
    return passed, f"{pct:.2f}%", f"이상치 {n}건 ({pct:.2f}%, 허용 {max_pct}%)"`}</pre>
                <p className="mt-2 mb-1">② 규칙 등록 — 진단 유형 <b>CUSTOM_PYTHON</b>, 플러그인 설정(JSON):</p>
                <pre className="rounded-md border bg-muted/40 p-3 text-xs font-mono overflow-x-auto whitespace-pre">{`{"module": "rental_checks",
 "fn": "rental_duration_outlier",
 "params": {"max_outlier_pct": 1.0}}`}</pre>
                <ul className="list-disc pl-5 space-y-0.5 mt-2">
                  <li>평가는 <b>정밀 진단</b>에서 품질 워커가 원본 DB(PostgreSQL·MySQL·MariaDB·Oracle·SQL Server)를 직접 읽어 합니다 — Trino 로 진단하는 데이터셋도 원본이 이 유형이면 원본을 따로 읽어 함께 평가합니다. 행은 워커의
                    <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">direct.max_rows</code> 까지 읽습니다.</li>
                  <li>모듈·함수 이름은 영문 식별자만 허용되며, 플러그인 코드는 리뷰를 거친
                    신뢰 코드만 워커에 배포하세요 (워커가 그대로 실행합니다)</li>
                  <li>자세한 규약은 품질 워커의 <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">README.md</code> 참고</li>
                </ul>
              </div>
            </div>
          )}
      </div>

      {/* 규칙 추천 다이얼로그 — 프로파일 기반 후보를 선택해 일괄 생성 */}
      <Dialog open={recsOpen} onOpenChange={setRecsOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>규칙 추천 (프로파일 기반)</DialogTitle></DialogHeader>
          {recsLoading ? (
            <p className="py-8 text-center text-sm text-muted-foreground">프로파일 분석 중…</p>
          ) : recs.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              추천할 규칙이 없습니다 — 프로파일을 먼저 수집했는지, 이미 같은 규칙이 등록돼 있지 않은지 확인하세요.
            </p>
          ) : (
            <>
              <p className="text-xs">
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
                        <Badge variant="outline" title={r.check_type}>{checkTypeLabel(r.check_type)}</Badge>
                        <span className="font-medium">{r.rule_name}</span>
                        {r.column_name && <span className="font-mono text-xs">{r.column_name}</span>}
                        <SeverityBadge severity={r.severity} />
                      </div>
                      <p className="mt-0.5 text-xs">{r.reason}
                        {r.expected_value && <span className="ml-1 font-mono">기대값: {LIST_CHECK_TYPES.has(r.check_type) ? decodeList(r.expected_value).join(" · ") : r.expected_value}</span>}
                      </p>
                    </div>
                  </label>
                ))}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs">{selectedRecs.size} / {recs.length} 선택</span>
                <Button onClick={createSelectedRecs} disabled={selectedRecs.size === 0}>
                  선택한 규칙 {selectedRecs.size}개 추가
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
          setCategory("REQUIRED"); setCheckType("NOT_NULL")
          setDateCustom(false)
        }
      }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{editingRuleId != null ? "규칙 수정" : "규칙 추가"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            {editingLinked && (
              <div className="rounded border bg-muted/40 px-3 py-2 text-xs">
                품질 지표 <b>{editingLinked.definition_name}</b> 을(를) 할당한 규칙입니다. 유형 · 기준값 · 심각도는 지표에서 정하며,
                여기서는 이름 · 컬럼 · 임계값만 바꿀 수 있습니다.
                <Button variant="link" size="sm" className="h-auto px-1 py-0 text-xs" onClick={() => void unlinkRule(editingLinked)}>
                  연결 끊기
                </Button>
              </div>
            )}
            <div><Label className="text-sm">규칙 이름</Label><Input value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="amount is not null" className="h-9" /></div>
            {/* 데이터 품질 > 품질 지표와 같은 순서 — 분류(무엇을 보는가) → 기준 방식(어떻게 보는가) */}
            <div className="grid grid-cols-2 gap-3">
              <div><Label className="text-sm">진단 기준</Label>
                <QualityCategorySelect categories={categories} value={category} onChange={pickCategory}
                  disabled={editingLinked != null} />
              </div>
              <div><Label className="text-sm">기준 방식</Label>
                <QualityCheckTypeSelect checkTypes={currentCategory?.check_types ?? [checkType]} value={checkType}
                  onChange={pickCheckType} disabled={editingLinked != null || (currentCategory?.check_types.length ?? 0) < 2} />
              </div>
            </div>
            <div className="space-y-1 text-xs">
              {currentCategory && <p>{currentCategory.description}</p>}
              {CHECK_TYPE_META[checkType] && <p>{CHECK_TYPE_META[checkType].desc}</p>}
              {engine?.available && skippedOnPath(checkType, engine.check_types) && (
                <p className="text-amber-700">{pathSkipReason(engine.engine, checkType)}</p>
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
                    <SelectItem value="__none__">선택 안 함 (테이블 수준 — ROW_COUNT/LOGICAL)</SelectItem>
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
                {checkType === "CUSTOM_SQL" ? "진단 SQL" : checkType === "CUSTOM_PYTHON" ? "플러그인 설정 (JSON)" : "기대값"}
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
                      fontSize: MONACO_FONT_SIZE,
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
              ) : checkType === "DATE_FORMAT" ? (
                <div className="space-y-1.5">
                  <Select disabled={editingLinked != null}
                    value={dateCustom || (expectedValue.trim() !== "" && !listedDateFormat(expectedValue))
                      ? "__custom__" : (listedDateFormat(expectedValue) ?? "")}
                    onValueChange={(v) => {
                      if (v === "__custom__") setDateCustom(true)
                      else { setDateCustom(false); setExpectedValue(v) }
                    }}>
                    <SelectTrigger className="h-9"><SelectValue placeholder="날짜 형식 선택" /></SelectTrigger>
                    <SelectContent>
                      {DATE_FORMAT_CHOICES.map((c) => (
                        <SelectItem key={c.value} value={c.value}>
                          <span className="font-mono">{c.value}</span>
                          <span > — 예: {c.example}</span>
                        </SelectItem>
                      ))}
                      <SelectItem value="__custom__">직접 입력…</SelectItem>
                    </SelectContent>
                  </Select>
                  {(dateCustom || (expectedValue.trim() !== "" && !listedDateFormat(expectedValue))) && (
                    <>
                      <Input value={expectedValue} onChange={e => setExpectedValue(e.target.value)} disabled={editingLinked != null}
                        placeholder="예: DD/MM/YYYY 또는 YYYY.MM.DD HH24:MI" className="h-9 font-mono" />
                      <p className="text-xs">
                        <span className="font-mono">YYYY MM DD HH24 MI SS SSS</span>(밀리초) 와 구분자
                        (<span className="font-mono">- / . :</span> 공백 <span className="font-mono">T</span>)를 이어 적습니다.
                        <span className="font-mono"> HH:MM:SS</span> 로 적어도 됩니다.
                        같은 낱말은 한 번만 쓰고, 틀리면 저장할 때 알려 줍니다.
                      </p>
                    </>
                  )}
                </div>
              ) : LIST_CHECK_TYPES.has(checkType) ? (
                // 허용 값 · 더미 값 — 배지 입력, JSON 배열 문자열로 저장(값 안의 쉼표 보존, TODO-143)
                <TagInput mono value={decodeList(expectedValue)} onChange={(v) => setExpectedValue(encodeList(v) ?? "")}
                  placeholder={CHECK_TYPE_META[checkType]?.expectedHint ?? undefined} disabled={editingLinked != null} />
              ) : (
                <Input
                  value={expectedValue}
                  onChange={e => setExpectedValue(e.target.value)}
                  placeholder={CHECK_TYPE_META[checkType]?.expectedHint ?? "이 유형은 기대값을 사용하지 않습니다"}
                  disabled={CHECK_TYPE_META[checkType]?.expectedHint === null || editingLinked != null}
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
                <Select value={severity} onValueChange={setSeverity} disabled={editingLinked != null}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="BREAKING">BREAKING</SelectItem>
                    <SelectItem value="WARNING">WARNING</SelectItem>
                    <SelectItem value="INFO">INFO</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end"><DisabledReason reason={checkType === "FRESHNESS" && !columnName ? "최신성은 적재 · 수정 시각 컬럼을 골라야 합니다." : undefined}><Button onClick={saveRule} disabled={!ruleName.trim() || (checkType === "FRESHNESS" && !columnName)}
             >{editingRuleId != null ? "저장" : "규칙 추가"}</Button></DisabledReason></div>
          </div>
        </DialogContent>
      </Dialog>

      <QualityDatasetAssignDialog
        open={assignOpen} datasetId={datasetId} columns={columns}
        assigned={rules.flatMap((r) => r.definition_id != null && r.column_name
          ? [{ definition_id: r.definition_id, column_name: r.column_name }] : [])}
        onOpenChange={setAssignOpen} onAssigned={() => void fetchData()}
      />
      <QualitySqlPreviewDialog
        title={`${sqlRule?.rule_name ?? ""} — 진단 쿼리`}
        description={sqlRule?.definition_name ? `품질 지표 '${sqlRule.definition_name}' 를 할당한 규칙` : undefined}
        request={sqlRule ? {
          check_type: sqlRule.check_type, expected_value: sqlRule.expected_value, column_name: sqlRule.column_name,
          threshold: sqlRule.threshold, dataset_id: datasetId,
        } : null}
        onClose={() => setSqlRule(null)}
      />
    </div>
  )
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
    <div className="flex items-center gap-3" title="최근 진단 점수 추세 (최대 20회)">
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
      <div className="text-xs whitespace-nowrap">
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


/** 상대 시각 표기 — 알림 페이지와 동일 포맷. */
/** 품질 탭 안의 구역 제목 — 품질 진단과 데이터 프로파일을 눈으로 나눈다. */
function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div className="pt-1">
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="text-sm">{description}</p>
    </div>
  )
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "방금 전"
  if (mins < 60) return `${mins}분 전`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}시간 전`
  return `${Math.floor(hours / 24)}일 전`
}
