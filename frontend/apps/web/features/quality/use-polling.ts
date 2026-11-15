import { useCallback, useEffect, useRef } from "react"

/**
 * active 인 동안 ms 마다 fn 을 부른다. 한 번이 끝난 뒤에 다음을 잡으므로 겹치지 않고,
 * fn 이 실패해도(예외) 멈추지 않는다 — 목록 상태가 바뀌어야 다음 차례가 잡히던 방식은 요청 한 번의 실패로 멈췄다.
 */
export function usePolling(active: boolean, fn: () => Promise<unknown>, ms = 5000) {
  const latest = useRef(fn)
  useEffect(() => {
    latest.current = fn
  })
  useEffect(() => {
    if (!active) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        await latest.current()
      } catch {
        // 다음 차례에 다시 시도한다
      }
      if (!stopped) timer = setTimeout(tick, ms)
    }
    timer = setTimeout(tick, ms)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [active, ms])
}

/** 늦게 온 응답을 버리기 위한 순번 — begin() 이 준 함수가 false 면 그 사이 새 요청이 나갔다. */
export function useLatest() {
  const seq = useRef(0)
  return useCallback(() => {
    const mine = ++seq.current
    return () => mine === seq.current
  }, [])
}
