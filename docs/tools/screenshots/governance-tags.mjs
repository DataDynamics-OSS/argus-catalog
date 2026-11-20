/**
 * 거버넌스 → 태그 (pages/governance/tags.adoc) 화면 캡처.
 *
 * 시드 태그(미디어 · demo · sakila)는 그대로 두고, 임시 태그 it-pii · it-tier1 을 만들어 찍은 뒤 지운다.
 * 다시 실행해도 안전하다(남아 있는 임시 태그는 시작할 때 지운다).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'governance-tags';
const TEMP = ['it-pii', 'it-tier1', 'it-renamed'];

const token = await apiToken();

async function removeTemp() {
  for (const t of await api(token, 'GET', '/catalog/tags')) {
    if (TEMP.includes(t.name)) await api(token, 'DELETE', `/catalog/tags/${t.id}`);
  }
}

async function datasetId(name) {
  const r = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`);
  const hit = r.items.find(d => d.name === name);
  if (!hit) throw new Error(`dataset ${name} not found`);
  return hit.id;
}

await removeTemp();
const customer = await datasetId('sakila.customer');
const rental = await datasetId('sakila.rental');

const { browser, page } = await open();
try {
  // 태그 카드 목록 (시드 태그만 있는 상태)
  await goto(page, '/dashboard/tags');
  await shot(page, `${AREA}/list`);

  // 일반 사용자 화면 — 추가 · 수정 · 삭제 버튼이 없음
  {
    const u = await open('mjkim', 'mjkim');
    await goto(u.page, '/dashboard/tags');
    await shot(u.page, `${AREA}/user-readonly`);
    await u.browser.close();
  }

  // 태그 추가 창 — 이름 · 설명 · 색상과 배지 미리보기
  await page.getByRole('button', { name: '태그 추가' }).click();
  await page.fill('#tag-name', 'it-pii');
  await page.fill('#tag-desc', '개인정보 포함');
  await page.getByLabel('색상 코드').fill('#ef4444');
  await settle(page, 300);
  await shot(page, `${AREA}/add-dialog`);

  // 잘못된 색상 코드 — 형식 안내가 뜨고 추가 버튼이 잠김
  await page.getByLabel('색상 코드').fill('red');
  await settle(page, 300);
  await shot(page, `${AREA}/color-invalid`);
  await page.keyboard.press('Escape');

  // 임시 태그를 만들고 데이터셋 두 개에 붙인다
  const pii = await api(token, 'POST', '/catalog/tags', { name: 'it-pii', description: '개인정보 포함', color: '#ef4444' });
  await api(token, 'POST', '/catalog/tags', { name: 'it-tier1' });
  await api(token, 'POST', `/catalog/datasets/${customer}/tags/${pii.id}`);
  await api(token, 'POST', `/catalog/datasets/${rental}/tags/${pii.id}`);

  // 태그 수정 창
  await goto(page, '/dashboard/tags');
  await page.getByRole('button', { name: 'it-tier1 태그 수정' }).click();
  await page.fill('#tag-name', 'it-renamed');
  await page.getByLabel('색상 코드').fill('#FACC15');
  await settle(page, 300);
  await shot(page, `${AREA}/edit-dialog`);
  await page.keyboard.press('Escape');

  // 태그 삭제 창 — 데이터셋 2개에서 사용 중
  await page.getByRole('button', { name: 'it-pii 태그 삭제' }).click();
  await page.getByText('에서 사용 중입니다').waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/delete-usage`);
  await page.keyboard.press('Escape');

  // 데이터셋 상세 메타데이터 탭 — 태그 줄에 it-pii 가 붙은 모습(그 줄만 잘라 냄)
  await goto(page, `/dashboard/datasets/${customer}`);
  await page.getByRole('tab', { name: '메타데이터' }).click();
  await settle(page);
  const row = page.locator('tr').filter({ hasText: '분류·검색용 태그' }).last();
  await row.scrollIntoViewIfNeeded();
  await shot(page, `${AREA}/dataset-tag`, { target: row });
} finally {
  await removeTemp();
  await browser.close();
}
