"use client"

import { useEffect, useState } from "react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import { UsageSteps } from "@/components/model-hub/usage-steps"

/**
 * MLflow 모델 등록 방법 안내.
 *
 * 모델은 화면에서 만들지 않고 MLflow 클라이언트가 학습 결과를 기록하면서 등록한다
 * (Unity Catalog 호환 API — 이름·버전·파일이 함께 생긴다). 빈 목록에서 이 안내를 연다.
 */
export function ModelsRegisterGuide({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  // 레지스트리 주소 = 지금 접속한 UI 주소 (UI 서버가 /api/2.1 을 카탈로그 서버로 전달한다)
  const [origin, setOrigin] = useState("http://<argus-catalog-ui>")
  useEffect(() => setOrigin(window.location.origin), [])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>MLflow 모델 등록 방법</DialogTitle>
          <DialogDescription>
            모델은 MLflow 클라이언트로 학습 결과를 기록할 때 등록됩니다. 이름은 <code>카탈로그.스키마.모델</code> 3단계로 씁니다.
            등록한 사용자가 모델의 등록자가 되며, 이후 새 버전은 등록자·소유자·관리자만 추가할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <UsageSteps
          steps={[
            {
              title: "설치 · 토큰 설정",
              description: "레지스트리 API 는 로그인 토큰이 필요합니다 (MLflow 3.x 환경변수).",
              code: `pip install mlflow scikit-learn\n\nexport MLFLOW_UC_OSS_TOKEN=$(curl -s -X POST "${origin}/api/v1/auth/login" \\\n  -H "Content-Type: application/json" \\\n  -d '{"username": "<사용자>", "password": "<비밀번호>"}' | python -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')`,
              language: "bash",
            },
            {
              title: "학습 결과 기록 · 등록",
              description: "registered_model_name 으로 기록하면 모델이 없을 때 새로 만들고 버전 1 을 등록합니다. 같은 이름으로 다시 기록하면 새 버전이 생깁니다.",
              code: `import mlflow\nfrom sklearn.datasets import load_iris\nfrom sklearn.linear_model import LogisticRegression\n\nmlflow.set_registry_uri("uc:${origin}")\n\nX, y = load_iris(return_X_y=True, as_frame=True)\nmodel = LogisticRegression(max_iter=200).fit(X, y)\n\nwith mlflow.start_run():\n    mlflow.sklearn.log_model(\n        model,\n        "model",\n        input_example=X.head(3),\n        registered_model_name="argus.ml.iris_classifier",\n    )`,
              language: "python",
            },
            {
              title: "확인",
              description: "목록을 새로 고치면 모델이 보입니다. 상세 화면의 '사용법' 탭에서 로드·예측 코드를 확인할 수 있습니다.",
              code: `from mlflow import MlflowClient\n\nclient = MlflowClient(registry_uri="uc:${origin}")\nprint(client.get_registered_model("argus.ml.iris_classifier"))`,
              language: "python",
            },
          ]}
        />
      </DialogContent>
    </Dialog>
  )
}
