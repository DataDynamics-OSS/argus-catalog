/**
 * 모델 영역 화면 캡처 — models-registry · models-stage-metrics · models-oci-hub · models-import.
 *
 * 시드 모델(MLflow 3개, OCI 3개)은 건드리지 않는다. 상황 재현용으로 아래 항목을 만들고 끝나면 지운다.
 *   - MLflow `argus.ml.bert`            서버 디렉터리 반입(import-local) 모델. v2 는 등록 실패로 마감.
 *   - MLflow `argus.ml.iris_classifier` 삭제 대화상자 · 삭제된 모델 보기용.
 *   - OCI    `prajjwal1-bert-tiny`      HuggingFace 에서 가져온 모델(백엔드가 huggingface.co 에 닿아야 한다).
 *   - `argus.ml.film_recommender` 의 데이터셋 리니지 1건(캡처 뒤 삭제).
 *
 * 저장소 장애 화면(OCI 버전 탭의 실패 · 가져오는 중 행, OCI 모델 파일의 연결 실패 배너)은 같이 쓰는 스택의
 * 저장소 설정을 바꾸지 않으려고, 브라우저가 받는 API 응답만 장애 때와 같은 내용으로 바꿔서 찍는다(서버는 그대로).
 *
 *   node docs/tools/screenshots/models.mjs            # 전부
 *   ONLY=list,versions node docs/tools/screenshots/models.mjs
 *   GROUP=oci node docs/tools/screenshots/models.mjs  # registry | stage | oci | import 중 일부만
 */
import { execFileSync } from 'node:child_process';
import { open, goto, settle, shot, apiToken, api, API } from './lib.mjs';

const BACKEND = process.env.BACKEND_CONTAINER || 'argus-catalog-backend';
const LOCAL_DIR = '/tmp/transferred/bert';          // 백엔드 컨테이너 안의 반입용 디렉터리
const BERT = 'argus.ml.bert';
const IRIS = 'argus.ml.iris_classifier';
const FILM = 'argus.ml.film_recommender';
const HF_ID = 'prajjwal1/bert-tiny';
const HF_NAME = 'prajjwal1-bert-tiny';
const OUTAGE = '오브젝트 스토리지에 연결할 수 없습니다. MinIO 실행 여부와 설정을 확인하세요.';

const groups = (process.env.GROUP || '').split(',').map(s => s.trim()).filter(Boolean);
const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
/** 그룹을 돌릴지 — GROUP 이 있으면 그 그룹만, ONLY 가 있으면 그 이름이 든 그룹만. */
const want = (group, names) =>
  (groups.length === 0 || groups.includes(group)) && (only.length === 0 || names.some(n => only.includes(n)));

const token = await apiToken();
const call = (method, path, body) => api(token, method, path, body);
const quiet = (method, path, body) => call(method, path, body).catch(() => null);
const sh = (cmd) => execFileSync('docker', ['exec', BACKEND, 'sh', '-c', cmd], { stdio: 'pipe' });

