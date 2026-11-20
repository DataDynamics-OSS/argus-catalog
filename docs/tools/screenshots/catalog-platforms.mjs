/**
 * 매뉴얼 "데이터 소스"(pages/catalog/platforms.adoc) 화면 캡처.
 *
 * 시드 상태 그림을 먼저 찍고, 그 뒤 임시 시스템 "온라인 쇼핑몰" 과 데이터 소스 "쇼핑몰 주문 DB"
 * (PostgreSQL, 통합 테스트 스택의 source-postgres)를 화면에서 만들어 추가 · 편집 · 수집 · 삭제 그림을 찍는다.
 * 끝나면 임시 시스템 · 데이터 소스를 지운다(KEEP=1 이면 남겨 둔다). 다시 실행해도 같은 그림이 나온다.
 *
 * 시드 데이터 소스(Sakila (MySQL) 등)는 조회만 한다.
 */
import { open, goto, settle, shot, apiToken, api, UI } from './lib.mjs';

const A = 'catalog-platforms';
const SYS_NAME = '온라인 쇼핑몰';
const DS_NAME = '쇼핑몰 주문 DB';
const DS2_NAME = '쇼핑몰 통계 DB';
const PG = { host: 'source-postgres', port: '5432', database: 'shop', username: 'shop', password: 'shop' };

const token = await apiToken();

// ---- 도우미 -------------------------------------------------------------------------------

const listDatasources = () => api(token, 'GET', '/catalog/datasources');
const findDs = async () => (await listDatasources()).find(d => d.name === DS_NAME);

function flattenSystems(topology) {
  const out = [...(topology.unassigned?.systems || topology.unassigned_systems || [])];
  const walk = o => { out.push(...(o.systems || [])); (o.children || []).forEach(walk); };
  (topology.organizations || []).forEach(walk);
  return out;
}
function findOrg(topology, name) {
  let hit = null;
  const walk = o => { if (o.name === name) hit = o; (o.children || []).forEach(walk); };
  (topology.organizations || []).forEach(walk);
  return hit;
}

/** 임시 데이터 소스 · 시스템을 지운다(진행 중인 수집이 있으면 끝나기를 기다린다). */
async function cleanup() {
  for (const ds of (await listDatasources()).filter(d => [DS_NAME, DS2_NAME].includes(d.name))) {
    for (let i = 0; i < 30; i++) {
      try { await api(token, 'DELETE', `/catalog/datasources/${ds.id}?force=true`); break; }
      catch (e) { if (e.status !== 409) throw e; await new Promise(r => setTimeout(r, 2000)); }
    }
  }
  const topo = await api(token, 'GET', '/catalog/topology');
  for (const s of flattenSystems(topo).filter(s => s.name === SYS_NAME)) {
    await api(token, 'DELETE', `/catalog/systems/${s.id}?force=true`);
  }
}

const tree = page => page.getByRole('tree');
const row = (page, text) => tree(page).getByRole('treeitem').filter({ hasText: text }).first();
const dialog = page => page.getByRole('dialog').last();

