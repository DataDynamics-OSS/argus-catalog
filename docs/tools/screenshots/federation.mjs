/**
 * 페더레이션(federation/) 화면 캡처 — docs/modules/ROOT/pages/federation/*.adoc 의 그림 40장.
 *
 * 실행할 때마다 허브(메인)의 it-peer 인스턴스와 가져온 로컬 사본을 지우고 처음부터 다시 만든다.
 * 끝나면 it-peer(혼합, 미러 2건)와 로컬 사본 it_fed.customers 가 남고, 오류 재현용 인스턴스는 지워진다.
 *
 * 환경 변수(lib.mjs 의 것 외)
 *   PEER_API   상대 카탈로그 API(호스트에서 보는 주소). 기본 http://127.0.0.1:46601/api/v1
 *   PEER_IN    허브에 등록할 상대 카탈로그 주소(컨테이너 안에서 보는 주소). 기본 http://backend-peer:4600
 *   FED_TOKEN  export API 서비스 토큰. 기본 it-federation-token
 *   STAGES     실행할 단계를 쉼표로(register,harvest,search,browse,detail,import,edit,policy,errors). 없으면 전부.
 *              register 를 빼면 이미 등록된 it-peer 를 그대로 쓴다.
 */
import { launch, newContext, login, goto, settle, shot, apiToken, api, API } from './lib.mjs';

const PEER_API = process.env.PEER_API || 'http://127.0.0.1:46601/api/v1';
const PEER_IN = process.env.PEER_IN || 'http://backend-peer:4600';
const FED_TOKEN = process.env.FED_TOKEN || 'it-federation-token';
const stages = (process.env.STAGES || '').split(',').map(s => s.trim()).filter(Boolean);
const on = name => !stages.length || stages.includes(name);

const KEY = 'it-peer';
const NAME = 'IT Peer';
// 오류 재현용 인스턴스(캡처 뒤 지운다)
const BAD_TOKEN = { instance_key: 'team-marketing', name: '마케팅팀 카탈로그', base_url: PEER_IN };
const UNREACHABLE = { instance_key: 'team-sales', name: '영업팀 카탈로그', base_url: 'http://catalog-sales:4600' };

const sleep = ms => new Promise(r => setTimeout(r, ms));
const fedTab = tab => `/dashboard/federation${tab ? `?tab=${tab}` : ''}`;
const detailUrl = furn => `/dashboard/federation/datasets?urn=${encodeURIComponent(furn)}`;

let token, peerToken;
const hub = (method, p, body) => api(token, method, p, body);
const peer = (method, p, body) => api(peerToken, method, p, body, PEER_API);

