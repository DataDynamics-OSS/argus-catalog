/**
 * 관리 → 설정 (ops/settings.adoc) 화면 캡처.
 *
 * 화면을 열어 찍기만 하고 아무 것도 저장하지 않는다(전역 설정이라 값은 그대로 둔다).
 * 각 항목 그림은 사이드바를 뺀 본문 왼쪽 920px(쿼리 분석은 본문 전체 폭)을, 내용이 길면 끝까지 보이게 화면을 늘려 찍는다.
 */
import { open, goto, settle, shot, VIEWPORT } from './lib.mjs';

const P = 'ops-settings';
const SIDEBAR = 170; // 왼쪽 사이드바 폭
const { browser, page } = await open();

/** 설정 항목 하나를 열고, 내용이 다 보이도록 화면 높이를 늘린 뒤 본문 영역만 잘라 찍는다. */
async function tabShot(tab, name, width = 920) {
  await page.setViewportSize(VIEWPORT);
  await goto(page, `/dashboard/settings?tab=${tab}`);
  await settle(page, 800);
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const full = Math.max(VIEWPORT.height, height);
  if (full > VIEWPORT.height) {
    await page.setViewportSize({ width: VIEWPORT.width, height: full });
    await settle(page, 500);
  }
  await page.mouse.move(SIDEBAR + 600, 40);
  await shot(page, `${P}/${name}`, { clip: { x: SIDEBAR, y: 0, width, height: full } });
  await page.setViewportSize(VIEWPORT);
}

try {
  // 인증 — 인증 방식 선택(화면만 열고 저장하지 않는다)
  await tabShot('auth', 'auth');
  // CORS — 허용 Origin
  await tabShot('cors', 'cors');
  // 임베딩 — 상태 · 제공자 · 관리
  await tabShot('embedding', 'embedding');
  // LLM / AI — AI 메타데이터 상태 · 제공자 · 일괄 생성
  await tabShot('llm', 'llm');
  // AI 어시스턴트
  await tabShot('assistant', 'assistant');
  // 쿼리 분석 — 컬럼 관계 분석 · 쿼리 lineage 해석
  await tabShot('relationships', 'relationships', VIEWPORT.width - SIDEBAR);
  // 오브젝트 스토리지
  await tabShot('oci-model-registry', 'oci-model-registry');
  // 수집 서비스(metadata-sync) — 서비스 주소 · API 토큰 · 수집 실행 이력 보관 기간
  await tabShot('metadata-sync', 'metadata-sync');
  // 이메일(SMTP)
  await tabShot('email', 'email');
  // 알림
  await tabShot('notify', 'notify');
  // 변경관리
  await tabShot('change', 'change');
  // 캐시
  await tabShot('cache', 'cache');

  // 설정 변경 이력 — 오른쪽 위 "변경 이력" 을 눌러 연 모습
  await page.setViewportSize(VIEWPORT);
  await goto(page, '/dashboard/settings?tab=cache');
  await page.getByRole('button', { name: '변경 이력' }).click();
  await page.getByRole('dialog').waitFor();
  await settle(page, 600);
  await shot(page, `${P}/audit`);
} finally {
  await browser.close();
}