/** 미분류 묶음을 접는다 — 다른 통합 테스트가 남긴 데이터 소스가 그림에 섞이지 않게. */
async function collapseUnassigned(page) {
  const u = page.locator('[role=treeitem][data-tree-key="__unassigned__"]');
  if (await u.count() && (await u.getAttribute('aria-expanded')) === 'true') {
    await u.locator('button').first().click();
    await page.waitForTimeout(200);
  }
}
async function openDatasources(page, query = '') {
  await goto(page, `/dashboard/datasources${query}`);
  await tree(page).waitFor();
  await collapseUnassigned(page);
  await page.mouse.move(900, 700);
}
const toast = (page, text) => page.locator('[data-sonner-toast]').filter({ hasText: text }).first();
/** 알림(토스트)이 사라질 때까지 기다린다. */
async function toastsGone(page) {
  await page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
}
async function waitRunsDone(dsId) {
  for (let i = 0; i < 90; i++) {
    const runs = await api(token, 'GET', `/catalog/datasources/${dsId}/sync-runs`);
    const items = Array.isArray(runs) ? runs : runs.items || [];
    if (items.length && !items.some(r => ['PENDING', 'QUEUED', 'RUNNING'].includes(r.status))) return items;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error('수집이 끝나지 않았습니다');
}

// ---- 준비 ---------------------------------------------------------------------------------

if (!process.env.REUSE) await cleanup();

const { browser, page } = await open();

// ---- 1. 시드 상태 -------------------------------------------------------------------------

try {

if (!(await findDs())) {
  // 데이터 소스 화면 — 조직 · 시스템 · 데이터 소스 트리(아무것도 고르지 않은 상태)
  await openDatasources(page);
  await shot(page, `${A}/topology-tree`);

  // 조직 루트 우클릭 메뉴 — 조직 추가 · 데이터 소스 추가
  await row(page, '조직').click({ button: 'right' });
  await page.getByRole('menu').waitFor();
  await page.waitForTimeout(300);
  await shot(page, `${A}/root-context-menu`);
  await page.keyboard.press('Escape');

  // 시스템 추가 대화상자 — 조직(미디어사업본부) 우클릭 › 시스템 추가, 값을 채운 상태
  await row(page, '미디어사업본부').click({ button: 'right' });
  await page.getByRole('menuitem', { name: '시스템 추가' }).click();
  await dialog(page).getByPlaceholder('예: 고객데이터 시스템').fill(SYS_NAME);
  await dialog(page).getByPlaceholder('한 줄 요약').fill('쇼핑몰 주문 · 결제 시스템');
  await dialog(page).getByPlaceholder('상세 설명').fill('상품 · 고객 · 주문을 관리하는 온라인 쇼핑몰의 운영 시스템입니다.');
  await dialog(page).getByPlaceholder('예: 홍길동').fill('김민지 (mjkim@example.com, 커머스개발팀)');
  await dialog(page).getByRole('heading').click(); // 입력란의 선택 표시를 없앤다
  await page.waitForTimeout(300);
  await shot(page, `${A}/system-add`);
  await dialog(page).getByRole('button', { name: '저장' }).click();
  await dialog(page).waitFor({ state: 'detached' });
  await toastsGone(page);

  // "무엇을 추가하나요?" — 시스템 우클릭 › 데이터 소스 추가, 유형 선택(카탈로그 · 데이터베이스 · 쿼리 엔진)
  await row(page, SYS_NAME).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '데이터 소스 추가' }).click();
  await dialog(page).getByText('무엇을 추가하나요?').waitFor();
  // 유형 카드가 세 묶음 모두 보이도록 이 그림만 화면 높이를 늘려 찍는다
  await page.setViewportSize({ width: 1440, height: 1060 });
  await page.waitForTimeout(400);
  await shot(page, `${A}/type-select`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);

  // PostgreSQL 추가 — 연결 설정을 입력하고 연결 테스트에 성공한 상태
  await dialog(page).getByRole('button', { name: /PostgreSQL/ }).click();
  const d = dialog(page);
  await d.getByPlaceholder('예: 운영 PostgreSQL').fill(DS_NAME);
  await d.getByLabel('Host').fill(PG.host);
  await d.getByLabel('Port').fill(PG.port);
  await d.getByLabel('Database').fill(PG.database);
  await d.getByLabel('Username').fill(PG.username);
  await d.getByLabel('Password').fill(PG.password);
  await d.getByRole('button', { name: '연결 테스트' }).click();
  await toast(page, '연결에 성공했습니다').waitFor();
  await page.waitForTimeout(400);
  await shot(page, `${A}/add-form-test-ok`);
  await toastsGone(page);

  // 생성 뒤 "<이름> — 수집 범위" 단계 — 나중에 · 범위 선택…
  await d.getByRole('button', { name: '추가', exact: true }).click();
  await page.getByText(`${DS_NAME} — 수집 범위`).waitFor();
  await toastsGone(page);
  await shot(page, `${A}/add-scope-step`);

  // 수집 범위 선택 대화상자 — 선택한 스키마만 · 트리 · 제외 패턴 · 저장하고 지금 수집
  await page.getByRole('button', { name: '범위 선택…' }).click();
  const sd = dialog(page);
  await sd.getByText('수집 범위 선택').waitFor();
  await sd.getByRole('checkbox', { name: 'shop', exact: true }).waitFor();
  await sd.getByRole('checkbox', { name: 'shop', exact: true }).click();
  await sd.locator('div.flex.items-center')
    .filter({ has: page.getByRole('checkbox', { name: 'shop', exact: true }) })
    .getByRole('button', { name: '펼치기' }).click();
  await sd.getByRole('checkbox', { name: 'orders', exact: true }).waitFor();
  await sd.locator('#sync-exclude').fill('tmp_*');
  await sd.locator('#sync-exclude').press('Enter');
  await page.mouse.move(1300, 800);
  await page.waitForTimeout(600);
  await shot(page, `${A}/sync-scope-dialog`);
  await sd.getByRole('button', { name: '저장하고 지금 수집' }).click();
  await sd.waitFor({ state: 'detached' });
  await toastsGone(page);
}

// ---- 2. 임시 데이터 소스로 찍는 그림 -------------------------------------------------------

