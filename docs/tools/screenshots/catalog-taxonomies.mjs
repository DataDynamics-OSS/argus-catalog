/**
 * 데이터 카탈로그 → 분류 체계 (pages/catalog/taxonomies.adoc) 화면 캡처.
 *
 * 시드 분류체계(업무 도메인 · 데이터 유형)는 보기만 하고, 편집 · 매핑 · 삭제 그림은
 * 임시 분류체계 "담당 조직"(영업 > 고객 · 주문, 재무회계)을 만들어 찍은 뒤 지운다.
 * 다시 실행해도 안전하다(남아 있는 임시 분류체계는 시작할 때 지운다).
 */
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'catalog-taxonomies';
const TAX = '담당 조직';
const TAX_DESC = '데이터를 관리하는 조직 기준의 분류';

const token = await apiToken();

/** 임시 분류체계 삭제 — 분류를 아래 단계부터 지운 뒤 분류체계를 지운다. */
async function removeTemp() {
  for (const t of await api(token, 'GET', '/catalog/taxonomies')) {
    if (t.name !== TAX) continue;
    const tree = await api(token, 'GET', `/catalog/taxonomies/${t.id}/tree`);
    const drop = async c => { for (const ch of c.children) await drop(ch); await api(token, 'DELETE', `/catalog/categories/${c.id}`); };
    for (const c of tree.categories) await drop(c);
    await api(token, 'DELETE', `/catalog/taxonomies/${t.id}`);
  }
}

async function datasetId(name) {
  const r = await api(token, 'GET', `/catalog/datasets?search=${encodeURIComponent(name)}&page_size=50`);
  const hit = r.items.find(d => d.name === name);
  if (!hit) throw new Error(`dataset ${name} not found`);
  return hit.id;
}

/** 왼쪽 트리의 분류 줄(이름이 정확히 일치). */
const node = (page, name) => page.locator('aside').getByText(name, { exact: true }).first();
const toastGone = page => page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 10000 }).catch(() => {});
const park = page => page.mouse.move(900, 860);
const menu = async (page, name, item) => {
  await node(page, name).click({ button: 'right' });
  await page.getByRole('menuitem', { name: item, exact: true }).click();
  await settle(page, 400);
};

await removeTemp();

const taxonomies = await api(token, 'GET', '/catalog/taxonomies');
const business = taxonomies.find(t => t.name === '업무 도메인');
const dataType = taxonomies.find(t => t.name === '데이터 유형');
const rental = await datasetId('sakila.rental');

