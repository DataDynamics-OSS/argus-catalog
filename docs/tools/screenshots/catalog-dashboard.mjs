/**
 * 매뉴얼 "대시보드"(pages/catalog/dashboard.adoc) 화면 캡처.
 *
 * 대시보드는 `GET /catalog/stats` 와 `GET /catalog/sync-runs/summary` 를 한 번씩 불러 그린다.
 * 같이 쓰는 스택에서는 다른 작업이 데이터를 계속 만들어 숫자가 흔들리므로, 실제 응답을 한 번 받아
 * 그 값을 그대로 다시 돌려주게 해(page.route) 그림들 사이의 숫자를 맞춘다.
 *   - "추가 전" 응답 하나로: dashboard, user-dashboard, summary-cards, 분포·스키마·태그 툴팁, recent-*
 *   - PROD 데이터 소스와 데이터셋 2개를 만든 "추가 후" 응답 하나로: after-add, hourly-trend-tooltip, daily-trend
 *   - 값을 모두 비운 응답으로: empty (시드 데이터를 지우지 않는다)
 * 만든 데이터 소스 · 데이터셋은 끝날 때 지운다.
 */
import { launch, newContext, login, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'catalog-dashboard';
const TZ = 'Asia/Seoul';

// "데이터를 추가하면" 절에 쓰는 임시 데이터
const DS_NAME = '주문 분석 DW';
const DATASETS = [
  {
    name: 'analytics.daily_orders',
    schema_fields: [
      { field_path: 'order_date', field_type: 'DATE' },
      { field_path: 'order_count', field_type: 'INTEGER' },
    ],
  },
  { name: 'analytics.customer_ltv' },
];
const OWNER = 'data-platform';

const admin = await apiToken();
const stats = (token) => api(token, 'GET', `/catalog/stats?tz=${encodeURIComponent(TZ)}`);
const summary = (token) => api(token, 'GET', '/catalog/sync-runs/summary').catch(() => null);

/** 임시 데이터 소스와 그 데이터셋을 지운다(다시 실행해도 안전). */
async function cleanup() {
  const list = await api(admin, 'GET', '/catalog/datasources');
  for (const ds of (list.items ?? list).filter((d) => d.name === DS_NAME)) {
    // force=true — 소속 데이터셋까지 함께 지운다
    await api(admin, 'DELETE', `/catalog/datasources/${ds.id}?force=true`).catch((e) => console.warn(`cleanup: ${e.message}`));
  }
}

/** 통계 · 수집 요약 응답을 고정한다. */
async function freeze(page, statsBody, summaryBody) {
  await page.unroute('**/api/v1/catalog/stats*').catch(() => {});
  await page.unroute('**/api/v1/catalog/sync-runs/summary*').catch(() => {});
  await page.route('**/api/v1/catalog/stats*', (r) => r.fulfill({ json: statsBody }));
  if (summaryBody) await page.route('**/api/v1/catalog/sync-runs/summary*', (r) => r.fulfill({ json: summaryBody }));
}

/** 대시보드를 열고 카드가 그려질 때까지 기다린다(개발 서버에서는 첫 화면이 늦게 그려질 수 있다). */
async function openDashboard(page) {
  await goto(page, '/dashboard');
  await page.locator('[data-slot="card-title"]').first().waitFor({ timeout: 30000 });
  await settle(page, 500);
}

const card = (page, title) =>
  page.locator('[data-slot="card"]').filter({ has: page.locator('[data-slot="card-title"]', { hasText: title }) }).first();

/** 차트 툴팁이 뜨도록 카드 안 도형(처음 또는 마지막) 위에 마우스를 올린다. at 은 도형 안 가로 위치(0~1). */
async function hover(page, cardLoc, selector, { pick = 'first', at = 0.5 } = {}) {
  await cardLoc.scrollIntoViewIfNeeded();
  const boxes = [];
  for (const el of await cardLoc.locator(selector).all()) {
    const b = await el.boundingBox();
    if (b && b.width > 1 && b.height > 1) boxes.push(b);
  }
  if (!boxes.length) throw new Error(`no shape: ${selector}`);
  const b = pick === 'last' ? boxes[boxes.length - 1] : boxes[0];
  await page.mouse.move(b.x + b.width * at, b.y + b.height / 2, { steps: 4 });
  await page.waitForTimeout(400);
}

/** 카드 하나를 찍는다 — 툴팁이 카드 밖으로 나가 있으면 툴팁까지 들어가게 범위를 넓힌다. */
async function shotCard(page, name, cardLoc) {
  await cardLoc.scrollIntoViewIfNeeded();
  const b = await cardLoc.boundingBox();
  let [x1, y1, x2, y2] = [b.x, b.y, b.x + b.width, b.y + b.height];
  const tip = cardLoc.locator('.recharts-tooltip-wrapper > *').first();
  const t = (await tip.count()) ? await tip.boundingBox() : null;
  if (t && t.width > 1) {
    x1 = Math.min(x1, t.x - 8); y1 = Math.min(y1, t.y - 8);
    x2 = Math.max(x2, t.x + t.width + 8); y2 = Math.max(y2, t.y + t.height + 8);
  }
  const vp = page.viewportSize();
  x1 = Math.max(0, Math.floor(x1)); y1 = Math.max(0, Math.floor(y1));
  x2 = Math.min(vp.width, Math.ceil(x2)); y2 = Math.min(vp.height, Math.ceil(y2));
  await shot(page, name, { clip: { x: x1, y: y1, width: x2 - x1, height: y2 - y1 } });
}

await cleanup();
const browser = await launch();
try {
  // ── 추가 전 ──────────────────────────────────────────────────────────────
  const user = await apiToken('mjkim', 'mjkim');
  const [before, beforeUser, sum, sumUser] = await Promise.all([stats(admin), stats(user), summary(admin), summary(user)]);

  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await login(page);
  await freeze(page, before, sum);
  await openDashboard(page);

  // 대시보드 첫 화면 — 요약 카드 · 분포 · 추이 차트
  await shot(page, `${AREA}/dashboard`);

  // 요약 카드 7개(수집 실패 포함)
  await shot(page, `${AREA}/summary-cards`, { target: page.locator('div.grid').filter({ has: card(page, '수집 실패') }).first() });

  // 데이터 소스 유형 분포 — 도넛 조각 툴팁(첫 조각은 3시 방향에서 시계 반대로 그려지므로 고리 위쪽이 가장 큰 유형)
  let c = card(page, '데이터 소스 유형 분포');
  const pie = await c.locator('svg.recharts-surface').first().boundingBox();
  await page.mouse.move(pie.x + pie.width / 2, pie.y + pie.height / 2 - 62, { steps: 4 });
  await page.waitForTimeout(400);
  await shotCard(page, `${AREA}/type-donut-tooltip`, c);

  // 데이터셋 수 상위 데이터 소스 — 막대 툴팁
  c = card(page, '데이터셋 수 상위 데이터 소스');
  await hover(page, c, '.recharts-bar-rectangle', { at: 0.2 });
  await shotCard(page, `${AREA}/top-datasources-tooltip`, c);
  await page.mouse.move(700, 40);

  // 데이터 소스별 스키마 필드 수 — 막대 툴팁
  c = card(page, '데이터 소스별 스키마 필드 수');
  await hover(page, c, '.recharts-bar-rectangle', { at: 0.2 });
  await shotCard(page, `${AREA}/schema-fields-tooltip`, c);
  await page.mouse.move(700, 40);

  // 태그 수 상위 데이터셋 — 막대 툴팁("태그 N개")
  c = card(page, '태그 수 상위 데이터셋');
  await hover(page, c, '.recharts-bar-rectangle', { at: 0.2 });
  await shotCard(page, `${AREA}/top-tagged-tooltip`, c);
  await page.mouse.move(700, 40);

  // 최근 데이터셋 표
  c = card(page, '최근 데이터셋');
  await c.scrollIntoViewIfNeeded();
  await shot(page, `${AREA}/recent-datasets`, { target: c });

  // 최근 데이터셋에서 이름을 눌러 상세 화면으로 이동
  await c.locator('tr', { hasText: 'Sakila (MySQL)' }).first().getByRole('link').click();
  await page.waitForURL(/\/dashboard\/datasets\/\d+/);
  await settle(page, 1200);
  await shot(page, `${AREA}/recent-to-detail`);

  // 일반 사용자(mjkim)로 본 대시보드 — 관리자와 같은 수치, 사이드바에 관리 그룹 없음
  const uctx = await newContext(browser);
  const upage = await uctx.newPage();
  await login(upage, 'mjkim', 'mjkim');
  await freeze(upage, beforeUser, sumUser);
  await openDashboard(upage);
  await shot(upage, `${AREA}/user-dashboard`);
  await uctx.close();

  // 데이터가 없는 대시보드 — 통계 응답을 빈 값으로 바꿔 차트별 안내 문구를 본다
  await freeze(page, {
    total_datasets: 0, total_datasources: 0, total_tags: 0, total_glossary_terms: 0, total_owners: 0, synced_datasets: 0,
    datasets_by_datasource: [], datasets_by_origin: [], datasets_by_datasource_type: [], schema_fields_by_datasource: [],
    top_tagged_datasets: [], daily_datasets_1d: [], daily_datasets_7d: [], daily_datasets_30d: [], recent_datasets: [],
  }, { ...(sum ?? {}), failed_24h: 0, succeeded_24h: 0, active: 0 });
  await openDashboard(page);
  await shot(page, `${AREA}/empty`);

  // ── 추가 후: PROD 데이터 소스와 데이터셋 2개를 직접 등록 ─────────────────────
  const ds = await api(admin, 'POST', '/catalog/datasources', { name: DS_NAME, type: 'postgresql', origin: 'PROD' });
  for (const d of DATASETS) {
    await api(admin, 'POST', '/catalog/datasets', { ...d, datasource_id: ds.id, origin: 'PROD', owners: [{ owner_name: OWNER }] });
  }
  const after = await stats(admin);
  await freeze(page, after, sum);
  await openDashboard(page);

  // 데이터 추가 후 — 카드 · 환경별 분포 · 추이 차트 반영
  await shot(page, `${AREA}/after-add`);

  // 신규 데이터셋 — 7일(처음 상태), 등록이 없던 날은 0
  c = card(page, '신규 데이터셋');
  await shot(page, `${AREA}/daily-trend`, { target: c });

  // 신규 데이터셋 — 24시간으로 바꾸고 현재 시각 막대의 툴팁
  await c.getByRole('tab', { name: '24시간' }).click();
  await page.waitForTimeout(400);
  await hover(page, c, '.recharts-bar-rectangle', { pick: 'last' });
  await shotCard(page, `${AREA}/hourly-trend-tooltip`, c);
} finally {
  await cleanup();
  await browser.close();
}
