/**
 * 매뉴얼 화면 캡처 — 거버넌스 › API 카탈로그 (docs/modules/ROOT/pages/governance/api-catalog.adoc)
 *
 * 실행 (저장소 루트에서):
 *   PLAYWRIGHT_DIR=... CHROMIUM_PATH=... node docs/tools/screenshots/governance-api-catalog.mjs
 *   ONLY=list,try-it   일부만 다시 찍기
 *   CLEANUP=1          캡처용으로 만든 API 를 지우고 끝낸다(캡처는 하지 않음)
 *
 * 캡처용 데이터(없으면 만든다 — 다시 실행해도 안전):
 *   film-catalog-api   영화 카탈로그 API  — OpenAPI 3 스펙 v1.0.0 → v2.0.0(엔드포인트 제거 = Breaking), 자격증명 · 리니지 · 호출 기록
 *   store-inventory-api 매장 재고 API     — 수동 REST API(엔드포인트 편집)
 *   film-search-graphql 영화 검색 GraphQL — 수동 GraphQL API(콘솔 · 계약 문서)
 *   inventory-report-api 재고 현황 리포트 API — 수동 REST API, 웹훅 수신기의 XML 고정 응답
 *   customer-api 고객 API(Swagger 2) · staff-api 직원 API — 목록을 채우는 스펙 API
 * 호출 대상은 통합 테스트 스택의 웹훅 수신기(http://webhook-receiver:8080)다.
 */
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'governance-api-catalog';
const RECV = 'http://webhook-receiver:8080';
const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const want = (...names) => !only.length || names.some(n => only.includes(n));
const TMP = process.env.SHOT_TMP || path.join(os.tmpdir(), 'argus-shots-api-catalog');

// ───────────────────────── 캡처용 스펙 ─────────────────────────

const ok = { 200: { description: '성공' } };
function filmSpec(version) {
  const spec = {
    openapi: '3.0.3',
    info: { title: '영화 카탈로그 API', version, description: '대여 가능한 영화와 출연 배우 정보를 조회·관리하는 API' },
    servers: [{ url: RECV, description: '통합 테스트' }],
    tags: [{ name: 'films', description: '영화' }, { name: 'actors', description: '배우' }],
    components: { securitySchemes: { catalogKey: { type: 'apiKey', in: 'header', name: 'X-API-Key' } } },
    security: [{ catalogKey: [] }],
    paths: {
      '/health': { get: { operationId: 'health', summary: '상태 확인', responses: ok } },
      '/films': {
        get: {
          operationId: 'listFilms', summary: '영화 목록 조회', tags: ['films'],
          parameters: [
            { name: 'category', in: 'query', description: '장르(예: Action)', schema: { type: 'string' } },
            { name: 'page', in: 'query', description: '페이지 번호', schema: { type: 'integer' } },
          ],
          responses: ok,
        },
        post: {
          operationId: 'createFilm', summary: '영화 등록', tags: ['films'],
          requestBody: { content: { 'application/json': {
            schema: { type: 'object', required: ['title'], properties: {
              title: { type: 'string' }, release_year: { type: 'integer' }, rental_rate: { type: 'number' } } },
            example: { title: 'ACADEMY DINOSAUR', release_year: 2006, rental_rate: 0.99 },
          } } },
          responses: { 201: { description: '등록됨' } },
        },
      },
      '/films/{filmId}': {
        get: {
          operationId: 'getFilm', summary: '영화 단건 조회', tags: ['films'],
          parameters: [{ name: 'filmId', in: 'path', required: true, description: '영화 ID', schema: { type: 'string' } }],
          responses: ok,
        },
        delete: {
          operationId: 'deleteFilm', summary: '영화 삭제', tags: ['films'],
          parameters: [{ name: 'filmId', in: 'path', required: true, description: '영화 ID', schema: { type: 'string' } }],
          responses: { 204: { description: '삭제됨' } },
        },
      },
      '/actors': { get: { operationId: 'listActors', summary: '배우 목록 조회', tags: ['actors'], responses: ok } },
      '/actors/{actorId}/films': {
        get: {
          operationId: 'listActorFilms', summary: '배우 출연작 조회', tags: ['actors'],
          parameters: [{ name: 'actorId', in: 'path', required: true, description: '배우 ID', schema: { type: 'string' } }],
          responses: ok,
        },
      },
    },
  };
  if (version !== '1.0.0') {
    // v2: 영화 삭제 엔드포인트 제거(Breaking), 재고 조회 추가
    delete spec.paths['/films/{filmId}'].delete;
    spec.paths['/films/{filmId}/inventory'] = {
      get: {
        operationId: 'getFilmInventory', summary: '매장별 재고 조회', tags: ['films'],
        parameters: [{ name: 'filmId', in: 'path', required: true, description: '영화 ID', schema: { type: 'string' } }],
        responses: ok,
      },
    };
  }
  if (version === '2.1.0') {
    spec.paths['/films/{filmId}/reviews'] = {
      get: {
        operationId: 'listFilmReviews', summary: '영화 리뷰 조회', tags: ['films'],
        parameters: [{ name: 'filmId', in: 'path', required: true, description: '영화 ID', schema: { type: 'string' } }],
        responses: ok,
      },
    };
  }
  return spec;
}

