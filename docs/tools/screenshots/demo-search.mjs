/**
 * 통합 검색(ops/search.adoc) 화면 캡처 가운데 임베딩(시맨틱 검색)이 켜져 있어야 하는 그림 — ops-search/*.png
 *
 * ** 데모 환경 전용 · 조회만 **
 * 통합 테스트 스택은 임베딩이 꺼져 있어, 임베딩이 켜지고 백필이 끝난 데모 환경의 화면을 찍는다.
 * 검색하고 설정 화면을 열어 보기만 한다 — 백필을 돌리거나 설정을 저장하거나 용어를 만들지 않는다(API 도 GET 만).
 *
 *   - results-hybrid    `세금계산서` 검색 — 혼합 · 의미 · 키워드 결과와 점수
 *   - korean-semantic   `직원 월급` 검색 — 어느 이름에도 없는 말로 급여 · 사원 데이터셋과 인건비 용어를 찾음
 *   - backfill          설정 → 임베딩 — 임베딩 상태 카드(커버리지 · 제공자 · 모델). 백필은 누르지 않는다
 * auto-embedded-term(새 용어 자동 임베딩)은 용어를 만들어야 해서 여기 없다.
 *
 *   UI=http://192.0.2.10:25100 API=http://192.0.2.10:25110/api/v1 \
 *   PLAYWRIGHT_DIR=... CHROMIUM_PATH=... node docs/tools/screenshots/demo-search.mjs     # ONLY=backfill 처럼 일부만
 */
import { open, goto, settle, shot, apiToken, api, UI } from './lib.mjs';

if (/127\.0\.0\.1|localhost/.test(UI)) throw new Error('데모 환경 전용입니다 — UI · API 환경 변수로 데모 주소를 주세요.');

const P = 'ops-search';
const token = await apiToken();
const emb = await api(token, 'GET', '/settings/embedding');
if (!emb.enabled) {
  console.log('임베딩이 꺼져 있어 results-hybrid · korean-semantic · backfill 을 건너뜀');
  process.exit(0);
}

const { browser, page } = await open();
const search = async q => { await goto(page, `/dashboard/search?q=${encodeURIComponent(q)}`); await settle(page, 800); };
try {
  // 하이브리드 검색 — 혼합(ar.invoices) · 의미(gl.journal_entries) · 키워드(commerce.orders) 결과와 점수
  await search('세금계산서');
  await shot(page, `${P}/results-hybrid`);

  // 한글 의미 검색 — 어느 항목에도 없는 말 '직원 월급' 으로 급여 · 사원 데이터셋을 찾음
  await search('직원 월급');
  await shot(page, `${P}/korean-semantic`);

  // 임베딩 상태 — 커버리지 100%, 제공자 · 모델 표시 (설정 화면을 열기만 한다)
  await goto(page, '/dashboard/settings?tab=embedding');
  await page.getByText('커버리지').first().waitFor({ timeout: 15000 });
  await settle(page, 600);
  await shot(page, `${P}/backfill`);
} finally {
  await browser.close();
}
