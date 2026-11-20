/**
 * 매뉴얼 화면 캡처 — 거버넌스 › 변경 관리 · 내 결재함 (docs/modules/ROOT/pages/governance/change-management.adoc)
 *
 * 실행 (저장소 루트에서):
 *   PLAYWRIGHT_DIR=... CHROMIUM_PATH=... node docs/tools/screenshots/governance-change-management.mjs
 *   ONLY=list,my-approvals   일부만 다시 찍기(이미 만든 변경 요청을 제목으로 찾아 다시 쓴다)
 *   RESET=1                  캡처용 데이터 소스 · 데이터셋(딸린 변경 요청 포함)을 지우고 처음부터 다시 만든다
 *   CLEANUP=1                캡처용 데이터를 지우고 끝낸다(캡처는 하지 않음)
 *
 * 시드 데이터셋(Sakila)의 스키마를 바꾸지 않도록 전용 데이터 소스 "대여 DW" 와 데이터셋 두 개를 만들어 쓴다.
 *   dw.rental_orders(대여 주문) → dw.rental_daily_mart(일별 대여 마트)  리니지 + 컬럼 매핑, 소비자 "연체 알림 배치"
 * 사람: 요청자 mjkim, 결재자 shlee(1단계) → admin(2단계), 참조자 jypark. 결재는 Temporal 워커가 처리하므로 상태를 기다린다.
 *
 * 변경 요청(제목으로 구분) — 끝난 뒤 상태
 *   A 고객 이메일 컬럼 길이 확장      적용완료 (참조자 이메일 · 소비자 Webhook 통지)
 *   B 레거시 플래그 컬럼 삭제         반려됨   (1단계 반려)
 *   C 대여 반납 예정일 컬럼 추가      결재중   (1단계 승인, 2단계 대기)
 *   D 고객 식별 컬럼 설명 보강        적용완료 (1단계 위임)
 *   E 매장 코드 타입 확장            적용예정 (일주일 뒤)
 *   F 대여 채널 컬럼 추가            적용완료 (D-7 · D-1 · 적용 Webhook 통지)
 *   G 결제 수단 컬럼 표기 통일        임시저장 (참조자 Slack 통지)
 *
 * 참조자 통지를 재현하느라 설정 › 연동의 이메일 · 알림 · 변경관리 값을 잠깐 바꾸고 곧바로 원래 값으로 되돌린다.
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'governance-change-management';
const RECV = 'http://webhook-receiver:8080';
const TEMPORAL_UI = process.env.TEMPORAL_UI || 'http://127.0.0.1:46234';
const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const want = (...names) => !only.length || names.some(n => only.includes(n));

const DS_NAME = '대여 DW';
const ORDERS_QN = 'dw.rental_orders';
const MART_QN = 'dw.rental_daily_mart';
const email = u => `${u}@data-dynamics.io`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const T = {
  A: '고객 이메일 컬럼 길이 확장',
  B: '레거시 플래그 컬럼 삭제',
  C: '대여 반납 예정일 컬럼 추가',
  D: '고객 식별 컬럼 설명 보강',
  E: '매장 코드 타입 확장',
  F: '대여 채널 컬럼 추가',
  G: '결제 수단 컬럼 표기 통일',
};

// ───────────────────────── 상황 만들기 ─────────────────────────

const tok = { admin: await apiToken(), mjkim: await apiToken('mjkim', 'mjkim'), shlee: await apiToken('shlee', 'shlee') };

async function findDatasource() {
  const list = await api(tok.admin, 'GET', '/catalog/datasources');
  return (list.items ?? list).find(d => d.name === DS_NAME) ?? null;
}
async function findDataset(name) {
  const r = await api(tok.admin, 'GET', `/catalog/datasets?search=${encodeURIComponent(name.split('.').pop())}&page_size=50`);
  return (r.items ?? r).find(d => d.name === name && d.datasource_name === DS_NAME) ?? null;
}

async function cleanup() {
  for (const qn of [ORDERS_QN, MART_QN]) {
    const d = await findDataset(qn);
    if (d) { await api(tok.admin, 'DELETE', `/catalog/datasets/${d.id}`); console.log(`deleted dataset ${qn}`); }
  }
  const ds = await findDatasource();
  if (ds) { await api(tok.admin, 'DELETE', `/catalog/datasources/${ds.id}`); console.log(`deleted datasource ${DS_NAME}`); }
}

async function ensureDatasets() {
  const ds = await findDatasource() ?? await api(tok.admin, 'POST', '/catalog/datasources', { name: DS_NAME, type: 'postgresql', origin: 'PROD' });
  let orders = await findDataset(ORDERS_QN);
  if (!orders) {
    const f = (field_path, field_type, ordinal, extra = {}) => ({ field_path, field_type, ordinal, ...extra });
    orders = await api(tok.admin, 'POST', '/catalog/datasets', {
      name: 'dw.rental_orders', display_name: '대여 주문', datasource_id: ds.id,
      summary: '매장별 DVD 대여 주문(대여~반납) 원장',
      schema_fields: [
        f('rental_id', 'INT', 0, { is_primary_key: 'true', nullable: 'false', description: '대여 번호' }),
        f('customer_id', 'INT', 1, { nullable: 'false', description: '고객 번호' }),
        f('store_id', 'SMALLINT', 2, { description: '매장 코드' }),
        f('email', 'VARCHAR(100)', 3, { description: '고객 이메일' }),
        f('rental_date', 'TIMESTAMP', 4, { description: '대여 일시' }),
        f('return_date', 'TIMESTAMP', 5, { description: '반납 일시' }),
        f('legacy_flag', 'CHAR(1)', 6, { description: '구 시스템 이관 여부(미사용)' }),
      ],
    });
    const mart = await api(tok.admin, 'POST', '/catalog/datasets', {
      name: 'dw.rental_daily_mart', display_name: '일별 대여 마트', datasource_id: ds.id,
      summary: '매장 · 일자별 대여 건수 집계',
      schema_fields: [
        f('rental_day', 'DATE', 0, { nullable: 'false' }), f('store_id', 'SMALLINT', 1), f('email', 'VARCHAR(100)', 2), f('rental_count', 'INT', 3),
      ],
    });
    await api(tok.admin, 'POST', '/catalog/lineage', { source_dataset_id: orders.id, target_dataset_id: mart.id, relation_type: 'ETL',
      column_mappings: [{ source_column: 'email', target_column: 'email' }, { source_column: 'store_id', target_column: 'store_id' }] });
    await api(tok.admin, 'POST', '/changes/consumers', { dataset_id: orders.id, consumer_name: '연체 알림 배치', consumer_type: 'SYSTEM',
      usage: 'ETL', criticality: 'MISSION_CRITICAL', contact_emails: 'overdue-batch@data-dynamics.io', webhook_url: `${RECV}/change-notice` });
  }
  return orders;
}

const fields = (list, drop = []) => JSON.stringify({ fields: list.map(([field_path, field_type]) => ({ field_path, field_type, nullable: 'true' })), ...(drop.length ? { drop } : {}) });
const chain = (...users) => users.map((approver, i) => ({ step_order: i + 1, approver }));
const cc = (...users) => users.map(([name, u]) => ({ name, email: email(u), channel: 'EMAIL' }));

async function listMine() { return (await api(tok.admin, 'GET', '/changes')).filter(c => Object.values(T).includes(c.title)); }
async function findCR(title) { return (await listMine()).filter(c => c.title === title).sort((a, b) => b.id - a.id)[0] ?? null; }
async function getCR(id) { return api(tok.admin, 'GET', `/changes/${id}`); }
async function waitCR(id, ...statuses) {
  for (let i = 0; i < 120; i++) {
    const c = await getCR(id);
    if (statuses.includes(c.status)) return c;
    await sleep(1000);
  }
  throw new Error(`변경 요청 ${id} 이(가) ${statuses.join('/')} 상태가 되지 않았습니다(결재 워커 argus-catalog-worker 확인).`);
}
async function waitStep(id, order, decision) {
  for (let i = 0; i < 60; i++) {
    const c = await getCR(id);
    if (c.approval_steps.find(s => s.step_order === order)?.decision === decision) return c;
    await sleep(1000);
  }
  throw new Error(`변경 요청 ${id} 의 ${order}단계가 ${decision} 이(가) 되지 않았습니다.`);
}
const create = body => api(tok.mjkim, 'POST', '/changes', body);
const submit = (id, channels) => api(tok.mjkim, 'POST', `/changes/${id}/submit`, channels);
const decide = (who, id, body) => api(tok[who], 'POST', `/changes/${id}/decisions`, body);

/** 전역 설정을 잠깐 바꿔 fn 을 실행하고 원래 값으로 되돌린다. */
async function withSettings(changes, fn) {
  const saved = {};
  for (const key of Object.keys(changes)) saved[key] = await api(tok.admin, 'GET', `/settings/${key}`);
  try {
    for (const [key, value] of Object.entries(changes)) await api(tok.admin, 'PUT', `/settings/${key}`, { ...saved[key], ...value });
    return await fn();
  } finally {
    for (const key of Object.keys(changes).reverse()) await api(tok.admin, 'PUT', `/settings/${key}`, saved[key]);
  }
}
const MAILPIT = { enabled: true, smtp_host: 'mailpit', smtp_port: 1025, use_tls: false, use_ssl: false,
  from_email: 'catalog@data-dynamics.io', from_name: 'Argus Catalog', smtp_user: '', smtp_password: '' };

