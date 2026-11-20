/**
 * 거버넌스 → 알림 (governance/alerts.adoc) 화면 캡처.
 *
 * 상황 만들기: 데이터 소스 it-alert-ds 와 데이터셋 it_alert.src · it_alert.dst(리니지 + email 컬럼 매핑),
 * 알림 규칙 두 개를 만들고 스키마 변경으로 알림을 발생시킨다.
 * 품질 진단 실패 알림: 시드 데이터셋 shop.customers 에 품질 규칙 세 개(둘은 실제 데이터에서 위반)와
 * 품질 진단 실패 알림 규칙을 임시로 만들고 정밀 진단(POST /quality/datasets/{id}/runs)을 돌린다 — 품질 워커가 떠 있어야 한다.
 * 끝나면 만든 규칙 · 데이터셋 · 데이터 소스를 지운다(KEEP=1 이면 남겨 둔다 — 일부만 다시 찍을 때).
 * 지울 수 없어 남는 것: shop.customers 의 진단 실행 이력 · 점수 기록, 해결로 바꾼 품질 알림 1건(알림 삭제 API 없음).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const DS_NAME = 'it-alert-ds';
const SRC_NAME = 'it_alert.src';
const DST_NAME = 'it_alert.dst';
const RULES = {
  any: '주문 원본 스키마 변경 감시',
  mapping: '주문 매핑 컬럼 변경 감시',
};

const BASE = [
  { field_path: 'id', field_type: 'INT', is_primary_key: 'true', nullable: 'false', ordinal: 0 },
  { field_path: 'email', field_type: 'VARCHAR(100)', ordinal: 1 },
  { field_path: 'amount', field_type: 'NUMERIC(10,2)', ordinal: 2 },
  { field_path: 'note', field_type: 'TEXT', ordinal: 3 },
];

// 품질 진단 실패 알림용 — shop.customers(원천 source-postgres)에는 일부러 넣은 위반이 있다(email 'not-an-email', grade 'VIP')
const Q_DATASET = 'shop.customers';
const Q_ALERT_RULE = '고객 데이터 품질 실패 통지';
const Q_RULES = [
  { rule_name: '이메일 형식', check_type: 'REGEX', column_name: 'email', expected_value: '^[^@]+@[^@]+\\.[^@]+$', severity: 'WARNING' },
  { rule_name: '고객 등급 허용값', check_type: 'ACCEPTED_VALUES', column_name: 'grade', expected_value: '["BRONZE","SILVER","GOLD"]', severity: 'WARNING' },
  { rule_name: '고객 번호 유일', check_type: 'UNIQUE', column_name: 'customer_id', severity: 'BREAKING' },
];

const token = await apiToken();
const call = (m, p, b) => api(token, m, p, b);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function findDatasets() {
  const r = await call('GET', `/catalog/datasets?search=it_alert&page_size=50`);
  const items = r.items || r;
  return { src: items.find(d => d.name === SRC_NAME), dst: items.find(d => d.name === DST_NAME) };
}
async function findDatasource() {
  const r = await call('GET', '/catalog/datasources');
  return (r.items || r).find(d => d.name === DS_NAME);
}
async function myRules() {
  const all = await call('GET', '/alerts/rules');
  return all.filter(r => [...Object.values(RULES), Q_ALERT_RULE].includes(r.rule_name));
}
async function qualityDataset() {
  const r = await call('GET', `/catalog/datasets?search=${Q_DATASET}&page_size=50`);
  const d = (r.items || r).find(x => x.name === Q_DATASET);
  if (!d) throw new Error(`데이터셋 없음: ${Q_DATASET}`);
  return d;
}
/** 임시 품질 규칙을 지우고, 그 때문에 생긴 품질 알림을 해결로 바꾼다(알림 규칙은 cleanup 의 myRules 가 지운다). */
async function cleanupQuality() {
  const d = await qualityDataset();
  const al = await call('GET', `/alerts?dataset_id=${d.id}&alert_type=QUALITY_FAILED&page_size=100`).catch(() => ({ items: [] }));
  const ids = al.items
    .filter(a => (a.status === 'OPEN' || a.status === 'ACKNOWLEDGED') && a.change_summary.includes(Q_RULES[0].rule_name))
    .map(a => a.id);
  if (ids.length) await call('POST', '/alerts/bulk-status', { ids, status: 'RESOLVED' }).catch(e => console.log(e.message));
  const names = Q_RULES.map(q => q.rule_name);
  for (const r of await call('GET', `/quality/rules?dataset_id=${d.id}`)) {
    if (names.includes(r.rule_name)) await call('DELETE', `/quality/rules/${r.id}`).catch(e => console.log(e.message));
  }
}

