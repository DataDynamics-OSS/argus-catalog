/**
 * 인증(ops/authentication.adoc) 화면 캡처 + 시작하기(getting-started.adoc)의 Keycloak 그림 2장.
 *
 *   ops-authentication/login                 로그인 카드(인증 방식과 상관없이 찍는다)
 *   ops-authentication/keycloak              설정 → 인증, 인증 방식 Keycloak OIDC (SSO)   ── Keycloak 모드에서만
 *   getting-started/login-keycloak           Keycloak 계정을 입력한 로그인 화면            ── Keycloak 모드에서만
 *   getting-started/profile-menu-keycloak    Keycloak 모드의 프로필 버튼 메뉴              ── Keycloak 모드에서만
 *
 * 이 스크립트는 설정을 바꾸지 않는다. 인증 방식이 keycloak 이 아니면 Keycloak 그림 3장은 건너뛴다.
 * 인증 방식은 스택 전체에 적용되므로(로컬 계정 로그인이 모두 막힌다) 아래 준비 · 복귀는 손으로 하고, 바꿔 둔 시간을 짧게 한다.
 * 절차는 design/integration-test/19-settings.md 의 SET-T04 와 같다.
 *
 * 수동 준비 (통합 테스트 스택, design/integration-test/scripts/env.sh 를 불러온 뒤)
 *   login                                                    # admin → $TOKEN
 *   api GET /settings/auth                                   # 지금 값을 적어 둔다
 *   api PUT /settings/auth '{"type":"keycloak","server_url":"http://keycloak:8080","realm":"argus","client_id":"argus-client",
 *     "client_secret":"••••••••","admin_role":"argus-admin","superuser_role":"argus-superuser","user_role":"argus-user","confirm":true}'
 *   (client_secret 에 마스킹 값 ••••••••  을 보내면 저장된 secret 이 유지된다.)
 *
 * 캡처
 *   node docs/tools/screenshots/ops-authentication.mjs
 *
 * 수동 복귀 (전환 뒤에는 로컬 토큰이 401 이므로 Keycloak 관리자 토큰으로)
 *   KC=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' \
 *          -d '{"username":"argus-admin","password":"argus-admin"}' | jq -r .access_token)
 *   api_as "$KC" PUT /settings/auth '{"type":"local", … 위와 같은 값 …, "confirm":true}'
 *   login; api GET /auth/me                                  # admin/admin 로컬 로그인 확인
 *   Keycloak 로그인으로 자동 등록된 사용자(argus-admin, argus-user)는 관리 → 사용자 또는
 *   DELETE /usermgr/users/{id} 로 지운다.
 *
 * Keycloak 계정은 통합 테스트 realm(argus)의 시드 계정 argus-admin · argus-user (비밀번호 = 사용자명).
 * 로그인은 카탈로그 로그인 화면에서 그대로 한다(백엔드가 Keycloak 에 대신 확인 — 브라우저가 Keycloak 으로 이동하지 않는다).
 */
import { UI, API, VIEWPORT, launch, newContext, login, goto, settle, shot } from './lib.mjs';

const KC_ADMIN = process.env.KC_ADMIN || 'argus-admin';
const KC_USER = process.env.KC_USER || 'argus-user';
const SIDEBAR = 170; // 왼쪽 사이드바 폭

const authType = (await (await fetch(`${API}/auth/type`)).json()).auth_type;
const keycloak = authType === 'keycloak';
if (!keycloak) console.log(`인증 방식이 ${authType} 이라 Keycloak 그림 3장은 건너뜁니다(머리 주석의 수동 준비 참고).`);

const profileButton = page => page.locator('[data-sidebar="footer"] button, [data-sidebar="menu-button"][data-size="lg"]').last();

const browser = await launch();
try {
  // ── 로그인 화면 ────────────────────────────────────────────────
  {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();

    // 로그인 카드(빈 입력) — 카드만 잘라서
    await goto(page, '/login');
    await page.evaluate(() => document.activeElement?.blur());
    await shot(page, 'ops-authentication/login', { target: page.locator('[data-slot="card"]').first() });

    if (keycloak) {
      // Keycloak 계정의 사용자명 · 비밀번호를 입력한 로그인 화면(로컬 로그인과 같은 화면)
      await page.fill('#username', KC_ADMIN);
      await page.fill('#password', KC_ADMIN);
      await shot(page, 'getting-started/login-keycloak');
    }
    await ctx.close();
  }

  if (keycloak) {
    // ── Keycloak 모드의 프로필 메뉴 (일반 사용자) ────────────────
    {
      const ctx = await newContext(browser);
      const page = await ctx.newPage();
      await login(page, KC_USER, KC_USER);
      await settle(page, 1500);

      // 프로필 버튼 메뉴 — 프로필 · 로그아웃만 있고 계정 설정이 없다
      await profileButton(page).click();
      await page.getByRole('menuitem', { name: '로그아웃' }).waitFor();
      await page.waitForTimeout(300);
      await shot(page, 'getting-started/profile-menu-keycloak');
      await ctx.close();
    }

    // ── 설정 → 인증 (Keycloak 관리자) ────────────────────────────
    {
      const ctx = await newContext(browser);
      const page = await ctx.newPage();
      await login(page, KC_ADMIN, KC_ADMIN);

      // 인증 방식이 Keycloak OIDC (SSO) 인 인증 설정 — 사이드바를 뺀 본문 왼쪽 920px(화면만 열고 저장하지 않는다)
      await goto(page, '/dashboard/settings?tab=auth');
      await page.getByText('Keycloak OIDC (SSO)').first().waitFor();
      await settle(page, 800);
      const height = Math.max(VIEWPORT.height, await page.evaluate(() => document.documentElement.scrollHeight));
      if (height > VIEWPORT.height) {
        await page.setViewportSize({ width: VIEWPORT.width, height });
        await settle(page, 500);
      }
      await page.mouse.move(SIDEBAR + 600, 40);
      await shot(page, 'ops-authentication/keycloak', { clip: { x: SIDEBAR, y: 0, width: 920, height } });
      await ctx.close();
    }
  }
} finally {
  await browser.close();
}