const { browser, page } = await open();
try {
  // 분류 체계 화면 — 업무 도메인 트리와 전체 데이터셋
  await goto(page, `/dashboard/taxonomies?taxonomy=${business.id}`);
  await park(page);
  await shot(page, `${AREA}/business-domain`);

  // 상위 분류(상품)를 고른 모습 — 하위 분류의 데이터셋까지 보이고, 그 행에는 제거가 없음
  await node(page, '상품').click();
  await settle(page);
  await park(page);
  await shot(page, `${AREA}/subtree`);

  // 미분류를 고른 모습 — 이 분류체계에 매핑되지 않은 데이터셋
  await node(page, '미분류').click();
  await settle(page);
  await park(page);
  await shot(page, `${AREA}/uncategorized`);

  // 페이지당 10행, 2페이지
  await goto(page, `/dashboard/taxonomies?taxonomy=${business.id}&size=10&page=2`);
  await page.locator('main main').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await park(page);
  await settle(page, 300);
  await shot(page, `${AREA}/pagination`);

  // 데이터 유형 — 단계 없는 평면 트리
  await goto(page, `/dashboard/taxonomies?taxonomy=${dataType.id}`);
  await park(page);
  await shot(page, `${AREA}/data-type`);

  // 편집 권한이 없는 사용자의 화면 — 조회만 가능
  {
    const u = await open('mjkim', 'mjkim');
    await goto(u.page, `/dashboard/taxonomies?taxonomy=${business.id}`);
    await park(u.page);
    await shot(u.page, `${AREA}/user-readonly`);
    await u.browser.close();
  }

  // 분류체계 추가 대화상자
  await page.getByRole('button', { name: '분류체계 추가' }).click();
  let dlg = page.getByRole('dialog');
  await dlg.locator('input').nth(0).fill(TAX);
  await dlg.locator('input').nth(1).fill(TAX_DESC);
  await settle(page, 300);
  await shot(page, `${AREA}/create-dialog`);
  await dlg.getByRole('button', { name: '추가', exact: true }).click();
  await page.getByText('분류체계를 생성했습니다').waitFor();
  await toastGone(page);

  // 임시 분류체계에 분류를 만든다: 영업 > 고객 · 주문, 재무회계
  const tax = (await api(token, 'GET', '/catalog/taxonomies')).find(t => t.name === TAX);
  const sales = await api(token, 'POST', '/catalog/categories', { taxonomy_id: tax.id, name: '영업' });
  const cCustomer = await api(token, 'POST', '/catalog/categories', { taxonomy_id: tax.id, name: '고객', parent_id: sales.id });
  const cOrder = await api(token, 'POST', '/catalog/categories', { taxonomy_id: tax.id, name: '주문', parent_id: sales.id });
  await api(token, 'POST', '/catalog/categories', { taxonomy_id: tax.id, name: '재무회계' });
  const home = `/dashboard/taxonomies?taxonomy=${tax.id}`;

  // 분류체계 편집 대화상자 — 현재 이름과 설명이 채워져 있음
  await goto(page, home);
  await page.getByRole('button', { name: '분류체계 편집' }).click();
  await settle(page, 400);
  await shot(page, `${AREA}/edit-dialog`);
  await page.keyboard.press('Escape');

  // 분류 우클릭 메뉴 — 하위 분류 추가 · 이름 변경 · 삭제
  await node(page, '영업').click({ button: 'right' });
  await page.getByRole('menuitem', { name: '하위 분류 추가' }).waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/context-menu`);
  await page.keyboard.press('Escape');

  // 분류 추가 대화상자 (영업 아래에 하위 분류)
  await menu(page, '영업', '하위 분류 추가');
  await page.getByRole('dialog').locator('input').first().fill('계약');
  await settle(page, 300);
  await shot(page, `${AREA}/add-child-dialog`);
  await page.keyboard.press('Escape');

  // 분류 이름 변경 대화상자
  await menu(page, '재무회계', '이름 변경');
  await shot(page, `${AREA}/rename-dialog`);
  await page.keyboard.press('Escape');
  await settle(page, 300);

  // 끌어서 순서 바꾸기 — 주문 을 고객 위쪽 절반으로 끌면 놓을 자리에 파란 선
  {
    const from = await node(page, '주문').boundingBox();
    const to = await node(page, '고객').locator('xpath=..').boundingBox();
    await page.mouse.move(from.x + 10, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + 14, from.y + from.height / 2 - 4, { steps: 3 });
    await page.mouse.move(to.x + 80, to.y + 5, { steps: 6 });
    await page.mouse.move(to.x + 82, to.y + 6, { steps: 2 });
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/drag-reorder`);
    // 놓지 않고 끝낸다(순서는 그대로)
    await page.keyboard.press('Escape');
    await page.mouse.move(to.x + 600, to.y + 300, { steps: 3 });
    await page.mouse.up();
  }

  // 데이터셋 매핑 대화상자 — '트랜잭션' 으로 검색해 두 개 선택
  await goto(page, `${home}&category=${cOrder.id}`);
  await page.getByRole('button', { name: '데이터셋 매핑' }).click();
  dlg = page.getByRole('dialog');
  await dlg.getByPlaceholder('데이터셋 검색…').fill('트랜잭션');
  await settle(page, 1200);
  await dlg.getByRole('checkbox').first().click();
  await dlg.getByText('2개 선택됨').waitFor();
  await shot(page, `${AREA}/map-dialog`);
  await dlg.getByRole('button', { name: '선택 추가' }).click();
  await page.getByText('데이터셋 2개를 매핑했습니다').waitFor();
  await toastGone(page);

  // 고객 분류에 1건 매핑한 직후 — 상위 분류(영업)에 하위 수가 합산되고 미분류가 줄어듦
  await node(page, '고객').click();
  await settle(page);
  await page.getByRole('button', { name: '데이터셋 매핑' }).click();
  dlg = page.getByRole('dialog');
  await dlg.getByPlaceholder('데이터셋 검색…').fill('sakila.customer');
  await settle(page, 1200);
  await dlg.getByRole('row').filter({ hasText: '회원(고객) 마스터' }).getByRole('checkbox').click();
  await dlg.getByRole('button', { name: '선택 추가' }).click();
  await page.getByText('데이터셋 1개를 매핑했습니다').waitFor();
  await settle(page, 500);
  await park(page);
  await shot(page, `${AREA}/rollup`);
  await toastGone(page);

  // 매핑 제거 확인 대화상자 (주문 분류의 결제)
  await node(page, '주문').click();
  await settle(page);
  await page.locator('main main').getByRole('row').filter({ hasText: '결제' }).getByText('제거', { exact: true }).click();
  await page.getByText('분류에서 데이터셋을 제거할까요?').waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/remove-confirm`);
  await page.getByRole('button', { name: '취소' }).click();
  await settle(page, 300);

  // 매핑된 분류(고객) 삭제 확인 — 매핑도 함께 해제됨을 안내
  await menu(page, '고객', '삭제');
  await page.getByText('분류를 삭제할까요?').waitFor();
  await shot(page, `${AREA}/category-delete-confirm`);
  await page.getByRole('button', { name: '취소' }).click();
  await settle(page, 300);

  // 분류체계 삭제 확인 — 남은 분류 수 안내
  await page.getByRole('button', { name: '분류체계 삭제' }).click();
  await page.getByText('분류체계를 삭제할까요?').waitFor();
  await settle(page, 300);
  await shot(page, `${AREA}/taxonomy-delete-confirm`);
  await page.getByRole('button', { name: '취소' }).click();

  // 데이터셋 상세 개요 — 메타데이터 표 맨 위의 분류 체계 행(매핑마다 한 줄)
  await goto(page, `/dashboard/datasets/${rental}`);
  await page.getByText(`${TAX} > 영업 > 주문`).first().waitFor();
  await page.mouse.move(700, 30); // 아래쪽 상세 설명 위에 마우스가 올라가 강조되지 않게
  await settle(page, 300);
  await shot(page, `${AREA}/dataset-categories`);
} finally {
  await removeTemp();
  await browser.close();
}