/** 만든 것을 모두 지운다. */
async function cleanup() {
  await cleanupQuality().catch(e => console.log(e.message));
  for (const r of await myRules()) await call('DELETE', `/alerts/rules/${r.id}`).catch(e => console.log(e.message));
  const { src, dst } = await findDatasets();
  for (const d of [src, dst]) {
    if (!d) continue;
    // 남은 알림은 해결로 바꿔 미확인 수를 어지럽히지 않게 한다(데이터셋 삭제로 지워지지 않는 경우 대비)
    const al = await call('GET', `/alerts?dataset_id=${d.id}&page_size=100`).catch(() => ({ items: [] }));
    const ids = al.items.filter(a => a.status === 'OPEN' || a.status === 'ACKNOWLEDGED').map(a => a.id);
    if (ids.length) await call('POST', '/alerts/bulk-status', { ids, status: 'RESOLVED' }).catch(e => console.log(e.message));
    await call('DELETE', `/catalog/datasets/${d.id}`).catch(e => console.log(e.message));
  }
  const ds = await findDatasource();
  if (ds) await call('DELETE', `/catalog/datasources/${ds.id}`).catch(e => console.log(e.message));
}

if (process.env.CLEANUP_ONLY) { await cleanup(); process.exit(0); }

// ── 상황 만들기 ──────────────────────────────────────────────────────────────
await cleanup();
const ds = await call('POST', '/catalog/datasources', { name: DS_NAME, type: 'postgresql' });
const src = await call('POST', '/catalog/datasets', { name: SRC_NAME, datasource_id: ds.id, schema_fields: BASE });
const dst = await call('POST', '/catalog/datasets', {
  name: DST_NAME, datasource_id: ds.id,
  schema_fields: [{ field_path: 'id', field_type: 'INT', ordinal: 0 }, { field_path: 'email', field_type: 'VARCHAR(100)', ordinal: 1 }],
});
await call('POST', `/catalog/datasets/${src.id}/owners`, { owner_name: 'admin', owner_type: 'TECHNICAL_OWNER' });
const lin = await call('POST', '/catalog/lineage', {
  source_dataset_id: src.id, target_dataset_id: dst.id,
  column_mappings: [{ source_column: 'email', target_column: 'email' }],
});
const setSchema = async fields => { await call('PUT', `/catalog/datasets/${src.id}/schema`, fields); await sleep(1200); };

await call('POST', '/alerts/rules', {
  rule_name: RULES.any, scope_type: 'DATASET', scope_id: src.id, trigger_type: 'ANY',
  channels: 'IN_APP,WEBHOOK', webhook_url: 'http://webhook-receiver:8080/alert/orders',
  notify_owners: 'true', subscribers: 'mjkim', description: '주문 원본 테이블의 컬럼이 바뀌면 담당자에게 알립니다.',
});
await call('POST', '/alerts/rules', {
  rule_name: RULES.mapping, scope_type: 'LINEAGE', scope_id: lin.id, trigger_type: 'MAPPING_BROKEN',
  channels: 'IN_APP', notify_owners: 'true', description: '하위 데이터셋으로 이어진 컬럼이 바뀌면 알립니다.',
});
// amount 타입 변경 → 경고
const modified = BASE.map(c => (c.field_path === 'amount' ? { ...c, field_type: 'NUMERIC(12,2)' } : c));
await setSchema(modified);
// 매핑 컬럼(email) 삭제 → 치명, 영향 데이터셋 it_alert.dst
await setSchema(modified.filter(c => c.field_path !== 'email'));
// email 을 다시 추가 → 정보(컬럼 추가 1개)
await setSchema(modified);

