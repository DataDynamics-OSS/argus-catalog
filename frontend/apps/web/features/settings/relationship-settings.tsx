"use client"

import { useState } from "react"
import { GitBranch, Loader2, Network, RefreshCw } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@workspace/ui/components/card"

import { recomputeRelationships, resolveLineage } from "@/features/settings/api"

type Msg = { type: "success" | "error"; text: string } | null

// 쿼리 기반 분석 유지보수 — 컬럼 관계 재계산 + lineage dataset_id 해석.
export function RelationshipSettings() {
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState<Msg>(null)
  const [linRunning, setLinRunning] = useState(false)
  const [linMessage, setLinMessage] = useState<Msg>(null)

  const handleRecompute = async () => {
    if (!confirm("수집된 모든 쿼리 이력을 재분석해 컬럼 관계를 재구축합니다.\n기존 관계는 초기화 후 다시 쌓입니다. 계속할까요?")) {
      return
    }
    setRunning(true)
    setMessage(null)
    try {
      const r = await recomputeRelationships(true)
      const perPlatform = Object.entries(r.per_platform)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => `${k} ${v}`)
        .join(", ") || "분석된 이력 없음"
      const unresolved = r.unresolved_tables?.length
        ? ` · 미해석 테이블 ${r.unresolved_tables.length}개: ${r.unresolved_tables.slice(0, 8).join(", ")}${r.unresolved_tables.length > 8 ? " …" : ""}`
        : ""
      setMessage({
        type: "success",
        text: `재계산 완료: 쿼리 ${r.queries_analyzed}건 분석, 관계쌍 ${r.pairs_pushed}건 적재 (초기화 ${r.reset_deleted ?? 0}건) · ${perPlatform}${unresolved}`,
      })
    } catch (e) {
      console.error("Failed to recompute relationships", e)
      setMessage({ type: "error", text: e instanceof Error ? e.message : "재계산에 실패했습니다." })
    } finally {
      setRunning(false)
    }
  }

  const handleResolveLineage = async () => {
    setLinRunning(true)
    setLinMessage(null)
    try {
      const r = await resolveLineage()
      setLinMessage({
        type: "success",
        text: `lineage 해석 완료: ${r.resolved}건 데이터셋 연결, 미해석 ${r.remaining_unresolved}건 남음`,
      })
    } catch (e) {
      console.error("Failed to resolve lineage", e)
      setLinMessage({ type: "error", text: e instanceof Error ? e.message : "lineage 해석에 실패했습니다." })
    } finally {
      setLinRunning(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 컬럼 관계 — 사용 기반(JOIN 키) */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Network className="h-5 w-5" />
            컬럼 관계 분석
          </CardTitle>
          <CardDescription>
            실제 사용된 쿼리의 JOIN 패턴을 분석해 컬럼 간 사용 관계(암묵 FK/조인키)를 발견합니다.
            데이터 흐름(리니지)과는 별개이며, 데이터셋 상세의 &quot;관계&quot; 탭에서 확인할 수 있습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button variant="outline" onClick={handleRecompute} disabled={running}>
            {running ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            {running ? "재계산 중..." : "전체 재계산"}
          </Button>
          <p className="text-xs text-muted-foreground">
            수집된 모든 쿼리 이력(Trino/Hive/Impala/StarRocks)을 다시 분석해 관계를 재구축합니다.
            파서 개선이나 기능 도입 이전 이력을 일괄 반영할 때 사용하세요. 평소에는 쿼리 수집 시 자동으로 갱신됩니다.
          </p>
          {message && (
            <p className={`text-sm ${message.type === "success" ? "text-green-600" : "text-red-600"}`}>
              {message.text}
            </p>
          )}
        </CardContent>
      </Card>

      {/* 쿼리 lineage 해석 — 테이블명 → 데이터셋 연결 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            쿼리 lineage 해석
          </CardTitle>
          <CardDescription>
            수집된 쿼리에서 만든 데이터 흐름(lineage)의 테이블명을 실제 데이터셋과 연결합니다.
            연결되어야 데이터셋 상세의 리니지 그래프에 쿼리 기반 엣지가 표시됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button variant="outline" onClick={handleResolveLineage} disabled={linRunning}>
            {linRunning ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            {linRunning ? "해석 중..." : "lineage 전체 해석"}
          </Button>
          <p className="text-xs text-muted-foreground">
            데이터셋 생성 시 자동으로도 점증 해석되며, 이 버튼은 과거 이력을 일괄 백필합니다.
          </p>
          {linMessage && (
            <p className={`text-sm ${linMessage.type === "success" ? "text-green-600" : "text-red-600"}`}>
              {linMessage.text}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
