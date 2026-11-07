"use client"

import { useState } from "react"

import { Button } from "@workspace/ui/components/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { CodeViewer } from "@/components/code-viewer"

import { Code2 } from "lucide-react"
import { EmptyState } from "@/components/model-hub/detail-layout"
import { SegmentedToggle } from "@/components/model-hub/segmented-toggle"
import { CodeSnippet, UsageSteps } from "@/components/model-hub/usage-steps"

import { currentServerUrl } from "./oci-common"

type GuideTab = "cli" | "sdk" | "api"
const TAB_LABELS: Record<GuideTab, string> = { cli: "CLI", sdk: "Python SDK", api: "REST API" }

/** 토큰 발급 — 모든 예시가 공통으로 쓰는 준비 단계. */
function tokenSetup(server: string): string {
  return `# ─── 준비: 서버 주소와 로그인 토큰 ───
export ARGUS_SERVER=${server}
export ARGUS_TOKEN=$(curl -s -X POST "$ARGUS_SERVER/api/v1/auth/login" \\
  -H "Content-Type: application/json" \\
  -d '{"username": "<사용자>", "password": "<비밀번호>"}' | python -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')`
}

function importGuide(tab: GuideTab, server: string): string {
  if (tab === "cli") {
    return `# ─── CLI 설치 ───
pip install argus-catalog-sdk

${tokenSetup(server)}
# (또는 --username / --password 옵션, ARGUS_USERNAME / ARGUS_PASSWORD 환경변수)

# ─── 로컬 디렉터리를 새 모델 버전으로 push (로그인 사용자) ───
# 모델이 없으면 만들고, 있으면 다음 버전으로 올린다. 기존 모델은 소유자·생성자·관리자만 push 가능.
argus-model push /path/to/my-model my-custom-model \\
  --description "직접 학습한 커스텀 모델"

# ─── HuggingFace 에서 가져오기 (관리자) ───
# 서버가 백그라운드로 내려받는다. 기본은 완료까지 기다리며, --no-wait 면 접수만 한다.
argus-model oci-import-hf bert-base-uncased --name my-bert \\
  --description "BERT 기본 모델"

# ─── OCI 모델 목록 ───
argus-model oci-list --search bert

# ─── 모델 삭제 (파일 포함, 소유자·생성자·관리자) ───
argus-model oci-delete my-custom-model`
  }
  if (tab === "sdk") {
    return `from argus_catalog_sdk import ModelClient

# token= 대신 username= / password= 를 주면 생성 시 로그인한다.
client = ModelClient("${server}", token="<ARGUS_TOKEN>")

# ─── 로컬 디렉터리를 새 모델 버전으로 push ───
result = client.push(
    "/path/to/my-model",
    "my-custom-model",
    description="직접 학습한 커스텀 모델",
)
print(f"v{result['version']} 푸시 완료: 파일 {result['file_count']}개")

# ─── HuggingFace 에서 가져오기 (관리자) ───
# wait=True(기본) 면 버전이 준비될 때까지 기다린 뒤 버전 정보를 돌려준다.
version = client.oci_import_huggingface(
    "bert-base-uncased",
    name="my-bert",
    description="BERT 기본 모델",
)
print(f"{version['name']} v{version['version']}: 파일 {version['file_count']}개")

# ─── OCI 모델 목록 ───
for m in client.oci_list_models(search="bert")["items"]:
    print(m["name"], f"v{m['version_count']}", m["status"])`
  }
  return `${tokenSetup(server)}

# ─── HuggingFace 에서 가져오기 (관리자) — 202 로 접수되고 백그라운드에서 진행 ───
curl -X POST "$ARGUS_SERVER/api/v1/oci-models/import/huggingface" \\
  -H "Authorization: Bearer $ARGUS_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "hf_model_id": "bert-base-uncased",
    "name": "my-bert",
    "description": "BERT 기본 모델"
  }'
# 응답: {"name": "my-bert", "version": 1, "status": "importing", ...}

# 진행 상태 확인 — 해당 버전의 status 가 ready 또는 failed 가 될 때까지
curl -H "Authorization: Bearer $ARGUS_TOKEN" "$ARGUS_SERVER/api/v1/oci-models/my-bert/versions"

# ─── 모델 직접 생성 (로그인 사용자, 생성자가 소유자가 됨) ───
curl -X POST "$ARGUS_SERVER/api/v1/oci-models" \\
  -H "Authorization: Bearer $ARGUS_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "my-custom-model", "description": "직접 만든 커스텀 모델", "task": "classification"}'

# ─── 파일 업로드 → 버전 확정 ───
curl -X POST "$ARGUS_SERVER/api/v1/model-store/my-custom-model/versions/1/upload" \\
  -H "Authorization: Bearer $ARGUS_TOKEN" \\
  -F "file=@model.onnx"
curl -X POST "$ARGUS_SERVER/api/v1/oci-models/my-custom-model/versions/1/finalize" \\
  -H "Authorization: Bearer $ARGUS_TOKEN"

# ─── README 갱신 (소유자·생성자·관리자) ───
curl -X PUT "$ARGUS_SERVER/api/v1/oci-models/my-custom-model/readme" \\
  -H "Authorization: Bearer $ARGUS_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"readme": "# my-custom-model\\n\\n분류 모델입니다."}'`
}

