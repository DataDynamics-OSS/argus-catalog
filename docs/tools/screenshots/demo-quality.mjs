/**
 * 데이터 품질(catalog/quality.adoc) 화면 캡처 가운데 품질 워커가 실제로 돈 기록이 있어야 하는 그림 — catalog-quality/*.png
 *
 * ** 데모 환경 전용 · 조회만 **
 * 통합 테스트 스택에는 품질 워커가 없어, 진단 이력 · 작업 이력 · 리포트가 쌓여 있는 데모 환경의 화면을 찍는다.
 * 화면을 열어 보기만 한다 — 진단을 실행하거나 규칙 · 작업 · 설정을 만들거나 고치지 않는다(API 도 GET 만).
 *
 * 전제: 데모의 commerce-mysql 데이터 소스(commerce.customers 등), 품질 진단 작업 '커머스 원천 주간 진단' 과 그 실행 이력.
 *
 *   UI=http://192.0.2.10:25100 API=http://192.0.2.10:25110/api/v1 \
 *   PLAYWRIGHT_DIR=... CHROMIUM_PATH=... node docs/tools/screenshots/demo-quality.mjs     # ONLY=runs,report 처럼 일부만
 *
 * governance-alerts/detail-quality(품질 진단 실패 알림 상세)는 데모에 알림이 한 건도 없어 담지 못했다 —
 * 데모에 '품질 진단 실패' 알림 규칙을 두고 진단이 실패하면 /dashboard/alerts?alert=<id> 를 열어 찍으면 된다.
 */
import { open, goto, settle, shot, apiToken, api, UI } from './lib.mjs';

if (/127\.0\.0\.1|localhost/.test(UI)) throw new Error('데모 환경 전용입니다 — UI · API 환경 변수로 데모 주소를 주세요.');

const AREA = 'catalog-quality';
const DATASET = 'commerce.customers';
const JOB = '커머스 원천 주간 진단';
const token = await apiToken();

const found = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(DATASET)}&page_size=50`);
const dataset = (found.items || []).find(d => d.name === DATASET);
if (!dataset) throw new Error(`데이터셋 없음: ${DATASET}`);

/** 데이터셋 상세의 품질 탭을 연다. */
async function qualityTab(p) {
  await goto(p, `/dashboard/datasets/${dataset.id}?tab=quality`);
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
/** 관리 > 데이터 품질의 탭을 연다. */
async function adminTab(p, name) {
  await goto(p, '/dashboard/quality-runs');
  await p.getByRole('tab', { name, exact: true }).click();
  await settle(p, 1000);
}

const { browser, page } = await open();
try {
  // 품질 점수 영역 — 점수와 막대 · 통과 9 / 실패 1 · 점수 추세(최근 N회) · 실행 버튼 (commerce.customers)
  await qualityTab(page);
  await shot(page, `${AREA}/batch-result`);

  // 임계값에 따른 판정 — phone_no 는 NULL 이 있어도 임계값 95% 이상이라 통과, 임계값 100% 규칙은 1건도 허용하지 않음
  await scrollTop(page, page.getByText('기준 방식', { exact: true }).first(), 100);
  await shot(page, `${AREA}/threshold-results`);

  // 데이터 프로파일 표 — NULL 비율 · 고유값 · 최빈값 (commerce.customers)
  await qualityTab(page);
  await scrollTop(page, page.getByRole('heading', { name: '데이터 프로파일' }).first(), 30);
  await shot(page, `${AREA}/profile`);

  // 관리 > 데이터 품질 > 실행 현황 — 워커 상태 줄 · 필터 · 실행 표
  await adminTab(page, '실행 현황');
  await shot(page, `${AREA}/runs`);

  // 실행 상세 대화상자 — 개요 탭 (commerce.customers 의 가장 최근 진단)
  // 기본 기간(최근 24시간)에는 그 진단이 없고, 다른 데이터 소스의 진단이 많으면 첫 페이지 밖으로 밀린다 —
  // 전체 기간 · 그 데이터셋의 데이터 소스로 걸러 연다
  const detail = await api(token, 'GET', `/catalog/datasets/${dataset.id}`);
  await goto(page, `/dashboard/quality-runs?period=all&datasource=${detail.datasource.id}`);
  await page.getByRole('row').filter({ hasText: DATASET }).first().getByText('완료').click();
  await page.getByRole('dialog').waitFor();
  await settle(page, 800);
  await shot(page, `${AREA}/run-detail`);
  await page.keyboard.press('Escape');

  // 관리 > 데이터 품질 > 작업 이력 — 한 줄을 펼쳐 데이터셋별 결과가 보이는 모습
  await adminTab(page, '작업 이력');
  await page.getByRole('row').filter({ hasText: JOB }).first().getByText('완료').click();
  await settle(page, 800);
  await shot(page, `${AREA}/job-history`);

  // 관리 > 데이터 품질 > 리포트 — 머리글 · 핵심 숫자 · 영역별 / 진단 기준별 표 (가장 최근에 끝난 작업 실행)
  await adminTab(page, '리포트');
  await page.getByText('진단 기준별').first().waitFor({ timeout: 15000 });
  await settle(page, 800);
  await shot(page, `${AREA}/report`);

  // 리포트 아래쪽 — 회차별 추이 차트와 데이터셋별 표
  await scrollTop(page, page.getByText('회차별 추이').first(), 100);
  await settle(page, 800);
  await shot(page, `${AREA}/report-trend`);
} finally {
  await browser.close();
}
