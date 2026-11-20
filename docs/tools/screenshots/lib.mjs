/**
 * 매뉴얼 화면 캡처 공용 도구.
 *
 * 화면별 스크립트(같은 폴더의 <영역>.mjs)가 이 파일을 불러 통합 테스트 스택의 화면을 찍어
 * docs/modules/ROOT/assets/images/<영역>/<이름>.png 로 저장한다.
 *
 * 환경 변수
 *   PLAYWRIGHT_DIR  playwright 가 설치된 폴더(node_modules 의 부모). 없으면 docs/node_modules 에서 찾는다.
 *   CHROMIUM_PATH   쓸 Chromium 실행 파일. 없으면 playwright 기본 브라우저.
 *   UI / API        화면 · API 주소. 기본은 통합 테스트 스택(127.0.0.1:46300 / 46600).
 *   ONLY            찍을 이름을 쉼표로 나열(예: ONLY=list,add-dialog). 없으면 전부.
 *   ARGUS_USER / ARGUS_PASSWORD  기본 로그인 계정(기본 admin / admin). 데모 환경처럼 admin 비밀번호가 다를 때.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(
  process.env.PLAYWRIGHT_DIR ? path.join(process.env.PLAYWRIGHT_DIR, 'x.js') : import.meta.url,
);
const { chromium } = require('playwright');

export const UI = process.env.UI || 'http://127.0.0.1:46300';
export const API = process.env.API || 'http://127.0.0.1:46600/api/v1';
export const IMAGES = path.resolve(here, '../../modules/ROOT/assets/images');
export const VIEWPORT = { width: 1440, height: 900 };

const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);

export async function launch() {
  return chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
}

/** 새 브라우저 컨텍스트(사용자 한 명의 세션). 화면 크기는 1440×900, 한국어 · 서울 시간대. */
export async function newContext(browser, { viewport = VIEWPORT } = {}) {
  return browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
}

/** 로그인 화면에서 계정으로 로그인하고 대시보드가 뜰 때까지 기다린다. */
const DEFAULT_USER = process.env.ARGUS_USER || 'admin';
const DEFAULT_PASSWORD = process.env.ARGUS_PASSWORD || 'admin';

export async function login(page, username = DEFAULT_USER, password = DEFAULT_PASSWORD) {
  await page.goto(`${UI}/login`);
  await page.fill('#username', username);
  await page.fill('#password', password);
  await page.click('button[type=submit]');
  await page.waitForURL(/\/dashboard/, { timeout: 30000 });
  await settle(page);
}

/** 브라우저를 띄우고 로그인한 페이지를 돌려준다. */
export async function open(username = DEFAULT_USER, password = DEFAULT_PASSWORD, opts = {}) {
  const browser = await launch();
  const context = await newContext(browser, opts);
  const page = await context.newPage();
  await login(page, username, password);
  return { browser, context, page };
}

/** 화면이 그려지기를 기다린다 — 네트워크가 잠잠해지고 스피너 · 애니메이션이 끝날 때까지. */
export async function settle(page, ms = 600) {
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

export async function goto(page, urlPath) {
  await page.goto(urlPath.startsWith('http') ? urlPath : `${UI}${urlPath}`);
  await settle(page);
}

/**
 * 캡처 저장. name 은 "<영역>/<이름>"(확장자 없이).
 *   target   locator 를 주면 그 요소만, 없으면 보이는 화면(1440×900).
 *   clip     { x, y, width, height } 로 화면 일부만.
 *   fullPage 스크롤 전체.
 */
export async function shot(page, name, { target, clip, fullPage = false } = {}) {
  const base = name.split('/').pop();
  if (only.length && !only.includes(base)) return;
  const file = path.join(IMAGES, `${name}.png`);
  await mkdir(path.dirname(file), { recursive: true });
  // 깜빡이는 입력 커서가 찍히지 않게
  // 개발 서버(next dev)로 찍을 때 Next.js 개발 표시("N" 배지 · 오류 오버레이)가 찍히지 않게
  await page.addStyleTag({
    content: '*{caret-color:transparent !important} nextjs-portal,[data-nextjs-toast],[data-next-badge-root]{display:none !important}',
  }).catch(() => {});
  if (target) await target.screenshot({ path: file, animations: 'disabled' });
  else await page.screenshot({ path: file, clip, fullPage, animations: 'disabled' });
  console.log(`saved ${name}.png`);
}

/** API 호출(상황을 만들 때 씀). 응답 JSON 을 돌려주고, 실패하면 예외. */
export async function apiToken(username = DEFAULT_USER, password = DEFAULT_PASSWORD, base = API) {
  const r = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) throw new Error(`login ${username}: ${r.status}`);
  return (await r.json()).access_token;
}

export async function api(token, method, urlPath, body, base = API) {
  const r = await fetch(`${base}${urlPath}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw Object.assign(new Error(`${method} ${urlPath}: ${r.status} ${text.slice(0, 300)}`), { status: r.status, data });
  return data;
}