const ds = await findDs();
if (!ds) throw new Error(`데이터 소스 "${DS_NAME}" 가 없습니다`);
const dsUrl = `?datasource=${encodeURIComponent(ds.datasource_id)}`;
await waitRunsDone(ds.id);
// 최근 실행이 두 줄 이상 보이게 저장된 범위로 한 번 더 수집한다
if ((await waitRunsDone(ds.id)).length < 2) {
  await api(token, 'POST', `/catalog/datasources/${ds.id}/sync-runs`);
  await waitRunsDone(ds.id);
}
// 수집에 실패한 실행도 보이게, 닿지 않는 주소의 데이터 소스를 하나 더 만들어 수집을 실행한다
if (!(await listDatasources()).some(d => d.name === DS2_NAME)) {
  const sys = flattenSystems(await api(token, 'GET', '/catalog/topology')).find(x => x.name === SYS_NAME);
  const d2 = await api(token, 'POST', '/catalog/datasources', { name: DS2_NAME, type: 'postgresql', origin: 'DEV' });
  await api(token, 'PUT', `/catalog/datasources/${d2.id}/configuration`, {
    config: { host: 'stats-postgres', port: 5432, database: 'stats', username: 'stats', password: 'stats', ssl_enabled: false },
  });
  if (sys) await api(token, 'PUT', `/catalog/datasources/${d2.id}/system`, { system_id: sys.id });
  await api(token, 'PUT', `/catalog/datasources/${d2.id}/sync-scope`, { scope: { include_all: true, include: [], exclude: [] } });
  await api(token, 'POST', `/catalog/datasources/${d2.id}/sync-runs`);
  await waitRunsDone(d2.id);
}

const main = page.locator('main').last();
const field = label => page.getByLabel(new RegExp(`^${label}`)).first();

// 데이터 소스 상세 — 머리 요약(소속 · ID · 데이터셋 수 · 마지막 수집 · 카탈로그)과 기본 정보 탭
await openDatasources(page, dsUrl);
await field('Host').waitFor();
await shot(page, `${A}/datasource-detail`);

// 저장된 비밀번호 — Password 를 비운 채 연결 테스트 성공
await field('SSL Enabled').scrollIntoViewIfNeeded().catch(() => {});
await page.getByRole('button', { name: '연결 테스트' }).click();
await toast(page, '연결에 성공했습니다').waitFor();
await page.waitForTimeout(400);
await shot(page, `${A}/stored-secret-test`);
await toastsGone(page);

// 연결 테스트 실패 — 틀린 비밀번호로 "인증에 실패했습니다"
await field('Password').fill('wrong-password');
await page.getByRole('button', { name: '연결 테스트' }).click();
await toast(page, '인증에 실패').waitFor();
await page.waitForTimeout(400);
await shot(page, `${A}/test-auth-failed`);
await toastsGone(page);
await page.getByRole('button', { name: '취소', exact: true }).click();

// Host 변경 — 저장된 비밀값을 쓸 수 없다는 안내, 저장 비활성
await openDatasources(page, dsUrl);
await field('Host').fill('shop-db.example.com');
await page.getByText('접속 대상이 바뀌어').first().waitFor();
await page.getByText('저장 안 된 변경이 있습니다').scrollIntoViewIfNeeded();
await page.mouse.move(1300, 500);
await page.waitForTimeout(300);
await shot(page, `${A}/target-changed`);
await page.getByRole('button', { name: '취소', exact: true }).click();

// 저장 안 된 변경 줄 — 표시 이름을 고치면 화면 아래에 취소 · 저장이 붙는다
await openDatasources(page, dsUrl);
await field('표시 이름').fill(`${DS_NAME} (운영)`);
await page.getByText('저장 안 된 변경이 있습니다').scrollIntoViewIfNeeded();
await page.mouse.move(1300, 500);
await page.waitForTimeout(300);
await shot(page, `${A}/unsaved-changes`);
await page.getByRole('button', { name: '취소', exact: true }).click();

// 환경 변경 확인 — DEV 에서 STAGING 으로 바꾸고 저장을 누른 상태
await page.getByRole('combobox').filter({ hasText: 'DEV' }).first().click();
await page.getByRole('option', { name: /STAGING/ }).click();
await page.getByRole('button', { name: '저장', exact: true }).click();
await page.getByRole('alertdialog').or(page.getByRole('dialog')).filter({ hasText: '환경 변경' }).first().waitFor();
await page.waitForTimeout(400);
await shot(page, `${A}/origin-confirm`);
await page.getByRole('button', { name: '취소', exact: true }).last().click();
await page.waitForTimeout(300);
await page.getByRole('button', { name: '취소', exact: true }).click();

// 수집 영역 — 수집 범위 · 범위 선택… · 지금 수집 · 이번 한 번만… · 예약 · 최근 실행
await openDatasources(page, `${dsUrl}&tab=sync`);
await page.getByRole('switch', { name: '예약 수집' }).waitFor();
if ((await page.getByRole('switch', { name: '예약 수집' }).getAttribute('aria-checked')) !== 'true') {
  await page.getByRole('switch', { name: '예약 수집' }).click();
  await toast(page, '예약 수집').waitFor();
  await toastsGone(page);
}
await page.mouse.move(900, 760);
await shot(page, `${A}/sync-panel`);