// ───────────────────────── 화면 도우미 ─────────────────────────

const sessions = {};
async function as(user) {
  if (!sessions[user]) sessions[user] = await open(user, user === 'admin' ? 'admin' : user);
  return sessions[user].page;
}
const dialog = page => page.getByRole('dialog').or(page.getByRole('alertdialog')).last();
const crUrl = id => `/dashboard/changes/${id}`;
/** 섹션 제목이 화면 위쪽에 오도록 스크롤한다. */
async function scrollTo(page, heading, offset = 100) {
  const el = page.getByText(heading, { exact: true }).first();
  await el.evaluate((node, off) => {
    let p = node.parentElement;
    while (p && !(p.scrollHeight > p.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement;
    const sc = p || document.scrollingElement;
    sc.scrollTop += node.getBoundingClientRect().top - (p ? p.getBoundingClientRect().top : 0) - off;
  }, offset);
  await page.waitForTimeout(300);
}
async function scrollBottom(page) {
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) el.scrollTop = el.scrollHeight;
    }
    window.scrollTo(0, document.body.scrollHeight);
  });
  await page.waitForTimeout(300);
}
async function noToast(page) {
  await page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
}
async function pick(page, trigger, optionName) {
  await trigger.click();
  await page.getByRole('option', { name: optionName }).first().click();
  await page.waitForTimeout(200);
}
function skip(name, why) { console.warn(`skip ${name}: ${why} — RESET=1 로 처음부터 다시 찍으세요.`); }

