"use client"

import { useCallback, useEffect, useState } from "react"

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

import { useAuth } from "@/features/auth"
import { createAIAgent } from "../api"
import { useAIAgents } from "./ai-agents-provider"
import { errorMessage } from "@/lib/notify"

const STATUS_OPTIONS = ["draft", "staging", "active", "blocked", "deprecated", "retired"]

export function AIAgentsAddDialog() {
  const { open, setOpen, refreshAgents } = useAIAgents()
  const { user } = useAuth()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [status, setStatus] = useState("draft")
  // 필수 — 소유자 이메일(기본: 로그인 사용자)·부서(기본: 로그인 사용자 부서)·분류
  const [ownerEmail, setOwnerEmail] = useState("")
  const [department, setDepartment] = useState("")
  const [category, setCategory] = useState("")
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (open !== "add") return
    setOwnerEmail((v) => v || user?.email || "")
    setDepartment((v) => v || user?.department || "")
  }, [open, user])

  const reset = useCallback(() => {
    setName("")
    setDisplayName("")
    setDescription("")
    setStatus("draft")
    setOwnerEmail("")
    setDepartment("")
    setCategory("")
    setTouched(false)
    setError(null)
  }, [])

  const ownerValid = ownerEmail.trim().includes("@")
  const canSubmit = !!name.trim() && ownerValid && !!department.trim() && !!category.trim()

  const handleSubmit = useCallback(async () => {
    setTouched(true)
    if (!canSubmit) return
    setSaving(true)
    setError(null)
    try {
      await createAIAgent({
        name: name.trim(),
        display_name: displayName.trim() || undefined,
        description: description.trim() || undefined,
        status,
        owner_email: ownerEmail.trim(),
        department: department.trim(),
        category: category.trim(),
      })
      reset()
      setOpen(null)
      await refreshAgents()
    } catch (e) {
      setError(errorMessage(e) ?? "AI 에이전트를 생성하지 못했습니다.")
    } finally {
      setSaving(false)
    }
  }, [name, displayName, description, status, ownerEmail, department, category, canSubmit, reset, setOpen, refreshAgents])

  return (
    <Dialog open={open === "add"} onOpenChange={() => setOpen(null)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>AI Agent 등록</DialogTitle>
        </DialogHeader>
        <div className="grid max-h-[70vh] gap-4 overflow-y-auto py-2">
          <div className="grid gap-2">
            <Label htmlFor="agent-name">이름 (고유 식별자)</Label>
            <Input
              id="agent-name"
              placeholder="예: cs.payment-refund-assistant"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="agent-display">표시명</Label>
            <Input
              id="agent-display"
              placeholder="예: 결제·환불 지원 어시스턴트"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="agent-desc">설명</Label>
            <Textarea
              id="agent-desc"
              placeholder="이 에이전트는 무엇을 하나요?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="grid gap-1.5 sm:col-span-3">
              <Label htmlFor="agent-owner">
                소유자 이메일 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="agent-owner"
                placeholder="예: hong@corp.example"
                value={ownerEmail}
                onChange={(e) => setOwnerEmail(e.target.value)}
                aria-invalid={touched && !ownerValid}
              />
              {touched && !ownerValid && <p className="text-xs text-destructive">이메일 형식으로 입력하세요.</p>}
            </div>
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor="agent-dept">
                부서 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="agent-dept"
                placeholder="예: 고객지원팀"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                aria-invalid={touched && !department.trim()}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="agent-cat">
                분류 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="agent-cat"
                placeholder="예: customer-service"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                aria-invalid={touched && !category.trim()}
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label>상태</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-xs text-muted-foreground">
            소유자·부서·분류는 필수입니다(책임자 관리 기준). 프레임워크·기저 모델 등 나머지 항목은 등록 후 상세 화면에서 편집할 수 있습니다.
          </p>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setOpen(null)} disabled={saving}>
              취소
            </Button>
            <Button onClick={handleSubmit} disabled={saving || !canSubmit}>
              {saving ? "생성 중..." : "생성"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
