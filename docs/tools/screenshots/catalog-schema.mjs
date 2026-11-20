/**
 * 스키마 관리(catalog/schema.adoc) 화면 캡처 — catalog-schema/*.png
 *
 * 읽기만 하는 그림은 시드 데이터셋 sakila.film 으로, 스키마 · Avro 를 고치는 그림은 이 스크립트가 만드는
 * 임시 데이터셋(데이터 소스 '주문 분석 DB' 의 order_mart.orders)으로 찍는다. 임시 데이터는 끝나면 지운다.
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'catalog-schema';
const SOURCE = '주문 분석 DB';
const DATASET = 'order_mart.orders';
const token = await apiToken();

async function cleanup() {
  for (const ds of await api(token, 'GET', '/catalog/datasources')) {
    if (ds.name !== SOURCE) continue;
    const list = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(DATASET)}&page_size=50`);
    for (const d of list.items || []) if (d.name === DATASET && d.datasource_name === SOURCE) await api(token, 'DELETE', `/catalog/datasets/${d.id}`);
    await api(token, 'DELETE', `/catalog/datasources/${ds.id}`).catch(e => console.warn(`데이터 소스 삭제 실패: ${e.message}`));
  }
}

const films = await api(token, 'GET', '/catalog/datasets?search=sakila.film&page_size=50');
const FILM = films.items.find(d => d.name === 'sakila.film' && d.datasource_name === 'Sakila (MySQL)').id;

// 임시 데이터셋 — 스냅숏 1: 최초 등록
await cleanup();
const source = await api(token, 'POST', '/catalog/datasources', { name: SOURCE, type: 'postgresql', origin: 'DEV' });
const created = await api(token, 'POST', '/catalog/datasets', {
  name: DATASET, display_name: '주문 내역', datasource_id: source.id, origin: 'DEV', summary: '온라인 쇼핑몰 주문 내역',
  schema_fields: [
    { field_path: 'order_id', field_type: 'INTEGER', is_primary_key: 'true', nullable: 'false', ordinal: 0 },
    { field_path: 'customer_id', field_type: 'INTEGER', ordinal: 1 },
    { field_path: 'amount', field_type: 'NUMERIC', ordinal: 2 },
  ],
});
const ORD = created.id;
const putSchema = fields => api(token, 'PUT', `/catalog/datasets/${ORD}/schema`, fields);
const pk = { field_path: 'order_id', field_type: 'INTEGER', is_primary_key: 'true', is_unique: 'true', is_indexed: 'true', nullable: 'false', ordinal: 0 };

const { browser, page } = await open();
const tab = async (id, name) => { await goto(page, `/dashboard/datasets/${id}?tab=${name}`); await settle(page, 600); };

try {
  // 스키마 탭 — 컬럼 목록 (sakila.film)
  await tab(FILM, 'schema');
  await shot(page, `${AREA}/overview`);

  // Avro 스키마 — 자동 생성 (sakila.film)
  await tab(FILM, 'avro');
  await page.locator('.monaco-editor .view-lines').first().waitFor({ timeout: 20000 }).catch(() => {});
  await settle(page, 800);
  await shot(page, `${AREA}/avro`);

  // Avro 스키마 가져오기 가이드 — curl · wget · Python 탭 (sakila.film)
  await page.getByRole('button', { name: '가져오기 가이드' }).click();
  await page.getByRole('dialog').waitFor();
  await settle(page, 500);
  await shot(page, `${AREA}/avro-fetch-guide`);
  await page.keyboard.press('Escape');

  // 스냅숏 2: email 추가 · amount 타입 변경 · customer_id 삭제
  await putSchema([
    pk,
    { field_path: 'amount', field_type: 'DECIMAL(12,2)', description: '주문 금액', ordinal: 1 },
    { field_path: 'email', field_type: 'VARCHAR', description: '주문자 이메일', ordinal: 2 },
  ]);

  // 스키마 변경 이력 — 최신 스냅숏을 펼친 모습(추가 · 삭제 · 변경)
  await tab(ORD, 'history');
  await page.locator('tbody tr').first().click();
  await settle(page, 500);
  await shot(page, `${AREA}/history`);

  // 스냅숏 3: amount 타입만 변경(설명은 보내지 않음)
  await putSchema([
    pk,
    { field_path: 'amount', field_type: 'DECIMAL(14,2)', ordinal: 1 },
    { field_path: 'email', field_type: 'VARCHAR', ordinal: 2 },
  ]);

  // 타입이 바뀌어도 유지된 컬럼 설명 — 스키마 탭
  await tab(ORD, 'schema');
  await shot(page, `${AREA}/description-kept`);

  // 타입만 바뀐 스냅숏 — 변경 1개
  await tab(ORD, 'history');
  await page.locator('tbody tr').first().click();
  await settle(page, 500);
  await shot(page, `${AREA}/history-modify`);

  // 스키마 수정 그리드 — 필드를 하나 더하고 타입 목록을 연 모습 (저장하지 않음)
  await tab(ORD, 'schema');
  await page.getByRole('button', { name: /스키마 (편집|수정)/ }).click();
  await settle(page, 800);
  await page.getByRole('button', { name: '필드 추가' }).click();
  await settle(page, 300);
  {
    const row = page.locator('.ag-center-cols-container .ag-row').last();
    await row.locator('.ag-cell[col-id="field_path"]').dblclick();
    await page.keyboard.type('phone');
    await page.keyboard.press('Enter');
    await row.locator('.ag-cell[col-id="field_type"]').dblclick();
    await settle(page, 500);
  }
  await shot(page, `${AREA}/edit-grid`);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '취소' }).click();

  // Avro 편집기 — doc 을 고친 모습 (저장 전)
  await tab(ORD, 'avro');
  await page.getByRole('button', { name: '편집', exact: true }).click();
  await page.locator('.monaco-editor .view-lines').first().waitFor({ timeout: 20000 });
  await settle(page, 800);
  const edited = await page.evaluate(() => {
    const model = window.monaco?.editor.getModels().find(m => m.getValue().includes('"fields"'));
    if (!model) return false;
    model.setValue(model.getValue().replace(/"doc": "Avro schema for [^"]*"/, '"doc": "온라인 쇼핑몰 주문 내역 (주문 1건 = 1행)"'));
    return true;
  });
  if (!edited) throw new Error('Avro 편집기 모델을 찾지 못했습니다');
  await settle(page, 600);
  await shot(page, `${AREA}/avro-edit`);

  // 저장된 사용자 편집본 — 저장 알림과 '사용자 편집본' 배지
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await page.getByText('Avro 스키마를 저장했습니다').first().waitFor({ timeout: 10000 });
  await settle(page, 500);
  await shot(page, `${AREA}/avro-saved`);
} finally {
  await browser.close();
  if (!process.env.KEEP) await cleanup();
}
