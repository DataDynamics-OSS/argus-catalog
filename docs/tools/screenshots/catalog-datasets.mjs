/**
 * 매뉴얼 "데이터셋"(pages/catalog/datasets.adoc) 화면 캡처.
 *
 * 읽기만 하는 그림(목록 · 상세 · DDL · 메뉴)은 시드 데이터(Sakila)로 찍고,
 * 편집하는 그림(논리명 · 설명 · 소유자 · 메타데이터 저장 · 샘플 · 댓글 · 삭제)은
 * 이 스크립트가 만드는 데이터 소스 "주문 관리 DB" 의 데이터셋 `oms.orders`(주문)로 찍는다.
 * 시작할 때 같은 이름의 데이터 소스가 남아 있으면 지우고 새로 만들며, 끝날 때 다시 지운다.
 *
 * DEBUG_DIR 을 주면 실패한 단계의 화면을 그 폴더에 남긴다.
 */
import { writeFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { launch, newContext, login, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'catalog-datasets';
const DS_NAME = '주문 관리 DB';
const DATASET = {
  name: 'oms.orders',
  display_name: '주문',
  summary: '온라인 주문 내역 (주문 1건 = 1행)',
  schema_fields: [
    { field_path: 'order_id', field_type: 'BIGINT', nullable: 'false' },
    { field_path: 'customer_id', field_type: 'BIGINT' },
    { field_path: 'amount', field_type: 'NUMERIC' },
    { field_path: 'ordered_at', field_type: 'TIMESTAMP' },
  ],
};
// 샘플 탭에 올릴 파일 — 필드 구분자가 세미콜론
const SAMPLE_CSV = 'order_id;amount;email\n1;12000;kim@example.com\n2;25000;lee@example.com\n3;42000;park@example.com\n';

const only = (process.env.ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
const want = (...names) => !only.length || names.some((n) => only.includes(n));
const failed = [];

const admin = await apiToken();

async function cleanup() {
  const list = await api(admin, 'GET', '/catalog/datasources');
  for (const ds of (list.items ?? list).filter((d) => d.name === DS_NAME)) {
    await api(admin, 'DELETE', `/catalog/datasources/${ds.id}?force=true`).catch((e) => console.warn(`cleanup: ${e.message}`));
  }
}

/** 한 단계 — ONLY 에 걸리지 않으면 건너뛰고, 실패해도 다음 단계로 간다. */
async function step(names, page, fn) {
  names = [].concat(names);
  if (!want(...names)) return;
  try {
    await fn();
  } catch (e) {
    failed.push(names.join(','));
    console.error(`FAILED ${names.join(',')}: ${String(e.message).split('\n').slice(0, 6).join('\n')}`);
    if (process.env.DEBUG_DIR && page) {
      await page.screenshot({ path: path.join(process.env.DEBUG_DIR, `fail-${names[0]}.png`) }).catch(() => {});
    }
    await page?.keyboard.press('Escape').catch(() => {});
  }
}

const tab = (page, name) => page.getByRole('tab', { name }).first();

await cleanup();
const ds = await api(admin, 'POST', '/catalog/datasources', { name: DS_NAME, type: 'postgresql', origin: 'DEV' });
const mine = await api(admin, 'POST', '/catalog/datasets', { ...DATASET, datasource_id: ds.id });
const film = (await api(admin, 'GET', '/catalog/datasets?search=sakila.film&page_size=50')).items.find((d) => d.name === 'sakila.film');
const rental = (await api(admin, 'GET', '/catalog/datasets?search=sakila.rental&page_size=50')).items.find((d) => d.name === 'sakila.rental');
const MINE = `/dashboard/datasets/${mine.id}`;
const FILM = `/dashboard/datasets/${film.id}`;

const browser = await launch();
try {
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await login(page);

  // ── 목록 ────────────────────────────────────────────────────────────────
  // 데이터셋 목록 — 테이블 보기
  await step('list', page, async () => {
    await goto(page, '/dashboard/datasets');
    await page.getByRole('button', { name: '테이블 뷰' }).click();
    await settle(page);
    await shot(page, `${AREA}/list`);
  });

  // 데이터 소스 · 환경 · 상태 필터를 고른 모습(상태 필터 목록이 열린 상태)
  await step('list-filters', page, async () => {
    await goto(page, '/dashboard/datasets');
    await page.getByRole('button', { name: '테이블 뷰' }).click();
    const pick = async (filter, option, close = true) => {
      await page.getByRole('button', { name: filter, exact: true }).or(page.getByRole('button', { name: new RegExp(`^${filter}`) })).first().click();
      await page.getByRole('option', { name: option }).first().click();
      if (close) await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
    };
    await pick('데이터 소스', 'Sakila (MySQL)');
    await pick('환경', /DEV/);
    await pick('상태', '활성', false);
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/list-filters`);
    await page.keyboard.press('Escape');
  });

  // 카드 보기 — Sakila 데이터 소스로 거른 카드(경로 · 논리명 · 컬럼 칩 · 바닥 줄)
  await step('card-view', page, async () => {
    await goto(page, '/dashboard/datasets?datasource=sakila-mysql');
    await page.getByRole('button', { name: '카드 뷰' }).click();
    await settle(page, 1000);
    await shot(page, `${AREA}/card-view`);
    await page.getByRole('button', { name: '테이블 뷰' }).click();
  });

  // 데이터셋 추가 대화상자 — 값을 채운 상태(등록하지는 않는다)
  await step('add-dialog', page, async () => {
    await goto(page, '/dashboard/datasets');
    await page.getByRole('button', { name: '데이터셋 추가' }).click();
    await page.fill('#ds-name', 'oms.order_items');
    await page.fill('#ds-display-name', '주문 상품');
    await page.click('#ds-catalog');  // 데이터 소스와 함께 생긴 카탈로그(이름 = 데이터 소스 이름)
    await page.getByRole('option', { name: new RegExp(DS_NAME) }).click();
    await page.fill('#ds-summary', '주문에 담긴 상품 (주문 상품 1건 = 1행)');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/add-dialog`);
    await page.getByRole('button', { name: '취소' }).click();
  });

  // ── 상세(시드 데이터 sakila.film) ─────────────────────────────────────────
  // 데이터셋 상세 — 헤더와 개요 탭
  await step('detail', page, async () => {
    await goto(page, FILM);
    await settle(page, 800);
    await shot(page, `${AREA}/detail`);
  });

  // 더보기 메뉴
  await step('more-menu', page, async () => {
    await goto(page, FILM);
    await page.getByRole('button', { name: /더보기/ }).or(tab(page, /더보기/)).first().click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/more-menu`);
    await page.keyboard.press('Escape');
  });

  // PySpark 이관 코드(더보기 → PySpark)
  await step('pyspark', page, async () => {
    await goto(page, FILM);
    await page.getByRole('button', { name: /더보기/ }).or(tab(page, /더보기/)).first().click();
    await page.getByRole('menuitem', { name: 'PySpark' }).click();
    await settle(page, 1000);
    await shot(page, `${AREA}/pyspark`);
  });

  // DDL 탭 — CREATE TABLE DDL
  await step('ddl', page, async () => {
    await goto(page, `${FILM}?tab=ddl`);
    await settle(page, 1000);
    await shot(page, `${AREA}/ddl`);
  });

  // 헤더 작업(⋯) 메뉴
  await step('action-menu', page, async () => {
    await goto(page, FILM);
    await page.getByRole('button', { name: 'sakila.film 메뉴' }).click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/action-menu`);
    await page.keyboard.press('Escape');
  });

  // 메타데이터 탭 — 분류 · 소유 영역
  await step('metadata-tab', page, async () => {
    await goto(page, `${FILM}?tab=metadata`);
    await settle(page, 800);
    await shot(page, `${AREA}/metadata-tab`);
  });

  // 권한이 없는 사용자의 메타데이터 탭 — mjkim 이 소유자가 아닌 sakila.rental 을 연 화면
  await step('metadata-read-only', page, async () => {
    const uctx = await newContext(browser);
    const upage = await uctx.newPage();
    await login(upage, 'mjkim', 'mjkim');
    await goto(upage, `/dashboard/datasets/${rental.id}?tab=metadata`);
    await settle(upage, 800);
    await shot(upage, `${AREA}/metadata-read-only`);
    await uctx.close();
  });

  // ── 편집(임시 데이터셋 oms.orders) ─────────────────────────────────────────
  // 헤더 논리명 편집 — 제목을 눌러 입력란이 열린 상태
  await step('title-edit', page, async () => {
    await goto(page, MINE);
    await page.locator('h1[title="클릭하여 논리명 편집"]').click();
    await page.getByPlaceholder(/^논리명/).fill('주문 (온라인)');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/title-edit`);
    await page.getByRole('button', { name: '취소' }).click();
  });

  // 상세 설명 서식 편집기
  await step('description-editor', page, async () => {
    await goto(page, MINE);
    await page.getByText('클릭해서 설명을 추가할 수 있습니다.').click();
    const ed = page.locator('.ProseMirror').first();
    await ed.click();
    await page.keyboard.press('Control+b');
    await page.keyboard.type('온라인 몰의 주문 헤더');
    await page.keyboard.press('Control+b');
    await page.keyboard.type(' 테이블입니다.');
    await page.keyboard.press('Enter');
    await page.keyboard.type('- 주문 1건이 1행이며, 상품별 내역은 oms.order_items 에 있습니다.');
    await page.keyboard.press('Enter');
    await page.keyboard.type('amount 는 원(KRW) 기준이고 부가세를 포함합니다.');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/description-editor`);
  });

  const row = (p, label) => p.locator('tr').filter({ has: p.getByRole('cell', { name: label, exact: true }) }).first();

  // 소유자 추가 — 유형을 고르고 사용자를 검색한 상태
  await step('owner-search', page, async () => {
    await goto(page, `${MINE}?tab=metadata`);
    const r = row(page, '소유자');
    await r.getByRole('combobox').click();
    await page.getByRole('option', { name: '비즈니스 소유자' }).click();
    await r.getByRole('button', { name: '추가' }).click();
    await page.getByPlaceholder('사용자 검색…').fill('mjkim');
    await page.getByRole('option', { name: /mjkim/ }).waitFor();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/owner-search`);
    await page.keyboard.press('Escape');
  });

  // 용어집 용어 선택 창 — 왼쪽 폴더 트리, 오른쪽 용어 검색
  await step('glossary-picker', page, async () => {
    await goto(page, `${MINE}?tab=metadata`);
    await row(page, '용어집').getByRole('button', { name: '추가' }).click();
    await page.getByPlaceholder('용어 검색…').waitFor();
    await settle(page, 600);
    await shot(page, `${AREA}/glossary-picker`);
    await page.keyboard.press('Escape');
  });

  // 확장 메타데이터 저장 — 값을 고치고 저장한 뒤의 알림
  await step('metadata-saved', page, async () => {
    await goto(page, `${MINE}?tab=metadata`);
    const choose = async (label, option) => {
      await row(page, label).getByRole('combobox').first().click();
      await page.getByRole('option', { name: option }).first().click();
    };
    await choose('민감도 등급', /대내한정|INTERNAL/);
    await choose('등급(Tier)', /Gold|GOLD|골드/);
    await choose('인증 상태', /CERTIFIED|인증/);
    const tier = row(page, '등급(Tier)');
    await tier.scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: '저장', exact: true }).last().click();
    await page.getByText('메타데이터를 저장했습니다.').waitFor();
    await tier.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/metadata-saved`);
  });

  // ── 샘플 탭 ──────────────────────────────────────────────────────────────
  const tmp = await mkdtemp(path.join(tmpdir(), 'argus-shot-'));
  const csv = path.join(tmp, 'orders_sample.csv');
  await writeFile(csv, SAMPLE_CSV);

  // 샘플 업로드 영역(샘플이 아직 없는 상태)
  await step(['sample-upload', 'sample-preview', 'sample-parquet'], page, async () => {
    await api(admin, 'DELETE', `/catalog/datasets/${mine.id}/sample`).catch(() => {});
    await goto(page, `${MINE}?tab=sample`);
    await page.getByText('CSV 파일을 업로드하려면 클릭하세요').waitFor();
    await shot(page, `${AREA}/sample-upload`);

    // 구분자를 세미콜론으로 바꾼 미리보기
    await page.locator('input[type=file]').setInputFiles(csv);
    await page.getByText('필드 구분자', { exact: true }).waitFor();
    await page.getByText('필드 구분자', { exact: true }).locator('xpath=..').getByRole('combobox').click();
    await page.getByRole('option', { name: /Semicolon|세미콜론/ }).click();
    await page.getByRole('button', { name: '새로고침' }).click();
    await page.getByText(/3행/).waitFor();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/sample-preview`);

    // 저장 → Parquet 로 변환된 샘플과 알림
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await page.getByText('샘플 데이터를 Parquet 형식으로 변환했습니다.').waitFor();
    await page.waitForTimeout(600);
    await shot(page, `${AREA}/sample-parquet`);
  });

  // ── 댓글 탭 ──────────────────────────────────────────────────────────────
  await step(['comment-editor', 'comment-reply', 'comment-menu', 'comment-edited'], page, async () => {
    // 댓글 작성 — 서식(굵게 · 목록)을 넣고 분류를 고른 상태
    await goto(page, `${MINE}?tab=comments`);
    const ed = page.locator('.ProseMirror').first();
    await ed.click();
    await page.keyboard.press('Control+b');
    await page.keyboard.type('amount 컬럼 단위');
    await page.keyboard.press('Control+b');
    await page.keyboard.type('를 설명에 적어 주세요.');
    await page.keyboard.press('Enter');
    await page.keyboard.type('- 원(KRW) 기준인지');
    await page.keyboard.press('Enter');
    await page.keyboard.type('부가세 포함 여부');
    await page.getByRole('combobox').filter({ hasText: '일반' }).click();
    await page.getByRole('option', { name: '기능 요청' }).click();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/comment-editor`);
    await page.getByRole('button', { name: '등록', exact: true }).click();
    await page.getByText('amount 컬럼 단위').first().waitFor();
    await settle(page);

    // 답글 — mjkim 이 admin 의 댓글에 답글을 단 화면
    const uctx = await newContext(browser);
    const upage = await uctx.newPage();
    await login(upage, 'mjkim', 'mjkim');
    await goto(upage, `${MINE}?tab=comments`);
    await upage.getByRole('button', { name: '댓글', exact: true }).first().click();
    const reply = upage.locator('.ProseMirror').last();
    await reply.click();
    await upage.keyboard.type('원(KRW) 기준이고 부가세 포함입니다. 설명에 반영해 두겠습니다.');
    await upage.getByRole('button', { name: '댓글', exact: true }).last().click();
    await upage.getByText('설명에 반영해 두겠습니다.').first().waitFor();
    await settle(upage);
    await upage.mouse.move(700, 40);
    await shot(upage, `${AREA}/comment-reply`);
    await uctx.close();

    // 댓글 ⋯ 메뉴 — 편집 · 삭제
    await goto(page, `${MINE}?tab=comments`);
    await page.getByRole('button', { name: '댓글 메뉴' }).first().click();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/comment-menu`);

    // 수정된 댓글 — (수정됨) 표시
    await page.getByRole('menuitem', { name: '편집' }).click();
    const edit = page.locator('.ProseMirror').nth(1);
    await edit.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.press('Enter');
    await page.keyboard.type('통화(currency) 컬럼 유무');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await page.getByText('(수정됨)').first().waitFor();
    await settle(page);
    await page.mouse.move(700, 40);
    await shot(page, `${AREA}/comment-edited`);
  });

  // ── 삭제 ────────────────────────────────────────────────────────────────
  // 데이터셋 삭제 확인 — 입력란에 "삭제" 를 넣어 버튼이 켜진 상태(누르지는 않는다)
  await step('delete-confirm', page, async () => {
    await goto(page, MINE);
    await page.getByRole('button', { name: `${DATASET.name} 메뉴` }).click();
    await page.getByRole('menuitem', { name: '삭제' }).click();
    await page.getByPlaceholder('삭제').fill('삭제');
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/delete-confirm`);
    await page.getByRole('button', { name: '취소' }).click();
  });
} finally {
  if (!process.env.KEEP) await cleanup();
  await browser.close();
}
if (failed.length) {
  console.error(`failed: ${failed.join(' ')}`);
  process.exitCode = 1;
}
