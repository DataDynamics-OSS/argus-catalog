/**
 * AI Agent 카탈로그(governance/ai-agent-catalog.adoc) 화면 캡처.
 *
 *   node docs/tools/screenshots/governance-ai-agent-catalog.mjs
 *   ONLY=list,add-dialog node ...        일부만
 *   KEEP=1 node ...                      끝난 뒤 만든 데이터를 지우지 않음(반복 작업용)
 *   CLEAN=1 node ...                     캡처 없이 만든 데이터만 지움
 *   A2A_CARD_URL=http://<데모 호스트>:25310/agents/data-analyst node ...   A2A 카드를 내놓는 데모 에이전트 주소(기본값은 예시 주소)
 *
 * 시드 에이전트(catalog.metadata-assistant · cs.rental-support · ops.quality-watchdog)는 건드리지 않는다.
 * 아래 MINE 의 에이전트 · 사용자 · MCP 배포를 새로 만들어 찍고, 끝나면 지운다(다시 실행해도 같은 그림).
 */
import { execFileSync } from 'node:child_process';
import { open, goto, settle, shot, apiToken, api, API, VIEWPORT } from './lib.mjs';

const AREA = 'governance-ai-agent-catalog';
const MAIN = 'sales.order-assistant';          // 상세 탭을 찍는 주 에이전트(화면 등록)
const MANAGED = 'cs.refund-helper';            // 정의 파일로 관리되는 에이전트
const MINE = [MAIN, MANAGED, 'cs.faq-bot', 'hr.onboarding-guide', 'fin.invoice-reader', 'mkt.campaign-writer'];
const SHADOW = 'sales.lead-scorer';            // 미등록(발견) 에이전트
const INACTIVE_USER = { username: 'dhchoi', email: 'dhchoi@data-dynamics.io', first_name: '도현', last_name: '최' };
const DEPLOY = 'kafka-mcp-prod';               // 카탈로그 MCP 배포(kafka-mcp 의 PROD)
const CARD_URL = process.env.A2A_CARD_URL || 'http://192.0.2.10:25310/agents/data-analyst';  // A2A 카드를 내놓는 데모 에이전트 — 실제 주소는 A2A_CARD_URL 로
const MINE_DEPT = '고객지원팀';

const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const want = (...names) => !only.length || names.some(n => only.includes(n));

