/**
 * MCP Server 카탈로그(governance/mcp-server-catalog.adoc, mcp-server-catalog-advanced.adoc) 화면 캡처.
 *
 *   node docs/tools/screenshots/governance-mcp-server-catalog.mjs
 *   ONLY=server-list,findings node ...   일부만
 *   KEEP=1 node ...                      끝난 뒤 만든 데이터를 지우지 않음(반복 작업용)
 *   CLEAN=1 node ...                     캡처 없이 만든 데이터만 지움
 *
 * 시드 MCP 서버 정의(kafka-mcp 등 Forge 10종)와 시드 AI Agent 는 지우거나 이름을 바꾸지 않는다.
 * kafka-mcp 의 PROD 배포(DEPLOY), 수동 등록 서버(GITHUB), 에이전트(AGENTS)를 새로 만들어 찍고 끝나면 지운다.
 * 캡처 순서가 상태를 바꾼다: 관측 모드 그림을 먼저 찍고, 강제(enforce) 모드로 바꾼 뒤 나머지를 찍는다.
 */
import { execFileSync } from 'node:child_process';
import { open, goto, settle, shot, apiToken, api, API, VIEWPORT } from './lib.mjs';

const AREA = 'governance-mcp-server-catalog';
const DEPLOY = 'kafka-mcp-prod';
const GITHUB = 'github-mcp';
const BOT = 'ops.kafka-operator';        // 배포를 쓰는 에이전트(허용 그룹 read·peek + 에이전트 범위)
const AUDITOR = 'ops.topic-auditor';     // 배포를 쓰는 에이전트(전체 허용)
const DIRECT = 'etl.pipeline-helper';    // 주소만 직접 입력한(미등록) 연결을 가진 에이전트
const AGENTS = [BOT, AUDITOR, DIRECT];

const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const want = (...names) => !only.length || names.some(n => only.includes(n));

// 통합 테스트(22-mcp-servers.md)와 같은 도구 구성 — 시드 kafka-mcp 정의가 바뀌지 않게 같은 내용을 쓴다
const MANIFEST = {
  server: { name: 'kafka-mcp', version: '2.1.0', instance_id: 'kafka-mcp-prod-1' },
  target: { platform: 'kafka', endpoint: 'kafka-1.corp.example:9093', principal: 'svc-mcp', auth: 'SASL_SSL' },
  groups: [{ key: 'peek', enabled: true }, { key: 'topics', enabled: true },
    { key: 'offsets', enabled: false }, { key: 'destructive', enabled: false }],
  tools: [
    { name: 'list_topics', group: 'read', mutating: false, exposed: true, description: '토픽 목록' },
    { name: 'peek_messages', group: 'peek', mutating: false, exposed: true, description: '메시지 조회' },
    { name: 'create_topic', group: 'topics', mutating: true, exposed: true, description: '토픽 생성', resource_types: ['topic'] },
    { name: 'reset_offsets', group: 'offsets', mutating: true, exposed: false },
    { name: 'delete_topic', group: 'destructive', mutating: true, exposed: false }],
  resource_scopes: [
    { config_key: 'kafka.policy.mutable_topics', resource_type: 'topic', action: 'write', effect: 'allow', patterns: ['app-*'] },
    { config_key: 'kafka.policy.protected_topics', resource_type: 'topic', action: 'write', effect: 'protect', patterns: ['__*'] }],
  limits: { 'kafka.peek.max_messages': 500 },
  security_warnings: ['TLS certificate verification is disabled'],
};
const MANIFEST_TEXT = JSON.stringify(MANIFEST, null, 1);
const MCP_JSON = JSON.stringify({ mcpServers: { 'fs-remote': { url: 'http://fs-mcp.corp.example:9000/mcp', headers: { Authorization: 'Bearer secret' } } } }, null, 2);

