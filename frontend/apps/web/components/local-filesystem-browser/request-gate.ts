/**
 * 마지막 요청만 반영하기 위한 순번 게이트 (DEF-008).
 *
 * 목록 요청이 겹치면(장애 중 보낸 요청이 끝나기 전에 "다시 시도", 폴더를 빠르게 이동) 늦게 도착한 이전 요청의
 * 오류·목록이 최신 결과를 덮어쓴다. 요청마다 `begin()` 으로 받은 `isLatest()` 가 참일 때만 상태를 바꾼다.
 */
export function createRequestGate() {
  let seq = 0
  return {
    begin(): () => boolean {
      const id = ++seq
      return () => id === seq
    },
  }
}