const mine = (await call('GET', `/alerts?dataset_id=${src.id}&page_size=100`)).items;
const pick = (pred, what) => { const a = mine.find(pred); if (!a) throw new Error(`알림 없음: ${what}`); return a; };
const aAdd = pick(a => a.rule_name === RULES.any && /추가 1개/.test(a.change_summary), '컬럼 추가');
const aMap = pick(a => a.rule_name === RULES.mapping && /삭제/.test(a.change_summary), '매핑 삭제');

// 품질 진단 실패 알림 — shop.customers 에 임시 품질 규칙과 알림 규칙을 만들고 정밀 진단을 돌린다(워커가 평가)
const qds = await qualityDataset();
for (const q of Q_RULES) await call('POST', '/quality/rules', { dataset_id: qds.id, ...q });
await call('POST', '/alerts/rules', {
  rule_name: Q_ALERT_RULE, scope_type: 'DATASET', scope_id: qds.id, trigger_type: 'QUALITY_FAILED',
  channels: 'IN_APP', notify_owners: 'true', description: '고객 테이블의 품질 진단이 실패하면 알립니다.',
});
const run = await call('POST', `/quality/datasets/${qds.id}/runs`, {});
let runState = run;
for (let i = 0; i < 90 && ['QUEUED', 'RUNNING', 'PENDING'].includes(runState.status); i++) {
  await sleep(2000);
  runState = await call('GET', `/quality/runs/${run.id}`);
}
console.log('quality run:', run.id, runState.status, runState.score);
const aQual = (await call('GET', `/alerts?dataset_id=${qds.id}&alert_type=QUALITY_FAILED&page_size=50`)).items
  .find(a => a.rule_name === Q_ALERT_RULE && a.status === 'OPEN');
if (!aQual) throw new Error(`알림 없음: 품질 진단 실패(실행 ${run.id} ${runState.status})`);
console.log('alerts:', mine.length, { add: aAdd.id, map: aMap.id, qual: aQual.id });

// ── 캡처 ────────────────────────────────────────────────────────────────────
const { browser, page } = await open();
const P = 'governance-alerts';
const selectNth = async (n, label) => {
  await page.locator('main [role=combobox], [data-slot=select-trigger]').nth(n).click();
  await page.getByRole('option', { name: label, exact: true }).click();
  await settle(page);
};
// 처리 결과 토스트가 사라질 때까지 기다린다(그림을 가리지 않게)
const toastGone = async () => {
  await settle(page);
  await page.mouse.move(700, 120);
  await page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
  await settle(page, 300);
};
const rowOf = id => page.locator('tr', { has: page.getByLabel(`알림 ${id} 선택`) });

