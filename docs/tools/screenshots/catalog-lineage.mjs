/**
 * 매뉴얼 "리니지 & ERD"(pages/catalog/lineage.adoc) 화면 캡처.
 *
 * - 시드 Sakila 데이터셋(rental · film)으로 리니지 그래프 · 리니지 상세 · 관계 · ER 다이어그램을 찍는다.
 *   관계(JOIN) 데이터는 시드에 없어서, 비어 있으면 Relationship Analyzer 가 하는 적재를 API 로 재현한다(지우지 않고 남긴다).
 * - 리니지 추가 · 컬럼 매핑 · DDL 외래 키 ERD 는 임시 데이터 소스 "주문 분석 DB"(sales.orders · sales.order_summary)와
 *   파이프라인 "daily-order-summary" 를 만들어 찍고, 끝나면 지운다(KEEP=1 이면 남겨 둔다).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const A = 'catalog-lineage';
const DS_NAME = '주문 분석 DB';
const PIPELINE = 'daily-order-summary';

const token = await apiToken();

// ---- 도우미 -------------------------------------------------------------------------------

async function datasetId(name) {
  const r = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`);
  return r.items.find(d => d.name === name)?.id;
}
async function cleanup() {
  for (const ds of (await api(token, 'GET', '/catalog/datasources')).filter(d => d.name === DS_NAME)) {
    await api(token, 'DELETE', `/catalog/datasources/${ds.id}?force=true`);
  }
  for (const p of (await api(token, 'GET', '/catalog/pipelines')).filter(p => p.pipeline_name === PIPELINE)) {
    await api(token, 'DELETE', `/catalog/pipelines/${p.id}`);
  }
}
const dialog = page => page.getByRole('dialog').last();
const toast = (page, text) => page.locator('[data-sonner-toast]').filter({ hasText: text }).first();
async function toastsGone(page) {
  await page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
}
/** 데이터셋 상세를 탭 지정으로 열고 그래프가 그려질 때까지 기다린다. */
async function openTab(page, id, tab) {
  await goto(page, `/dashboard/datasets/${id}?tab=${tab}`);
  await page.locator('.react-flow__node').first().waitFor();
  await settle(page, 1200);
  await page.mouse.move(1300, 130);
}
/** 리니지 그래프에서 두 노드 사이의 엣지를 눌러 리니지 상세 창을 연다. */
async function clickEdge(page, sourceId, targetId) {
  const path = page.locator(`.react-flow__edge[aria-label*="${sourceId}"][aria-label*="${targetId}"] path`).last();
  await path.waitFor({ state: 'attached' });
  // 곧은 엣지는 높이가 0 이라 요소 클릭이 안 되므로, 선의 가운데 좌표를 직접 누른다
  const pt = await path.evaluate(el => {
    const p = el.getPointAtLength(el.getTotalLength() / 2).matrixTransform(el.getScreenCTM());
    return { x: p.x, y: p.y };
  });
  await page.mouse.click(pt.x, pt.y);
  await page.getByText('리니지 상세').waitFor();
  await page.mouse.move(1300, 130);
  await page.waitForTimeout(500);
}

// ---- 준비 ---------------------------------------------------------------------------------

await cleanup();

const RENTAL = await datasetId('sakila.rental');
const STAFF = await datasetId('sakila.staff');
const FILM = await datasetId('sakila.film');

// 관계(JOIN) 데이터 — 비어 있을 때만 적재
if (!(await api(token, 'GET', `/catalog/datasets/${RENTAL}/relationships`)).tableRelationships.length) {
  const pair = (b, col, kind = 'explicit') => ({ a_table: 'rental', a_col: col, b_table: b, b_col: col, kind });
  const batches = [
    [pair('customer', 'customer_id'), pair('inventory', 'inventory_id'), pair('payment', 'rental_id'), pair('staff', 'staff_id')],
    [pair('customer', 'customer_id'), pair('inventory', 'inventory_id'), pair('payment', 'rental_id', 'implicit')],
    [pair('customer', 'customer_id'), pair('inventory', 'inventory_id', 'implicit')],
  ];
  for (const pairs of batches) await api(token, 'POST', '/catalog/relationships/ingest', { datasource_id: 'sakila-mysql', pairs });
}

// 임시 데이터 소스 · 데이터셋 · 파이프라인
const src = await api(token, 'POST', '/catalog/datasources', { name: DS_NAME, type: 'postgresql', origin: 'DEV' });
const f = (field_path, field_type, extra = {}) => ({ field_path, field_type, ...extra });
const ORD = (await api(token, 'POST', '/catalog/datasets', {
  name: 'sales.orders', display_name: '주문', datasource_id: src.id, origin: 'DEV', summary: '온라인 주문 원장',
  schema_fields: [
    f('order_id', 'INTEGER', { is_primary_key: 'true', nullable: 'false' }), f('customer_id', 'INTEGER'),
    f('order_date', 'DATE'), f('amount', 'NUMERIC(12,2)'), f('summary_id', 'INTEGER'),
  ],
})).id;
const SUMM = (await api(token, 'POST', '/catalog/datasets', {
  name: 'sales.order_summary', display_name: '일별 주문 요약', datasource_id: src.id, origin: 'DEV', summary: '일별 주문 합계',
  schema_fields: [
    f('summary_id', 'INTEGER', { is_primary_key: 'true', nullable: 'false' }), f('order_date', 'DATE'), f('total', 'NUMERIC(14,2)'),
  ],
})).id;
await api(token, 'PUT', `/catalog/datasets/${SUMM}`, {
  ddl: 'CREATE TABLE order_summary (\n  summary_id INT NOT NULL,\n  order_date DATE,\n  total NUMERIC(14,2),\n  PRIMARY KEY (summary_id)\n)',
});
await api(token, 'PUT', `/catalog/datasets/${ORD}`, {
  ddl: 'CREATE TABLE orders (\n  order_id INT NOT NULL,\n  customer_id INT,\n  order_date DATE,\n  amount NUMERIC(12,2),\n  summary_id INT,\n  PRIMARY KEY (order_id),\n  CONSTRAINT fk_orders_summary FOREIGN KEY (summary_id) REFERENCES order_summary (summary_id)\n)',
});
await api(token, 'POST', '/catalog/pipelines', { pipeline_name: PIPELINE, pipeline_type: 'ETL' });

