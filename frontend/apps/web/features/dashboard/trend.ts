// 대시보드 신규 데이터셋 추이 — API 는 데이터가 있는 버킷만 돌려주므로(빈 시간·날짜 없음)
// 화면에서 0 으로 채워 시간축을 연속으로 만든다. 채우지 않으면 범주축에 데이터가 있는 버킷만
// 나란히 놓여 "어제 20:00 → 오늘 16:00" 이 바로 옆 막대로 보이고, 선 차트는 빈 날을 건너 이어진다.

export type TrendPoint = { date: string; count: number }

const HOUR_MS = 3_600_000
const pad2 = (n: number) => String(n).padStart(2, "0")

/** 로컬 날짜 YYYY-MM-DD */
export function localDateKey(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/**
 * 최근 24시간(현재 시 포함 24개) 시 버킷을 0 으로 채운다.
 * 버킷 키는 UTC ISO 시각(서버와 같은 정시 기준) — 같은 순간이면 표기와 관계없이 맞춘다.
 */
export function fillHourly(items: TrendPoint[], now: Date = new Date(), hours = 24): TrendPoint[] {
  const counts = new Map<number, number>()
  for (const it of items) {
    const t = new Date(it.date).getTime()
    if (!Number.isNaN(t)) counts.set(t, (counts.get(t) ?? 0) + it.count)
  }
  const end = Math.floor(now.getTime() / HOUR_MS) * HOUR_MS // 현재 UTC 정시
  const out: TrendPoint[] = []
  for (let i = hours - 1; i >= 0; i--) {
    const t = end - i * HOUR_MS
    out.push({ date: new Date(t).toISOString(), count: counts.get(t) ?? 0 })
  }
  return out
}

/** 오늘(브라우저 로컬 날짜) 포함 최근 N일을 0 으로 채운다. 날짜 키는 YYYY-MM-DD. */
export function fillDaily(items: TrendPoint[], days: number, now: Date = new Date()): TrendPoint[] {
  const counts = new Map<string, number>()
  for (const it of items) counts.set(it.date, (counts.get(it.date) ?? 0) + it.count)
  const out: TrendPoint[] = []
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i)
    const key = localDateKey(d)
    out.push({ date: key, count: counts.get(key) ?? 0 })
  }
  return out
}
