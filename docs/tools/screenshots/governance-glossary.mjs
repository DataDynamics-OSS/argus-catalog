/**
 * 거버넌스 → 용어집 (pages/governance/glossary.adoc) 화면 캡처.
 *
 * 시드 분류(물류 · 반도체 · 영화 대여)와 용어는 그대로 두고, 임시 분류 it-고객 · it-고객-식별정보 와
 * 임시 용어 it-이탈률 · it-고객 생애 가치 를 만들어 찍은 뒤 지운다.
 * 다시 실행해도 안전하다(남아 있는 임시 항목은 시작할 때 지운다).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'governance-glossary';
const CAT = 'it-고객';
const SUB = 'it-고객-식별정보';
const TERM1 = 'it-이탈률';
const TERM2 = 'it-고객 생애 가치';
const TEMP = [CAT, SUB, TERM1, TERM2];

const token = await apiToken();

/** 임시 항목 삭제 — 용어 먼저, 그다음 하위 분류, 마지막에 상위 분류. */
async function removeTemp() {
  for (let pass = 0; pass < 3; pass++) {
    const all = (await api(token, 'GET', '/catalog/glossary')).filter(t => TEMP.includes(t.name));
    if (!all.length) return;
    all.sort((a, b) => (a.term_type === 'CATEGORY') - (b.term_type === 'CATEGORY'));
    for (const t of all) await api(token, 'DELETE', `/catalog/glossary/${t.id}`).catch(() => {});
  }
}

