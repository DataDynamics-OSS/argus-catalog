/**
 * 시작하기(getting-started.adoc) 화면 캡처.
 *
 * 로그인 잠금 · 비활성 · 비밀번호 변경 강제 · 내 계정 그림은 전용 사용자를 만들어 찍고, 끝나면 지운다.
 * (admin 과 시드 사용자의 비밀번호 · 상태 · 아바타는 건드리지 않는다.)
 *
 * login-keycloak.png, profile-menu-keycloak.png 은 인증 방식을 Keycloak 으로 바꿔야 해서 여기서 찍지 않는다(ops-authentication.mjs 가 찍는다).
 */
import { UI, API, launch, newContext, login, open, goto, settle, shot, apiToken, api } from './lib.mjs';

const AREA = 'getting-started';
const ORG = '데이터다이나믹스';

// 캡처 전용 사용자 — 스크립트가 만들고 끝나면 지운다.
const USERS = {
  // 내 계정(프로필 · 아바타 · 계정 설정)
  account: { username: 'dwlim', last_name: '임', first_name: '도원', department: '데이터분석팀', password: 'welcome2026' },
  // 로그인 실패 · 잠금
  lock: { username: 'hsnam', last_name: '남', first_name: '현수', department: '데이터분석팀', password: 'welcome2026' },
  // 비활성 계정
  inactive: { username: 'yrbae', last_name: '배', first_name: '유리', department: '데이터분석팀', password: 'welcome2026' },
  // 최초 로그인 비밀번호 변경
  gate: { username: 'jwkwon', last_name: '권', first_name: '지우', department: '데이터분석팀', password: 'welcome2026', must_change_password: true },
};

const token = await apiToken();

async function findUser(username) {
  const r = await api(token, 'GET', `/usermgr/users?search=${encodeURIComponent(username)}&page_size=100`);
  return r.items.find(u => u.username === username);
}

async function removeUser(username) {
  const u = await findUser(username);
  if (u) await api(token, 'DELETE', `/usermgr/users/${u.id}`);
}

/** 전용 사용자를 새로 만든다(남아 있으면 지우고 다시 — 잠금 · 아바타 · 비밀번호 상태를 처음으로). */
async function createUser(spec) {
  await removeUser(spec.username);
  return api(token, 'POST', '/usermgr/users', {
    email: `${spec.username}@data-dynamics.io`, organization: ORG, role: 'argus-user', ...spec,
  });
}

/** 로그인 화면에서 계정을 입력하고 로그인 버튼을 누른다(성공을 기다리지 않는다). */
async function tryLogin(page, username, password) {
  await page.goto(`${UI}/login`);
  await page.fill('#username', username);
  await page.fill('#password', password);
  await page.click('button[type=submit]');
  await settle(page);
}

/** 사이드바 메뉴 영역을 맨 아래로 내린다. */
async function scrollSidebarToBottom(page) {
  await page.locator('[data-sidebar="content"]').first().evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForTimeout(300);
}

const profileButton = page => page.locator('[data-sidebar="footer"] button, [data-sidebar="menu-button"][data-size="lg"]').last();

async function openProfileMenu(page) {
  await profileButton(page).click();
  await page.getByRole('menuitem', { name: '로그아웃' }).waitFor();
  await page.waitForTimeout(300);
}

