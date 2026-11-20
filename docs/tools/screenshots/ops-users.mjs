/**
 * 사용자 관리(ops/users.adoc) 화면 캡처.
 *
 * 시드 사용자를 바꾸지 않는다 — 대화상자는 열어 보기만 하고 저장하지 않는다.
 */
import { open, goto, settle, shot } from './lib.mjs';

const AREA = 'ops-users';
const { browser, page } = await open();

/** 사용자명으로 목록의 행을 찾는다. */
const row = username => page.locator('tbody tr').filter({ has: page.getByText(username, { exact: true }) });

async function openList() {
  await goto(page, '/dashboard/users');
  await page.locator('tbody tr').first().waitFor();
  await page.mouse.move(700, 30); // 행 위에 마우스가 올라가 강조되지 않게
  await settle(page, 500);
}

try {
  // 사용자 목록
  await openList();
  await shot(page, `${AREA}/list`);

  // 오른쪽 위 "작업" 메뉴 — 활성화 · 비활성화 · 추가
  await page.getByRole('button', { name: '작업' }).click();
  await page.getByRole('menuitem', { name: '추가' }).waitFor();
  await page.waitForTimeout(300);
  await shot(page, `${AREA}/actions-menu`);

  // 사용자 추가 대화상자 — 예시 값을 채운 모습(저장하지 않는다)
  await page.getByRole('menuitem', { name: '추가' }).click();
  const add = page.getByRole('dialog', { name: '사용자 추가' });
  await add.waitFor();
  await add.locator('input[name="firstName"]').fill('길동');
  await add.locator('input[name="lastName"]').fill('홍');
  await add.locator('input[name="username"]').fill('gdhong');
  await add.locator('input[name="organization"]').fill('데이터다이나믹스');
  await add.locator('input[name="department"]').fill('데이터 플랫폼팀');
  await add.locator('input[name="email"]').fill('gdhong@data-dynamics.io');
  await add.getByRole('combobox').click();
  await page.getByRole('option', { name: '사용자', exact: true }).click();
  await settle(page, 500);
  await add.getByRole('heading', { name: '사용자 추가' }).click(); // 역할 칸의 포커스 테두리를 없앤다
  await page.evaluate(() => document.activeElement?.blur());
  await shot(page, `${AREA}/add-dialog`);
  await page.keyboard.press('Escape');
  await add.waitFor({ state: 'hidden' });

  // 행 오른쪽 ⋯ 버튼의 작업 메뉴 — 편집 · 비밀번호 변경 · 삭제
  await openList();
  await row('jypark').getByRole('button').last().click();
  await page.getByRole('menuitem', { name: '삭제' }).waitFor();
  await page.waitForTimeout(300);
  await shot(page, `${AREA}/row-actions`);

  // 사용자 정보 수정 대화상자(저장하지 않는다)
  await page.getByRole('menuitem').first().click();
  const edit = page.getByRole('dialog', { name: '사용자 정보 수정' });
  await edit.waitFor();
  await settle(page, 500);
  await page.evaluate(() => document.activeElement?.blur());
  await shot(page, `${AREA}/edit-dialog`);
} finally {
  await browser.close();
}