try {
  // 알림 목록 — 처음 열면 미확인 알림만
  await goto(page, '/dashboard/alerts');
  await shot(page, `${P}/list`);

  // 심각도 '치명'으로 걸러 본 모습
  await selectNth(1, '치명');
  await shot(page, `${P}/list-filter`);

  // 알림 상세 — 컬럼 추가 1개(정보)
  await goto(page, `/dashboard/alerts?alert=${aAdd.id}`);
  await page.getByRole('dialog').waitFor();
  await shot(page, `${P}/detail`);

  // 매핑 컬럼 삭제 알림 상세 — 영향 데이터셋 it_alert.dst
  await goto(page, `/dashboard/alerts?alert=${aMap.id}`);
  await page.getByRole('dialog').waitFor();
  await shot(page, `${P}/detail-mapping`);

  // 품질 진단 실패 알림 상세 — shop.customers 정밀 진단에서 실패한 규칙 이름과 점수
  await goto(page, `/dashboard/alerts?alert=${aQual.id}`);
  await page.getByRole('dialog').waitFor();
  await shot(page, `${P}/detail-quality`);

  // 미확인 행 — 확인 · 해결 · 무시 버튼 (전체 상태로 보아 처리한 행이 사라지지 않게)
  await goto(page, '/dashboard/alerts');
  await selectNth(0, '전체 상태');
  await shot(page, `${P}/row-open`);

  // 확인 후 — 해결 · 무시 · 다시 열기 버튼
  await rowOf(aAdd.id).getByRole('button', { name: '확인', exact: true }).click();
  await toastGone();
  await shot(page, `${P}/row-acknowledged`);

  // 해결 후 — 다시 열기 버튼만 남음
  await rowOf(aAdd.id).getByRole('button', { name: '해결', exact: true }).click();
  await toastGone();
  await shot(page, `${P}/row-resolved`);
  await rowOf(aAdd.id).getByRole('button', { name: '다시 열기', exact: true }).click();
  await settle(page);

  // 2건 선택 — 일괄 처리 버튼과 오래된 알림 정리
  await goto(page, '/dashboard/alerts');
  const boxes = page.locator('tbody [role=checkbox]');
  await boxes.nth(0).click();
  await boxes.nth(1).click();
  await page.mouse.move(700, 120);
  await settle(page, 300);
  await shot(page, `${P}/bulk`);

  // 헤더 벨 — 미확인 건수와 최신 알림 5건
  await goto(page, '/dashboard/alerts');
  await page.locator('header button:has(svg.lucide-bell)').click();
  await settle(page);
  await shot(page, `${P}/bell`);
  await page.keyboard.press('Escape');

  // 규칙 탭 — 규칙 카드
  await goto(page, '/dashboard/alerts');
  await page.getByRole('tab', { name: '규칙' }).click();
  await settle(page);
  await shot(page, `${P}/rules`);

  // 규칙 탭 도움말 펼침
  const help = page.getByText('도움말 — 규칙은 언제 적용되나요?');
  await help.click();
  await settle(page, 400);
  // 목록 영역을 끝까지 내려 펼친 도움말이 보이게 한다
  await page.mouse.move(800, 500);
  await page.mouse.wheel(0, 5000);
  await settle(page, 300);
  await shot(page, `${P}/rules-help`);

  // 규칙 추가 1단계 — 이름 · 감시 대상 · 트리거 · 심각도
  await goto(page, '/dashboard/alerts');
  await page.getByRole('tab', { name: '규칙' }).click();
  await settle(page);
  await page.getByRole('button', { name: '규칙 추가' }).click();
  const dlg = page.getByRole('dialog');
  await dlg.waitFor();
  await dlg.getByPlaceholder('PII 데이터 컬럼 삭제 감시').fill('PII 데이터 컬럼 삭제 감시');
  await settle(page, 300);
  await shot(page, `${P}/rule-step1`);

  // 트리거 선택지 — 목록을 펼친 모습
  const trigger = dlg.getByRole('combobox').filter({ hasText: /스키마 변경|컬럼 감시|품질|모든/ }).first();
  await trigger.click();
  await settle(page, 300);
  await shot(page, `${P}/rule-triggers`);

  // 컬럼 감시 — 감시 컬럼을 넣기 전에는 다음 버튼이 잠김
  await page.getByRole('option', { name: '컬럼 감시 (특정 컬럼)' }).click();
  await settle(page, 300);
  await shot(page, `${P}/rule-column-watch`);

  // 규칙 추가 2단계 — 채널 · 구독자 · 소유자 알림
  await trigger.click();
  await page.getByRole('option', { name: '모든 스키마 변경' }).click();
  await dlg.getByRole('button', { name: '다음' }).click();
  await settle(page, 300);
  await dlg.getByText('Webhook', { exact: true }).click();
  await dlg.getByPlaceholder('https://hooks.slack.com/services/...').fill('https://hooks.slack.com/services/T000/B000/XXXX');
  await settle(page, 300);
  await shot(page, `${P}/rule-step2`);
  await page.keyboard.press('Escape');

  // 일반 사용자 화면 — 상태 버튼이 없음
  const u = await open('mjkim', 'mjkim');
  await goto(u.page, '/dashboard/alerts');
  await shot(u.page, `${P}/user-readonly`);
  await u.browser.close();
} finally {
  await browser.close();
  if (!process.env.KEEP) await cleanup();
}