function usageGuide(tab: GuideTab, server: string, name = "my-bert", version = 1): string {
  if (tab === "cli") {
    return `# ─── CLI 설치 ───
pip install argus-catalog-sdk

${tokenSetup(server)}

# ─── 사용 가능한 모델 목록 ───
argus-model oci-list

# ─── 모델을 로컬 디렉터리로 다운로드 ───
argus-model pull ${name} ${version} /tmp/${name}

# ─── 버전의 파일 목록 / OCI manifest ───
argus-model files ${name} ${version}
argus-model manifest ${name} ${version}

# ─── 받은 모델 사용 (프레임워크에 맞게 로드) ───
python -c "
from transformers import AutoModel, AutoTokenizer
model = AutoModel.from_pretrained('/tmp/${name}')
tokenizer = AutoTokenizer.from_pretrained('/tmp/${name}')
print(model(**tokenizer('Hello world', return_tensors='pt')).last_hidden_state.shape)
"`
  }
  if (tab === "sdk") {
    return `from argus_catalog_sdk import ModelClient

client = ModelClient("${server}", token="<ARGUS_TOKEN>")

# ─── 모델·버전 확인 ───
model = client.oci_get_model("${name}")
ready = [v for v in client.oci_list_versions("${name}") if v["status"] == "ready"]
print(model["name"], "준비된 버전:", [v["version"] for v in ready])

# ─── 모델을 로컬 디렉터리로 다운로드 ───
files = client.pull("${name}", version=${version}, dest="/tmp/${name}")
print(f"파일 {len(files)}개를 /tmp/${name} 로 내려받음")

# ─── presigned 다운로드 URL 만 받기 (다른 도구와 연동) ───
urls = client.get_download_urls("${name}", version=${version})
for filename, url in urls["files"].items():
    print(filename, url)

# ─── 내려받은 모델 사용 ───
from transformers import AutoModel, AutoTokenizer
model = AutoModel.from_pretrained("/tmp/${name}")
tokenizer = AutoTokenizer.from_pretrained("/tmp/${name}")
print(model(**tokenizer("Hello world", return_tensors="pt")).last_hidden_state.shape)`
  }
  return `${tokenSetup(server)}

# ─── 모델 목록·상세·버전 ───
curl -H "Authorization: Bearer $ARGUS_TOKEN" "$ARGUS_SERVER/api/v1/oci-models?page=1&page_size=10"
curl -H "Authorization: Bearer $ARGUS_TOKEN" "$ARGUS_SERVER/api/v1/oci-models/${name}"
curl -H "Authorization: Bearer $ARGUS_TOKEN" "$ARGUS_SERVER/api/v1/oci-models/${name}/versions"

# ─── 버전의 모든 파일 presigned 다운로드 URL (다운로드 1회로 집계) ───
curl -H "Authorization: Bearer $ARGUS_TOKEN" \\
  "$ARGUS_SERVER/api/v1/model-store/${name}/versions/${version}/download-urls"
# 응답: {"files": {"config.json": "https://...", "model.safetensors": "https://..."}}

# ─── 파일 하나의 presigned URL ───
curl -H "Authorization: Bearer $ARGUS_TOKEN" \\
  "$ARGUS_SERVER/api/v1/model-store/${name}/versions/${version}/download-url?filename=config.json"
# 받은 URL 로 내려받기 (URL 자체에 서명이 있어 토큰 불필요)
curl -o config.json "<위에서 받은 presigned URL>"

# ─── OCI manifest ───
curl -H "Authorization: Bearer $ARGUS_TOKEN" \\
  "$ARGUS_SERVER/api/v1/model-store/${name}/versions/${version}/manifest"`
}

function GuideTabs({ tab, onTab }: { tab: GuideTab; onTab: (t: GuideTab) => void }) {
  return (
    <div className="flex items-center border rounded-md w-fit">
      {(Object.keys(TAB_LABELS) as GuideTab[]).map((t) => (
        <Button
          key={t}
          variant={tab === t ? "secondary" : "ghost"}
          size="sm"
          onClick={() => onTab(t)}
          className="rounded-none first:rounded-l-md last:rounded-r-md"
        >
          {TAB_LABELS[t]}
        </Button>
      ))}
    </div>
  )
}