const { browser, page } = await open();
try {
  // ---- 1. 시드 Sakila -----------------------------------------------------------------------

  // 리니지 그래프 — sakila.rental 중심
  await openTab(page, RENTAL, 'lineage');
  await shot(page, `${A}/graph`);

  // 리니지 상세 — 수동 등록 엣지(sakila.staff → sakila.rental)
  await clickEdge(page, STAFF, RENTAL);
  await shot(page, `${A}/edge-detail`);

  // 관계 그래프 — 1홉
  await openTab(page, RENTAL, 'relationships');
  await shot(page, `${A}/relationships`);

  // 함께 쓰인 데이터셋과 컬럼 관계 표 — 같은 화면을 아래로 내린다
  await page.getByText('컬럼 관계', { exact: true }).first().evaluate(el => { el.scrollIntoView({ block: 'end' }); window.scrollBy(0, 400); });
  for (const sc of await page.locator('main, .overflow-auto, .overflow-y-auto').all()) {
    await sc.evaluate(el => { el.scrollTop = el.scrollHeight; }).catch(() => {});
  }
  await page.waitForTimeout(500);
  await shot(page, `${A}/column-relationships`);

  // ER 다이어그램 — sakila.film 중심
  await openTab(page, FILM, 'erd');
  await shot(page, `${A}/erd`);

  // ---- 2. 리니지 추가(임시 데이터셋 sales.orders → sales.order_summary) ----------------------

  // 리니지 추가 ② 데이터셋 · 관계 유형 · 파이프라인
  await goto(page, `/dashboard/datasets/${ORD}?tab=lineage`);
  await page.getByRole('button', { name: '리니지 추가' }).first().click();
  const d = dialog(page);
  await d.getByText('소스 (상위)').click();
  await d.getByRole('button', { name: '다음' }).click();
  await d.getByRole('combobox').filter({ hasText: '전체 데이터 소스' }).click();
  await page.getByRole('option', { name: new RegExp(DS_NAME) }).click();
  await d.getByRole('combobox').nth(1).click();
  await page.getByPlaceholder('데이터셋 검색…', { exact: true }).fill('order_summary');
  await page.getByRole('option', { name: /sales\.order_summary/ }).first().click();
  await d.getByRole('combobox').filter({ hasText: '없음' }).click();
  await page.getByRole('option', { name: new RegExp(PIPELINE) }).click();
  await d.getByPlaceholder(/HR 데이터를/).fill('주문 원장을 일별로 집계해 요약 테이블에 적재');
  await d.getByRole('heading').click();
  await page.waitForTimeout(300);
  await shot(page, `${A}/add-step2`);

  // 리니지 추가 ③ 컬럼 매핑 — 행 두 개를 추가한 상태
  await d.getByRole('button', { name: '다음' }).click();
  const pick = async (index, name) => {
    await d.getByRole('combobox').nth(index).click();
    await page.getByRole('option', { name: new RegExp(`^${name}\\b`) }).first().click();
  };
  await d.getByRole('button', { name: '행 추가' }).click();
  await pick(0, 'order_date');
  await pick(1, 'order_date');
  await d.getByRole('button', { name: '행 추가' }).click();
  await pick(3, 'amount');
  await pick(4, 'total');
  await d.getByRole('heading').click();
  await page.waitForTimeout(300);
  await shot(page, `${A}/add-step3`);

  // 파이프라인을 지정해 등록한 리니지와 컬럼 매핑 — 저장 뒤 새 엣지를 누른 상태
  await d.getByRole('button', { name: '저장', exact: true }).click();
  await toast(page, '리니지를 추가했습니다').waitFor();
  await toastsGone(page);
  await openTab(page, ORD, 'lineage');
  await clickEdge(page, ORD, SUMM);
  await shot(page, `${A}/column-mapping`);

  // EXPRESSION 유형의 컬럼 매핑 — 파생(DERIVED) 리니지로 바꿔 등록하고 매핑을 SUM(amount) 로
  for (const l of await api(token, 'GET', `/catalog/lineage?dataset_id=${ORD}`)) await api(token, 'DELETE', `/catalog/lineage/${l.id}`);
  const lin = await api(token, 'POST', '/catalog/lineage', { source_dataset_id: ORD, target_dataset_id: SUMM, relation_type: 'DERIVED' });
  await api(token, 'PUT', `/catalog/lineage/${lin.id}/column-mappings`, [
    { source_column: 'amount', target_column: 'total', transform_type: 'EXPRESSION', transform_expr: 'SUM(amount)' },
  ]);
  await openTab(page, ORD, 'lineage');
  await clickEdge(page, ORD, SUMM);
  await shot(page, `${A}/expression-mapping`);

  // DDL 외래 키로 그린 orders → order_summary 관계
  await openTab(page, ORD, 'erd');
  await shot(page, `${A}/erd-orders`);
} catch (e) {
  if (process.env.DEBUG_DIR) await page.screenshot({ path: `${process.env.DEBUG_DIR}/fail.png` }).catch(() => {});
  throw e;
} finally {
  await browser.close();
  if (!process.env.KEEP) await cleanup();
}