/** 상대 카탈로그에 데이터 소스 · 데이터셋 2개 · 리니지 · 샘플을 준비한다(있으면 그대로 쓴다). */
async function ensurePeerData() {
  const sources = await peer('GET', '/catalog/datasources');
  const list = Array.isArray(sources) ? sources : sources.items;
  const ds = list.find(d => d.name === 'it-peer-pg')
    || await peer('POST', '/catalog/datasources', { name: 'it-peer-pg', type: 'postgresql', origin: 'PROD' });
  const tags = await peer('GET', '/catalog/tags');
  const tag = tags.find(t => t.name === 'it-fed') || await peer('POST', '/catalog/tags', { name: 'it-fed', color: '#10b981' });
  const found = async name =>
    (await peer('GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`)).items.find(d => d.name === name);

  let cust = await found('it_fed.customers');
  if (!cust) {
    cust = await peer('POST', '/catalog/datasets', {
      name: 'it_fed.customers', datasource_id: ds.id, description: '연합 테스트 고객', tags: [tag.id],
      schema_fields: [
        { field_path: 'customer_id', field_type: 'INT', is_primary_key: 'true', ordinal: 0 },
        { field_path: 'email', field_type: 'VARCHAR(255)', ordinal: 1 },
        { field_path: 'grade', field_type: 'VARCHAR(10)', ordinal: 2 },
        { field_path: 'created_at', field_type: 'TIMESTAMP', ordinal: 3 },
      ],
    });
    const form = new FormData();
    form.append('file', new Blob(['customer_id,email,grade,created_at\n1,a@x.io,GOLD,2026-01-01\n2,b@x.io,SILVER,2026-01-02\n'],
      { type: 'text/csv' }), 'it_fed.csv');
    const r = await fetch(`${PEER_API}/catalog/datasets/${cust.id}/sample`, {
      method: 'POST', headers: { Authorization: `Bearer ${peerToken}` }, body: form,
    });
    if (!r.ok) throw new Error(`sample upload: ${r.status} ${await r.text()}`);
    await peer('POST', `/catalog/datasets/${cust.id}/sample/convert-to-parquet`);
  }
  let mart = await found('it_fed.customer_mart');
  if (!mart) {
    mart = await peer('POST', '/catalog/datasets', {
      name: 'it_fed.customer_mart', datasource_id: ds.id,
      schema_fields: [{ field_path: 'customer_id', field_type: 'INT', ordinal: 0 }],
    });
    await peer('POST', '/catalog/lineage', { source_dataset_id: cust.id, target_dataset_id: mart.id });
  }
  return { cust, mart };
}

const instanceByKey = async key => (await hub('GET', '/federation/instances')).find(i => i.instance_key === key);
const dropInstance = async key => {
  const inst = await instanceByKey(key);
  if (inst) await hub('DELETE', `/federation/instances/${inst.id}`);
};
const allKeys = async base => (await api(base === PEER_API ? peerToken : token, 'GET', '/federation/export-policy', undefined, base))
  .items.map(i => i.key);

/** 가져오기 — 스케줄러 실행과 겹치면(409) 잠시 뒤 다시 한다. */
async function harvest(id, full = false) {
  for (let i = 0; i < 20; i++) {
    try { return await hub('POST', `/federation/instances/${id}/harvest?full=${full}`); }
    catch (e) { if (e.status !== 409) throw e; await sleep(2000); }
  }
  throw new Error('harvest: 계속 409');
}

/** 가져온 로컬 사본(Federation Imports 아래 it_fed.customers)을 지운다. */
async function dropImportedCopy() {
  const r = await hub('GET', `/catalog/datasets?search=${encodeURIComponent('it_fed.customers')}&page_size=50`);
  for (const d of r.items.filter(d => d.name === 'it_fed.customers' && d.datasource_name === 'Federation Imports')) {
    await hub('DELETE', `/catalog/datasets/${d.id}`);
  }
}

/** 입력 포커스 테두리를 없애고, 대화상자를 맨 위로 올리고, 마우스를 빈 곳으로 치운다. */
async function calm(page, { top = false } = {}) {
  await page.evaluate(scrollTop => {
    document.activeElement?.blur?.();
    if (scrollTop) document.querySelectorAll('[role=dialog], [role=dialog] *').forEach(el => { el.scrollTop = 0; });
  }, top);
  await page.mouse.move(1300, 870);
  await page.waitForTimeout(300);
}

const row = (page, name) => page.locator('tbody tr').filter({ hasText: name }).first();
const dialog = page => page.getByRole('dialog');

async function fillInstanceForm(page, { key, name, url, interval, tokenValue, description }) {
  const d = dialog(page);
  await d.getByPlaceholder('team-payments').fill(key);
  await d.getByPlaceholder('Payments Catalog').fill(name);
  await d.getByPlaceholder('https://catalog.payments.internal').fill(url);
  if (interval) await d.locator('input[type=number]').fill(String(interval));
  if (tokenValue) await d.locator('input[type=password]').fill(tokenValue);
  if (description) await d.locator('textarea').fill(description);
}

async function main() {
  token = await apiToken();
  peerToken = await apiToken('admin', 'admin', PEER_API);
  const { cust } = await ensurePeerData();
  const FURN = `${KEY}::${cust.urn}`;
  // 두 카탈로그 모두 전체 항목 노출에서 시작
  await peer('PUT', '/federation/export-policy', { exposed: await allKeys(PEER_API) });
  await dropInstance(BAD_TOKEN.instance_key);
  await dropInstance(UNREACHABLE.instance_key);

  const browser = await launch();
  const context = await newContext(browser);
  const page = await context.newPage();
  await login(page);
  const userContext = await newContext(browser);
  const userPage = await userContext.newPage();
  await login(userPage, 'mjkim', 'mjkim');

  try {
    if (on('register')) {
      await dropInstance(KEY);
      await dropImportedCopy();

      // 인스턴스 관리 탭 — 아직 등록한 인스턴스가 없는 처음 모습
      await goto(page, fedTab('instances'));
      await shot(page, 'federation/instances-empty');

      // 인스턴스 등록 대화상자 — 항목을 채운 모습(모드는 기본값 혼합)
      await page.getByRole('button', { name: '인스턴스 등록' }).click();
      await fillInstanceForm(page, {
        key: KEY, name: NAME, url: PEER_IN, interval: 60, tokenValue: FED_TOKEN, description: '통합 테스트 peer',
      });
      await calm(page, { top: true });
      await shot(page, 'federation/instance-register');

      // 노출 항목 불러오기 — 그룹별 표시 정보 체크리스트
      await dialog(page).getByRole('button', { name: '노출 항목 불러오기' }).click();
      await dialog(page).getByText(/개 표시 \//).waitFor();
      await dialog(page).getByText(/개 표시 \//).scrollIntoViewIfNeeded();
      await dialog(page).getByRole('button', { name: '등록', exact: true }).scrollIntoViewIfNeeded();
      await settle(page, 300);
      await shot(page, 'federation/instance-capabilities');

      await dialog(page).getByRole('button', { name: '등록', exact: true }).click();
      await dialog(page).waitFor({ state: 'hidden' });
      await row(page, NAME).waitFor();
      await settle(page);

      // 등록된 인스턴스 행과 작업 아이콘(관리자에게 보이는 다섯 탭)
      await shot(page, 'federation/tabs-admin');

      // 이미 있는 식별 키로 다시 등록 — 입력란 아래 안내
      await page.getByRole('button', { name: '인스턴스 등록' }).click();
      await fillInstanceForm(page, { key: KEY, name: 'IT Peer 2', url: PEER_IN });
      await dialog(page).getByRole('button', { name: '등록', exact: true }).click();
      await dialog(page).getByText('이미 사용 중인 식별 키입니다').waitFor();
      await calm(page, { top: true });
      await shot(page, 'federation/instance-dup-key');
      await dialog(page).getByRole('button', { name: '취소' }).click();
    }

    const inst = await instanceByKey(KEY);
    if (!inst) throw new Error('it-peer 인스턴스가 없습니다 — register 단계부터 실행하세요');

    if (on('harvest')) {
      await goto(page, fedTab('instances'));
      // 상태점검 성공 — 지연 시간과 peer 버전
      await row(page, NAME).getByRole('button', { name: '헬스 체크' }).click();
      await row(page, NAME).getByTitle('다시 점검').waitFor();
      await page.mouse.move(700, 600);
      await shot(page, 'federation/health-ok');

      // 가져오기 (증분) 진행 바 — 데이터가 적어 금방 끝나므로 응답만 잠시 늦춰 잡는다
      await harvest(inst.id, false); // 스케줄러와 겹치지 않게 먼저 한 번
      await goto(page, fedTab('instances'));
      const hold = async route => { await sleep(2500); await route.continue(); };
      await page.route('**/federation/instances/*/harvest*', hold);
      await row(page, NAME).getByRole('button', { name: '가져오기 (증분)' }).click();
      await page.getByText('가져오는 중').waitFor();
      await page.mouse.move(700, 600);
      await shot(page, 'federation/harvest-running');

      // 가져오기 완료 — 진행 바 자리에 결과 숫자
      await page.getByText('가져오기 완료').waitFor({ timeout: 30000 });
      await shot(page, 'federation/harvest-done');
      await page.unroute('**/federation/instances/*/harvest*', hold);
      await page.getByText('가져오기 완료').waitFor({ state: 'hidden', timeout: 15000 });

      // 전체 재동기화 확인 대화상자
      await row(page, NAME).getByRole('button', { name: '전체 재동기화' }).click();
      await page.getByRole('alertdialog').or(dialog(page)).first().waitFor();
      await settle(page, 300);
      await shot(page, 'federation/full-resync-confirm');
      await page.getByRole('button', { name: '전체 재동기화' }).last().click();
      await page.getByText(/가져오기 (완료|실패)/).waitFor({ timeout: 30000 });
      await page.getByText(/가져오기 (완료|실패)/).waitFor({ state: 'hidden', timeout: 15000 });

      // 동기화 이력 — 전체 · 증분 실행 이력
      await row(page, NAME).getByRole('button', { name: '동기화 이력' }).click();
      await dialog(page).getByText('성공').first().waitFor();
      await calm(page);
      await shot(page, 'federation/sync-runs');
      await page.keyboard.press('Escape');
    }

    if (on('search')) {
      // 일반 사용자 화면 — 통합 검색 · 탐색 탭만
      await goto(userPage, fedTab());
      await shot(userPage, 'federation/tabs-user');

      // 통합 검색 결과 — 결과 건수와 조회한 인스턴스 수
      await goto(page, fedTab());
      await page.getByPlaceholder('모든 팀 카탈로그를 한 번에 검색').fill('customer');
      await page.getByRole('button', { name: '검색', exact: true }).click();
      await page.getByText(/개 인스턴스 조회/).waitFor();
      await settle(page);
      await calm(page);
      await shot(page, 'federation/search-results');

      // peer 결과 — 출처 배지와 상세 · 리니지 버튼
      const peerCard = page.getByText('it_fed.customer_mart', { exact: true }).first();
      await peerCard.scrollIntoViewIfNeeded();
      await page.mouse.wheel(0, 400);
      await settle(page, 400);
      await shot(page, 'federation/search-results-peer');

      // Cross-instance 리니지 대화상자 — 보고 출처 표시
      const card = page.locator('div').filter({ hasText: /^.*it_fed\.customers/ })
        .filter({ has: page.getByRole('button', { name: '상세' }) }).last();
      await card.getByRole('button', { name: '리니지' }).click();
      await dialog(page).getByText(/보고:/).first().waitFor();
      await settle(page, 400);
      await shot(page, 'federation/lineage-dialog');
      await page.keyboard.press('Escape');
    }

    if (on('browse')) {
      // 탐색 탭 — 미러를 가진 인스턴스 트리
      await goto(page, fedTab('browse'));
      await page.getByText(NAME, { exact: true }).first().waitFor();
      await shot(page, 'federation/browse-tree');

      // 데이터 소스를 고른 그리드
      if (!(await page.getByText('it-peer-pg', { exact: true }).first().isVisible().catch(() => false))) {
        await page.getByText(NAME, { exact: true }).first().click();
      }
      await page.getByText('it-peer-pg', { exact: true }).first().click();
      await page.getByText('customer_mart').first().waitFor();
      await page.mouse.move(900, 700);
      await settle(page, 300);
      await shot(page, 'federation/browse-grid');

      // 필터 적용 — 트리 건수가 줄어든 모습
      await page.getByPlaceholder('데이터셋 이름/설명으로 트리 좁히기').fill('mart');
      await page.getByRole('button', { name: '필터', exact: true }).click();
      await settle(page);
      if (!(await page.getByText('it-peer-pg', { exact: true }).first().isVisible().catch(() => false))) {
        await page.getByText(NAME, { exact: true }).first().click();
      }
      await page.getByText('it-peer-pg', { exact: true }).first().click();
      await settle(page);
      await page.mouse.move(900, 700);
      await shot(page, 'federation/browse-filter');
    }

    if (on('detail')) {
      // 원격 데이터셋 상세 — 개요 탭과 출처 배지
      await goto(page, detailUrl(FURN));
      await page.getByRole('tab', { name: /개요/ }).waitFor();
      await settle(page);
      await shot(page, 'federation/detail-overview');

      // 원격 상세 — 스키마 탭
      await page.getByRole('tab', { name: /스키마/ }).click();
      await settle(page);
      await shot(page, 'federation/detail-schema');

      // 원격 상세 — 미러 샘플
      await page.getByRole('tab', { name: /샘플/ }).click();
      await page.getByText('a@x.io').waitFor();
      await settle(page);
      await shot(page, 'federation/detail-sample');

      // 원격 상세 — 리니지 탭
      await page.getByRole('tab', { name: /리니지/ }).click();
      await page.getByText(/보고:/).first().waitFor();
      await settle(page);
      await shot(page, 'federation/detail-lineage');

      // 일반 사용자가 연 원격 상세 — 로컬로 가져오기 버튼 없음
      await goto(userPage, detailUrl(FURN));
      await userPage.getByRole('tab', { name: /개요/ }).waitFor();
      await settle(userPage);
      await shot(userPage, 'federation/detail-user-no-import');
    }

    if (on('import')) {
      await dropImportedCopy();
      // 로컬 카탈로그로 가져오기 확인 대화상자
      await goto(page, detailUrl(FURN));
      await page.getByRole('button', { name: '로컬로 가져오기' }).click();
      await page.getByText('로컬 카탈로그로 가져오기').waitFor();
      await settle(page, 300);
      await shot(page, 'federation/import-confirm');

      // 가져오기 완료 안내와 로컬 데이터셋 열기 링크
      await page.getByRole('button', { name: '가져오기', exact: true }).click();
      await page.getByText('로컬 데이터셋으로 가져왔습니다').waitFor();
      await page.mouse.move(800, 750);
      await settle(page, 300);
      await shot(page, 'federation/import-done');

      // 가져온 로컬 데이터셋 — Federation Imports 데이터 소스와 페더레이션 태그
      await page.getByText('로컬 데이터셋 열기').click();
      await page.waitForURL(/\/dashboard\/datasets\/\d+/);
      await page.getByText('Federation Imports').first().waitFor();
      await settle(page);
      await shot(page, 'federation/import-local-copy');

      // 이미 가져온 데이터셋을 다시 가져올 때의 오류
      await goto(page, detailUrl(FURN));
      await page.getByRole('button', { name: '로컬로 가져오기' }).click();
      await page.getByRole('button', { name: '가져오기', exact: true }).click();
      await page.getByText('이미 로컬로 가져온 데이터셋입니다').waitFor();
      await page.mouse.move(800, 750);
      await settle(page, 300);
      await shot(page, 'federation/import-duplicate');
    }

    if (on('edit')) {
      // 인스턴스 수정 — 저장된 토큰 유지와 상태 선택
      await goto(page, fedTab('instances'));
      await row(page, NAME).getByRole('button', { name: '수정' }).click();
      await dialog(page).getByText('인스턴스 수정').waitFor();
      await calm(page, { top: true });
      await shot(page, 'federation/instance-edit');

      // 표시 정보 선택 — 샘플 데이터 · DDL · 오너를 해제(저장하지 않고 닫는다)
      await dialog(page).getByRole('button', { name: '노출 항목 불러오기' }).click();
      await dialog(page).getByText(/개 표시 \//).waitFor();
      for (const label of ['오너', '샘플 데이터', 'DDL']) {
        await dialog(page).getByLabel(label, { exact: true }).uncheck();
      }
      await dialog(page).getByRole('button', { name: '저장', exact: true }).scrollIntoViewIfNeeded();
      await settle(page, 300);
      await shot(page, 'federation/display-fields');
      await dialog(page).getByRole('button', { name: '취소' }).click();

      // 인스턴스 삭제 확인 대화상자(취소로 닫는다)
      await row(page, NAME).getByRole('button', { name: '삭제' }).click();
      await page.getByText('인스턴스 삭제').waitFor();
      await calm(page);
      await shot(page, 'federation/instance-delete');
      await page.getByRole('button', { name: '취소' }).click();

      // PAUSED 인스턴스 — 가져오기 아이콘이 사라진 행(찍고 ACTIVE 로 되돌린다)
      await hub('PUT', `/federation/instances/${inst.id}`, { status: 'PAUSED' });
      try {
        await goto(page, fedTab('instances'));
        await row(page, NAME).waitFor();
        await shot(page, 'federation/instance-paused');
      } finally {
        await hub('PUT', `/federation/instances/${inst.id}`, { status: 'ACTIVE' });
      }
    }

    if (on('policy')) {
      // 노출 설정 탭 — 그룹별 제공 항목 체크리스트
      const hubKeys = await allKeys(API);
      const before = (await hub('GET', '/federation/export-policy')).exposed;
      await goto(page, fedTab('export-policy'));
      await page.getByText(/개 항목 노출/).waitFor();
      await shot(page, 'federation/export-policy');

      // DDL 을 해제하고 저장한 결과(찍고 원래 정책으로 되돌린다)
      try {
        await page.getByLabel('DDL', { exact: true }).uncheck();
        await page.getByRole('button', { name: '저장', exact: true }).click();
        await page.getByText('노출 정책을 저장했습니다').waitFor();
        await page.mouse.move(900, 850);
        await settle(page, 300);
        await shot(page, 'federation/export-policy-saved');
      } finally {
        await hub('PUT', '/federation/export-policy', { exposed: before.length ? before : hubKeys });
      }

      // peer 가 노출을 설명 · 스키마 · 태그로 줄인 뒤 허브의 원격 상세 — 개요 · 스키마 탭만
      const peerKeys = await allKeys(PEER_API);
      await peer('PUT', '/federation/export-policy', { exposed: ['description', 'schema', 'tags'] });
      try {
        await goto(page, detailUrl(FURN));
        await page.getByRole('tab', { name: /개요/ }).waitFor();
        await settle(page);
        await shot(page, 'federation/detail-peer-limited');
      } finally {
        await peer('PUT', '/federation/export-policy', { exposed: peerKeys });
        await harvest(inst.id, true); // 그 사이 스케줄러가 지웠을 수 있는 미러 샘플 · 리니지 복구
      }

      // 관측성 탭 — 요약 카드와 peer 상태 표(정상)
      await goto(page, fedTab('observability'));
      await page.getByText('미러 데이터셋').waitFor();
      await settle(page);
      await shot(page, 'federation/observability');
    }

    if (on('errors')) {
      // ── 토큰이 틀린 peer: 가져오기 실패 ──
      const bad = await hub('POST', '/federation/instances', {
        ...BAD_TOKEN, auth_token: 'wrong-token', mode: 'HYBRID', sync_interval_sec: 900, description: '서비스 토큰 오류 재현',
      });
      try {
        // 가져오기 실패 — 행 아래 빨간 사유
        await goto(page, fedTab('instances'));
        await row(page, BAD_TOKEN.name).getByRole('button', { name: '가져오기 (증분)' }).click();
        await page.getByText('가져오기 실패').waitFor({ timeout: 30000 });
        await page.mouse.move(700, 700);
        await shot(page, 'federation/harvest-failed');

        // 관측성 — 최근 동기화 실패와 사유
        await goto(page, fedTab('observability'));
        await page.getByText('실패').first().waitFor();
        await settle(page);
        await shot(page, 'federation/observability-failed');
      } finally {
        await hub('DELETE', `/federation/instances/${bad.id}`);
      }

      // ── 닿지 않는 peer: 상태점검 실패 · 회로 열림 · 실시간 검색 부분 결과 ──
      const down = await hub('POST', '/federation/instances', {
        ...UNREACHABLE, mode: 'LIVE', sync_interval_sec: 900, description: '연결 실패 재현',
      });
      try {
        // 실시간(LIVE) peer 실패 — 실패 인스턴스 표시와 나머지 결과
        await goto(page, `${fedTab()}?q=customer`);
        await page.getByText(/실패:/).waitFor({ timeout: 30000 });
        await settle(page);
        await shot(page, 'federation/search-live-partial');

        // 상태점검 실패 — 불가와 사유
        await goto(page, fedTab('instances'));
        await row(page, NAME).getByRole('button', { name: '헬스 체크' }).click();
        await row(page, UNREACHABLE.name).getByRole('button', { name: '헬스 체크' }).click();
        await row(page, UNREACHABLE.name).getByText('불가').waitFor({ timeout: 30000 });
        await row(page, NAME).getByTitle('다시 점검').waitFor();
        await page.mouse.move(700, 700);
        await shot(page, 'federation/health-fail');

        // 연달아 실패해 회로가 열린 뒤의 원격 상세 안내
        const downUrn = `${UNREACHABLE.instance_key}::${cust.urn}`;
        for (let i = 0; i < 4; i++) {
          await hub('GET', `/federation/datasets/detail?urn=${encodeURIComponent(downUrn)}`).catch(() => {});
        }
        await goto(page, detailUrl(downUrn));
        await page.getByText(/circuit open/).waitFor({ timeout: 30000 });
        await settle(page);
        await shot(page, 'federation/detail-circuit-open');

        // 관측성 — 회로 열림
        await goto(page, fedTab('observability'));
        await page.getByText(/열림 \(/).waitFor();
        await settle(page);
        await shot(page, 'federation/observability-breaker-open');
      } finally {
        await hub('DELETE', `/federation/instances/${down.id}`);
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch(e => { console.error(e); process.exit(1); });