export function OciGuideDialog({
  kind, open, onOpenChange,
}: { kind: "import" | "use"; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [tab, setTab] = useState<GuideTab>("cli")
  const server = currentServerUrl()
  const code = kind === "import" ? importGuide(tab, server) : usageGuide(tab, server)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{kind === "import" ? "모델 가져오기 방법" : "모델 사용 방법"}</DialogTitle>
          <DialogDescription>
            조회를 포함한 모든 API 호출에 로그인 토큰이 필요합니다. 예시의 서버 주소는 지금 접속한 주소입니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <GuideTabs tab={tab} onTab={setTab} />
          <CodeViewer
            language={tab === "sdk" ? "python" : "shell"}
            height={520}
            copyLabel={`${TAB_LABELS[tab]} ${kind === "import" ? "가져오기" : "사용법"}`}
            code={code}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** 프레임워크별 "받은 모델 쓰기" 예시. */
function loadSnippet(framework: string | null, dir: string): { code: string; note: string } {
  const fw = (framework ?? "").toLowerCase()
  if (fw.includes("sentence")) {
    return {
      note: "sentence-transformers 로 임베딩을 만듭니다.",
      code: `from sentence_transformers import SentenceTransformer

model = SentenceTransformer("${dir}")
vectors = model.encode(["모델 허브 사용 예시", "다른 문장"])
print(vectors.shape)`,
    }
  }
  if (fw.includes("onnx")) {
    return {
      note: "ONNX Runtime 으로 추론합니다. 입력 이름·형태는 session.get_inputs() 로 확인하세요.",
      code: `import glob
import onnxruntime as ort

path = glob.glob("${dir}/**/*.onnx", recursive=True)[0]
session = ort.InferenceSession(path)
print([(i.name, i.shape, i.type) for i in session.get_inputs()])
# outputs = session.run(None, {"<입력 이름>": <numpy 배열>})`,
    }
  }
  if (fw.includes("transformers") || fw.includes("pytorch") || fw === "") {
    return {
      note: "HuggingFace Transformers 형식이면 로컬 경로를 그대로 넘기면 됩니다.",
      code: `from transformers import AutoModel, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("${dir}")
model = AutoModel.from_pretrained("${dir}")
print(model(**tokenizer("안녕하세요", return_tensors="pt")).last_hidden_state.shape)`,
    }
  }
  return {
    note: `${framework} 프레임워크의 로더로 내려받은 디렉터리를 읽으세요.`,
    code: `import os
print(os.listdir("${dir}"))`,
  }
}

/** 상세 화면 사용법 탭 — 이 모델·최신 버전·프레임워크에 맞춘 3단계. */
export function OciUsageGuide({ name, version, framework }: { name: string; version: number; framework: string | null }) {
  const [via, setVia] = useState<"cli" | "sdk">("cli")
  const [restOpen, setRestOpen] = useState(false)
  const server = currentServerUrl()
  if (version <= 0) {
    return (
      <EmptyState
        icon={Code2}
        text="아직 준비된 버전이 없습니다. 파일을 push 하거나 가져오기가 끝나면 사용 예시가 표시됩니다."
      />
    )
  }
  const dir = `./models/${name}`
  const load = loadSnippet(framework, dir)
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          <strong className="text-foreground">{name}</strong> v{version} 을(를) 내려받아 쓰는 방법입니다.
        </p>
        <SegmentedToggle
          value={via}
          onChange={setVia}
          options={[{ value: "cli", label: "CLI" }, { value: "sdk", label: "Python SDK" }]}
        />
      </div>
      <UsageSteps
        steps={[
          {
            title: "설치하고 로그인 토큰 지정",
            description: "조회·다운로드 주소 발급을 포함한 모든 API 호출에 로그인 토큰이 필요합니다. 토큰은 로그인 API 또는 웹 세션에서 발급받습니다.",
            language: "bash",
            code: `pip install argus-catalog-sdk
export ARGUS_SERVER=${server}
export ARGUS_TOKEN=<액세스 토큰>`,
          },
          via === "cli"
            ? {
                title: "모델 내려받기",
                description: "버전의 모든 파일을 지정한 디렉터리로 받습니다.",
                language: "bash",
                code: `argus-model pull ${name} ${version} ${dir}`,
              }
            : {
                title: "모델 내려받기",
                description: "환경 변수의 토큰을 써서 버전의 모든 파일을 받습니다.",
                language: "python",
                code: `import os
from argus_catalog_sdk import ModelClient

client = ModelClient(os.environ["ARGUS_SERVER"], token=os.environ["ARGUS_TOKEN"])
files = client.pull("${name}", version=${version}, dest="${dir}")
print(f"{len(files)}개 파일")`,
              },
          { title: "모델 사용", description: load.note, language: "python", code: load.code },
        ]}
      />
      <div className="rounded-md border">
        <button
          type="button"
          className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium"
          onClick={() => setRestOpen((o) => !o)}
          aria-expanded={restOpen}
        >
          REST API 로 직접 호출하기
          <span className="text-xs text-muted-foreground">{restOpen ? "접기" : "펼치기"}</span>
        </button>
        {restOpen && (
          <div className="border-t p-4">
            <CodeSnippet language="bash" code={`curl -H "Authorization: Bearer $ARGUS_TOKEN" \\
  "$ARGUS_SERVER/api/v1/model-store/${name}/versions/${version}/download-urls"
# 응답의 files 에 파일별 presigned URL 이 있습니다 (URL 자체에 서명이 있어 토큰 없이 내려받기 가능)`} />
          </div>
        )}
      </div>
    </div>
  )
}