const KAFKA_MANIFEST = {
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

const MANAGED_DOC = {
  apiVersion: 'argus.catalog/v1', kind: 'Agent',
  metadata: { name: MANAGED, displayName: '환불 도우미', owner: 'mjkim@data-dynamics.io', department: MINE_DEPT,
    category: 'customer-service', tags: ['환불', '결제'] },
  spec: { description: '환불 문의 처리', version: '1.0.0', status: 'active', model: { name: 'gpt-4o' },
    runtime: { framework: 'langgraph' }, interface: { endpoint: 'https://agents.corp.example/refund' } },
};
const FAQ_DOC = {
  apiVersion: 'argus.catalog/v1', kind: 'Agent',
  metadata: { name: 'cs.faq-bot', displayName: 'FAQ 응답 봇', owner: 'mjkim@data-dynamics.io', department: MINE_DEPT,
    category: 'customer-service', tags: ['FAQ'] },
  spec: { description: '자주 묻는 질문에 답하는 고객 응대 봇', version: '1.2.0', status: 'active', model: { name: 'gpt-4o-mini' },
    runtime: { framework: 'langgraph' } },
};

const sql = q => execFileSync('docker', ['exec', 'argus-catalog-postgres', 'psql', '-U', 'argus', '-d', 'argus_catalog', '-Atc', q]).toString().trim();
const quiet = p => p.catch(() => null);

/** 이 스크립트가 만든 데이터를 지운다. */
async function cleanup(t) {
  for (const n of MINE) await quiet(api(t, 'DELETE', `/ai-agents/${n}`));
  const views = await quiet(api(t, 'GET', '/ai-agents/views')) || [];
  for (const v of views) if (v.name === '고객지원 고위험' && v.kind !== 'builtin') await quiet(api(t, 'DELETE', `/ai-agents/views/${v.id}`));
  const users = await api(t, 'GET', `/usermgr/users?search=${INACTIVE_USER.username}&page_size=50`);
  for (const u of users.items || []) if (u.username === INACTIVE_USER.username) await quiet(api(t, 'DELETE', `/usermgr/users/${u.id}`));
  // 발견 기록과 MCP 배포는 API 로 완전히 지울 수 없어(무시 · 폐기만 가능) DB 에서 이 스크립트가 만든 행만 지운다.
  sql(`DELETE FROM catalog_ai_agent_discoveries WHERE name='${SHADOW}'`);
  sql(`DELETE FROM catalog_mcp_status_history WHERE entity_type='deployment' AND entity_id IN (SELECT id FROM catalog_mcp_deployments WHERE name='${DEPLOY}')`);
  sql(`DELETE FROM argus_lineage_alert WHERE entity_type='mcp_deployment' AND entity_ref='${DEPLOY}'`);
  sql(`DELETE FROM catalog_mcp_deployments WHERE name='${DEPLOY}'`);
}

/** 캡처할 상황을 만든다. 먼저 지우고 새로 만들어 다시 실행해도 같은 상태가 된다. */
async function setup(t) {
  await cleanup(t);

  // MCP 배포(관측값 포함) — 도구·통합 탭의 카탈로그 배포 연결용
  await api(t, 'POST', '/mcp-deployments', { name: DEPLOY, server_name: 'kafka-mcp', environment: 'PROD', status: 'active',
    owner_email: 'mjkim@data-dynamics.io', endpoint: 'http://kafka-mcp.corp.example:8794/mcp' });
  await api(t, 'POST', `/mcp-deployments/${DEPLOY}/manifest-import`, { content: KAFKA_MANIFEST });

  // 주 에이전트 — 초안으로 만들고 스테이징 → 활성으로 올려 상태 이력을 남긴다
  await api(t, 'POST', '/ai-agents', {
    name: MAIN, display_name: '주문 상담 도우미', description: '주문 · 배송 · 결제 문의에 답하고 주문 변경을 접수하는 상담 에이전트',
    owner_email: 'mjkim@data-dynamics.io', department: MINE_DEPT, category: 'customer-service', tags: ['주문', '상담'],
    version: '1.3.0', base_model: 'gpt-4o', model_provider: 'openai', framework: 'langgraph', execution_policy: 'react',
    memory_type: 'short_term_buffer', max_steps: 12,
    endpoint: 'https://agents.corp.example/order-assistant', protocol: 'a2a', streaming: true, invocation_method: 'rest', auth_method: 'oauth2',
    capabilities: ['order-lookup', 'order-change', 'delivery-tracking'], supported_languages: ['ko', 'en'],
    use_cases: ['주문 상태 안내', '배송지 변경 접수', '결제 내역 확인'], limitations: ['환불 승인은 상담원이 처리'],
    input_schema: { type: 'object', required: ['question'], properties: { question: { type: 'string' }, order_id: { type: 'string' } } },
    output_schema: { type: 'object', properties: { answer: { type: 'string' }, sources: { type: 'array', items: { type: 'string' } } } },
    default_input_modes: ['text/plain'], default_output_modes: ['text/plain', 'application/json'],
    inference_params: { temperature: 0.2, max_tokens: 1024 },
    pii_handling: 'masking', data_residency: '한국', budget_limit: 300, hitl_required: true, audit_log_ref: 's3://audit/order-assistant',
    dlp_policies: ['email-masking', 'card-number-scan'], hitl_config: { approvers: ['mjkim'], timeout_min: 30 },
    guardrails: { input: ['prompt-injection'], output: ['pii-leak'] },
    rag_config: { index: 'order-faq', top_k: 5 }, network_allowlist: ['api.corp.example', 'kafka-1.corp.example'],
    risk_tier: 'limited', risk_rationale: '고객과 직접 대화하지만 금전 처리는 사람 승인을 거침',
    impact_assessed_at: '2026-10-20', impact_assessment_result: 'conditional', impact_assessment_ref: 'https://wiki.corp.example/ai/impact/order-assistant',
    compliance: ['ISO/IEC 42001', '개인정보 보호법'], log_retention_days: 365, autonomy_level: 'supervised',
    identity_type: 'oauth_client', identity_principal: 'svc-order-assistant', identity_provider: 'keycloak',
    granted_scopes: ['orders:read', 'orders:update'], on_behalf_of: false,
  });
  await api(t, 'PATCH', `/ai-agents/${MAIN}`, { status: 'staging' });
  await api(t, 'PATCH', `/ai-agents/${MAIN}`, { status: 'active' });
  await api(t, 'POST', `/ai-agents/${MAIN}/tools`, { name: 'lookup_order', description: '주문 번호로 주문 · 배송 상태를 조회한다', risk: 'low',
    tool_schema: { type: 'object', required: ['order_id'], properties: { order_id: { type: 'string' } } } });
  await api(t, 'POST', `/ai-agents/${MAIN}/tools`, { name: 'update_shipping_address', description: '출고 전 주문의 배송지를 바꾼다', risk: 'medium' });
  await api(t, 'POST', `/ai-agents/${MAIN}/tools`, { name: 'cancel_order', description: '주문을 취소한다 — 사람 승인 필요', risk: 'high', requires_approval: true });
  await api(t, 'POST', `/ai-agents/${MAIN}/mcp-servers`, { mcp_deployment_name: DEPLOY, allowed_groups: ['read'] });
  await api(t, 'POST', `/ai-agents/${MAIN}/mcp-servers`, { name: 'order-db-mcp', url: 'https://mcp.corp.example/order-db', auth_method: 'ApiKey' });
  await api(t, 'POST', `/ai-agents/${MAIN}/skills`, { skill_key: 'order-status', name: '주문 상태 안내', description: '주문 번호로 현재 처리 · 배송 상태를 알려 줍니다',
    tags: ['주문', '배송'], examples: ['주문 1024 어디쯤 왔나요?', '어제 주문한 상품 출고됐나요?'] });
  await api(t, 'POST', `/ai-agents/${MAIN}/skills`, { skill_key: 'address-change', name: '배송지 변경 접수', description: '출고 전 주문의 배송지를 바꿉니다',
    tags: ['주문'], examples: ['배송지를 회사로 바꿔 주세요'] });
  await api(t, 'POST', `/ai-agents/${MAIN}/data-access`, { dataset_urn: 'sakila-mysql.sakila.customer.dataset', access: 'read' });
  await api(t, 'POST', `/ai-agents/${MAIN}/data-access`, { dataset_urn: 'sakila-mysql.sakila.payment.dataset', access: 'read' });
  await api(t, 'POST', `/ai-agents/${MAIN}/data-access`, { dataset_urn: 'sakila-mysql.sakila.rental.dataset', access: 'read_write' });
  await api(t, 'POST', `/ai-agents/${MAIN}/lineage`, { target_type: 'agent', target_ref: 'cs.rental-support', relation: 'depends_on' });
  await api(t, 'POST', `/ai-agents/${MAIN}/lineage`, { target_type: 'dataset', target_ref: 'sakila-mysql.sakila.film.dataset', relation: 'depends_on' });
  await api(t, 'POST', `/ai-agents/${MAIN}/lineage`, { target_type: 'model', target_ref: 'order-intent-classifier', relation: 'depends_on' });
  await api(t, 'POST', `/ai-agents/${MAIN}/versions`, { version: '1.1.0', changelog: '주문 조회 도구 추가' });
  await api(t, 'POST', `/ai-agents/${MAIN}/versions`, { version: '1.2.0', changelog: '배송지 변경 접수 지원' });
  await api(t, 'POST', `/ai-agents/${MAIN}/versions`, { version: '1.3.0', changelog: '취소 요청에 사람 승인 적용', system_prompt: 'You are an order support assistant.' });
  await api(t, 'POST', `/ai-agents/${MAIN}/evals`, { eval_type: 'accuracy', metric_key: 'answer_accuracy', metric_value: 0.92, version: '1.3.0', passed: true });
  await api(t, 'POST', `/ai-agents/${MAIN}/evals`, { eval_type: 'user_rating', metric_key: 'csat', metric_value: 0.86 });
  await api(t, 'POST', `/ai-agents/${MAIN}/evals`, { eval_type: 'safety', metric_key: 'jailbreak_rate', metric_value: 0.04, passed: true });
  await api(t, 'POST', `/ai-agents/${MAIN}/evals`, { eval_type: 'accuracy', metric_key: 'tool_call_accuracy', metric_value: 0.71, passed: false });
  const calls = [['web-chat', 420, 0.004], ['web-chat', 510, 0.005], ['web-chat', 380, 0.004], ['mobile-app', 640, 0.006], ['mobile-app', 450, 0.004],
    ['web-chat', 700, 0.007], ['call-center', 900, 0.009], ['web-chat', 330, 0.003], ['mobile-app', 480, 0.005], ['web-chat', 560, 0.005]];
  for (const [consumer, latency_ms, cost] of calls) {
    await api(t, 'POST', `/ai-agents/${MAIN}/invocations`, { status: 'success', consumer, latency_ms, input_tokens: 320, output_tokens: 140, cost });
  }
  await api(t, 'POST', `/ai-agents/${MAIN}/invocations`, { status: 'error', error_type: 'timeout', consumer: 'call-center', latency_ms: 3000 });
  await api(t, 'POST', `/ai-agents/${MAIN}/invocations`, { status: 'error', error_type: 'tool_error', consumer: 'web-chat', latency_ms: 1200 });
  await api(t, 'POST', `/ai-agents/${MAIN}/hook-events`, { stage: 'pre_exec', decision: 'allow', action_type: 'tool_call', target: 'lookup_order', consumer: 'web-chat' });
  await api(t, 'POST', `/ai-agents/${MAIN}/hook-events`, { stage: 'pre_exec', decision: 'deny', action_type: 'tool_call', target: 'cancel_order',
    policy_ref: 'hitl-required', reason: '사람 승인 없이 취소 시도', consumer: 'mobile-app' });
  await api(t, 'POST', `/ai-agents/${MAIN}/hook-events`, { stage: 'post_exec', decision: 'mask', action_type: 'response', target: 'email',
    policy_ref: 'email-masking', reason: '응답에 이메일 포함', consumer: 'web-chat' });

  // 정의 파일로 관리되는 에이전트 2개
  await api(t, 'POST', `/ai-agents/apply?source_ref=${encodeURIComponent('my-repo#agents/')}`, { agents: [MANAGED_DOC, FAQ_DOC] });

  // 책임자 확인이 필요한 에이전트 3종 — 사용자 목록에 없음 · 비활성 계정 · 소유자 없음
  await api(t, 'POST', '/ai-agents', { name: 'hr.onboarding-guide', display_name: '입사 안내 도우미', description: '신규 입사자의 절차 · 제도 질문에 답합니다',
    owner_email: 'nobody@corp.example', department: '인사팀', category: 'assistant', status: 'active', version: '1.0.0', framework: 'crewai' });
  const u = await api(t, 'POST', '/usermgr/users', { ...INACTIVE_USER, organization: '데이터다이나믹스', department: '재무팀', password: 'Dhchoi2026x' });
  await api(t, 'PUT', `/usermgr/users/${u.id}/deactivate`);
  await api(t, 'POST', '/ai-agents', { name: 'fin.invoice-reader', display_name: '세금계산서 판독기', description: '세금계산서 이미지를 읽어 전표 초안을 만듭니다',
    owner_email: INACTIVE_USER.email, department: '재무팀', category: 'document', status: 'active', version: '1.0.0', framework: 'langgraph' });
  await api(t, 'POST', `/ai-agents/fin.invoice-reader/tools`, { name: 'post_journal_entry', description: '전표를 회계 시스템에 기표한다', risk: 'critical', requires_approval: true });
  await api(t, 'POST', '/ai-agents', { name: 'mkt.campaign-writer', display_name: '캠페인 문안 작성기', description: '마케팅 캠페인 문안 초안을 작성합니다',
    owner_email: 'mjkim@data-dynamics.io', department: '마케팅팀', category: 'content', status: 'active', version: '1.0.0' });
  await api(t, 'PATCH', '/ai-agents/mkt.campaign-writer', { owner_email: '' });

  // 미등록(그림자) 에이전트 — 카탈로그에 없는 이름으로 호출 기록 · 훅 이벤트를 보낸다(응답은 404 지만 발견 기록이 쌓인다)
  await quiet(api(t, 'POST', `/ai-agents/${SHADOW}/invocations`, { status: 'success', consumer: 'crm-batch' }));
  await quiet(api(t, 'POST', `/ai-agents/${SHADOW}/invocations`, { status: 'success', consumer: 'crm-batch' }));
  await quiet(api(t, 'POST', `/ai-agents/${SHADOW}/hook-events`, { stage: 'pre_exec', decision: 'allow' }));
}

const detail = name => `/dashboard/ai-agents?agent=${encodeURIComponent(name)}`;
async function tab(page, name) {
  await page.getByRole('tab', { name }).click();
  await settle(page);
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
const dialog = page => page.getByRole('dialog').last();
async function closeDialog(page) {
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
}

const t = await apiToken();
if (process.env.CLEAN) { await cleanup(t); console.log('cleaned'); process.exit(0); }
await setup(t);
console.log('setup done');

const { browser, page } = await open();
try {
  /* ───────── 목록 ───────── */
  if (want('list')) {
    // AI Agent 목록과 지표 카드
    await goto(page, '/dashboard/ai-agents');
    await shot(page, `${AREA}/list`);
  }
  if (want('owner-states')) {
    // 책임자 필터 "확인 필요" 로 걸러진 목록 — 소유자 칸 아래 책임자 상태
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('combobox').filter({ hasText: '책임자 전체' }).click();
    await page.getByRole('option', { name: /확인 필요/ }).click();
    await settle(page);
    await shot(page, `${AREA}/owner-states`);
  }
  if (want('card-view', 'card-view-new')) {
    // 카드 보기
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '카드 보기' }).click();
    await settle(page);
    await shot(page, `${AREA}/card-view`);
    await page.getByRole('button', { name: '테이블 보기' }).click();
    await settle(page);
  }
  if (want('department-quality')) {
    // 부서별 정보 품질 펼침
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '부서별 정보 품질' }).click();
    await settle(page);
    await shot(page, `${AREA}/department-quality`);
  }
  if (want('discoveries')) {
    // 미등록 에이전트 탭
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('tab', { name: '미등록 에이전트' }).click().catch(() => page.getByRole('button', { name: '미등록 에이전트' }).click());
    await settle(page);
    await shot(page, `${AREA}/discoveries`);
  }

  /* ───────── 상세 탭 ───────── */
  if (want('detail-overview')) {
    // 개요 탭
    await goto(page, detail(MAIN));
    await shot(page, `${AREA}/detail-overview`);
  }
  if (want('completeness')) {
    // 정보 완성도 카드와 "책임자 확인 필요"
    await goto(page, detail('hr.onboarding-guide'));
    await shot(page, `${AREA}/completeness`);
  }
  if (want('managed-notice')) {
    // 정의 파일로 관리되는 에이전트 상세 — 위쪽 파란 안내
    await goto(page, detail(MANAGED));
    await shot(page, `${AREA}/managed-notice`);
  }
  const tabs = [
    ['capability', /기능·I\/O/, true],        // 기능·I/O 탭(스킬 카드까지)
    ['tools', /도구·통합/, true],             // 도구·통합 탭
    ['governance', /거버넌스/, true],         // 거버넌스 탭
    ['observability', /관측·비용/, false],    // 관측·비용 탭
    ['evals', /평가/, false],                 // 평가 탭
    ['lineage', /리니지/, true],              // 리니지 탭
    ['versions', /버전/, true],               // 버전 탭
    ['a2a-card', /에이전트 카드/, false],     // 에이전트 카드(A2A) 탭
  ];
  for (const [name, label, fullPage] of tabs) {
    if (!want(name)) continue;
    await goto(page, detail(MAIN));
    await tab(page, label);
    if (fullPage) await tallShot(page, `${AREA}/${name}`);
    else await shot(page, `${AREA}/${name}`);
  }

  if (want('enforcement')) {
    // 집행(연동) 탭 — 정책 번들 끝부분과 집행 훅 이벤트가 함께 보이게 내려서 찍는다
    await goto(page, detail(MAIN));
    await tab(page, /집행/);
    await page.getByText(/훅 이벤트/).last().evaluate(el => {
      const y = el.getBoundingClientRect().top + window.scrollY - 430;
      (document.scrollingElement || document.documentElement).scrollTo(0, y);
      el.scrollIntoView({ block: 'center' });
    });
    await settle(page, 400);
    await shot(page, `${AREA}/enforcement`);
  }
  if (want('status-change')) {
    // 헤더 상태 배지를 눌러 상태 선택이 열린 모습
    await goto(page, detail(MAIN));
    await page.getByRole('combobox', { name: /상태/ }).first().click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/status-change`);
    await page.keyboard.press('Escape');
  }
  if (want('read-only')) {
    // 권한이 없는 사용자(shlee)의 상세 화면 — 편집 · 삭제 버튼 없음
    const ro = await open('shlee', 'shlee');
    await goto(ro.page, detail(MAIN));
    await shot(ro.page, `${AREA}/read-only`);
    await ro.browser.close();
  }
  if (want('manifest-view')) {
    // 정의 파일 보기 대화상자
    await goto(page, detail(MANAGED));
    await page.getByRole('button', { name: '정의 파일 보기' }).click();
    await settle(page);
    await shot(page, `${AREA}/manifest-view`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('delete-confirm')) {
    // ⋯ 메뉴 → 삭제 확인 대화상자
    await goto(page, detail('cs.faq-bot'));
    await page.getByRole('button', { name: '더 보기' }).click();
    await page.getByRole('menuitem', { name: '삭제' }).click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/delete-confirm`, { target: page.getByRole('alertdialog').or(page.getByRole('dialog')).last() });
    await closeDialog(page);
  }
  if (want('tool-add-dialog')) {
    // 도구 추가 대화상자 — 위험도 높음을 고르면 승인 필요가 켜진다
    await goto(page, detail(MAIN));
    await tab(page, /도구·통합/);
    await page.getByRole('button', { name: '도구 추가' }).click();
    const d = dialog(page);
    await d.getByRole('textbox').nth(0).fill('issue_coupon');
    await d.getByRole('textbox').nth(1).fill('보상 쿠폰을 발급한다');
    await d.getByRole('combobox').first().click();
    await page.getByRole('option', { name: /^높음/ }).click();
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/tool-add-dialog`, { target: d });
    await closeDialog(page);
  }
  if (want('mcp-linked')) {
    // 도구·통합 탭의 MCP 서버 카드 — 카탈로그 배포 연결과 미등록 연결
    await goto(page, detail(MAIN));
    await tab(page, /도구·통합/);
    await page.getByText(/^MCP 서버/).first().scrollIntoViewIfNeeded();
    await page.getByText('RAG 설정').first().scrollIntoViewIfNeeded();
    await settle(page, 300);
    await shot(page, `${AREA}/mcp-linked`);
  }
  if (want('mcp-scope-dialog')) {
    // 허용 그룹·도구 대화상자
    await goto(page, detail(MAIN));
    await tab(page, /도구·통합/);
    await page.getByRole('button', { name: '허용 범위' }).first().click();
    await settle(page);
    await shot(page, `${AREA}/mcp-scope-dialog`, { target: dialog(page) });
    await closeDialog(page);
  }

  /* ───────── 등록 · 가져오기 ───────── */
  if (want('add-dialog')) {
    // AI Agent 등록 대화상자
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '에이전트 등록' }).click();
    await page.fill('#agent-name', 'cs.payment-refund-assistant');
    await page.fill('#agent-display', '결제·환불 지원 어시스턴트');
    await page.fill('#agent-desc', '결제 오류와 환불 문의를 접수하고 처리 상태를 안내합니다');
    await page.fill('#agent-owner', 'mjkim@data-dynamics.io');
    await page.fill('#agent-dept', '고객지원팀');
    await page.fill('#agent-cat', 'customer-service');
    await page.locator('#agent-desc').focus();
    await shot(page, `${AREA}/add-dialog`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('import-dialog')) {
    // 정의 파일 가져오기 대화상자(처음 열린 모습)
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '가져오기' }).click();
    await page.waitForTimeout(400);
    await shot(page, `${AREA}/import-dialog`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('import-preview')) {
    // 미리 보기 — 변경 없음과 실패(오타가 있는 문서)
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '가져오기' }).click();
    const yaml = [
      'apiVersion: argus.catalog/v1', 'kind: Agent',
      'metadata: {name: cs.refund-helper, displayName: 환불 도우미, owner: mjkim@data-dynamics.io, department: 고객지원팀, category: customer-service, tags: [환불, 결제]}',
      'spec: {description: 환불 문의 처리, version: 1.0.0, status: active, model: {name: gpt-4o}, runtime: {framework: langgraph}, interface: {endpoint: "https://agents.corp.example/refund"}}',
      '---',
      'apiVersion: argus.catalog/v1', 'kind: Agent',
      'metadata: {name: cs.exchange-helper, owner: mjkim@data-dynamics.io, department: 고객지원팀, category: customer-service}',
      'spec:', '  descrption: 교환 문의 처리',
    ].join('\n');
    // 내용 칸은 코드 편집기라 파일 선택으로 넣는다
    await dialog(page).locator('input[type=file]').setInputFiles({ name: 'agents.yaml', mimeType: 'application/yaml', buffer: Buffer.from(yaml) });
    await page.waitForTimeout(500);
    await dialog(page).getByRole('button', { name: /미리\s?보기/ }).click();
    await settle(page);
    await shot(page, `${AREA}/import-preview`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('card-loaded')) {
    // A2A 소개 정보 불러오기 — 카드 내용이 채워지고 빠진 필수 항목을 노란 안내로 알려 준다
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('button', { name: '가져오기' }).click();
    await page.getByPlaceholder(/외부 에이전트 주소/).fill(CARD_URL);
    await page.getByRole('button', { name: '소개 정보 불러오기' }).click();
    await settle(page, 1500);
    await page.locator('[data-sonner-toast]').evaluateAll(els => els.forEach(e => e.remove())).catch(() => {});
    await shot(page, `${AREA}/card-loaded`, { target: dialog(page) });
    await closeDialog(page);
  }

  /* ───────── 저장된 보기 · 일괄 변경 · 발견 등록 · 검색 ───────── */
  if (want('save-view')) {
    // 현재 조건을 보기로 저장 대화상자
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('combobox').filter({ hasText: '위험도 전체' }).click();
    await page.getByRole('option', { name: /^높음/ }).click();
    await settle(page);
    await page.getByRole('button', { name: '현재 조건 저장' }).click();
    await page.fill('#view-name', '고객지원 고위험');
    await dialog(page).getByRole('checkbox').check().catch(() => dialog(page).getByText(/모두에게 공유/).click());
    await page.waitForTimeout(300);
    await shot(page, `${AREA}/save-view`, { target: dialog(page) });
    await closeDialog(page);
  }
  if (want('bulk-preview')) {
    // 일괄 변경 미리 보기 — 정의 파일 관리 에이전트에는 "다음 반영 때 되돌아갑니다" 안내
    await goto(page, '/dashboard/ai-agents');
    for (const n of ['hr.onboarding-guide', 'cs.faq-bot', 'cs.refund-helper']) await page.getByRole('checkbox', { name: `${n} 선택` }).click();
    await page.getByRole('button', { name: /일괄 변경/ }).click();
    await page.fill('#bulk-owner', 'shlee@data-dynamics.io');
    await page.fill('#bulk-add', '정리대상');
    await page.keyboard.press('Enter');
    await dialog(page).getByRole('button', { name: /미리\s?보기/ }).click();
    await settle(page);
    await shot(page, `${AREA}/bulk-preview`);
    await closeDialog(page);
  }
  if (want('discovery-register')) {
    // 미등록 에이전트 등록 대화상자
    await goto(page, '/dashboard/ai-agents');
    await page.getByRole('tab', { name: '미등록 에이전트' }).click();
    await settle(page);
    await page.getByRole('row', { name: new RegExp(SHADOW.replace('.', '\\.')) }).getByRole('button', { name: '등록' }).click();
    await page.waitForTimeout(400);
    const d = dialog(page);
    await d.getByRole('textbox').nth(2).fill('sales');
    await shot(page, `${AREA}/discovery-register`, { target: d });
    await closeDialog(page);
  }
  if (want('global-search')) {
    // 통합 검색 결과의 AI Agent 항목
    await goto(page, '/dashboard');
    const box = page.getByPlaceholder(/데이터셋 검색/).first();
    await box.fill('상담');
    await box.press('Enter');
    // 검색 결과 화면으로 넘어갈 때까지 기다린다(개발 서버는 첫 컴파일이 느려 대시보드가 그대로 찍힐 수 있다)
    await page.getByText(/검색 결과 \d+건/).first().waitFor({ timeout: 60000 }).catch(() => {});
    await settle(page, 1500);
    await shot(page, `${AREA}/global-search`);
  }
} finally {
  await browser.close();
  if (!process.env.KEEP) { await cleanup(t); console.log('cleaned'); }
}