// ───────────────────────── 캡처 ─────────────────────────

if (process.env.CLEANUP) { await cleanup(); process.exit(0); }
if (process.env.RESET) await cleanup();
const orders = await ensureDatasets();
const DS = orders.id;

try {
  await run();
} finally {
  for (const s of Object.values(sessions)) await s.browser.close();
}

async function run() {
  // ── 변경 요청 만들기 화면(저장하지 않는다) ──
  // 맨 먼저 찍는다 — 아래 변경 요청이 적용되면 컬럼이 늘어 스키마 변경 표(최대 높이 360px)가 안에서 스크롤된다.
  if (want('create-schema-change', 'insert-schema-table', 'create-approval-line')) {
    const page = await as('mjkim');
    await goto(page, `/dashboard/changes/new?dataset_id=${DS}`);
    await page.locator('#cr-title').fill('대여 주문 테이블 컬럼 정비');
    await page.getByLabel('store_id 타입').fill('INT');
    await page.locator('tr').filter({ hasText: 'legacy_flag' }).getByRole('button', { name: '컬럼 삭제' }).click();
    await page.getByRole('button', { name: '컬럼 추가' }).click();
    await page.getByLabel('신규 컬럼 이름').fill('due_date');
    await page.locator('tr').filter({ has: page.getByLabel('신규 컬럼 이름') }).locator('input').nth(1).fill('DATE');
    await page.locator('.ProseMirror, [contenteditable=true]').first().click();
    await page.keyboard.insertText('매장 코드를 INT 로 넓히고, 반납 예정일(due_date) 컬럼을 추가하며, 쓰지 않는 legacy_flag 컬럼을 삭제합니다.');
    await page.locator('#cr-title').click();
    await page.mouse.move(1200, 500);
    await page.waitForTimeout(300);
    // 스키마 변경 표 — 수정 · 삭제 · 추가 배지와 변경 건수, 유형 제안
    await shot(page, `${AREA}/create-schema-change`);

    // 스키마 추가 — 설명 편집기에 현재 스키마 표 삽입
    if (want('insert-schema-table')) {
      await page.getByRole('button', { name: '스키마 추가' }).click();
      await page.locator('.ProseMirror table, [contenteditable=true] table').first().waitFor();
      await scrollTo(page, '설명', 80);
      await page.mouse.move(1200, 500);
      await shot(page, `${AREA}/insert-schema-table`);
    }

    // 결재선 2단계와 참조자 지정
    if (want('create-approval-line')) {
      await page.locator('#cr-rollback-plan').fill('store_id 를 SMALLINT 로 되돌리고 due_date 를 삭제, legacy_flag 를 복원합니다.');
      await page.locator('#cr-business-justification').fill('신규 매장 코드 체계 도입과 연체 알림 서비스 준비');
      await pick(page, page.getByRole('combobox').filter({ hasText: /미지정|승인자 선택/ }).first(), /shlee/);
      await page.getByRole('button', { name: '결재자 추가' }).click();
      await pick(page, page.getByRole('combobox').filter({ hasText: /미지정|승인자 선택/ }).first(), /\(admin\)/);
      await page.getByRole('button', { name: '참조자 추가' }).click();
      await pick(page, page.getByRole('combobox').filter({ hasText: /미지정|참조자 선택/ }).first(), /jypark/);
      await scrollBottom(page);
      await page.mouse.move(1200, 300);
      await shot(page, `${AREA}/create-approval-line`);
    }
  }

  // 스키마를 바꾸지 않고 만들 때의 확인 창(만들지 않는다)
  if (want('no-schema-change-confirm')) {
    const page = await as('mjkim');
    await goto(page, `/dashboard/changes/new?dataset_id=${DS}`);
    await page.locator('#cr-title').fill('대여 주문 테이블 점검 기록');
    await page.locator('#cr-rollback-plan').fill('해당 없음');
    await page.locator('#cr-business-justification').fill('분기 정기 점검');
    await pick(page, page.getByRole('combobox').filter({ hasText: /미지정|승인자 선택/ }).first(), /shlee/);
    await page.getByRole('button', { name: '만들기', exact: true }).click();
    await dialog(page).waitFor();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/no-schema-change-confirm`);
    await dialog(page).getByRole('button', { name: '취소' }).click();
  }

  // ── G: 참조자 통지 채널이 Slack 일 때의 이력(임시저장으로 남긴다) ──
  // 상신하지 않는 요청이라 맨 먼저 만든다 — 변경 요청 코드가 순서대로 매겨지므로 나머지 요청의 코드가 이어진다.
  let G = await findCR(T.G);
  if (!G) {
    G = await withSettings({
      notify: { enabled: true, provider: 'slack', slack: { webhook_url: `${RECV}/slack-change`, channel: '#data-change', username: 'argus-bot', icon_emoji: ':bell:' } },
      change: { notify_enabled: true, notify_channel: 'slack' },
    }, () => create({
      title: T.G, dataset_id: DS, change_type: 'COSMETIC', priority: 'LOW',
      description: '<p>결제 수단 코드 표기(카드/현금/포인트)를 표준 용어에 맞춰 통일합니다.</p>',
      rollback_plan: '표기를 이전 값으로 되돌립니다.', business_justification: '표준 용어 정비',
      approval_chain: chain('shlee'), referrers: cc(['박지영', 'jypark'], ['서하윤', 'hyseo']),
    }));
  }

  // ── A: 2단계 결재 뒤 적용 — 참조자 이메일(상신) · 소비자 인앱/Webhook(적용) 통지 ──
  let A = await findCR(T.A);
  if (!A) {
    A = await withSettings({ mail: MAILPIT, change: { notify_enabled: true, notify_channel: 'email' } }, () => create({
      title: T.A, dataset_id: DS, change_type: 'NON_BREAKING', priority: 'HIGH',
      description: '<p>해외 고객의 긴 이메일 주소를 담을 수 있도록 <strong>email</strong> 컬럼을 255자로 늘리고, 연락처(phone) 컬럼을 추가합니다.</p>',
      rollback_plan: 'email 을 VARCHAR(100) 으로 되돌리고 phone 컬럼을 삭제합니다.',
      business_justification: '해외 고객의 긴 이메일 주소 수용 및 연락처 수집',
      schema_after: fields([['email', 'VARCHAR(255)'], ['phone', 'VARCHAR(20)']]),
      approval_chain: chain('shlee', 'admin'), referrers: cc(['박지영', 'jypark']),
    }));
    await submit(A.id, ['WEBHOOK', 'IN_APP']);
    await waitCR(A.id, 'APPROVING');
    await decide('shlee', A.id, { decision: 'APPROVED', comment: '마트 컬럼 길이도 함께 확장 확인' });
    await waitStep(A.id, 1, 'APPROVED');
    await decide('admin', A.id, { decision: 'APPROVED', comment: '승인' });
    await waitCR(A.id, 'APPLIED');
    await sleep(3000);   // 적용 통지가 기록될 때까지
  }

  // ── D: 1단계 위임 → 2단계 승인 → 적용(기록용) ──
  let D = await findCR(T.D);
  if (!D) {
    D = await create({
      title: T.D, dataset_id: DS, change_type: 'COSMETIC', priority: 'NORMAL',
      description: '<p>customer_id · email 컬럼 설명에 개인정보 등급과 마스킹 기준을 보강합니다.</p>',
      rollback_plan: '컬럼 설명을 이전 문구로 되돌립니다.', business_justification: '개인정보 점검 지적 사항 반영',
      approval_chain: chain('shlee', 'admin'),
    });
    await submit(D.id, ['IN_APP']);
    await waitCR(D.id, 'APPROVING');
    await decide('shlee', D.id, { decision: 'DELEGATED', delegated_to: 'jypark', comment: '출장 중 — 대결 요청' });
    await waitStep(D.id, 1, 'DELEGATED');
  }
  D = await getCR(D.id);
  // 위임 처리된 1단계 — 위임 대상 배지와 사유(2단계는 아직 대기)
  if (want('delegated')) {
    if (D.status !== 'APPROVING') skip('delegated', '이미 2단계까지 끝났습니다');
    else {
      const page = await as('admin');
      await goto(page, crUrl(D.id));
      await scrollTo(page, '결재선');
      await shot(page, `${AREA}/delegated`);
    }
  }
  if (D.status === 'APPROVING') {
    await decide('admin', D.id, { decision: 'APPROVED', comment: '승인' });
    await waitCR(D.id, 'APPLIED');
  }

  // ── B: 1단계에서 반려 ──
  let B = await findCR(T.B);
  if (!B) {
    B = await create({
      title: T.B, dataset_id: DS, change_type: 'BREAKING', priority: 'HIGH',
      description: '<p>구 시스템 이관 뒤 쓰지 않는 <strong>legacy_flag</strong> 컬럼을 삭제합니다.</p>',
      rollback_plan: 'legacy_flag CHAR(1) 컬럼을 다시 추가하고 백업 테이블에서 값을 복원합니다.',
      business_justification: '미사용 컬럼 정리',
      schema_after: fields([], ['legacy_flag']),
      approval_chain: chain('shlee', 'admin'),
    });
    await submit(B.id, ['EMAIL', 'IN_APP']);
    await waitCR(B.id, 'APPROVING');
  }
  B = await getCR(B.id);
  // 반려 사유 입력 대화상자 — 1단계 결재자(shlee)가 반려를 누른 모습
  if (B.status === 'APPROVING') {
    const page = await as('shlee');
    await goto(page, crUrl(B.id));
    await scrollTo(page, '결재선', 300);
    await page.getByRole('button', { name: '반려', exact: true }).first().click();
    await dialog(page).waitFor();
    await dialog(page).locator('textarea').fill('연체 알림 배치가 아직 legacy_flag 를 읽고 있습니다. 배치 수정 뒤 다시 올려 주세요.');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/reject-dialog`);
    await dialog(page).getByRole('button', { name: '반려', exact: true }).click();
    await waitCR(B.id, 'REJECTED');
  } else if (want('reject-dialog')) skip('reject-dialog', '이미 반려했습니다');
  // 반려됨 상세 — 1단계 반려 사유, 2단계 진행 안 함
  if (want('detail-rejected')) {
    const page = await as('mjkim');
    await goto(page, crUrl(B.id));
    await noToast(page);
    await scrollTo(page, '스키마 변경');
    await shot(page, `${AREA}/detail-rejected`);
  }

  // ── E: 승인 뒤 적용 예정 시각을 기다림(적용예정) ──
  let E = await findCR(T.E);
  if (!E) {
    E = await create({
      title: T.E, dataset_id: DS, change_type: 'NON_BREAKING', priority: 'NORMAL',
      description: '<p>매장 수 증가에 대비해 <strong>store_id</strong> 를 INT 로 넓힙니다.</p>',
      rollback_plan: 'store_id 를 SMALLINT 로 되돌립니다.', business_justification: '신규 매장 코드 체계(5자리) 도입',
      schema_after: fields([['store_id', 'INT']]),
      scheduled_at: new Date(Date.now() + 7 * 86400e3).toISOString(),
      approval_chain: chain('shlee'),
    });
    await submit(E.id, ['EMAIL', 'IN_APP']);
    await waitCR(E.id, 'APPROVING');
    await decide('shlee', E.id, { decision: 'APPROVED', comment: '정기 배포 일정에 맞춰 적용' });
    await waitCR(E.id, 'SCHEDULED', 'APPROVED');
    await sleep(2000);
  }
  // 승인 뒤 적용 예정 시각을 기다리는 변경 요청 — 오른쪽 위 취소 버튼
  if (want('approved-waiting')) {
    const page = await as('mjkim');
    await goto(page, crUrl(E.id));
    await shot(page, `${AREA}/approved-waiting`);
  }

  // ── F: 적용 예정 시각이 있는 변경 요청 — D-7 · D-1 · 적용 Webhook 통지 ──
  let F = await findCR(T.F);
  if (!F) {
    F = await create({
      title: T.F, dataset_id: DS, change_type: 'ADDITIVE', priority: 'NORMAL',
      description: '<p>대여가 접수된 경로(매장 · 키오스크 · 앱)를 구분하는 <strong>channel</strong> 컬럼을 추가합니다.</p>',
      rollback_plan: 'channel 컬럼을 삭제합니다.', business_justification: '채널별 대여 실적 분석',
      schema_after: fields([['channel', 'VARCHAR(20)']]),
      scheduled_at: new Date(Date.now() + 60e3).toISOString(),
      approval_chain: chain('shlee'),
    });
    await submit(F.id, ['WEBHOOK']);
    await waitCR(F.id, 'APPROVING');
    await decide('shlee', F.id, { decision: 'APPROVED', comment: '승인' });
    // 소비자가 사전 통지(D-7 · D-1)를 확인 처리한다 — 미확인이면 적용 직전에 에스컬레이션 통지가 더 남는다
    for (let i = 0, acked = 0; i < 40 && acked < 2; i++) {
      await sleep(1000);
      for (const n of await api(tok.admin, 'GET', `/changes/${F.id}/notifications`)) {
        if (n.status === 'ACKED' || !['T_MINUS_7', 'T_MINUS_1'].includes(n.stage)) continue;
        await api(tok.admin, 'POST', `/changes/notifications/${n.id}/ack`, { comment: '배치 담당자 확인' });
        acked++;
      }
    }
    await waitCR(F.id, 'APPLIED');
    await sleep(3000);
  }
  // 적용 예정 변경 요청의 D-7 · D-1 · 적용 통지 이력
  if (want('notification-phased')) {
    const page = await as('admin');
    await goto(page, crUrl(F.id));
    await scrollBottom(page);
    await shot(page, `${AREA}/notification-phased`);
  }

  // Slack 채널로 보낸 참조자 통지 이력
  if (want('referrer-slack-log')) {
    const page = await as('mjkim');
    await goto(page, crUrl(G.id));
    await scrollBottom(page);
    await shot(page, `${AREA}/referrer-slack-log`);
  }

  // ── A 의 적용완료 화면들 ──
  // 적용완료 상세 — 결재선 두 단계 승인 시각
  if (want('detail-applied')) {
    const page = await as('mjkim');
    await goto(page, crUrl(A.id));
    await shot(page, `${AREA}/detail-applied`);
  }
  // 알림 발송 이력 — 소비자 적용 통지와 참조자 이메일
  if (want('notification-log')) {
    const page = await as('mjkim');
    await goto(page, crUrl(A.id));
    await scrollBottom(page);
    await shot(page, `${AREA}/notification-log`);
  }

  // ── C: 임시저장 → 상신 → 1단계 승인 → 2단계 대기 ──
  let C = await findCR(T.C);
  if (!C) {
    C = await withSettings({ mail: MAILPIT, change: { notify_enabled: true, notify_channel: 'email' } }, () => create({
      title: T.C, dataset_id: DS, change_type: 'ADDITIVE', priority: 'HIGH',
      description: '<p>연체 알림 서비스가 쓸 <strong>반납 예정일(due_date)</strong> 컬럼을 추가합니다. 기존 행은 대여일 + 대여 기간으로 채웁니다.</p>',
      rollback_plan: 'due_date 컬럼을 삭제합니다.', business_justification: '연체 알림 서비스 도입',
      schema_after: fields([['due_date', 'DATE']]),
      approval_chain: chain('shlee', 'admin'), referrers: cc(['박지영', 'jypark']),
    }));
  }
  C = await getCR(C.id);
  if (C.status === 'DRAFT') {
    const page = await as('mjkim');
    // 임시저장 상세 — 오른쪽 위 편집 · 결재 상신 · 삭제 · 취소 버튼
    await goto(page, crUrl(C.id));
    await shot(page, `${AREA}/detail-draft`);
    // 변경 요청 취소 확인 대화상자(닫기로 그대로 둔다)
    await page.getByRole('button', { name: '취소', exact: true }).click();
    await dialog(page).waitFor();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/cancel-confirm`);
    await dialog(page).getByRole('button', { name: '닫기' }).first().click();
    await submit(C.id, ['EMAIL', 'IN_APP']);
    await waitCR(C.id, 'APPROVING');
  } else if (want('detail-draft', 'cancel-confirm')) skip('detail-draft · cancel-confirm', '이미 상신했습니다');
  C = await getCR(C.id);
  const step = n => C.approval_steps.find(s => s.step_order === n)?.decision;
  if (C.status === 'APPROVING' && step(1) === 'PENDING') {
    // 상신 후 결재중 — 1단계 결재자(shlee) 행에 승인 · 반려 버튼
    const p1 = await as('shlee');
    await goto(p1, crUrl(C.id));
    await shot(p1, `${AREA}/detail-approving`);
    // 2단계 결재자(admin)가 연 상세 — 아직 차례가 아니어서 버튼이 없다
    const p2 = await as('admin');
    await goto(p2, crUrl(C.id));
    await scrollTo(p2, '스키마 변경');
    await shot(p2, `${AREA}/not-my-turn`);
    await decide('shlee', C.id, { decision: 'APPROVED', comment: '연체 관리 요구사항 확인' });
    await waitStep(C.id, 1, 'APPROVED');
  } else if (want('detail-approving', 'not-my-turn')) skip('detail-approving · not-my-turn', '1단계 결재가 이미 끝났습니다');

  // 1단계 승인 뒤 2단계 결재 대기와 영향 분석 결과
  if (want('detail-step2-impact')) {
    const page = await as('admin');
    await goto(page, crUrl(C.id));
    await scrollTo(page, '스키마 변경');
    await shot(page, `${AREA}/detail-step2-impact`);
  }
  // 내 결재함 — 사이드바 배지와 승인 · 반려 버튼(admin 차례인 C)
  if (want('my-approvals')) {
    const page = await as('admin');
    await goto(page, '/dashboard/my-approvals');
    await shot(page, `${AREA}/my-approvals`);
  }
  // 데이터셋 상세 › 더보기 › 변경 요청 탭
  if (want('dataset-tab')) {
    const page = await as('mjkim');
    await goto(page, `/dashboard/datasets/${DS}`);
    await page.getByRole('button', { name: '더보기' }).click();
    await page.getByRole('menuitem', { name: '변경 요청' }).click();
    await settle(page);
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/dataset-tab`);
  }
  // 변경 관리 목록 — 요청자(mjkim)의 "내 요청" 탭: 결재중 · 반려됨 · 적용예정 · 적용완료 · 임시저장
  if (want('list')) {
    const page = await as('mjkim');
    await goto(page, '/dashboard/changes');
    await page.getByRole('tab', { name: /내 요청/ }).click();
    await settle(page);
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/list`);
  }
  // Temporal UI — 결재 대기 중인 변경 요청 워크플로(cr-<코드>)
  if (want('temporal-workflow')) {
    const page = await as('admin');
    const q = encodeURIComponent(`WorkflowId="cr-${C.cr_code}"`);
    await page.goto(`${TEMPORAL_UI}/namespaces/default/workflows?query=${q}`);
    await page.getByText(`cr-${C.cr_code}`).first().waitFor({ timeout: 30000 });
    await settle(page, 1000);
    await shot(page, `${AREA}/temporal-workflow`);
  }

  // 설정 › 연동 › 변경관리 — 참조자 통지 활성화와 채널
  if (want('settings-referrer-notify')) {
    const page = await as('admin');
    await goto(page, '/dashboard/settings?tab=change');
    await shot(page, `${AREA}/settings-referrer-notify`);
  }
}
