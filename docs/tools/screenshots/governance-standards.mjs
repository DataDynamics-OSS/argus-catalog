/**
 * 거버넌스 → 데이터 표준 (pages/governance/standards.adoc) 화면 캡처.
 *
 * 시드 사전(영화대여 · 반도체 · 물류 표준 사전)은 그대로 두고, 임시 사전 "고객 표준 사전" 과
 * 임시 데이터셋 crm.customer(cust_no · cust_tel_no · memo)를 만들어 찍은 뒤 지운다.
 * 다시 실행해도 안전하다(남아 있는 임시 사전 · 데이터셋은 시작할 때 지운다).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'governance-standards';
const DICT = '고객 표준 사전';
const DATASET = 'crm.customer';

const token = await apiToken();

async function removeTemp() {
  const ds = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(DATASET)}&page_size=50`);
  for (const d of ds.items.filter(d => d.name === DATASET)) await api(token, 'DELETE', `/catalog/datasets/${d.id}`);
  for (const d of await api(token, 'GET', '/standards/dictionaries')) {
    if (d.dict_name === DICT) await api(token, 'DELETE', `/standards/dictionaries/${d.id}`);
  }
}

/** 사전 선택 상자에서 사전을 고른다. */
async function pickDict(page, name) {
  await page.getByRole('combobox').first().click();
  await page.getByRole('option', { name: new RegExp(name) }).click();
  await settle(page);
}
const tab = async (page, name) => { await page.getByRole('tab', { name }).click(); await settle(page); };
/** AG Grid 칸 — 줄에 든 글자와 열(col-id)로 찾는다. */
const cell = (scope, rowText, col) => scope.locator('.ag-row').filter({ hasText: rowText }).locator(`[col-id="${col}"]`).first();
/** 칸을 더블클릭해 값을 바꾸고 Enter 로 저장한다. */
async function editCell(page, locator, value) {
  await page.keyboard.press('Escape'); // 추가 직후에는 새 줄이 편집 상태라 먼저 닫는다
  await locator.dblclick();
  await page.keyboard.press('Control+A');
  await page.keyboard.type(value);
  await page.keyboard.press('Enter');
}
const toastGone = page => page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 10000 }).catch(() => {});
const park = page => page.mouse.move(760, 700);

await removeTemp();

// 임시 사전과 단어 · 도메인
const dict = await api(token, 'POST', '/standards/dictionaries', { dict_name: DICT, version: '1.0', description: '고객 도메인 데이터 표준' });
const D = dict.id;
await api(token, 'POST', '/standards/words', { dictionary_id: D, word_name: '고객', word_english: 'Customer', word_abbr: 'CUST' });
await api(token, 'POST', '/standards/words', { dictionary_id: D, word_name: '번호', word_english: 'Number', word_abbr: 'NO', word_type: 'SUFFIX' });
await api(token, 'POST', '/standards/words', { dictionary_id: D, word_name: '전화', word_english: 'Telephone', word_abbr: 'TEL', description: '전화 관련' });
await api(token, 'POST', '/standards/domains', { dictionary_id: D, domain_name: '번호', data_type: 'VARCHAR', data_length: 20, description: '식별 번호' });