// 파일로 올리는 YAML 스펙(등록 미리보기) — 등록하지 않는다
const RENTAL_STATS_YAML = `openapi: 3.0.3
info:
  title: 대여 통계 API
  version: 1.0.0
  description: 매장·기간별 대여 실적을 집계해 제공하는 API
servers:
  - url: ${RECV}
paths:
  /stats/daily:
    get:
      summary: 일별 대여 건수
      responses:
        "200":
          description: 성공
  /stats/stores/{storeId}:
    get:
      summary: 매장별 대여 실적
      parameters:
        - name: storeId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: 성공
`;

// 서버 주소가 상대 경로(/v1)인 스펙 — Base URL 입력란이 나타난다. 등록하지 않는다
const RELATIVE_SERVER_SPEC = JSON.stringify({
  openapi: '3.0.3',
  info: { title: '매장 API', version: '1.0.0', description: '매장과 영업 시간 정보를 제공하는 API' },
  servers: [{ url: '/v1' }],
  components: { securitySchemes: { storeKey: { type: 'apiKey', in: 'header', name: 'X-Store-Key' } } },
  security: [{ storeKey: [] }],
  paths: {
    '/stores': { get: { summary: '매장 목록', responses: ok } },
    '/stores/{storeId}': { get: { summary: '매장 단건 조회',
      parameters: [{ name: 'storeId', in: 'path', required: true, schema: { type: 'string' } }], responses: ok } },
  },
}, null, 2);

const ASYNCAPI_YAML = `asyncapi: 3.0.0
info:
  title: 대여 이벤트
  version: "1.0.0"
  description: 대여·반납이 일어날 때 발행하는 이벤트
channels:
  rentals:
    address: rentals.created
    messages:
      rentalCreated:
        payload:
          type: object
          properties:
            rentalId: { type: string }
            customerId: { type: string }
            amount: { type: number }
operations:
  onRentalCreated:
    action: receive
    channel:
      $ref: "#/channels/rentals"
`;

const GRAPHQL_SDL = `type Query {
  film(id: ID!): Film
  films(category: String, first: Int = 10): [Film!]!
}

type Mutation {
  rateFilm(id: ID!, score: Int!): Film
}

type Film {
  id: ID!
  title: String!
  releaseYear: Int
  rentalRate: Float
  category: String
  actors: [Actor!]!
}

type Actor {
  id: ID!
  name: String!
}
`;

const CUSTOMER_SWAGGER2 = {
  swagger: '2.0',
  info: { title: '고객 API', version: '1.2.0', description: '회원 가입 정보와 대여 이력을 조회하는 API' },
  host: 'webhook-receiver:8080', schemes: ['http'], basePath: '/',
  paths: {
    '/customers': { get: { summary: '고객 목록', responses: { 200: { description: '성공' } } } },
    '/customers/{customerId}': { get: { summary: '고객 단건 조회',
      parameters: [{ name: 'customerId', in: 'path', required: true, type: 'string' }], responses: { 200: { description: '성공' } } } },
    '/customers/{customerId}/rentals': { get: { summary: '고객 대여 이력',
      parameters: [{ name: 'customerId', in: 'path', required: true, type: 'string' }], responses: { 200: { description: '성공' } } } },
  },
};

const STAFF_SPEC = {
  openapi: '3.0.3',
  info: { title: '직원 API', version: '0.9.0', description: '매장 직원과 근무 매장 정보를 제공하는 API' },
  servers: [{ url: RECV }],
  paths: {
    '/staff': { get: { summary: '직원 목록', responses: ok } },
    '/staff/{staffId}': { get: { summary: '직원 단건 조회',
      parameters: [{ name: 'staffId', in: 'path', required: true, schema: { type: 'string' } }], responses: ok } },
  },
};