const sql = q => execFileSync('docker', ['exec', 'argus-catalog-postgres', 'psql', '-U', 'argus', '-d', 'argus_catalog', '-Atc', q]).toString().trim();
const quiet = p => p.catch(() => null);
const now = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');
let ingestToken = null;

async function ingest(body) {
  const r = await fetch(`${API}/mcp-deployments/${DEPLOY}/audit-events`, {
    method: 'POST', headers: { 'X-Argus-Ingest-Token': ingestToken, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`audit ingest ${r.status} ${await r.text()}`);
}

/** 이 스크립트가 만든 데이터를 지운다. 폐기(retire)는 이름을 남기므로 배포 · 서버 정의는 DB 에서 이 스크립트가 만든 행만 지운다. */
async function cleanup(t) {
  for (const n of AGENTS) await quiet(api(t, 'DELETE', `/ai-agents/${n}`));
  const names = `'${DEPLOY}'`;
  sql(`DELETE FROM catalog_mcp_status_history WHERE entity_type='deployment' AND entity_id IN (SELECT id FROM catalog_mcp_deployments WHERE name IN (${names}))`);
  sql(`DELETE FROM catalog_mcp_status_history WHERE entity_type='server' AND entity_id IN (SELECT id FROM catalog_mcp_servers WHERE name='${GITHUB}')`);
  sql(`DELETE FROM argus_lineage_alert WHERE entity_type='mcp_deployment' AND entity_ref IN (${names})`);
  sql(`DELETE FROM catalog_mcp_deployments WHERE name IN (${names})`);
  sql(`DELETE FROM catalog_mcp_servers WHERE name='${GITHUB}'`);
}

/** 관측 모드 상태를 만든다: 배포 + 관측값 + 승인 정책(차이 있음) + finding + 서비스 토큰 + 감사 이벤트 + 사용 에이전트. */
async function setup(t) {
  await cleanup(t);
  const D = `/mcp-deployments/${DEPLOY}`;

  // 수동 등록 서버 정의(도구 · 리소스 · 프롬프트) — 리소스·프롬프트 탭용
  await api(t, 'POST', '/mcp-servers', { name: GITHUB, display_name: 'GitHub MCP', description: '저장소 이슈 · PR 을 다루는 사내 GitHub MCP 서버',
    provider_type: 'community', category: 'devtools', target_platform: 'github' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/tools`, { name: 'create_issue', description: '이슈 생성', annotations: { readOnlyHint: false, destructiveHint: false }, risk: 'medium' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/tools`, { name: 'list_pull_requests', description: 'PR 목록', annotations: { readOnlyHint: true }, risk: 'low' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/resources`, { uri: 'repo://platform/readme', name: 'readme', mime_type: 'text/markdown' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/resources`, { uri: 'repo://platform/issues/{number}', name: 'issue' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/prompts`, { name: 'triage', description: '새 이슈를 분류하고 담당 팀을 제안' });
  await api(t, 'POST', `/mcp-servers/${GITHUB}/prompts`, { name: 'release-notes', description: '병합된 PR 로 릴리스 노트 초안 작성' });

  // 배포 + 관측값(manifest) + 자격증명 + 헬스 체크 3회(연속 실패 finding)
  await api(t, 'POST', '/mcp-deployments', { name: DEPLOY, server_name: 'kafka-mcp', environment: 'PROD', status: 'active',
    description: '운영 Kafka 클러스터용 MCP 서버', owner_email: 'mjkim@data-dynamics.io', endpoint: 'http://kafka-mcp.corp.example:8794/mcp' });
  await api(t, 'POST', `${D}/manifest-import`, { content: MANIFEST });
  await api(t, 'PUT', `${D}/credential`, { kind: 'bearer', secret: 'kafka-mcp-collect-token' });
  for (let i = 0; i < 3; i++) await quiet(api(t, 'POST', `${D}/health-check`));

  // 승인 정책 — 관측값과 일부러 다르게(토픽 관리 꺼짐, 범위 · 제한값 · 버전 차이) 저장한 뒤 그룹만 관측값으로 채택
  await api(t, 'PUT', `${D}/policy`, {
    groups: { topics: false }, tools: { delete_topic: { blocked: true } },
    scopes: [{ resource_type: 'topic', action: 'write', effect: 'allow', patterns: ['app-orders'] },
      { resource_type: 'topic', action: 'write', effect: 'protect', patterns: ['__*', '*-changelog'] }],
    limits: { 'kafka.peek.max_messages': 100 }, approved_version: '2.0.0', note: '최초 승인' });
  await api(t, 'POST', `${D}/policy/adopt-observed`, { groups: true, scopes: false, limits: false, version: false });

  // 서비스 토큰 + 감사 이벤트(허용 1 · 범위 밖 1 · 차단 도구 1)
  ingestToken = (await api(t, 'POST', `${D}/ingest-token`)).token;
  await ingest({ events: [
    { ts: now(), session: 's1', tool: 'create_topic', user: 'agent-a', args: { topic: 'app-orders', partitions: 3 }, ok: true },
    { ts: now(), session: 's1', tool: 'create_topic', user: 'agent-a', args: { topic: 'payments', sasl_password: 'x' }, ok: false, error: 'tool failed' },
    { ts: now(), session: 's1', tool: 'delete_topic', user: 'agent-b', args: { topic: 'app-orders' }, ok: true }] });

  // 이 배포를 쓰는 에이전트 2개 + 미등록 연결을 가진 에이전트 1개
  const base = { owner_email: 'mjkim@data-dynamics.io', department: '데이터플랫폼팀', category: 'operations', status: 'active' };
  await api(t, 'POST', '/ai-agents', { ...base, name: BOT, display_name: 'Kafka 운영 봇', description: '토픽 상태를 점검하고 메시지를 조회하는 운영 에이전트' });
  await api(t, 'POST', '/ai-agents', { ...base, name: AUDITOR, display_name: '토픽 점검 봇', description: '토픽 설정을 주기적으로 점검하는 에이전트', status: 'draft' });
  await api(t, 'POST', '/ai-agents', { ...base, name: DIRECT, display_name: '파이프라인 도우미', description: '적재 파이프라인 장애를 진단하는 에이전트', category: 'data-engineering' });
  await api(t, 'POST', `/ai-agents/${BOT}/mcp-servers`, { mcp_deployment_name: DEPLOY, allowed_groups: ['read', 'peek'] });
  await api(t, 'POST', `/ai-agents/${AUDITOR}/mcp-servers`, { mcp_deployment_name: DEPLOY });
  await api(t, 'POST', `/ai-agents/${DIRECT}/mcp-servers`, { name: 'kafka-direct', url: 'http://kafka-mcp.corp.example:8794/mcp' });
  const link = await botLink(t);
  await api(t, 'PUT', `${D}/agents/${link}/scopes`, { scopes: [
    { resource_type: 'topic', action: 'read', effect: 'deny', patterns: ['secret-*'] },
    { resource_type: 'topic', action: 'read', effect: 'deny', patterns: ['audit-*'] }] });
}

async function botLink(t) {
  const agents = await api(t, 'GET', `/mcp-deployments/${DEPLOY}/agents`);
  return agents.find(a => a.agent_name === BOT).link_id;
}

/** 강제(enforce) 모드로 바꾸고 서버가 번들을 받아 간 것처럼 pull 한 뒤, 서버 거부 감사 이벤트를 보낸다. */
let enforced = false;
async function enforce(t) {
  if (enforced) return;
  enforced = true;
  const D = `/mcp-deployments/${DEPLOY}`;
  if (!ingestToken) ingestToken = (await api(t, 'POST', `${D}/ingest-token`)).token;
  await api(t, 'PUT', `${D}/policy`, { policy_mode: 'enforce', note: '운영 반영 — 강제 모드' });
  const r = await fetch(`${API}${D}/policy-bundle`, { headers: { Authorization: `Bearer ${ingestToken}`, 'X-Argus-Instance-Id': 'kafka-mcp-prod-1' } });
  if (!r.ok) throw new Error(`policy-bundle pull ${r.status}`);
  await ingest({ events: [{ ts: now(), session: 's9', tool: 'delete_topic', user: 'u1', agent: BOT, args: { topic: 'app-returns' }, ok: false,
    error: 'blocked by catalog policy', decision: 'denied', reason: "tool 'delete_topic' is blocked" }] });
}

const depUrl = `/dashboard/mcp-servers?deployment=${DEPLOY}`;
const srvUrl = name => `/dashboard/mcp-servers?server=${name}`;
async function tab(page, name) {
  await page.getByRole('tab', { name }).click();
  await settle(page);
}
async function depTab(page, name) {
  await goto(page, depUrl);
  await tab(page, name);
}
/** 세로로 긴 화면 전체를 찍는다 — 보이는 화면을 내용 높이만큼 늘려, 아래에 붙는 저장 줄 같은 고정 요소가 가운데에 겹치지 않게 한다. */
async function tallShot(page, name) {
  const height = async () => page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
  // 늘리면 늦게 그려지는 부분(코드 편집기 등)이 더 길어질 수 있어 높이가 멈출 때까지 되풀이한다
  for (let i = 0, prev = 0; i < 6; i++) {
    const h = Math.max(await height(), VIEWPORT.height);
    if (h === prev) break;
    prev = h;
    await page.setViewportSize({ width: VIEWPORT.width, height: h });
    await settle(page, 700);
  }
  await shot(page, name);
  await page.setViewportSize(VIEWPORT);
}
const dialog = page => page.locator('[role=dialog], [role=alertdialog]').last();
async function closeDialog(page) {
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
}
const clearToasts = page => page.locator('[data-sonner-toast]').evaluateAll(els => els.forEach(e => e.remove())).catch(() => {});
/** 코드 편집기(붙여 넣기 칸)에 글을 붙여 넣는다 — 타이핑하면 편집기가 괄호 · 들여쓰기를 덧붙이므로 클립보드로 넣는다. */
async function pasteInEditor(page, label, text) {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.evaluate(v => navigator.clipboard.writeText(v), text);
  await page.getByLabel(label).first().focus();
  await page.keyboard.press('Control+V');
  await page.waitForTimeout(500);
}
/** 대화상자의 파일 선택으로 내용을 넣는다. */
async function chooseFile(d, name, text) {
  await d.locator('input[type=file]').setInputFiles({ name, mimeType: 'application/json', buffer: Buffer.from(text) });
  await d.page().waitForTimeout(500);
}

const t = await apiToken();
if (process.env.CLEAN) { await cleanup(t); console.log('cleaned'); process.exit(0); }
await setup(t);
console.log('setup done');

const { browser, page } = await open();
try {
  /* ───────── 목록 ───────── */
  if (want('server-list')) {
    // MCP Server 목록(서버 정의 탭)과 지표 카드
    await goto(page, '/dashboard/mcp-servers');
    await shot(page, `${AREA}/server-list`);
  }
  if (want('import-menu')) {
    // 가져오기 메뉴(관리자) 펼침
    await goto(page, '/dashboard/mcp-servers');
    await page.getByRole('button', { name: '가져오기' }).click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/import-menu`);
    await page.keyboard.press('Escape');
  }
  if (want('register-dialog')) {
    // MCP 서버 정의 등록 대화상자
    await goto(page, '/dashboard/mcp-servers');
    await page.getByRole('button', { name: '서버 등록' }).click();
    const d = dialog(page);
    await d.getByPlaceholder('예: github-mcp, hive-mcp').fill('slack-mcp');
    await d.getByRole('textbox').nth(1).fill('Slack MCP');
    await d.getByPlaceholder('예: hive, kafka, github, slack').fill('slack');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/register-dialog`, { target: d });
    await closeDialog(page);
  }
  if (want('mcp-json-import')) {
    // server.json · mcp.json 가져오기 — mcp.json 을 붙여 넣고 미리보기
    await goto(page, '/dashboard/mcp-servers');
    await page.getByRole('button', { name: '가져오기' }).click();
    await page.getByRole('menuitem', { name: /server\.json/ }).click();
    const d = dialog(page);
    await d.getByRole('tab', { name: '클라이언트 mcp.json' }).click();
    await pasteInEditor(page, 'mcp.json 내용', MCP_JSON);
    await d.getByRole('button', { name: '미리보기' }).click();
    await settle(page);
    await shot(page, `${AREA}/mcp-json-import`, { target: d });
    await closeDialog(page);
  }
  if (want('control-overview')) {
    // 통제 현황 탭(전체)
    await goto(page, '/dashboard/mcp-servers');
    await tab(page, /통제 현황/);
    await settle(page, 800);
    await tallShot(page, `${AREA}/control-overview`);
  }
  if (want('unregistered')) {
    // 미등록 연결 탭
    await goto(page, '/dashboard/mcp-servers');
    await tab(page, /미등록 연결/);
    await shot(page, `${AREA}/unregistered`);
  }

  /* ───────── 서버 정의 상세 ───────── */
  if (want('server-tools')) {
    // 서버 정의 — 도구 탭(도구 그룹과 도구 목록)
    await goto(page, srvUrl('kafka-mcp'));
    await tab(page, /^도구/);
    await tallShot(page, `${AREA}/server-tools`);
  }
  if (want('resources-prompts')) {
    // 리소스·프롬프트 탭
    await goto(page, srvUrl(GITHUB));
    await tab(page, '리소스·프롬프트');
    await shot(page, `${AREA}/resources-prompts`);
  }
  if (want('server-versions')) {
    // 버전 이력 탭
    await goto(page, srvUrl('kafka-mcp'));
    await tab(page, '버전 이력');
    await shot(page, `${AREA}/server-versions`);
  }
  if (want('deploy-dialog')) {
    // MCP 배포 등록 대화상자
    await goto(page, srvUrl('kafka-mcp'));
    await page.getByRole('button', { name: '배포 등록' }).click();
    const d = dialog(page);
    await d.getByPlaceholder('예: hive-mcp-prod-a').fill('kafka-mcp-dev');
    await d.getByPlaceholder('https://host:port/mcp').fill('http://kafka-mcp-dev.corp.example:8794/mcp');
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/deploy-dialog`, { target: d });
    await closeDialog(page);
  }

  /* ───────── 배포 상세 (관측 모드) ───────── */
  if (want('manifest-preview')) {
    // ⋯ 메뉴 → 매니페스트 가져오기 — 붙여 넣고 미리보기
    await goto(page, depUrl);
    await page.getByRole('button', { name: '더 보기' }).click();
    await page.getByRole('menuitem', { name: /매니페스트 가져오기/ }).click();
    const d = dialog(page);
    await chooseFile(d, 'kafka-manifest.json', MANIFEST_TEXT);
    await d.getByRole('button', { name: '미리보기' }).click();
    await settle(page);
    await shot(page, `${AREA}/manifest-preview`, { target: d });
    await closeDialog(page);
  }
  if (want('deployment-overview')) {
    // 배포 상세 — 개요 탭(위쪽 보안 경고 상자 포함)
    await depTab(page, '개요');
    await tallShot(page, `${AREA}/deployment-overview`);
  }
  if (want('credential')) {
    // 자격증명 탭 — 저장한 비밀값은 마스킹
    await depTab(page, '자격증명');
    await shot(page, `${AREA}/credential`);
  }
  if (want('effective')) {
    // 실효 상태 탭
    await depTab(page, '실효 상태');
    await tallShot(page, `${AREA}/effective`);
  }
  if (want('scopes')) {
    // 리소스 범위 탭과 접근 시뮬레이터 — 보호 토픽 쓰기는 거부
    await depTab(page, /리소스 범위/);
    const sim = page.getByPlaceholder('예: prod_sales').locator('xpath=ancestor::div[contains(@class,"items-end")][1]');
    await sim.locator('input').nth(0).fill('topic');
    await sim.getByPlaceholder('예: prod_sales').fill('__consumer_offsets');
    await sim.locator('button[role=combobox]').nth(0).click();
    await page.getByRole('option', { name: 'write' }).click();
    await sim.getByRole('button').last().click();
    await settle(page);
    await tallShot(page, `${AREA}/scopes`);
  }
  if (want('adopt-dialog')) {
    // 관측값 채택 대화상자
    await depTab(page, '승인 정책');
    await page.getByRole('button', { name: /관측값 채택/ }).click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/adopt-dialog`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('policy-edit')) {
    // 승인 정책 편집(관측 모드) — 탭 전체
    await depTab(page, '승인 정책');
    await tallShot(page, `${AREA}/policy-edit`);
  }
  if (want('policy-history')) {
    // 승인 정책 이력 대화상자
    await depTab(page, '승인 정책');
    await page.getByRole('button', { name: '이력' }).click();
    await settle(page);
    await shot(page, `${AREA}/policy-history`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('findings')) {
    // finding 탭
    await depTab(page, /^finding/);
    await shot(page, `${AREA}/findings`);
  }
  if (want('ack-dialog')) {
    // finding 확인 대화상자
    await depTab(page, /^finding/);
    await page.getByRole('row', { name: /승인되지 않은|리소스 범위|제한값/ }).first().getByRole('button', { name: '확인' }).click();
    await page.waitForTimeout(400);
    const d = dialog(page);
    await d.getByRole('textbox').fill('변경 요청 중');
    await shot(page, `${AREA}/ack-dialog`, { target: d });
    await closeDialog(page);
  }
  if (want('audit-upload')) {
    // 감사 로그(JSONL) 업로드 — 올린 뒤 받음 · 저장 · 중복 · 무효 · 위반 건수
    await depTab(page, '감사 이벤트');
    await page.getByRole('button', { name: 'JSONL 업로드' }).click();
    const d = dialog(page);
    const lines = [
      { ts: now(), session: 's2', tool: 'create_topic', user: 'agent-a', args: { topic: 'app-orders-v2', partitions: 3 }, ok: true },
      { ts: now(), session: 's2', tool: 'create_topic', user: 'agent-a', args: { topic: 'billing' }, ok: false, error: 'tool failed' },
      { ts: now(), session: 's2', tool: 'delete_topic', user: 'agent-b', args: { topic: 'app-orders-v2' }, ok: true },
    ].map(e => JSON.stringify(e)).join('\n') + '\nnot-json';
    await chooseFile(d, 'audit.jsonl', lines);
    await d.getByRole('button', { name: '업로드' }).click();
    await settle(page);
    await clearToasts(page);
    await shot(page, `${AREA}/audit-upload`, { target: d });
    await closeDialog(page);
  }
  if (want('audit-events')) {
    // 감사 이벤트 탭 — 서비스 토큰 카드와 이벤트 목록
    await depTab(page, '감사 이벤트');
    await tallShot(page, `${AREA}/audit-events`);
  }
  if (want('agents-tab')) {
    // 사용 에이전트 탭
    await depTab(page, /사용 에이전트/);
    await shot(page, `${AREA}/agents-tab`);
  }
  if (want('agent-scope-dialog')) {
    // 에이전트 리소스 범위 대화상자
    await depTab(page, /사용 에이전트/);
    await page.getByRole('row', { name: /Kafka 운영 봇/ }).getByRole('button', { name: /범위 편집/ }).click();
    await settle(page);
    await dialog(page).getByRole('heading').first().click();   // 첫 입력 칸의 선택 표시를 푼다
    await shot(page, `${AREA}/agent-scope-dialog`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('link-card')) {
    // 미등록 MCP 연결을 이 배포로 연결(관리자) 카드 — 연결 하나를 고른 모습
    await depTab(page, /사용 에이전트/);
    await page.getByRole('row', { name: /kafka-direct/ }).getByRole('checkbox').click();
    await page.getByRole('row', { name: /kafka-direct/ }).scrollIntoViewIfNeeded();
    await settle(page, 300);
    await shot(page, `${AREA}/link-card`);
  }
  if (want('simulate-agent')) {
    // 에이전트를 지정한 접근 시뮬레이션 — 에이전트 범위(deny secret-*)까지 적용
    await depTab(page, /리소스 범위/);
    const sim = page.getByPlaceholder('예: prod_sales').locator('xpath=ancestor::div[contains(@class,"items-end")][1]');
    await sim.locator('input').nth(0).fill('topic');
    await sim.getByPlaceholder('예: prod_sales').fill('secret-pay');
    await sim.locator('button[role=combobox]').nth(2).click();
    await page.getByRole('option', { name: 'Kafka 운영 봇' }).click();
    await sim.getByRole('button').last().click();
    await settle(page);
    await tallShot(page, `${AREA}/simulate-agent`);
  }

  /* ───────── 강제(enforce) 모드 ───────── */
  if (want('bundle-preview')) {
    // 정책 번들 미리보기 대화상자
    await depTab(page, '승인 정책');
    await page.getByRole('button', { name: /번들 미리보기/ }).click();
    await settle(page);
    await shot(page, `${AREA}/bundle-preview`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('server-snippet')) {
    // 서버 설정 — 카탈로그 정책 pull 예시
    await depTab(page, '승인 정책');
    await page.getByRole('button', { name: /서버 설정/ }).click();
    await settle(page);
    await shot(page, `${AREA}/server-snippet`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('enforce-applied', 'server-denied', 'policy-readonly', 'retire-conflict')) await enforce(t);
  if (want('enforce-applied')) {
    // 강제 모드 — 정책 집행 카드의 적용 상태
    await depTab(page, '승인 정책');
    await shot(page, `${AREA}/enforce-applied`);
  }
  if (want('server-denied')) {
    // 서버 거부 감사 이벤트 — 판정을 "서버가 거부" 로 거른 목록
    await depTab(page, '감사 이벤트');
    await page.getByRole('combobox').filter({ hasText: /전체|위반/ }).last().click();
    await page.getByRole('option', { name: /서버가 거부/ }).click();
    await settle(page);
    await shot(page, `${AREA}/server-denied`);
  }
  if (want('policy-readonly')) {
    // 권한 없는 사용자(shlee)의 승인 정책 탭 — 읽기 전용
    const ro = await open('shlee', 'shlee');
    await goto(ro.page, depUrl);
    await tab(ro.page, '승인 정책');
    await tallShot(ro.page, `${AREA}/policy-readonly`);
    await ro.browser.close();
  }
  if (want('retire-conflict')) {
    // 사용 중인 배포 폐기 시도 — 에이전트 이름이 든 오류
    await goto(page, depUrl);
    await page.getByRole('button', { name: '더 보기' }).click();
    await page.getByRole('menuitem', { name: /폐기/ }).click();
    await page.waitForTimeout(400);
    await dialog(page).getByRole('button', { name: /폐기/ }).click();
    await page.waitForTimeout(1200);
    await shot(page, `${AREA}/retire-conflict`);
  }
} finally {
  await browser.close();
  if (!process.env.KEEP) { await cleanup(t); console.log('cleaned'); }
}
