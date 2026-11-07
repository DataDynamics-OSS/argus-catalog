/**
 * 페더레이션(원격) 데이터셋 상세 — 정보 항목(capability)을 화면에 보일지 정하는 순수 함수.
 *
 * - ``displayFields`` : 소비자(허브) 표시 선택. null/undefined = 전부 표시.
 * - ``peerExposed``   : 노출자(peer) 가 노출하는 항목(상세 응답 ``peer_exposed``).
 *   null/undefined = 알 수 없음(구버전 백엔드·capabilities 조회 실패) → 예전처럼 보인다(하위 호환).
 *
 * 메타데이터 항목은 peer 가 이미 걸러 보내지만, 샘플·리니지는 별도 호출이라 탭이 항상 보였다.
 * peer 가 노출하지 않으면 탭을 숨긴다(예전에는 탭을 열면 오류·빈 화면이었다).
 */
export function remoteItemVisible(
  key: string,
  displayFields: readonly string[] | null | undefined,
  peerExposed: readonly string[] | null | undefined,
): boolean {
  if (displayFields && !displayFields.includes(key)) return false
  if (peerExposed && !peerExposed.includes(key)) return false
  return true
}