// 목록 맨 위에 보일 순서(최근 수정순) — 캡처용 API + 시드 API
const LIST_ORDER = [
  'film-catalog-api', 'rental-service', 'payment-service', 'store-inventory-api', 'customer-api',
  'film-search-graphql', 'recommendation-api', 'rental-events', 'inventory-report-api', 'staff-api',
];
const MINE = ['film-catalog-api', 'store-inventory-api', 'film-search-graphql', 'inventory-report-api', 'customer-api', 'staff-api'];

// ───────────────────────── 상황 만들기 ─────────────────────────

const email = u => `${u}@data-dynamics.io`;
async function exists(t, name) {
  try { return await api(t, 'GET', `/apis/${name}`); } catch (e) { if (e.status === 404) return null; throw e; }
}
function psql(sql) {
  return execFileSync('docker', ['exec', 'argus-catalog-postgres', 'psql', '-U', 'argus', '-d', 'argus_catalog', '-At', '-c', sql]).toString().trim();
}

async function ensureData(t) {
  // 영화 카탈로그 API — 스펙 v1 등록 → 게시 → v2 등록(Breaking 경고가 남는다)
  let film = await exists(t, 'film-catalog-api');
  if (!film) {
    await api(t, 'POST', '/apis', { name: 'film-catalog-api', spec_text: JSON.stringify(filmSpec('1.0.0')),
      owner_email: email('khshin'), category: 'core', tags: ['sakila', 'catalog'] });
    await api(t, 'PATCH', '/apis/film-catalog-api', { status: 'published', status_note: '운영 전환',
      certification: 'CERTIFIED', tier: 'GOLD', department: '물류운영팀' });
    await api(t, 'POST', '/apis/film-catalog-api/specs', { spec_text: JSON.stringify(filmSpec('2.0.0')) });
    await api(t, 'POST', '/apis/film-catalog-api/credentials',
      { scheme_name: 'catalogKey', label: '운영 키', type: 'apiKey', values: { key: 'film-catalog-prod-key' } });
    await api(t, 'POST', '/apis/film-catalog-api/credentials',
      { scheme_name: 'catalogKey', label: '개발 키', type: 'apiKey', values: { key: 'film-catalog-dev-key' } });
    for (const [relation, target_type, target_ref, target_label] of [
      ['consumes', 'dataset', 'sakila-mysql.sakila.film.dataset', '영화'],
      ['consumes', 'dataset', 'sakila-mysql.sakila.actor.dataset', '배우'],
      ['depends_on', 'api', 'store-inventory-api', '매장 재고 API'],
      ['provides', 'system', '대여 키오스크', '대여 키오스크'],
      ['provides', 'api', 'recommendation-api', '영화 추천 API'],
    ]) await api(t, 'POST', '/apis/film-catalog-api/lineage', { relation, target_type, target_ref, target_label });
  }

  // 매장 재고 API — 수동 REST
  if (!await exists(t, 'store-inventory-api')) {
    await api(t, 'POST', '/apis', { name: 'store-inventory-api', display_name: '매장 재고 API', protocol: 'REST', version: '1.1.0',
      description: '매장별 DVD 재고 수량을 조회하고 입고·이동을 기록하는 API', base_url: RECV,
      owner_email: email('sbmoon'), category: 'logistics', tags: ['sakila', 'inventory'] });
    await api(t, 'PATCH', '/apis/store-inventory-api', { status: 'published', tier: 'SILVER', department: '풀필먼트팀' });
    const eps = [
      { method: 'GET', path: '/stores/{storeId}/inventory', summary: '매장 재고 조회', tags: ['inventory'],
        parameters: [{ name: 'storeId', in: 'path', required: true, schema: { type: 'string' }, description: '매장 ID' }] },
      { method: 'POST', path: '/inventory/transfers', summary: '재고 이동 등록', tags: ['inventory'],
        request_body: { type: 'object', required: ['film_id', 'to_store_id'], properties: {
          film_id: { type: 'integer', description: '영화 ID' }, from_store_id: { type: 'integer', description: '보내는 매장' },
          to_store_id: { type: 'integer', description: '받는 매장' }, quantity: { type: 'integer', description: '수량' } } },
        responses: { 201: '등록됨', 409: '재고 부족' } },
      { method: 'GET', path: '/health', summary: '상태 확인' },
    ];
    for (const ep of eps) await api(t, 'POST', '/apis/store-inventory-api/endpoints', ep);
  }

  // 영화 검색 GraphQL — 수동 GraphQL + 계약 문서(SDL)
  if (!await exists(t, 'film-search-graphql')) {
    await api(t, 'POST', '/apis', { name: 'film-search-graphql', display_name: '영화 검색 GraphQL', protocol: 'GraphQL', version: '1.0.0',
      description: '제목·장르·배우로 영화를 찾는 GraphQL API', base_url: `${RECV}/graphql`,
      owner_email: email('mjkim'), category: 'search', tags: ['sakila', 'graphql'], contract_text: GRAPHQL_SDL });
    await api(t, 'POST', '/apis/film-search-graphql/endpoints', { method: 'QUERY', path: 'film', summary: '영화 단건 조회', tags: ['films'] });
    await api(t, 'POST', '/apis/film-search-graphql/endpoints', { method: 'QUERY', path: 'films', summary: '영화 검색', tags: ['films'] });
    await api(t, 'POST', '/apis/film-search-graphql/endpoints', { method: 'MUTATION', path: 'rateFilm', summary: '평점 등록', tags: ['films'] });
  }

  // 재고 현황 리포트 API — 웹훅 수신기의 XML 고정 응답(/content/xml)
  if (!await exists(t, 'inventory-report-api')) {
    await api(t, 'POST', '/apis', { name: 'inventory-report-api', display_name: '재고 현황 리포트 API', protocol: 'REST', version: '1.0.0',
      description: '정산 시스템이 내려받는 재고 현황 리포트(XML)', base_url: `${RECV}/content`,
      owner_email: email('ejcho'), category: 'logistics', tags: ['report'] });
    await api(t, 'POST', '/apis/inventory-report-api/endpoints', { method: 'GET', path: '/xml', summary: '재고 현황(XML)', tags: ['report'] });
    await api(t, 'POST', '/apis/inventory-report-api/endpoints', { method: 'GET', path: '/yaml', summary: '재고 현황(YAML)', tags: ['report'] });
  }

  if (!await exists(t, 'customer-api')) {
    await api(t, 'POST', '/apis', { name: 'customer-api', spec_text: JSON.stringify(CUSTOMER_SWAGGER2),
      owner_email: email('hyseo'), category: 'core', tags: ['sakila', 'customer'] });
    await api(t, 'PATCH', '/apis/customer-api', { certification: 'IN_REVIEW', tier: 'SILVER', department: 'SCM팀' });
  }
  if (!await exists(t, 'staff-api')) {
    await api(t, 'POST', '/apis', { name: 'staff-api', spec_text: JSON.stringify(STAFF_SPEC),
      owner_email: email('gwjang'), category: 'hr', tags: ['sakila'] });
  }

  // 관측 탭용 호출 기록 — 처음 한 번만 만들고, 최근 7일에 고르게 퍼지도록 시각을 옮긴다
  const usage = await api(t, 'GET', '/apis/film-catalog-api/usage?days=30');
  if ((usage.total_calls ?? usage.total ?? 0) < 20) {
    const callers = [t, await apiToken('khshin', 'khshin'), await apiToken('mjkim', 'mjkim')];
    const calls = [
      ['GET', '/films', '/films'], ['GET', '/films', '/films'], ['GET', '/films', '/films'],
      ['GET', '/films/{filmId}', '/films/1'], ['GET', '/films/{filmId}', '/films/42'],
      ['GET', '/actors', '/actors'], ['GET', '/actors/{actorId}/films', '/actors/7/films'],
      ['GET', '/films/{filmId}/inventory', '/films/1/inventory'], ['GET', '/health', '/health'],
      ['POST', '/films', '/films'],
    ];
    let n = 0;
    for (let round = 0; round < 5; round++) {
      for (const [method, epPath, real] of calls) {
        // 일부는 실패 경로(/fail → 500)로 보내 오류 수 · 상태 코드 분포가 보이게 한다
        const fail = n % 9 === 4;
        await api(callers[n % 7 === 3 ? 1 : n % 11 === 5 ? 2 : 0], 'POST', '/apis/invoke', {
          method, url: `${RECV}${fail ? '/fail' : ''}${real}`, api_name: 'film-catalog-api',
          endpoint_method: method, endpoint_path: epPath, ...(method === 'POST' ? { body: { title: 'ACADEMY DINOSAUR' } } : {}),
        }).catch(() => {});
        n++;
      }
    }
    try {
      psql(`UPDATE catalog_api_invocations i SET created_at = now() - r.rn * interval '3 hours 20 minutes',
            latency_ms = 18 + (i.id * 37) % 140 + CASE WHEN i.id % 13 = 0 THEN 400 ELSE 0 END
            FROM (SELECT id, row_number() OVER (ORDER BY id DESC) AS rn FROM catalog_api_invocations
                  WHERE api_id = (SELECT id FROM catalog_apis WHERE name = 'film-catalog-api')) r
            WHERE i.id = r.id`);
    } catch (e) { console.warn('호출 기록 시각을 옮기지 못했습니다(오늘 하루치로만 보입니다):', e.message); }
  }
}

