/**
 * 권한 관리(ops/permissions.adoc) 화면 캡처.
 *
 * 권한 표는 보기만 하고 저장하지 않는다(체크를 바꾸지 않는다).
 */
import { open, goto, settle, shot } from './lib.mjs';

const AREA = 'ops-permissions';
const { browser, page } = await open();

/** 제목(메뉴 권한 · 기능 권한 · 역할별 미리보기)이 들어 있는 카드. */
const card = title =>
  page.locator('[data-slot="card"]').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).last();

/** 카드가 머리글 바로 아래에 오도록 화면을 내린다(고정된 머리글 높이만큼 여유를 둔다). */
async function scrollToCard(title) {
  await card(title).evaluate(el => { el.style.scrollMarginTop = '80px'; el.scrollIntoView({ block: 'start' }); });
  await settle(page, 500);
}

try {
  await goto(page, '/dashboard/permissions');
  await page.getByRole('heading', { name: '역할별 미리보기' }).waitFor();
  await page.mouse.move(700, 30);
  await settle(page, 500);

  // 메뉴 권한 표 — 그룹 행 · 배지 · 역할별 체크박스
  await scrollToCard('메뉴 권한');
  await shot(page, `${AREA}/menu`);

  // 기능 권한 표 — 표가 화면 위쪽에 오도록 내린 모습
  await scrollToCard('기능 권한');
  await shot(page, `${AREA}/feature`);

  // 역할별 미리보기 — User 역할을 고른 카드만 잘라서
  const preview = card('역할별 미리보기');
  await preview.scrollIntoViewIfNeeded();
  await preview.getByRole('button', { name: 'User', exact: true }).click();
  await page.mouse.move(700, 30);
  await settle(page, 500);
  await shot(page, `${AREA}/preview`, { target: preview });
} finally {
  await browser.close();
}