async function datasetId(name) {
  const r = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`);
  const hit = r.items.find(d => d.name === name);
  if (!hit) throw new Error(`dataset ${name} not found`);
  return hit.id;
}

/** 왼쪽 트리에서 이름이 정확히 일치하는 분류 줄. */
const treeRow = (page, name) =>
  page.locator('div.group').filter({ has: page.getByRole('button', { name, exact: true }) }).first();
const selectCat = async (page, name) => {
  await page.getByRole('button', { name, exact: true }).first().click();
  await settle(page, 400);
};
/** 토스트가 사라질 때까지 기다린다(다음 그림에 섞이지 않게). */
const toastGone = page => page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 10000 }).catch(() => {});

await removeTemp();
const customer = await datasetId('sakila.customer');

const { browser, page } = await open();
try {
  // 분류 추가 창 — 전체 용어를 고른 상태에서 최상위에 새 분류
  await goto(page, '/dashboard/glossary');
  await page.getByRole('button', { name: '추가', exact: true }).click();
  const dlg = page.getByRole('dialog');
  await dlg.locator('input').first().fill(CAT);
  await dlg.locator('textarea').fill('IT 분류');
  await settle(page, 300);
  await shot(page, `${AREA}/category-add`);
  await dlg.getByRole('button', { name: '추가', exact: true }).click();
  await settle(page);
  await toastGone(page);

  // 하위 분류를 추가한 직후 — 상위가 펼쳐지고, 마우스를 올리면 연필 · 휴지통 아이콘이 보임
  await selectCat(page, CAT);
  await page.getByRole('button', { name: '추가', exact: true }).click();
  await page.getByRole('dialog').locator('input').first().fill(SUB);
  await page.getByRole('dialog').getByRole('button', { name: '추가', exact: true }).click();
  await page.getByText('분류를 추가했습니다').waitFor();
  await treeRow(page, SUB).hover();
  await settle(page, 400);
  await shot(page, `${AREA}/tree-subcategory`);
  await toastGone(page);

  // 용어 두 건을 it-고객 아래에 만든다
  const all = await api(token, 'GET', '/catalog/glossary');
  const cat = all.find(t => t.name === CAT);
  const t1 = await api(token, 'POST', '/catalog/glossary', { name: TERM1, description: '월간 해지 고객 비율', parent_id: cat.id, term_type: 'TERM' });
  const t2 = await api(token, 'POST', '/catalog/glossary', { name: TERM2, description: 'CLV', parent_id: cat.id, term_type: 'TERM' });

  // 분류 it-고객 을 고른 모습 — 오른쪽 표에 용어 2건
  await goto(page, '/dashboard/glossary');
  await selectCat(page, CAT);
  await page.mouse.move(700, 600);
  await shot(page, `${AREA}/terms-grid`);

  // 일반 사용자 화면 — 편집 버튼과 체크박스가 없음
  {
    const u = await open('mjkim', 'mjkim');
    await goto(u.page, '/dashboard/glossary');
    await selectCat(u.page, CAT);
    await u.page.mouse.move(700, 600);
    await shot(u.page, `${AREA}/user-readonly`);
    await u.browser.close();
  }

  // 용어 1건 선택 → 이동 (1) → 대상 분류 목록
  await page.locator('.ag-row').filter({ hasText: TERM2 }).locator('.ag-checkbox-input').first().click();
  await page.getByRole('button', { name: '이동 (1)' }).click();
  await settle(page, 400);
  // 대상 목록에서 it-고객 을 펼쳐 하위 분류까지 보이게 한다
  const pop = page.locator('[data-radix-popper-content-wrapper]');
  const expander = pop.locator('div').filter({ hasText: new RegExp(`^${CAT}$`) }).getByRole('button', { name: '펼치기' }).first();
  if (await expander.count()) { await expander.click(); await settle(page, 300); }
  await shot(page, `${AREA}/move-terms`);
  await pop.getByText(SUB, { exact: true }).click();
  await settle(page);
  await toastGone(page);

  // 분류 it-고객-식별정보 를 최상위로 옮긴 결과
  await goto(page, '/dashboard/glossary');
  await treeRow(page, CAT).getByRole('button', { name: '펼치기' }).click();
  await treeRow(page, SUB).getByRole('checkbox').click();
  await page.getByRole('button', { name: '이동', exact: true }).click();
  await settle(page, 300);
  await page.locator('[data-radix-popper-content-wrapper]').getByText('Root (최상위)').click();
  await page.getByText('항목을 이동했습니다').waitFor();
  await selectCat(page, SUB);
  await shot(page, `${AREA}/move-category-root`);
  await toastGone(page);

  // 이동 대상 목록 — 옮기려는 분류(it-고객) 자신은 보이지 않음
  await goto(page, '/dashboard/glossary');
  await treeRow(page, CAT).getByRole('checkbox').click();
  await page.getByRole('button', { name: '이동', exact: true }).click();
  await settle(page, 400);
  await shot(page, `${AREA}/move-excludes-self`);
  await page.keyboard.press('Escape');
  await treeRow(page, CAT).getByRole('checkbox').click();

  // 분류 삭제 확인 창
  await treeRow(page, CAT).hover();
  await treeRow(page, CAT).getByRole('button', { name: '삭제', exact: true }).click();
  await page.getByRole('alertdialog').or(page.getByRole('dialog')).first().waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/category-delete`);

  // 용어가 남아 있어 삭제가 거부된 안내
  await page.getByRole('alertdialog').or(page.getByRole('dialog')).first().getByRole('button', { name: '삭제', exact: true }).click();
  await page.getByText('분류를 삭제할 수 없습니다').first().waitFor();
  await page.mouse.move(700, 600);
  await settle(page, 300);
  await shot(page, `${AREA}/category-delete-refused`);
  await page.keyboard.press('Escape');
  await toastGone(page);

  // ?term= 링크로 연 용어 — 경로와 1행
  await goto(page, `/dashboard/glossary?term=${t1.id}`);
  await shot(page, `${AREA}/deeplink`);

  // 전역 검색 결과의 용어
  await goto(page, `/dashboard/search?q=${encodeURIComponent(TERM1)}`);
  await settle(page, 1000);
  await shot(page, `${AREA}/global-search`);

  // 데이터셋 상세 메타데이터 탭 — 용어집 줄에 용어를 연결한 모습(그 줄만 잘라 냄)
  await api(token, 'POST', `/catalog/datasets/${customer}/glossary/${t2.id}`);
  await goto(page, `/dashboard/datasets/${customer}`);
  await page.getByRole('tab', { name: '메타데이터' }).click();
  await settle(page);
  const row = page.locator('tr').filter({ hasText: '연결된 비즈니스 용어' }).last();
  await row.scrollIntoViewIfNeeded();
  await shot(page, `${AREA}/dataset-link`, { target: row });
} finally {
  await removeTemp();
  await browser.close();
}