/** 목록 맨 위에 LIST_ORDER 순서로 오도록 수정일을 갱신한다(값은 그대로 다시 저장). */
async function touchListOrder(t) {
  for (const name of [...LIST_ORDER].reverse()) {
    const a = await exists(t, name);
    if (!a) continue;
    // 같은 값을 다시 저장하면 수정일이 바뀌지 않아, 비고를 잠깐 바꿨다가 원래 값으로 되돌린다
    await api(t, 'PATCH', `/apis/${name}`, { note: `${a.note ?? ''} ` + '·' });
    await api(t, 'PATCH', `/apis/${name}`, { note: a.note ?? null });
  }
}

async function cleanup(t) {
  for (const name of MINE) {
    if (await exists(t, name)) { await api(t, 'DELETE', `/apis/${name}`); console.log(`deleted ${name}`); }
  }
}

// ───────────────────────── 화면 도우미 ─────────────────────────

const detail = (name, tab) => `/dashboard/apis?api=${name}${tab ? `&tab=${tab}` : ''}`;
const dialog = page => page.getByRole('dialog').last();
async function writeTmp(name, text) {
  await mkdir(TMP, { recursive: true });
  const f = path.join(TMP, name);
  await writeFile(f, text);
  return f;
}
/** 토스트가 사라질 때까지 기다린다(토스트가 주제가 아닌 그림에서). */
async function noToast(page) {
  await page.locator('[data-sonner-toast]').first().waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
}

