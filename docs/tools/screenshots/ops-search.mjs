/**
 * 통합 검색 (ops/search.adoc) 화면 캡처.
 *
 * 시드 데이터(Sakila · 용어집 · AI Agent · API · MCP 서버)만 쓰고 설정은 바꾸지 않는다.
 * 통합 테스트 스택은 임베딩이 꺼져 있어(키워드 검색만) 시맨틱 검색 그림은 임베딩이 켜져 있을 때만 찍는다:
 *   results-hybrid · korean-semantic · backfill — 임베딩이 켜져 있고 백필이 끝난 스택에서만.
 *   auto-embedded-term — "쓰기 시 자동 임베딩"까지 켜진 스택에서만(용어를 하나 만들고 찍은 뒤 지운다).
 */
import { open, goto, settle, shot, apiToken, api, VIEWPORT } from './lib.mjs';

const P = 'ops-search';
const token = await apiToken();
const emb = await api(token, 'GET', '/settings/embedding');
const { browser, page } = await open();
const search = q => goto(page, `/dashboard/search?q=${encodeURIComponent(q)}`);
const header = () => page.getByPlaceholder(/이름·설명·의미로 데이터셋 검색/);

try {
  // 헤더 검색창에 검색어 입력(대시보드에서, Enter 누르기 전)
  await goto(page, '/dashboard');
  await header().click();
  await header().fill('대여');
  await settle(page, 300);
  await shot(page, `${P}/header-search`);

  // 데이터셋 행 — 물리명 · 논리명 · 요약, 키워드 매칭
  await header().press('Enter');
  await page.waitForURL(/\/dashboard\/search/);
  await settle(page);
  await page.mouse.move(700, 40);
  await shot(page, `${P}/results-keyword`);

  // 검색 결과 전체 — 데이터셋 · 용어집 · AI Agent · API 그룹 (결과가 다 보이도록 화면을 늘려 찍는다)
  const fullHeight = await page.evaluate(() => {
    let h = document.documentElement.scrollHeight;
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(el).overflowY)) {
        h = Math.max(h, el.scrollHeight + el.getBoundingClientRect().top);
      }
    }
    return Math.ceil(h);
  });
  await page.setViewportSize({ width: VIEWPORT.width, height: Math.max(VIEWPORT.height, fullHeight + 16) });
  await settle(page, 400);
  await shot(page, `${P}/results-full`);
  await page.setViewportSize(VIEWPORT);

  // 용어집 결과를 눌러 이동 — 해당 분류가 선택됨
  await search('대여');
  await page.getByText('대여점포', { exact: true }).first().click();
  await page.waitForURL(/\/dashboard\/glossary/);
  await settle(page);
  await shot(page, `${P}/goto-glossary`);

  // AI Agent 결과를 눌러 이동 — 에이전트 상세
  await search('대여');
  await page.getByText('대여 고객지원 에이전트', { exact: true }).first().click();
  await page.waitForURL(/\/dashboard\/ai-agents/);
  await settle(page, 1200);
  await shot(page, `${P}/goto-agent`);

  // kafka 검색 — MCP 서버 그룹과 분류 · 플랫폼 · 상태 배지
  await search('kafka');
  await shot(page, `${P}/mcp-group`);

  // 결과 없음 안내
  await search('분기별 매출 전망');
  await shot(page, `${P}/no-results`);

  // 임베딩 모델 선택 — 384차원 모델, 다국어 모델이 기본 (목록만 펼치고 저장하지 않는다)
  await goto(page, '/dashboard/settings?tab=embedding');
  await page.getByRole('combobox').filter({ hasText: /MiniLM|bge|all-minilm/ }).first().click();
  await settle(page, 300);
  await shot(page, `${P}/model-options`);
  await page.keyboard.press('Escape');

  if (!emb.enabled) {
    // 임베딩 비활성 — 키워드 검색만(글자가 일치하지 않는 movie 는 결과 없음)
    await search('movie');
    await shot(page, `${P}/keyword-only`);
    console.log('임베딩이 꺼져 있어 results-hybrid · korean-semantic · backfill · auto-embedded-term 은 건너뜀');
  } else {
    // 하이브리드 검색 — hybrid · semantic · keyword 결과와 점수
    await search('customer');
    await shot(page, `${P}/results-hybrid`);

    // 한글 의미 검색 — 비디오 빌리기
    await search('비디오 빌리기');
    await shot(page, `${P}/korean-semantic`);

    // 백필 완료 — 커버리지 100%, 제공자 · 모델 표시
    await goto(page, '/dashboard/settings?tab=embedding');
    await shot(page, `${P}/backfill`);

    if (emb.auto_on_write) {
      // 자동 임베딩 — 방금 만든 용어가 백필 없이 바로 검색됨
      const term = await api(token, 'POST', '/catalog/glossary', { name: '영화 스트리밍 구독', description: '월 정액으로 영화를 온라인에서 시청하는 서비스.' });
      try {
        await page.waitForTimeout(5000);
        await search('온라인 영화 정액제');
        await shot(page, `${P}/auto-embedded-term`);
      } finally {
        await api(token, 'DELETE', `/catalog/glossary/${term.id}`).catch(e => console.log(e.message));
      }
    }
  }
} finally {
  await browser.close();
}
