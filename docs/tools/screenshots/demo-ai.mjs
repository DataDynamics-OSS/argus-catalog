/**
 * AI 영역 화면 캡처 가운데 실제 LLM 응답이 있어야 하는 그림 — ai-assistant · ai-metadata-generation
 *
 * ** 데모 환경 전용 · 조회만 **
 * 통합 테스트 스택에는 LLM 이 없어, LLM(ollama)과 AI 어시스턴트가 켜져 있는 데모 환경의 화면을 찍는다.
 * 데이터 · 설정을 만들거나 고치지 않는다. 하는 동작은 어시스턴트에 질문 1개를 보내는 것뿐이다.
 *
 *   - ai-metadata-generation/ai-generate-menu  데이터셋 상세 ⋯ → AI 자동 생성 하위 메뉴 (열기만 하고 고르지 않는다)
 *   - ai-assistant/chat                        우하단 어시스턴트에서 질문 → 도구 호출 카드 → 답변
 *
 * ai-metadata-generation/preview-dialog("AI 생성 컬럼 설명" 미리보기)는 여기 없다 — 데모의 모든 컬럼에 설명이 있어
 * 열 설명을 고르면 "모든 컬럼에 이미 설명이 있습니다." 만 나오고, 미리보기를 띄우면 그 결과가 대기 중인 AI 제안으로 남기 때문이다.
 *
 *   UI=http://192.0.2.10:25100 API=http://192.0.2.10:25110/api/v1 \
 *   PLAYWRIGHT_DIR=... CHROMIUM_PATH=... node docs/tools/screenshots/demo-ai.mjs     # ONLY=chat 처럼 일부만
 */
import { open, goto, settle, shot, apiToken, api, UI } from './lib.mjs';

if (/127\.0\.0\.1|localhost/.test(UI)) throw new Error('데모 환경 전용입니다 — UI · API 환경 변수로 데모 주소를 주세요.');

const DATASET = 'commerce.orders';
const QUESTION = process.env.QUESTION || '고객별 주문 금액을 보려면 어떤 테이블을 쓰면 돼?';
const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const wanted = name => !only.length || only.includes(name);

const token = await apiToken();
const found = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(DATASET)}&page_size=50`);
const dataset = (found.items || []).find(d => d.name === DATASET);
if (!dataset) throw new Error(`데이터셋 없음: ${DATASET}`);

const { browser, page } = await open();
try {
  // 데이터셋 상세의 ⋯ 메뉴 — AI 자동 생성 하위 메뉴가 열린 모습 (commerce.orders). 항목은 누르지 않는다
  if (wanted('ai-generate-menu')) {
    await goto(page, `/dashboard/datasets/${dataset.id}`);
    await page.getByRole('button', { name: `${dataset.name} 메뉴` }).click();
    await page.waitForTimeout(300);
    await page.getByRole('menuitem', { name: /AI 자동 생성/ }).hover();
    await page.getByRole('menuitem', { name: '열 설명' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'ai-metadata-generation/ai-generate-menu');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
  }

  // AI 어시스턴트 — 질문을 보내고, 도구 호출 카드와 답변(SQL 코드 블록)이 다 나온 뒤의 패널 (대시보드 위, 최대화)
  if (wanted('chat')) {
    await goto(page, '/dashboard');
    await page.getByRole('button', { name: /어시스턴트/ }).last().click();
    const input = page.getByPlaceholder(/메시지를 입력/);
    await input.waitFor();
    await input.fill(QUESTION);
    await input.press('Enter');
    // 답변이 끝날 때까지 — 패널 글이 8초 동안 더 바뀌지 않으면 끝난 것으로 본다(최대 4분)
    let last = '', still = 0;
    for (let i = 0; i < 120 && still < 4; i++) {
      await page.waitForTimeout(2000);
      const now = await page.locator('body').innerText();
      still = now === last && i > 3 ? still + 1 : 0;
      last = now;
    }
    if (process.env.DEBUG_DIR) await page.screenshot({ path: `${process.env.DEBUG_DIR}/chat-small.png` });
    // 질문 · 도구 호출 카드 · 답변이 한 화면에 들어오게 패널을 최대화하고 대화의 처음으로 올린다
    await page.getByRole('button', { name: '최대화' }).click();
    await settle(page, 500);
    await input.evaluate(el => {
      let p = el;
      while ((p = p.parentElement)) {
        const sc = [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(e).overflowY) && e.innerText.length > 50);
        if (sc) { sc.scrollTop = 0; return; }
        if (p.getAttribute('role') === 'dialog') return;
      }
    });
    await settle(page, 500);
    await shot(page, 'ai-assistant/chat');
  }
} finally {
  await browser.close();
}