// ───────────────────────── 캡처 ─────────────────────────

const t = await apiToken();
if (process.env.CLEANUP) { await cleanup(t); process.exit(0); }
await ensureData(t);

const { browser, page } = await open();
try {
  await run(page);
} finally {
  await browser.close();
}

async function run(page) {
  // ── 목록 ──
  if (want('list', 'list-card-view', 'list-row-menu', 'list-stat-filter')) await touchListOrder(t);

  // API 목록 — 통계 카드 · 필터 · 표 보기
  if (want('list')) {
    await goto(page, '/dashboard/apis?size=10');
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/list`);
  }

  // 통계 카드("미확인 Breaking 변경")를 눌러 필터를 건 모습 — 검색창 아래 조건 칩
  if (want('list-stat-filter')) {
    await goto(page, '/dashboard/apis');
    await page.getByTitle('미확인 Breaking 변경 기준으로 목록 보기').click();
    await settle(page);
    await page.mouse.move(700, 700);
    await shot(page, `${AREA}/list-stat-filter`);
  }

  // 카드 보기 — 부서 › 분류, 이름, 배지, 설명, 기본 주소, 엔드포인트 수 · 소유자 · 수정일
  if (want('list-card-view')) {
    await goto(page, '/dashboard/apis?size=10&view=card');
    await page.mouse.move(700, 30);
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/list-card-view`);
  }

  // 행 메뉴(⋮) — 새 탭에서 열기 / 링크 복사 / URN 복사
  if (want('list-row-menu')) {
    await goto(page, '/dashboard/apis?size=10');
    await page.getByRole('button', { name: '결제 서비스 API 메뉴' }).click();
    await page.getByRole('menu').waitFor();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/list-row-menu`);
    await page.keyboard.press('Escape');
  }

  // ── 등록 창 ──
  // YAML 파일을 불러와 등록할 때의 미리보기(등록하지는 않는다)
  if (want('register-spec')) {
    await withTall(async page => {
      await goto(page, '/dashboard/apis?size=10');
      await page.getByRole('button', { name: 'API 등록' }).click();
      await dialog(page).waitFor();
      await dialog(page).locator('input[type=file]').first().setInputFiles(await writeTmp('rental-stats-api.yaml', RENTAL_STATS_YAML));
      await dialog(page).getByText('등록될 식별자').waitFor();
      await dialog(page).locator('#api-add-name').fill('rental-stats-api');
      await dialog(page).getByText('사용할 수 있는 식별자입니다').waitFor();
      await settle(page);
      await shot(page, `${AREA}/register-spec`, { target: dialog(page) });
    });
  }

  // 서버 주소가 상대 경로(/v1)인 스펙 — 경고와 Base URL 입력란
  if (want('register-base-url')) {
    await goto(page, '/dashboard/apis?size=10');
    await page.getByRole('button', { name: 'API 등록' }).click();
    await dialog(page).waitFor();
    await setEditor(page, dialog(page), RELATIVE_SERVER_SPEC);
    await dialog(page).getByText('등록될 식별자').waitFor();
    await dialog(page).locator('#api-add-spec-base-url').fill('https://api.data-dynamics.io/store');
    await settle(page);
    await shot(page, `${AREA}/register-base-url`);
    await closeDialog(page);
  }

  // AsyncAPI 문서를 스펙 탭에 넣었을 때의 안내
  if (want('register-asyncapi-rejected')) {
    await withTall(async page => {
      await goto(page, '/dashboard/apis?size=10');
      await page.getByRole('button', { name: 'API 등록' }).click();
      await dialog(page).waitFor();
      await dialog(page).locator('input[type=file]').first().setInputFiles(await writeTmp('rental-events.asyncapi.yaml', ASYNCAPI_YAML));
      await dialog(page).getByText('AsyncAPI 스펙은').waitFor();
      await settle(page);
      await shot(page, `${AREA}/register-asyncapi-rejected`, { target: dialog(page) });
    });
  }

  // 수동 등록 탭 — 프로토콜 Webhook, 계약 문서로 AsyncAPI 원문
  if (want('register-manual')) {
    await withTall(async page => {
      await goto(page, '/dashboard/apis?size=10');
      await page.getByRole('button', { name: 'API 등록' }).click();
      await dialog(page).waitFor();
      await dialog(page).getByRole('tab', { name: '수동 등록' }).click();
      await dialog(page).locator('#api-add-name').fill('return-events');
      await dialog(page).locator('#api-add-display-name').fill('반납 이벤트 Webhook');
      await dialog(page).locator('#api-add-description').fill('DVD 가 반납될 때 구독 시스템에 알리는 Webhook');
      await pickSelect(page, dialog(page).locator('#api-add-protocol'), 'Webhook');
      await dialog(page).locator('#api-add-version').fill('1.0.0');
      await dialog(page).locator('#api-add-base-url').fill('https://hooks.data-dynamics.io/return');
      await setEditor(page, dialog(page), ASYNCAPI_YAML.replaceAll('대여', '반납').replaceAll('rental', 'return').replaceAll('Rental', 'Return'));
      await dialog(page).locator('#api-add-name').scrollIntoViewIfNeeded();
      await settle(page);
      await shot(page, `${AREA}/register-manual`, { target: dialog(page) });
    });
  }

  // ── 상세 머리글 ──
  // 상태를 "사용 중단"으로 바꿀 때의 사유 입력 창(바꾸지는 않는다)
  if (want('status-dialog')) {
    await goto(page, detail('store-inventory-api'));
    await page.getByTitle('상태 변경').click();
    await page.getByRole('option', { name: '사용 중단' }).click();
    await dialog(page).waitFor();
    await dialog(page).locator('#api-status-note').fill('매장 재고 API v2(store-inventory-v2)로 대체 — 2026-12-31 까지 이전');
    await settle(page);
    await shot(page, `${AREA}/status-dialog`, { target: dialog(page) });
    await dialog(page).getByRole('button', { name: '취소' }).click();
  }

  // 새 스펙 버전의 Breaking 변경 경고 배너(자세히 · 확인)
  if (want('breaking-banner')) {
    await goto(page, detail('film-catalog-api'));
    await shot(page, `${AREA}/breaking-banner`);
  }

  // 개요 탭 편집 화면(저장하지 않는다)
  if (want('overview-edit')) {
    await goto(page, detail('customer-api'));
    await page.getByRole('button', { name: '편집' }).click();
    await settle(page);
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/overview-edit`);
    await page.getByRole('button', { name: '취소' }).click();
  }

  // ── 엔드포인트 탭 ──
  // 태그별 그룹(films · actors · 기타)
  if (want('endpoints')) {
    await goto(page, detail('film-catalog-api', 'endpoints'));
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/endpoints`);
  }

  // Try-it 호출 결과 — 자격증명을 고르고 GET /films 호출
  if (want('try-it')) {
    await goto(page, detail('film-catalog-api', 'endpoints'));
    await pickSelect(page, page.getByRole('combobox').filter({ hasText: '사용 안 함' }), /운영 키/);
    const row = await openEndpoint(page, '영화 목록 조회');
    await page.getByPlaceholder('category').fill('Action');
    await page.getByPlaceholder('page', { exact: true }).fill('1');
    await page.getByRole('button', { name: '호출', exact: true }).click();
    await page.getByText('응답 본문').waitFor();
    await settle(page);
    await noToast(page);
    await scrollTop(row);
    await shot(page, `${AREA}/try-it`);
  }

  // 필수 경로 파라미터를 비운 채 호출 — 빨간 표시와 안내
  if (want('try-it-required')) {
    await goto(page, detail('film-catalog-api', 'endpoints'));
    const row = await openEndpoint(page, '영화 단건 조회');
    await scrollTop(row);
    await page.getByRole('button', { name: '호출', exact: true }).click();
    await page.waitForTimeout(500);
    await shot(page, `${AREA}/try-it-required`);
  }

  // XML 응답 표시 — 응답 영역만
  if (want('response-xml')) {
    await goto(page, detail('inventory-report-api', 'endpoints'));
    await openEndpoint(page, '재고 현황(XML)');
    await page.getByRole('button', { name: '호출', exact: true }).click();
    await page.getByText('응답 본문').waitFor();
    await settle(page);
    await noToast(page);
    await shot(page, `${AREA}/response-xml`, { target: page.locator('div.rounded-md.border.p-3').filter({ hasText: '응답 본문' }).last() });
  }

  // GraphQL 콘솔 — 쿼리와 변수
  if (want('graphql')) {
    await goto(page, detail('film-search-graphql', 'endpoints'));
    const row = await openEndpoint(page, '영화 단건 조회');
    await setEditor(page, page, 'query FilmById($id: ID!) {\n  film(id: $id) {\n    title\n    releaseYear\n    category\n    actors {\n      name\n    }\n  }\n}\n');
    await page.getByRole('button', { name: '쿼리 변수로 채우기' }).click();
    await setEditor(page, page, '{\n  "id": "1"\n}', 1);
    await noToast(page);
    await scrollTop(row);
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/graphql`);
  }

  // 수동 API 의 엔드포인트 편집 창 — 요청 본문 필드
  if (want('endpoint-edit')) {
    await goto(page, detail('store-inventory-api', 'endpoints'));
    await page.getByRole('button', { name: '수정', exact: true }).nth(1).click();   // 둘째 행: POST /inventory/transfers
    await dialog(page).waitFor();
    await settle(page);
    await shot(page, `${AREA}/endpoint-edit`);
    await closeDialog(page);
  }

  // ── 인증 · 리니지 · 관측 · 린팅 탭 ──
  // 인증 탭 — 저장된 자격증명
  if (want('auth')) {
    await goto(page, detail('film-catalog-api', 'auth'));
    await shot(page, `${AREA}/auth`);
  }

  // 리니지 그래프 보기
  if (want('lineage-graph')) {
    await goto(page, detail('film-catalog-api', 'lineage'));
    await page.getByRole('button', { name: '그래프', exact: true }).click();
    await settle(page, 1500);
    await scrollTop(page.getByRole('tablist').first(), 80);
    await page.mouse.move(700, 30);
    await shot(page, `${AREA}/lineage-graph`);
  }

  // 관측 탭 — 지표 · 일별 호출 · 상태 코드 · 엔드포인트 · 호출자
  if (want('usage')) {
    await goto(page, detail('film-catalog-api', 'usage'));
    await settle(page, 1000);
    await shot(page, `${AREA}/usage`);
  }

  // 린팅 결과 — 점수와 문제 목록
  if (want('lint')) {
    await goto(page, detail('film-catalog-api', 'lint'));
    await settle(page, 800);
    await shot(page, `${AREA}/lint`);
  }

  // ── 원본 · 이력 탭 ──
  // 새 버전 등록 미리보기 — 현재 버전 → 새 버전, 엔드포인트 수 변화(등록하지 않는다)
  if (want('spec-new-version')) {
    const tall = await open('admin', 'admin', { viewport: { width: 1440, height: 1000 } });
    try {
      await goto(tall.page, detail('film-catalog-api', 'spec'));
      await tall.page.getByRole('button', { name: '새 버전 등록' }).click();
      await dialog(tall.page).waitFor();
      await dialog(tall.page).locator('input[type=file]').first()
        .setInputFiles(await writeTmp('film-catalog-api-v2.1.json', JSON.stringify(filmSpec('2.1.0'), null, 2)));
      await dialog(tall.page).getByText('엔드포인트:').waitFor();
      await settle(tall.page);
      await shot(tall.page, `${AREA}/spec-new-version`, { target: dialog(tall.page) });
    } finally { await tall.browser.close(); }
  }

  // 수동 API 의 계약 문서(GraphQL SDL) 보기
  if (want('contract')) {
    await goto(page, detail('film-search-graphql', 'spec'));
    await settle(page, 1000);
    await shot(page, `${AREA}/contract`);
  }

  // 이력 탭 — 변경 이력과 버전 비교 결과
  if (want('history-diff')) {
    await goto(page, detail('film-catalog-api', 'history'));
    await settle(page, 800);
    await shot(page, `${AREA}/history-diff`);
  }

  // ── 즐겨찾기 ──
  if (want('favorites', 'favorites-undo')) {
    // 통합 테스트가 남긴 즐겨찾기(it-pet-api)는 찍는 동안만 빼 두었다가 되돌린다
    const before = await api(t, 'GET', '/apis/favorites');
    const hidden = before.filter(f => f.api_name.startsWith('it-'));
    for (const f of hidden) await api(t, 'DELETE', `/apis/${f.api_name}/favorites?method=${f.method}&path=${encodeURIComponent(f.path)}`);
    const mine = [
      ['film-catalog-api', 'GET', '/films'], ['film-catalog-api', 'GET', '/films/{filmId}'], ['film-catalog-api', 'POST', '/films'],
      ['store-inventory-api', 'GET', '/stores/{storeId}/inventory'],
    ];
    for (const [name, method, p] of mine) await api(t, 'POST', `/apis/${name}/favorites`, { method, path: p }).catch(() => {});
    try {
      // 즐겨찾기 화면 — API 별 그룹, 서버 · 자격증명 선택
      await goto(page, '/dashboard/apis/favorites');
      await page.mouse.move(700, 30);
      await shot(page, `${AREA}/favorites`);

      // ★ 를 다시 눌러 해제한 직후 — "되돌리기"
      if (want('favorites-undo')) {
        await page.getByRole('button', { name: '즐겨찾기 해제' }).nth(1).click();   // 둘째 그룹: 매장 재고 API
        await page.getByRole('button', { name: '되돌리기' }).waitFor();
        await page.waitForTimeout(400);
        await shot(page, `${AREA}/favorites-undo`);
        await page.getByRole('button', { name: '되돌리기' }).click();
        await settle(page);
      }
    } finally {
      for (const f of hidden) await api(t, 'POST', `/apis/${f.api_name}/favorites`, { method: f.method, path: f.path }).catch(() => {});
    }
  }

  // ── 일반 사용자 화면 ──
  // 일반 사용자(mjkim)가 남의 API 를 볼 때 — 편집 · 삭제 · 상태 변경 버튼이 없다
  if (want('user-readonly')) {
    const u = await open('mjkim', 'mjkim');
    try {
      await goto(u.page, detail('film-catalog-api'));
      await shot(u.page, `${AREA}/user-readonly`);
    } finally { await u.browser.close(); }
  }
}