const { browser, page } = await open();
try {
  // 사전 선택 상자와 편집 · 삭제 · 사전 추가 버튼 (시드 사전을 고른 모습)
  await goto(page, '/dashboard/standards');
  await pickDict(page, '영화대여 표준 사전');
  await park(page);
  await shot(page, `${AREA}/dictionary`);

  // 단어 탭 — 고객 · 번호(접미사) · 전화
  await pickDict(page, DICT);
  await park(page);
  await shot(page, `${AREA}/words`);

  // 추가를 두 번 누른 모습 — (new), (new 2)
  const add = page.getByRole('button', { name: '추가', exact: true });
  await add.click(); await settle(page, 500);
  await add.click(); await settle(page, 500);
  await page.keyboard.press('Escape'); // 새 줄의 편집 상태를 닫아 이름이 보이게
  await page.getByText('(new 2)').first().waitFor();
  await settle(page, 300);
  await park(page);
  await shot(page, `${AREA}/add-new-rows`);

  // (new 2) 를 이미 있는 이름 "고객" 으로 바꾸면 경고가 뜨고 원래 값으로 돌아감
  await editCell(page, cell(page, '(new 2)', 'word_name'), '고객');
  await page.getByText('이미 이 사전에 있습니다').first().waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/cell-dup-revert`);
  await toastGone(page);
  for (const w of await api(token, 'GET', `/standards/words?dictionary_id=${D}`)) {
    if (w.word_name.startsWith('(new')) await api(token, 'DELETE', `/standards/words/${w.id}`);
  }

  // 도메인 탭 — 번호 VARCHAR(20)
  await goto(page, '/dashboard/standards');
  await pickDict(page, DICT);
  await tab(page, '도메인');
  await park(page);
  await shot(page, `${AREA}/domains`);

  // 용어 추가(형태소 분석) — 고객번호 = 고객(CUST) + 번호(NO), 도메인 추천
  await tab(page, '용어');
  await page.getByRole('button', { name: '추가', exact: true }).click();
  const dlg = page.getByRole('dialog');
  await dlg.locator('input').first().fill('고객번호');
  await dlg.getByRole('button', { name: '분석' }).click();
  await dlg.getByText('단어 분해').waitFor();
  await settle(page, 400);
  await shot(page, `${AREA}/term-analyze`);

  // 사전에 없는 글자가 있는 이름 — 고객명 의 '명' 을 강조
  await dlg.locator('input').first().fill('고객명');
  await dlg.getByRole('button', { name: '분석' }).click();
  await dlg.getByText('사전에 없는 부분').first().waitFor();
  await settle(page, 400);
  await shot(page, `${AREA}/term-analyze-unmatched`);
  await page.keyboard.press('Escape');

  // 용어 탭 — '고객' 검색 결과 2건
  const tNo = await api(token, 'POST', '/standards/terms', { dictionary_id: D, term_name: '고객번호' });
  await api(token, 'POST', '/standards/terms', { dictionary_id: D, term_name: '고객전화번호' });
  await api(token, 'PUT', `/standards/terms/${tNo.id}`, { description: '고객 식별 번호' });
  await goto(page, '/dashboard/standards');
  await pickDict(page, DICT);
  await tab(page, '용어');
  await page.getByPlaceholder('용어명·물리명·영문명 검색…').fill('고객');
  await settle(page, 800);
  await park(page);
  await shot(page, `${AREA}/terms`);

  // 코드 탭 — 그룹 성별 에서 값 추가를 두 번 누른 모습(NEW1, NEW2)
  const cg = await api(token, 'POST', '/standards/code-groups', { dictionary_id: D, group_name: '성별' });
  await api(token, 'PUT', `/standards/code-groups/${cg.id}`, { group_english: 'Gender' });
  await api(token, 'POST', `/standards/code-groups/${cg.id}/values`, { code_value: 'M', code_name: '남성', sort_order: 1 });
  await api(token, 'POST', `/standards/code-groups/${cg.id}/values`, { code_value: 'F', code_name: '여성', sort_order: 2 });
  await goto(page, '/dashboard/standards');
  await pickDict(page, DICT);
  await tab(page, '코드');
  await cell(page, '성별', 'group_name').click();
  await settle(page, 500);
  const addValue = page.getByRole('button', { name: '값 추가' });
  await addValue.click(); await settle(page, 500);
  await addValue.click(); await settle(page, 500);
  await park(page);
  await shot(page, `${AREA}/code-values`);

  // 코드 F 를 같은 그룹의 기존 코드 M 으로 바꾸면 경고가 뜨고 F 로 돌아감
  await editCell(page, cell(page, '여성', 'code_value'), 'M');
  await page.getByText('이미 이 그룹에 있습니다').first().waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/code-value-dup`);
  await toastGone(page);
  for (const v of (await api(token, 'GET', `/standards/code-groups/${cg.id}`)).values) {
    if (/^NEW\d+$/.test(v.code_value)) await api(token, 'DELETE', `/standards/code-values/${v.id}`);
  }

  // 임시 데이터셋 — cust_no(일치) · cust_tel_no(타입 위반) · memo(미연결)
  const sources = await api(token, 'GET', '/catalog/datasources');
  const src = (sources.items ?? sources).find(s => s.name === 'Sakila (MySQL)');
  const ds = await api(token, 'POST', '/catalog/datasets', {
    name: DATASET, display_name: '고객', datasource_id: src.id, summary: '고객 기본 정보',
    schema_fields: [
      { field_path: 'cust_no', field_type: 'VARCHAR', ordinal: 1 },
      { field_path: 'cust_tel_no', field_type: 'INT', ordinal: 2 },
      { field_path: 'memo', field_type: 'TEXT', ordinal: 3 },
    ],
  });
  await api(token, 'PUT', `/standards/datasets/${ds.id}/dictionary`, { dictionary_id: D });

  // 데이터셋 표준 용어 탭 — 자동 매핑 결과와 준수도
  await goto(page, `/dashboard/datasets/${ds.id}`);
  await tab(page, '표준 용어');
  await page.getByRole('button', { name: '자동 매핑' }).click();
  await page.getByText('위반').first().waitFor();
  await settle(page, 800);
  await park(page);
  await shot(page, `${AREA}/dataset-automap`);

  // 준수율 탭 — 전체 3, 매칭 1, 유사 0, 위반 1, 미매핑 1 → 33.3%
  {
    const { browser: b2, page: p2 } = await open(); // 로그인 상태가 탭마다 따로라 새 세션으로 연다
    await goto(p2, '/dashboard/standards');
    await pickDict(p2, DICT);
    await tab(p2, '준수율');
    await p2.getByText('표준 준수율').waitFor();
    await park(p2);
    await shot(p2, `${AREA}/compliance`);
    await b2.close();
  }

  // memo 컬럼을 용어 고객번호 에 수동 연결 — 유사
  await page.getByRole('button', { name: '용어 매핑' }).click();
  await page.getByPlaceholder('용어 검색…').fill('고객번호');
  await settle(page, 800);
  await page.getByRole('option').filter({ hasText: '고객번호' }).first().click();
  await page.getByText('유사').first().waitFor();
  await settle(page, 2500);
  await toastGone(page);
  await park(page);
  await shot(page, `${AREA}/dataset-manual-mapping`);

  // 변경 이력 탭 — 변경자 admin
  await goto(page, '/dashboard/standards');
  await pickDict(page, DICT);
  await tab(page, '변경 이력');
  await park(page);
  await shot(page, `${AREA}/history`);

  // 일반 사용자 화면 — 사전 추가 · 추가 · 삭제 버튼이 없음
  {
    const u = await open('mjkim', 'mjkim');
    await goto(u.page, '/dashboard/standards');
    await pickDict(u.page, DICT);
    await park(u.page);
    await shot(u.page, `${AREA}/user-readonly`);
    await u.browser.close();
  }

  // 사전 삭제 확인 창 — 함께 지워질 단어 · 도메인 · 용어 · 코드 그룹 수
  await tab(page, '단어');
  await page.getByRole('button', { name: '삭제', exact: true }).first().click();
  await page.getByText('되돌릴 수 없습니다').waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/dictionary-delete`);
  await page.keyboard.press('Escape');
} finally {
  await removeTemp();
  await browser.close();
}