// 수집 실행 상세 대화상자 — 최근 실행의 실행 번호를 누른다
await page.getByRole('button', { name: /^#\d+$/ }).or(page.getByRole('link', { name: /^#\d+$/ })).first().click();
await dialog(page).getByText(/수집 실행 #\d+/).waitFor();
await settle(page);
await shot(page, `${A}/sync-run-detail`);
await page.keyboard.press('Escape');

// 메타데이터 수집 화면 — metadata-sync 상태 · 필터 · 실행 표
await goto(page, '/dashboard/sync-runs');
await page.getByText(DS_NAME).first().waitFor();
await page.mouse.move(900, 860);
await shot(page, `${A}/sync-runs`);

// 동기화 후 데이터셋 목록 — 데이터 소스 필터로 확인
await goto(page, `/dashboard/datasets?datasource=${encodeURIComponent(ds.datasource_id)}`);
await page.getByText('shop.orders').first().waitFor();
await shot(page, `${A}/sync-datasets`);

// 데이터 소스 전체 ER 다이어그램 — 시드 Sakila (MySQL, 테이블 23개)의 ER 다이어그램 탭. 열자마자 전체가 그림 칸에 맞춰진다.
// 그림 칸(높이 720)이 900 높이 화면의 상세 영역보다 길어 아래가 스크롤 밖에 놓이므로, 이 그림만 화면 높이를 늘려 찍는다
await page.setViewportSize({ width: 1440, height: 1130 });
await openDatasources(page, '?datasource=sakila-mysql&tab=erd');
await page.getByRole('button', { name: 'Mermaid 복사' }).waitFor();
await page.locator('.react-flow__node').first().waitFor();
await page.mouse.move(1300, 120);
await settle(page, 1500);
await shot(page, `${A}/erd`);
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(300);

// 권한 관리 — 기능 권한 표의 데이터 소스 그룹(데이터 소스 관리 · 연결 설정 조회 · 메타데이터 동기화)
await goto(page, '/dashboard/permissions');
{
  const group = page.locator('tr').filter({ hasText: /^\s*데이터 소스\s*3/ }).getByRole('button');
  if ((await group.getAttribute('aria-expanded')) !== 'true') await group.click();
  await group.evaluate(el => { el.scrollIntoView({ block: 'start' }); window.scrollBy(0, -330); });
  for (const sc of await page.locator('.overflow-auto').all()) await sc.evaluate(() => {}).catch(() => {});
  await page.mouse.move(1300, 120);
  await page.waitForTimeout(400);
  await shot(page, `${A}/feature-matrix`);
}

// 시스템 삭제 확인 — 소속 데이터 소스는 지워지지 않고 미분류로 이동
await openDatasources(page);
await row(page, SYS_NAME).click({ button: 'right' });
await page.getByRole('menuitem', { name: '삭제' }).click();
await page.getByText('시스템 삭제').first().waitFor();
await page.waitForTimeout(400);
await shot(page, `${A}/system-delete-confirm`);
await page.getByRole('button', { name: '취소', exact: true }).click();

// 데이터 소스 삭제 — 함께 지워지는 데이터셋 수 안내, 확인 문자열을 입력한 상태
await row(page, DS_NAME).click({ button: 'right' });
await page.getByRole('menuitem', { name: '삭제' }).click();
await page.getByText('데이터 소스 삭제').first().waitFor();
await page.getByText(/함께 삭제됩니다/).first().waitFor();
await dialog(page).getByRole('textbox').fill(`DELETE ${DS_NAME}`);
await dialog(page).getByRole('heading').click(); // 입력란의 선택 표시를 없앤다
await page.waitForTimeout(400);
await shot(page, `${A}/delete-impact`);
await page.getByRole('button', { name: '취소', exact: true }).click();

// 조회 전용 모드 — 데이터 소스 관리 권한이 없는 일반 사용자(mjkim)로 본 데이터 소스 상세
{
  const u = await open('mjkim', 'mjkim');
  try {
    await openDatasources(u.page, '?datasource=sakila-mysql');
    await u.page.getByText('(조회 전용)').first().waitFor();
    await settle(u.page);
    await shot(u.page, `${A}/read-only`);
  } finally {
    await u.browser.close();
  }
}

} catch (e) {
  // 실패하면 그때 화면을 남긴다(DEBUG_DIR 지정 시)
  if (process.env.DEBUG_DIR) await page.screenshot({ path: `${process.env.DEBUG_DIR}/fail.png` }).catch(() => {});
  throw e;
} finally {
  await browser.close();
  if (!process.env.KEEP) await cleanup();
}