const browser = await launch();
try {
  // ── 로그인 화면 ────────────────────────────────────────────────
  {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();

    // 로그인 화면(빈 입력)
    await goto(page, '/login');
    await shot(page, `${AREA}/login`);

    // 틀린 비밀번호 — 비밀번호 칸 아래 오류 문구 (전용 사용자, 실패 1회째)
    await createUser(USERS.lock);
    await tryLogin(page, USERS.lock.username, 'wrongpass1');
    await page.getByText('사용자명 또는 비밀번호가 올바르지 않습니다.').waitFor();
    await shot(page, `${AREA}/login-error`);

    // 5번 연속 실패로 잠긴 계정 — 올바른 비밀번호를 넣어도 잠금 안내
    for (let i = 0; i < 4; i++) {
      await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: USERS.lock.username, password: 'wrongpass1' }),
      });
    }
    await tryLogin(page, USERS.lock.username, USERS.lock.password);
    await page.getByText('계정이 잠겼습니다').waitFor();
    await shot(page, `${AREA}/login-locked`);
    await removeUser(USERS.lock.username);

    // 비활성화된 계정으로 로그인
    const inactive = await createUser(USERS.inactive);
    await api(token, 'PUT', `/usermgr/users/${inactive.id}/deactivate`);
    await tryLogin(page, USERS.inactive.username, USERS.inactive.password);
    await page.getByText('비활성화된 계정입니다.').waitFor();
    await shot(page, `${AREA}/login-inactive`);
    await removeUser(USERS.inactive.username);

    // 최초 로그인 — 비밀번호 변경 필요 화면
    await createUser(USERS.gate);
    await tryLogin(page, USERS.gate.username, USERS.gate.password);
    await page.waitForURL(/change-password-required/);
    await settle(page);
    await shot(page, `${AREA}/password-change-required`);

    // 현재와 같은 비밀번호를 새 비밀번호로 입력한 경우
    await page.fill('#current', USERS.gate.password);
    await page.fill('#new', USERS.gate.password);
    await page.fill('#confirm', USERS.gate.password);
    await page.click('button[type=submit]');
    await page.getByText('새 비밀번호는 현재 비밀번호와 달라야 합니다.').waitFor();
    await settle(page, 300);
    await shot(page, `${AREA}/password-change-same`);
    await removeUser(USERS.gate.username);
    await ctx.close();
  }

  // ── 화면 구조 · 사이드바 (관리자) ──────────────────────────────
  {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();
    await login(page);

    // 로그인 직후 화면 — 사이드바 · 헤더 · 본문
    await settle(page, 1500);
    await shot(page, `${AREA}/layout`);

    // 관리자 사이드바 — 맨 아래 관리 그룹까지 내린 모습
    await scrollSidebarToBottom(page);
    await shot(page, `${AREA}/sidebar-admin`);
    await ctx.close();
  }

  // ── 사이드바 · 접근 권한 없음 (일반 사용자) ────────────────────
  {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();
    await login(page, 'mjkim', 'mjkim');

    // 일반 사용자 사이드바 — 관리 그룹이 없다
    await settle(page, 1500);
    await scrollSidebarToBottom(page);
    await shot(page, `${AREA}/sidebar-user`);

    // 권한 없는 화면(사용자 관리) 주소를 직접 연 경우
    await goto(page, '/dashboard/users');
    await page.getByText('이 화면에 접근할 권한이 없습니다').waitFor();
    await shot(page, `${AREA}/access-denied`);
    await ctx.close();
  }

  // ── 내 계정 (전용 사용자) ──────────────────────────────────────
  {
    await createUser(USERS.account);
    const ctx = await newContext(browser);
    const page = await ctx.newPage();
    await login(page, USERS.account.username, USERS.account.password);
    await settle(page, 1500);

    // 프로필 버튼 메뉴 — 프로필 · 계정 설정 · 로그아웃
    await openProfileMenu(page);
    await shot(page, `${AREA}/profile-menu`);

    // 프로필 대화상자(조회 전용)
    await page.getByRole('menuitem', { name: '프로필' }).click();
    const profile = page.getByRole('dialog', { name: '프로필' });
    await profile.waitFor();
    await settle(page, 500);
    await shot(page, `${AREA}/profile`);

    // 아바타 선택 대화상자 — 지금 쓰는 아바타에 체크 표시
    await profile.getByRole('button', { name: '아바타 변경' }).click();
    const picker = page.getByRole('dialog', { name: '아바타 선택' });
    await picker.waitFor();
    await picker.locator('button[aria-pressed]').nth(2).click();
    await settle(page, 500);
    if (!(await picker.isVisible())) {
      await profile.getByRole('button', { name: '아바타 변경' }).click();
      await picker.waitFor();
      await settle(page, 500);
    }
    await shot(page, `${AREA}/avatar-picker`);

    // 아바타가 적용된 프로필 대화상자와 사이드바 프로필 버튼
    await page.keyboard.press('Escape');
    await picker.waitFor({ state: 'hidden' });
    await page.mouse.move(1200, 700);
    await settle(page, 500);
    await shot(page, `${AREA}/avatar-applied`);
    await page.keyboard.press('Escape');
    await profile.waitFor({ state: 'hidden' });

    // 계정 설정 — 사용자 정보 수정 대화상자
    await openProfileMenu(page);
    await page.getByRole('menuitem', { name: '계정 설정' }).click();
    const settings = page.getByRole('dialog', { name: '사용자 정보 수정' });
    await settings.waitFor();
    await settle(page, 500);
    await page.evaluate(() => document.activeElement?.blur()); // 첫 칸의 선택 표시를 없앤다
    await shot(page, `${AREA}/account-settings`);

    // 현재 비밀번호가 틀린 경우 — 세 칸 모두 가려서 표시
    await settings.locator('input[name="currentPassword"]').fill('wrongpass1');
    await settings.locator('input[name="password"]').fill('newpass2026');
    await settings.locator('input[name="confirmPassword"]').fill('newpass2026');
    await settings.getByRole('button', { name: '저장' }).click();
    await settings.getByText('현재 비밀번호가 올바르지 않습니다.').waitFor();
    await settle(page, 300);
    await shot(page, `${AREA}/account-password-error`);
    await ctx.close();
    await removeUser(USERS.account.username);
  }
} finally {
  // 전용 사용자가 남지 않게 정리
  for (const u of Object.values(USERS)) await removeUser(u.username).catch(() => {});
  await browser.close();
}
