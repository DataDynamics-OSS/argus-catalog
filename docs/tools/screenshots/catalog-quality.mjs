/**
 * 데이터 품질(catalog/quality.adoc) 화면 캡처 — catalog-quality/*.png
 *
 * 전제: 통합 테스트 스택의 시드 데이터(Sakila 데이터셋의 품질 규칙 · 진단 결과 · 프로파일, shop.* 데이터셋).
 * 품질 워커 없이 찍는 그림만 담았다 — 진단 결과는 DB 에 이미 있는 것을 쓴다.
 * 상황을 만들려고 넣은 규칙 · 지표 · 작업은 캡처 뒤 모두 지운다(다시 실행해도 같은 그림).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'catalog-quality';
const token = await apiToken();

/** 이름으로 데이터셋 ID 를 찾는다 — sakila.* 는 시드 'Sakila (MySQL)', shop.* 는 통합 테스트의 'it-shop-pg' 데이터 소스의 것. */
async function datasetId(name) {
  const source = name.startsWith('shop.') ? 'it-shop-pg' : 'Sakila (MySQL)';
  const r = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`);
  const hit = (r.items || []).find(d => d.name === name && d.datasource_name === source);
  if (!hit) throw new Error(`데이터셋 없음: ${name}`);
  return hit.id;
}
const rulesOf = id => api(token, 'GET', `/quality/rules?dataset_id=${id}`);
/** 이름이 같은 규칙을 지운다(다시 실행할 때 남은 것 정리). */
async function dropRules(id, names) {
  for (const r of await rulesOf(id)) if (names.includes(r.rule_name)) await api(token, 'DELETE', `/quality/rules/${r.id}`);
}

const FILM = await datasetId('sakila.film');
const CUSTOMER = await datasetId('sakila.customer');
const RENTAL = await datasetId('sakila.rental');
const PAYMENT = await datasetId('sakila.payment');
const STAFF = await datasetId('sakila.staff');
const ADDRESS = await datasetId('sakila.address');
const ITEMS = await datasetId('shop.order_items');
const SHOP_CUSTOMERS = await datasetId('shop.customers');

const { browser, page } = await open();

/** 데이터셋 상세의 품질 탭을 연다. */
async function qualityTab(p, id) {
  await goto(p, `/dashboard/datasets/${id}?tab=quality`);
  await p.getByText('품질 점수').first().waitFor({ timeout: 15000 });
  await settle(p, 800);
}
/** 요소를 화면 위쪽(머리글 아래)으로 올린다. */
async function scrollTop(p, locator, offset = 80) {
  await locator.evaluate((el, off) => {
    let sc = el.parentElement;
    while (sc && !(sc.scrollHeight > sc.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
    const top = el.getBoundingClientRect().top;
    if (sc) sc.scrollBy(0, top - sc.getBoundingClientRect().top - off + 64);
    else window.scrollBy(0, top - off);
  }, offset);
  await settle(p, 400);
}
const toastGone = p => p.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});

try {
  // 품질 탭 전체 모습 — 점수 영역 · 결과 표 · 규칙 목록 (sakila.film, 100%)
  await qualityTab(page, FILM);
  await shot(page, `${AREA}/dashboard`);

  // 진단 결과 — 75%, 통과 3 / 실패 1 (sakila.staff)
  await qualityTab(page, STAFF);
  await shot(page, `${AREA}/check-result`);

  // 위반 샘플 펼침 — 실패한 규칙의 위반 행, 위반 컬럼 password 강조 (sakila.staff)
  await page.getByRole('button', { name: /위반 샘플/ }).first().click();
  await settle(page, 500);
  await scrollTop(page, page.getByText('품질 점수').first(), 60);
  await shot(page, `${AREA}/failed-samples`);

  // 데이터 프로파일 표 — NULL 비율이 0% 보다 큰 컬럼은 빨간색 (sakila.staff)
  await qualityTab(page, STAFF);
  await scrollTop(page, page.getByRole('heading', { name: '데이터 프로파일' }).first(), 30);
  await shot(page, `${AREA}/profile`);

  // 규칙 · 프로파일이 없는 데이터셋의 품질 탭 (shop.order_items)
  await qualityTab(page, ITEMS);
  await shot(page, `${AREA}/empty-state`);

  // 품질 점수 영역 — 점수 · 통과/실패 수 · 점수 추세 (sakila.address, 진단 3회)
  await qualityTab(page, ADDRESS);
  await shot(page, `${AREA}/batch-result`);

  // 업스트림 품질 경고 배너 — 원천 sakila.staff 가 주의 상태 (sakila.rental)
  await qualityTab(page, RENTAL);
  await shot(page, `${AREA}/upstream-warning`);

  // ── 규칙 대화상자 ──────────────────────────────────────────────
  const dlg = page.getByRole('dialog');
  const combo = i => dlg.getByRole('combobox').nth(i);
  const pick = async (i, name) => {
    await combo(i).click();
    await page.getByRole('option', { name, exact: typeof name === 'string' }).first().click();
    await settle(page, 300);
  };
  const openAdd = async id => {
    await qualityTab(page, id);
    await page.getByRole('button', { name: '규칙 추가' }).first().click();
    await dlg.waitFor();
  };
  const closeDialog = async () => { await page.keyboard.press('Escape'); await dlg.waitFor({ state: 'detached' }).catch(() => {}); };

  // 규칙 추가 — 진단 기준 '코드', 기준 방식 '허용 값 목록', 허용 값을 배지로 입력 (shop.customers.grade)
  await openAdd(SHOP_CUSTOMERS);
  await dlg.getByRole('textbox').first().fill('회원 등급 허용값');
  await pick(0, '코드');
  await pick(2, 'grade');
  {
    const values = dlg.locator('input:not([type=number])').nth(1);
    for (const v of ['BRONZE', 'SILVER', 'GOLD']) { await values.fill(v); await values.press('Enter'); }
  }
  await settle(page, 300);
  await shot(page, `${AREA}/rule-add`);
  await closeDialog();

  // 규칙 추가 — 커스텀 SQL 을 고르면 기대값 칸이 진단 SQL 편집기로 (sakila.payment)
  await openAdd(PAYMENT);
  await dlg.getByRole('textbox').first().fill('고아 결제 검출 (대여 참조 끊김)');
  await pick(0, '진단 기준 없음');
  await pick(1, '커스텀 SQL');
  await dlg.locator('.monaco-editor').first().waitFor({ timeout: 20000 });
  await dlg.locator('.monaco-editor .view-lines').first().click();
  await page.keyboard.insertText('SELECT count(*)\nFROM payment p\nLEFT JOIN rental r ON r.rental_id = p.rental_id\nWHERE p.rental_id IS NOT NULL AND r.rental_id IS NULL');
  await settle(page, 500);
  await shot(page, `${AREA}/rule-custom-sql`);
  await closeDialog();

  // 규칙 추가 — 커스텀 Python 을 고르면 플러그인 설정 (JSON) 편집기로 (sakila.rental)
  await openAdd(RENTAL);
  await dlg.getByRole('textbox').first().fill('대여 기간 이상치 검사 (IQR)');
  await pick(0, '진단 기준 없음');
  await pick(1, '커스텀 Python');
  await dlg.locator('.monaco-editor').first().waitFor({ timeout: 20000 });
  await settle(page, 800);
  await shot(page, `${AREA}/rule-custom-python`);
  await closeDialog();

  // 규칙 추가 성공 알림 — '반납일 필수' 를 저장한 직후 (sakila.rental). 찍은 뒤 규칙을 지운다
  await dropRules(RENTAL, ['반납일 필수']);
  await openAdd(RENTAL);
  await dlg.getByRole('textbox').first().fill('반납일 필수');
  await pick(2, 'return_date');
  await dlg.getByRole('button', { name: '규칙 추가' }).click();
  await page.getByText(/추가했습니다/).first().waitFor({ timeout: 10000 });
  await settle(page, 400);
  await shot(page, `${AREA}/rule-add-toast`);
  await dropRules(RENTAL, ['반납일 필수']);
  await toastGone(page);

  // 규칙 추천 — 프로파일 통계에서 나온 후보, 기본으로 모두 선택 (sakila.customer)
  const before = new Set((await rulesOf(CUSTOMER)).map(r => r.id));
  await qualityTab(page, CUSTOMER);
  await page.getByRole('button', { name: '규칙 추천' }).first().click();
  await dlg.getByText(/선택$/).first().waitFor({ timeout: 10000 });
  await settle(page, 400);
  await shot(page, `${AREA}/recommend`);

  // 추천 규칙 생성 성공 알림 (sakila.customer). 찍은 뒤 추가된 규칙을 지운다
  await dlg.getByRole('button', { name: /선택한 규칙 \d+개 추가/ }).click();
  await page.getByText(/추천 규칙 \d+건을 추가했습니다/).first().waitFor({ timeout: 15000 });
  await settle(page, 400);
  await shot(page, `${AREA}/recommend-toast`);
  for (const r of await rulesOf(CUSTOMER)) if (!before.has(r.id)) await api(token, 'DELETE', `/quality/rules/${r.id}`);
  await toastGone(page);

  /** 규칙 목록에서 그 이름의 규칙 줄에 있는 아이콘 단추. */
  const ruleButton = async (ruleName, label) => {
    const idx = await page.evaluate(([name, lab]) => {
      const sel = `button[aria-label="${lab}"], button[title="${lab}"]`;
      return [...document.querySelectorAll(sel)].findIndex(b => {
        let row = b.parentElement;
        while (row.parentElement && row.parentElement.querySelectorAll(sel).length === 1) row = row.parentElement;
        return row.textContent.includes(name);
      });
    }, [ruleName, label]);
    if (idx < 0) throw new Error(`규칙 줄 없음: ${ruleName}`);
    return page.locator(`button[aria-label="${label}"], button[title="${label}"]`).nth(idx);
  };

  // 규칙 수정 — 행 수 하한 규칙의 기대값을 바꾸는 중 (sakila.film, 저장하지 않음)
  await qualityTab(page, FILM);
  await (await ruleButton('영화 1000편 이상', '규칙 편집')).click();
  await dlg.waitFor();
  await settle(page, 400);
  await dlg.locator('input:not([type=number])').nth(1).fill('900');
  await shot(page, `${AREA}/rule-edit`);
  await closeDialog();

  // 규칙 삭제 확인 창 (sakila.film, 취소)
  await (await ruleButton('영화 1000편 이상', '규칙 삭제')).click();
  await page.getByRole('alertdialog').or(dlg).first().waitFor();
  await settle(page, 400);
  await shot(page, `${AREA}/rule-delete`);
  await page.keyboard.press('Escape');

  // 비활성 규칙 — 흐린 표시와 비활성 배지 (sakila.customer 의 '회원 500명 이상' 을 잠시 끈다)
  const rowCount = (await rulesOf(CUSTOMER)).find(r => r.rule_name === '회원 500명 이상');
  await api(token, 'PUT', `/quality/rules/${rowCount.id}`, { is_active: 'false' });
  try {
    await qualityTab(page, CUSTOMER);
    await scrollTop(page, page.getByRole('heading', { name: '품질 진단' }).first(), 30);
    await shot(page, `${AREA}/inactive-rule`);
  } finally {
    await api(token, 'PUT', `/quality/rules/${rowCount.id}`, { is_active: 'true' });
  }

  // 기능 권한 표 — 데이터셋 그룹의 품질 규칙 관리 · 품질 진단 실행 · 진단 주기 설정
  await goto(page, '/dashboard/permissions');
  await scrollTop(page, page.getByText('품질 규칙 관리', { exact: true }).first(), 330);
  await shot(page, `${AREA}/feature-permissions`);

  // 소유자가 아닌 일반 사용자의 품질 탭 — 실행 · 편집 버튼 없음 (mjkim, sakila.address)
  {
    const u = await open('mjkim', 'mjkim');
    try {
      await qualityTab(u.page, ADDRESS);
      await shot(u.page, `${AREA}/read-only`);
    } finally { await u.browser.close(); }
  }

  // ── 관리 › 데이터 품질 ─────────────────────────────────────────
  // 기본 지표를 넣고(없던 것만), 품질 진단 작업 세 개를 만들어 찍은 뒤 모두 지운다.
  const JOBS = ['고객 마스터 매일 진단', '대여 트랜잭션 주간 진단', '쇼핑몰 회원 품질 진단'];
  const cleanupAdmin = async createdDefs => {
    for (const j of await api(token, 'GET', '/quality/jobs')) if (JOBS.includes(j.name)) await api(token, 'DELETE', `/quality/jobs/${j.id}`);
    for (const d of createdDefs) {
      const assigned = await api(token, 'GET', `/quality/definitions/${d.id}/assignments`);
      if (assigned.length) await api(token, 'POST', `/quality/definitions/${d.id}/unassign`, { rule_ids: assigned.map(a => a.rule_id) });
      await api(token, 'DELETE', `/quality/definitions/${d.id}`);
    }
  };
  const defsBefore = new Set((await api(token, 'GET', '/quality/definitions')).map(d => d.id));
  await api(token, 'POST', '/quality/definitions/defaults');
  const defs = await api(token, 'GET', '/quality/definitions');
  const createdDefs = defs.filter(d => !defsBefore.has(d.id));
  try {
    const def = name => defs.find(d => d.name === name);
    const required = def('필수값'), unique = def('중복 없음');
    await api(token, 'POST', `/quality/definitions/${required.id}/assign`, { columns: [{ dataset_id: SHOP_CUSTOMERS, column_name: 'email' }, { dataset_id: SHOP_CUSTOMERS, column_name: 'full_name' }] });
    await api(token, 'POST', `/quality/definitions/${unique.id}/assign`, { columns: [{ dataset_id: SHOP_CUSTOMERS, column_name: 'customer_id' }] });
    for (const j of await api(token, 'GET', '/quality/jobs')) if (JOBS.includes(j.name)) await api(token, 'DELETE', `/quality/jobs/${j.id}`);
    await api(token, 'POST', '/quality/jobs', { name: JOBS[0], targets: [{ type: 'dataset', dataset_id: CUSTOMER }], schedule_type: 'DAILY', schedule_time: '03:00' });
    await api(token, 'POST', '/quality/jobs', { name: JOBS[1], targets: [{ type: 'dataset', dataset_id: RENTAL }], schedule_type: 'WEEKLY', schedule_day: 0, schedule_time: '02:00', include_profile: true });
    await api(token, 'POST', '/quality/jobs', { name: JOBS[2], targets: [{ type: 'dataset', dataset_id: SHOP_CUSTOMERS }], definition_ids: [required.id, unique.id], schedule_type: 'HOURLY', schedule_interval_hours: 6 });

    // 품질 지표 탭 — 지표 목록
    await goto(page, '/dashboard/quality-runs?tab=definitions');
    await page.getByText('필수값').first().waitFor();
    await settle(page, 500);
    await shot(page, `${AREA}/definitions`);

    // 지표 추가 대화상자 — 진단 기준 '코드', 허용 값, 아래에 진단 쿼리 (Trino) 미리보기
    await page.getByRole('button', { name: '지표 추가', exact: true }).click();
    await dlg.waitFor();
    await dlg.getByPlaceholder('예: 여부 Y/N').fill('회원 등급 코드');
    await pick(0, '코드');
    {
      const values = dlg.getByPlaceholder('예: Y 입력 후 Enter');
      await values.click();
      for (const v of ['BRONZE', 'SILVER', 'GOLD']) { await page.keyboard.type(v); await page.keyboard.press('Enter'); }
    }
    await dlg.getByPlaceholder('이 지표를 어떤 컬럼에 쓰는지').fill('회원 등급 컬럼(grade) — 정해진 등급 코드만 허용');
    await settle(page, 1000);
    await shot(page, `${AREA}/definition-add`);
    await closeDialog();

    // 품질 진단 작업 탭 — 작업 목록(대상 · 품질 지표 · 주기 · 다음 실행)
    await goto(page, '/dashboard/quality-runs?tab=jobs');
    await page.getByText(JOBS[0]).first().waitFor();
    await settle(page, 500);
    await shot(page, `${AREA}/jobs`);

    // 품질 진단 작업 등록 대화상자 — 대상 데이터셋 · 컬럼별 품질 지표 · 진단 범위 · 주기
    await page.getByRole('button', { name: '품질 진단 작업 등록' }).click();
    await dlg.waitFor();
    await dlg.getByPlaceholder('예: 주소 테이블 야간 품질 진단').fill('쇼핑몰 회원 야간 품질 진단');
    await pick(0, 'it-shop-pg');
    await dlg.getByRole('button', { name: /^shop\.customers/ }).click();
    await dlg.locator('input[type=time]').fill('02:00');
    await settle(page, 1000);
    await shot(page, `${AREA}/job-add`);
    await closeDialog();
  } finally {
    await cleanupAdmin(createdDefs);
  }
} finally {
  await browser.close();
}
