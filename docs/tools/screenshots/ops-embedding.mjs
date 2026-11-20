/**
 * 임베딩 운영 (ops/embedding.adoc) 화면 캡처.
 *
 * 관리 → 설정 → AI → 임베딩 화면을 열어 찍기만 한다(값을 바꾸거나 저장하지 않는다).
 */
import { open, goto, settle, shot, VIEWPORT } from './lib.mjs';

const SIDEBAR = 170; // 왼쪽 사이드바 폭
const { browser, page } = await open();
try {
  // 임베딩 설정 — 임베딩 상태 · 제공자 · 모델 · 임베딩 관리 (내용이 다 보이게 화면을 늘려 본문 920px 만 자른다)
  await goto(page, '/dashboard/settings?tab=embedding');
  await settle(page, 800);
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const full = Math.max(VIEWPORT.height, height);
  await page.setViewportSize({ width: VIEWPORT.width, height: full });
  await settle(page, 500);
  await page.mouse.move(SIDEBAR + 600, 40);
  await shot(page, 'ops-embedding/settings', { clip: { x: SIDEBAR, y: 0, width: 920, height: full } });
} finally {
  await browser.close();
}