/** 토스트가 사라질 때까지 기다린다(토스트가 주제가 아닌 그림에 찍히지 않게). */
async function noToast(page) {
  await page.waitForFunction(() => !document.querySelector('[data-sonner-toast]'), null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(300);
}

const dialog = (page) => page.locator('[role=dialog], [role=alertdialog]').last();

async function purgeMlflow(...names) {
  for (const n of names) await quiet('DELETE', `/models/${n}`);
  await quiet('POST', '/models/hard-delete', { names });
}

const { browser, page } = await open();

try {
  // ───────────────────────────── models-registry ─────────────────────────────
  if (want('registry', ['list', 'register-guide', 'failed-version', 'delete-dialog', 'deleted-models'])
    || want('import', ['local-import-versions'])) {
    await purgeMlflow(BERT, IRIS);

    // MLflow 모델 목록 — 시드 모델 3개
    await goto(page, '/dashboard/models?tab=models');
    await shot(page, 'models-registry/list');

    // 등록 방법 안내 — 검색 결과가 없을 때 나오는 "등록 방법 보기"
    await page.getByPlaceholder('모델 이름 검색…').fill('iris_classifier');
    await settle(page);
    await page.getByRole('button', { name: '등록 방법 보기' }).first().click();
    await settle(page);
    await shot(page, 'models-registry/register-guide');
    await page.keyboard.press('Escape');

    // 서버 디렉터리 반입(import-local)으로 argus.ml.bert v1 을 만든다
    sh(`mkdir -p ${LOCAL_DIR} && cd ${LOCAL_DIR} && `
      + `echo '{"model_type":"bert","hidden_size":128,"num_hidden_layers":2}' > config.json && `
      + `printf '[PAD]\\n[UNK]\\n[CLS]\\n[SEP]\\n[MASK]\\n' > vocab.txt && head -c 4096 /dev/urandom > pytorch_model.bin`);
    await call('POST', '/model-store/import/local', {
      local_dir: LOCAL_DIR, model_name: BERT,
      description: 'BERT base uncased — 에어갭 서버 디렉터리에서 반입',
    });

    // 로컬 반입으로 만든 MLflow 모델의 버전 탭
    await goto(page, `/dashboard/models/${BERT}?tab=versions`);
    await shot(page, 'models-import/local-import-versions');

    // 등록 실패 버전 — v2 를 저장소 장애 사유로 실패 마감
    await call('POST', `/models/${BERT}/versions`, { model_name: BERT });
    await call('PATCH', `/models/${BERT}/versions/2/finalize`, { status: 'FAILED_REGISTRATION', status_message: OUTAGE });
    await goto(page, `/dashboard/models/${BERT}?tab=versions`);
    await shot(page, 'models-registry/failed-version');

    // 삭제 대화상자 — 목록에서 한 모델을 골라 "1개 삭제"
    await call('POST', '/models', { name: IRIS, description: '붓꽃 품종 분류 모델 — scikit-learn RandomForest', owner: 'admin' });
    await call('POST', `/models/${IRIS}/versions`, { model_name: IRIS, source: 'mlflow', run_id: '3c9d1e7a5b2f0005' });
    await call('PATCH', `/models/${IRIS}/versions/1/finalize`, { status: 'READY' });
    await goto(page, '/dashboard/models?tab=models');
    const row = page.locator('tr', { hasText: IRIS });
    await row.getByRole('checkbox').click();
    await page.getByRole('button', { name: '1개 삭제' }).click();
    await settle(page);
    await shot(page, 'models-registry/delete-dialog', { target: dialog(page) });

    // 삭제된 모델 보기(관리자) — 방금 지운 모델이 복원 · 영구 삭제 대상으로 보인다
    await dialog(page).getByRole('button', { name: '삭제', exact: true }).click();
    await settle(page);
    await page.getByRole('button', { name: '삭제된 모델', exact: true }).click();
    await settle(page);
    await noToast(page);
    await shot(page, 'models-registry/deleted-models');

    await purgeMlflow(BERT, IRIS);
    sh(`rm -rf ${LOCAL_DIR}`);
  }

  // ─────────────────────────── models-stage-metrics ───────────────────────────
  if (want('stage', ['stage-confirm-production', 'performance', 'lineage-connect', 'lineage'])) {
    const clearLineage = async () => {
      const rows = await call('GET', `/models/${FILM}/lineage`);
      for (const r of (rows.items ?? rows)) await quiet('DELETE', `/models/${FILM}/lineage/${r.id}`);
    };
    await clearLineage();

    // 운영 스테이지로 변경 확인 — 운영 중인 v2 가 있는 모델에서 v1 을 운영으로 고른다(취소로 닫아 바꾸지 않는다)
    await goto(page, `/dashboard/models/${FILM}?tab=versions`);
    await page.getByRole('combobox', { name: 'v1 스테이지' }).click();
    await page.getByRole('option', { name: '운영', exact: true }).click();
    await settle(page);
    await shot(page, 'models-stage-metrics/stage-confirm-production');
    await dialog(page).getByRole('button', { name: '취소' }).click();

    // 성능 탭 — 버전별 지표와 최고 · 변화 표시
    await goto(page, `/dashboard/models/${FILM}?tab=performance`);
    await shot(page, 'models-stage-metrics/performance');

    // 데이터셋 연결 대화상자 — 데이터셋을 고르고 관계 · 버전 · 설명을 적은 상태
    await goto(page, `/dashboard/models/${FILM}?tab=lineage`);
    await page.getByRole('button', { name: '데이터셋 연결' }).first().click();
    await page.getByPlaceholder('데이터셋 이름 검색…').fill('sakila.rental');
    await settle(page);
    await dialog(page).locator('button', { hasText: 'sakila.rental' }).first().click();
    await dialog(page).locator('input').last().fill('회원별 대여 이력 — 추천 모델 학습 데이터');
    await page.waitForTimeout(300);
    await shot(page, 'models-stage-metrics/lineage-connect');

    // 리니지 탭 — 연결한 뒤의 그래프와 연결 목록
    await dialog(page).getByRole('button', { name: '연결', exact: true }).click();
    await settle(page, 1500);
    await noToast(page);
    await shot(page, 'models-stage-metrics/lineage');

    await clearLineage();
  }

  // ────────────────────────────── models-oci-hub ──────────────────────────────
  if (want('oci', ['model-list', 'hf-import', 'versions', 'version-delete-confirm', 'storage-outage-banner'])
    || want('import', ['import-guide'])) {
    await quiet('DELETE', `/oci-models/${HF_NAME}`);

    // OCI 모델 허브 — 모델 카드 목록(시드 모델 3개)
    await goto(page, '/dashboard/oci-hub?tab=models');
    await shot(page, 'models-oci-hub/model-list');

    // 모델 가져오기 방법 안내 — 가이드 ▼ → 모델 올리기
    await page.getByRole('button', { name: /가이드/ }).click();
    await page.getByRole('menuitem', { name: /모델 올리기/ }).click();
    await settle(page);
    await shot(page, 'models-import/import-guide');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);

    // HuggingFace 에서 가져오기 대화상자 — 모델 ID 와 저장할 이름을 적은 상태
    await page.getByRole('button', { name: /모델 등록/ }).click();
    await page.getByRole('menuitem', { name: /HuggingFace 에서 가져오기/ }).click();
    await settle(page);
    const inputs = dialog(page).locator('input');
    await inputs.nth(0).fill(HF_ID);
    await inputs.nth(1).fill(HF_NAME);
    await page.waitForTimeout(300);
    await shot(page, 'models-oci-hub/hf-import', { target: dialog(page) });
    await page.keyboard.press('Escape');

    if (want('oci', ['versions', 'version-delete-confirm'])) {
      // 실제로 두 번 가져와 준비됨 버전 v1 · v2 를 만든다
      for (const n of [1, 2]) {
        await call('POST', '/oci-models/import/huggingface', { hf_model_id: HF_ID });
        for (let i = 0; i < 90; i++) {
          const vs = await call('GET', `/oci-models/${HF_NAME}/versions`);
          const v = vs.find(x => x.version === n);
          if (v?.status === 'ready') break;
          if (v?.status === 'failed') throw new Error(`HF import v${n} failed: ${JSON.stringify(v.extra_metadata)}`);
          await new Promise(r => setTimeout(r, 2000));
        }
      }

      // 실패(v3) · 가져오는 중(v4) 행은 응답에만 끼워 넣는다 — 저장소 장애 중 가져오기를 했을 때의 응답과 같은 모양
      //   (실패 사유는 서버가 저장소 연결 불가일 때 남기는 안내 문구 — backend/app/models/model_store.py 의 STORAGE_DOWN)
      const HF_ERROR = OUTAGE;
      const extra = (real) => {
        const base = Math.max(...real.map(v => Date.parse(v.created_at)));
        const at = (sec) => new Date(base + sec * 1000).toISOString();
        const blank = { manifest: null, content_digest: null, file_count: 0, total_size: 0 };
        return [
          { ...real[0], ...blank, id: -4, version: 4, status: 'importing', extra_metadata: null, created_at: at(120), updated_at: at(120) },
          { ...real[0], ...blank, id: -3, version: 3, status: 'failed', extra_metadata: { error: HF_ERROR }, created_at: at(60), updated_at: at(70) },
        ];
      };
      const versionsUrl = new RegExp(`/oci-models/${HF_NAME}/versions$`);
      let withImporting = true;
      await page.route(versionsUrl, async (route) => {
        if (route.request().method() !== 'GET') return route.continue();
        const res = await route.fetch();
        const real = await res.json();
        const add = extra(real).filter(v => withImporting || v.status !== 'importing');
        await route.fulfill({ response: res, json: [...add, ...real] });
      });

      // 버전 탭 — 가져오는 중 · 실패 · 준비됨 버전과 행별 삭제 버튼
      await goto(page, `/dashboard/oci-hub/${HF_NAME}?tab=versions`);
      await shot(page, 'models-oci-hub/versions');

      // 버전 삭제 확인 — 실패한 v3 의 삭제(취소로 닫는다)
      withImporting = false;
      await goto(page, `/dashboard/oci-hub/${HF_NAME}?tab=versions`);
      await page.locator('tr', { hasText: 'v3' }).getByRole('button', { name: '삭제' }).click();
      await settle(page);
      await shot(page, 'models-oci-hub/version-delete-confirm');
      await dialog(page).getByRole('button', { name: '취소' }).click();
      await page.unroute(versionsUrl);

      await call('DELETE', `/oci-models/${HF_NAME}`);
    }

    // 저장소 연결 실패 배너 — OCI 모델 파일 화면의 목록 요청이 503 일 때
    const listUrl = /\/browse\/list\?/;
    await page.route(listUrl, (route) => route.fulfill({ status: 503, json: { detail: OUTAGE } }));
    await goto(page, '/dashboard/oci-files');
    await settle(page, 1500);
    await noToast(page);
    await shot(page, 'models-oci-hub/storage-outage-banner');
    await page.unroute(listUrl);
  }
} finally {
  await browser.close();
}
