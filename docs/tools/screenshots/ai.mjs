/**
 * AI 영역 화면 캡처 — ai-metadata-generation · ai-assistant.
 *
 * LLM 이 없어도 찍을 수 있는 그림은 언제나 찍는다.
 *   - ai-metadata-generation/ai-setting        설정 → LLM / AI (저장된 값 그대로, 아무것도 바꾸지 않는다)
 *   - ai-metadata-generation/ai-generate-menu  데이터셋 상세 ⋯ → AI 자동 생성 하위 메뉴
 *
 * 실제 LLM 응답이 있어야 하는 그림은 LLM 이 켜져 있을 때만(`GET /ai/status` 의 enabled) 찍고, 아니면 건너뛴다.
 *   - ai-metadata-generation/preview-dialog    ⋯ → AI 자동 생성 → 열 설명 → "AI 생성 컬럼 설명" 미리보기
 *     (적용하지 않고 닫으며, 이번 생성으로 남은 승인 대기 제안은 거절 처리한다. 생성 로그 자체는 남는다)
 *
 * LLM 을 켜고 끄는 일은 이 스크립트가 하지 않는다 — 찍기 전에 손으로 준비하고, 끝나면 원래 값으로 되돌린다.
 *   1. docker compose -f design/integration-test/docker/docker-compose.yml -p argus-catalog --profile llm up -d --no-build ollama
 *   2. PUT /settings/llm  {"enabled":true,"provider":"ollama","model":"qwen2.5:7b","api_url":"http://ollama:11434",
 *                          "temperature":0.3,"max_tokens":1024,"language":"ko", ...}   (design/integration-test/21-ai-assistant.md AI-T04)
 *   3. 찍은 뒤 PUT /settings/llm 으로 원래 값을 되돌리고 docker rm -f argus-catalog-ollama
 *   생성 문구는 실행할 때마다 달라지므로, 영어 · 한자가 섞이거나 어색하면 ONLY=preview-dialog 로 다시 찍는다.
 *
 * 아래 그림은 어시스턴트(ai-service)까지 연결해야 해서 이 스크립트에 없다(손으로 재현).
 *   - ai-assistant/chat                        우하단 어시스턴트에서 질문 → 도구 호출 뒤 답변
 *
 *   node docs/tools/screenshots/ai.mjs      # ONLY=ai-setting 처럼 일부만
 */
import { launch, newContext, login, open, goto, settle, shot, apiToken, api } from './lib.mjs';

const token = await apiToken();

// 설정 → LLM / AI — 설정 항목이 끝까지 보이게 세로로 긴 창에서 사이드바를 뺀 본문만 찍는다
{
  const browser = await launch();
  try {
    const context = await newContext(browser, { viewport: { width: 1440, height: 1400 } });
    const page = await context.newPage();
    await login(page);
    await goto(page, '/dashboard/settings?tab=llm');
    await settle(page, 1000);
    await page.evaluate(() => document.activeElement?.blur());
    await shot(page, 'ai-metadata-generation/ai-setting', { clip: { x: 170, y: 0, width: 1270, height: 1400 } });
  } finally {
    await browser.close();
  }
}

// 데이터셋 상세의 ⋯ 메뉴 — AI 자동 생성 하위 메뉴가 열린 모습
{
  const { browser, page } = await open();
  try {
    const hit = await api(token, 'GET', '/catalog/datasets?search=sakila.film&page_size=20');
    const film = hit.items.find(d => d.name === 'sakila.film');
    // 통합 테스트 스택은 LLM 이 꺼져 있어 하위 메뉴가 열리지 않는다. 설정은 그대로 두고,
    // 이 브라우저가 받는 LLM 상태 응답만 "켜짐" 으로 바꿔 메뉴를 연다(생성 요청은 보내지 않는다).
    await page.route(/\/api\/v1\/ai\/status$/, async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({ response: res, json: { ...body, enabled: true, provider: body.provider ?? 'ollama', model: body.model ?? 'qwen2.5:7b' } });
    });
    await goto(page, `/dashboard/datasets/${film.id}`);
    await page.getByRole('button', { name: `${film.name} 메뉴` }).click();
    await page.waitForTimeout(300);
    await page.getByRole('menuitem', { name: /AI 자동 생성/ }).hover();
    await page.getByRole('menuitem', { name: '열 설명' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'ai-metadata-generation/ai-generate-menu');
  } finally {
    await browser.close();
  }
}

// AI 생성 컬럼 설명 미리보기 — ⋯ → AI 자동 생성 → 열 설명 (LLM 이 켜져 있을 때만. 적용하지 않고 닫는다)
{
  const status = await api(token, 'GET', '/ai/status');
  if (!status.enabled) {
    console.log('LLM 이 꺼져 있어 preview-dialog 는 건너뜀');
  } else {
    // 컬럼 설명이 비어 있고 컬럼이 적은 shop.orders — 설명이 길면 표가 창 너비를 넘어 뒷부분과 신뢰도 열이 가려지므로
    // (표 칸이 줄바꿈되지 않는다) 넘치지 않고 한글로만 된 결과가 나올 때까지 몇 번 다시 생성한다.
    const hit = await api(token, 'GET', '/catalog/datasets?search=shop.orders&page_size=20');
    const ds = hit.items.find(d => d.name === 'shop.orders');
    const pending = async () => (await api(token, 'GET', `/ai/datasets/${ds.id}/suggestions`)).map(s => s.id);
    const before = new Set(await pending());
    // 미리보기만 하고 닫아도 승인 대기 제안으로 남는다 — 이번에 생긴 것만 거절(삭제) 처리
    const cleanup = async () => {
      for (const id of await pending()) {
        if (!before.has(id)) await api(token, 'POST', `/ai/suggestions/${id}/reject`).catch(e => console.log(e.message));
      }
    };
    const { browser, page } = await open();
    try {
      await goto(page, `/dashboard/datasets/${ds.id}`);
      for (let attempt = 1; attempt <= 4; attempt++) {
        await page.getByRole('button', { name: `${ds.name} 메뉴` }).click();
        await page.waitForTimeout(300);
        await page.getByRole('menuitem', { name: /AI 자동 생성/ }).hover();
        await page.getByRole('menuitem', { name: '열 설명' }).click();
        // CPU 추론이면 수십 초~몇 분 걸린다(백엔드의 LLM 요청 제한 시간은 180초)
        const dialog = page.getByRole('dialog').filter({ hasText: 'AI 생성 컬럼 설명' });
        await dialog.waitFor({ timeout: 240000 });
        await dialog.getByRole('button', { name: /전체 적용/ }).waitFor();
        await settle(page, 500);
        await page.evaluate(() => document.activeElement?.blur());
        const rows = await dialog.locator('tbody tr').allInnerTexts();
        const clipped = await dialog.locator('table').evaluate(t => t.parentElement.scrollWidth > t.parentElement.clientWidth + 1);
        const foreign = rows.some(r => /[\u4e00-\u9fff\u3040-\u30ff]/.test(r) || /[A-Za-z]{4,} [A-Za-z]{4,} [A-Za-z]{4,}/.test(r.split('\t')[1] ?? ''));
        console.log(`시도 ${attempt}: 넘침=${clipped} 외국어=${foreign}`, rows);
        const ok = !clipped && !foreign;
        if (ok || attempt === 4) await shot(page, 'ai-metadata-generation/preview-dialog');
        await dialog.getByRole('button', { name: '닫기' }).first().click();
        await dialog.waitFor({ state: 'hidden' });
        await cleanup();
        if (ok) break;
      }
    } finally {
      await browser.close();
      await cleanup();
    }
  }
}
