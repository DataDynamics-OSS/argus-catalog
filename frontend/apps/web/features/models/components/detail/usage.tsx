"use client"

import { useEffect, useState } from "react"

import { UsageSteps } from "@/components/model-hub/usage-steps"

import type { ModelDetail, ModelVersionItem } from "../../api"
import { frameworkLabel, primaryVersion } from "./shared"

// flavor 별 학습 모델 기록 함수 — 목록에 없는 flavor 는 python_function(pyfunc) 예시로 대체.
const LOG_MODEL_BY_FLAVOR: Record<string, string> = {
  sklearn: "mlflow.sklearn.log_model",
  xgboost: "mlflow.xgboost.log_model",
  lightgbm: "mlflow.lightgbm.log_model",
  catboost: "mlflow.catboost.log_model",
  pytorch: "mlflow.pytorch.log_model",
  tensorflow: "mlflow.tensorflow.log_model",
  keras: "mlflow.keras.log_model",
  transformers: "mlflow.transformers.log_model",
  prophet: "mlflow.prophet.log_model",
  statsmodels: "mlflow.statsmodels.log_model",
  onnx: "mlflow.onnx.log_model",
}

/** 사용법 탭 — 설치·토큰 → 레지스트리 지정·로드 → 예측 → 새 버전 등록. */
export function UsageTab({ detail, versions }: { detail: ModelDetail; versions: ModelVersionItem[] }) {
  // 레지스트리 주소 = 지금 접속한 UI 주소. UI 서버가 Unity Catalog 호환 API(/api/2.1)를
  // 카탈로그 서버로 전달하므로 MLflow 클라이언트도 같은 주소를 쓰면 된다.
  const [origin, setOrigin] = useState("http://<argus-catalog-ui>")
  useEffect(() => setOrigin(window.location.origin), [])

  const name = detail.name
  const primary = primaryVersion(versions)
  const ver = primary?.version ?? detail.max_version_number
  const flavor = detail.catalog?.flavor ?? null
  const logFn = (flavor && LOG_MODEL_BY_FLAVOR[flavor]) || "mlflow.pyfunc.log_model"
  const fw = frameworkLabel(detail.catalog)
  const py = detail.catalog?.python_version
  const pkgs = ["mlflow", flavor && LOG_MODEL_BY_FLAVOR[flavor] ? flavor.replace("pytorch", "torch").replace("sklearn", "scikit-learn") : null, "pandas"]
    .filter(Boolean).join(" ")

  return (
    <div className="max-w-4xl space-y-4">
      {fw && (
        <p className="text-sm text-muted-foreground">
          이 모델은 <span className="font-medium text-foreground">{fw}</span>{py ? ` / Python ${py}` : ""} 로 만들어졌습니다. 같은 환경에서 로드하세요.
        </p>
      )}
      <UsageSteps
        steps={[
          {
            title: "설치 · 토큰 설정",
            description: "레지스트리 API 는 조회·로드를 포함해 모두 로그인 토큰이 필요합니다 (MLflow 3.x 환경변수).",
            code: `pip install ${pkgs}\n\nexport MLFLOW_UC_OSS_TOKEN=$(curl -s -X POST "${origin}/api/v1/auth/login" \\\n  -H "Content-Type: application/json" \\\n  -d '{"username": "<사용자>", "password": "<비밀번호>"}' | python -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')`,
            language: "bash",
          },
          {
            title: "모델 로드",
            description: primary?.stage === "PRODUCTION"
              ? `운영 버전(v${ver})을 로드합니다. 최신 버전이 필요하면 버전 대신 latest 를 쓰세요.`
              : `최신 버전(v${ver})을 로드합니다.`,
            code: `import mlflow\n\nmlflow.set_registry_uri("uc:${origin}")\nmodel = mlflow.pyfunc.load_model("models:/${name}/${ver}")`,
            language: "python",
          },
          {
            title: "예측",
            description: "입력 열은 model.metadata.get_input_schema() 로 확인할 수 있습니다.",
            code: `import pandas as pd\n\nX_new = pd.DataFrame([...], columns=[...])  # 실제 입력으로 교체\npredictions = model.predict(X_new)`,
            language: "python",
          },
          {
            title: "새 버전 등록",
            description: "같은 이름으로 기록하면 새 버전이 생깁니다. 모델 등록자·소유자 또는 관리자만 등록할 수 있습니다.",
            code: `mlflow.set_registry_uri("uc:${origin}")\n\nwith mlflow.start_run():\n    ${logFn}(\n        model,\n        "model",\n        registered_model_name="${name}",\n    )`,
            language: "python",
          },
        ]}
      />
    </div>
  )
}