/** 창만 잘라 찍는 그림용 — 세로가 긴(1440×1000) 화면에서 실행하고 브라우저를 닫는다. */
async function withTall(fn) {
  const tall = await open('admin', 'admin', { viewport: { width: 1440, height: 1000 } });
  try { await fn(tall.page); } finally { await tall.browser.close(); }
}

/** 저장하지 않고 창을 닫는다("저장하지 않은 변경" 확인이 뜨면 수락). */
async function closeDialog(page) {
  page.once('dialog', d => d.accept().catch(() => {}));
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  const confirm = page.getByRole('alertdialog');
  if (await confirm.count()) await confirm.getByRole('button').last().click().catch(() => {});
  await page.waitForTimeout(300);
}

/** 엔드포인트 행(요약 글로 찾음)을 펼치고 "호출" 탭을 연다. 행 요소를 돌려준다. */
async function openEndpoint(page, summary) {
  const row = page.getByText(summary, { exact: false }).first();
  await row.click();
  await page.getByRole('tab', { name: '호출', exact: true }).click();
  await settle(page);
  return row;
}

/** 요소가 화면 위쪽(머리글 바로 아래)에 오도록 스크롤한다. */
async function scrollTop(locator, offset = 90) {
  await locator.evaluate((el, off) => {
    let p = el.parentElement;
    while (p && !(p.scrollHeight > p.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement;
    const sc = p || document.scrollingElement;
    sc.scrollTop += el.getBoundingClientRect().top - (p ? p.getBoundingClientRect().top : 0) - off;
  }, offset);
  await locator.page().waitForTimeout(300);
}

/** scope 안의 n 번째 코드 편집기(Monaco)에 글을 넣는다. */
async function setEditor(page, scope, text, n = 0) {
  const el = scope.locator('.monaco-editor').nth(n);
  await el.waitFor();
  const handle = await el.elementHandle();
  await page.evaluate(([node, value]) => {
    const ed = window.monaco.editor.getEditors().find(e => node.contains(e.getDomNode()) || e.getDomNode().contains(node));
    ed.setValue(value);
  }, [handle, text]);
  await page.waitForTimeout(300);
}

/** Select(콤보박스)에서 항목을 고른다. */
async function pickSelect(page, trigger, optionName) {
  await trigger.click();
  await page.getByRole('option', { name: optionName, exact: true }).click();
  await page.waitForTimeout(200);
}
