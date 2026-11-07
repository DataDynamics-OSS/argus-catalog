# Argus Catalog SDK

Argus Catalog Model Registry를 코드로 다루고 싶을 때 손에 잡는 도구가 바로 이 Python SDK와 CLI입니다.
**OCI 기반 모델 저장소** (S3/MinIO)에 모델을 올리고 내리고 정리하는 일을 이 SDK가 맡습니다.

> **MLflow와의 관계:** 헷갈리기 쉬운 부분부터 짚고 가겠습니다. MLflow로 모델을 등록하거나 추론할 때는 MLflow 자체 클라이언트(`mlflow` 패키지)를 그대로 쓰면 됩니다.
> 이 SDK는 그와는 역할이 다릅니다. OCI 모델 저장소를 관리하는 일 — 업로드, 다운로드, HuggingFace 임포트, Airgap 전송 — 을 담당합니다.

### SDK 적용 범위

그럼 어떤 작업에 무엇을 써야 할까요? 아래 표가 그 경계를 한눈에 보여 줍니다. 같은 모델을 다루더라도 학습·추론 쪽은 MLflow 클라이언트가, 저장소 관리 쪽은 Argus SDK가 맡는다는 점을 기억해 두면 헷갈릴 일이 없습니다.

| 기능 | 사용 도구 | API |
|------|----------|-----|
| sklearn/pytorch 학습 → 모델 등록 | **MLflow 클라이언트** (`mlflow.sklearn.log_model()`) | `/api/2.1/unity-catalog/*` |
| 등록된 MLflow 모델로 추론 | **MLflow 클라이언트** (`mlflow.pyfunc.load_model()`) | `/api/2.1/unity-catalog/*` |
| HuggingFace 모델 → S3 저장 | **Argus SDK** (`client.import_huggingface()`) | `/api/v1/model-store/*` |
| 모델 파일 Push/Pull (S3) | **Argus SDK** (`client.push()`, `client.pull()`) | `/api/v1/oci-models/*` (모델·버전 확정), `/api/v1/model-store/*` (파일) |
| Airgap 환경 모델 전송 | **Argus SDK** (`client.import_local()`) | `/api/v1/model-store/*` |
| 모델 목록 조회 / 검색 | **Argus SDK** (`client.list_models()`) | `/api/v1/models` |
| 모델 삭제 (DB + S3/디스크) | **Argus SDK** (`client.hard_delete_models()`) | `/api/v1/models/hard-delete` |
| OCI Manifest 조회 | **Argus SDK** (`client.get_manifest()`) | `/api/v1/model-store/*` |

> MLflow로 등록한 모델도 SDK의 `list_models()`, `get_model()`, `hard_delete_models()`로 조회/삭제가 가능합니다.
> MLflow 클라이언트로 기록한 모델의 파일은 저장소(S3)가 아니라 서버 디스크에 있습니다. `list_files()`·`pull()` 은 저장소에 파일이 없으면
> 서버의 MLflow 모델 파일 브라우저 API 로 찾아 같은 방식(하위 경로 유지)으로 보여 주고 받습니다(등록된 모델 폴더 안만 — OCI 모델과 같은 규칙). `get_manifest()` 는 저장소 버전 전용입니다.

## 설치

먼저 SDK부터 설치해 봅시다. 기본 기능은 한 줄이면 충분합니다.

```bash
pip install argus-catalog-sdk
```

HuggingFace 가져오기는 **서버가** 받아 저장소에 넣으므로 클라이언트에 추가로 설치할 것은 없습니다.

SDK 자체를 고치거나 기여하려는 개발 환경이라면 편집 가능(editable) 모드로 설치합니다.

```bash
cd sdk
pip install -e ".[dev]"
pytest          # sdk/tests — 가짜 서버(httpx.MockTransport)로 push·pull·오류 처리·정의 파일 수집 검증
```

`agent-apply`·`agent-export` 의 서버 연동 종단 테스트는 백엔드 쪽 `backend/tests/test_agent_cli.py` 에 있습니다.

## Python SDK

### 기본 사용

모든 작업은 `ModelClient` 하나에서 출발합니다. 카탈로그 서버 주소만 알려 주면, 이 클라이언트가 나머지 호출을 대신 처리해 줍니다.

```python
import httpx
from argus_catalog_sdk import ModelClient

# 조회를 포함한 모든 호출에 로그인이 필요합니다(인증 정보가 없으면 401)
client = ModelClient("http://catalog-server:4600", token="<액세스 토큰>")
client = ModelClient("http://catalog-server:4600", username="mjkim", password="...")  # 생성 시 로그인

# 클라이언트는 HTTP 연결을 재사용합니다. 다 쓰면 close() 하거나 with 블록으로 쓰세요.
with ModelClient("http://catalog-server:4600", token="<액세스 토큰>") as client:
    client.pull("my-model", 1, "/tmp/my-model")

# 프록시·사설 인증서 등은 직접 만든 httpx.Client 를 넘깁니다(이때는 SDK 가 닫지 않음).
client = ModelClient("https://catalog.corp", token="...", http_client=httpx.Client(verify="/etc/ssl/corp-ca.pem"))
```

토큰이 없으면 조회를 포함한 모든 호출을 서버가 401 로 거부합니다. 기존 모델에 대한 변경은 그 모델의 소유자·생성자·관리자만 할 수 있습니다(그 외 403).

### 모델 목록 조회

저장소에 어떤 모델이 있는지 둘러보는 일부터 시작해 봅시다. 전체를 한 번에 볼 수도, 키워드로 검색하거나 페이지 단위로 끊어 볼 수도 있습니다.

```python
# 전체 목록
result = client.list_models()
for model in result["items"]:
    print(f"{model['name']} v{model['max_version_number']} - {model['latest_version_status']}")

# 검색
result = client.list_models(search="iris")

# 페이지네이션
result = client.list_models(page=2, page_size=10)
```

### 모델 등록

새 모델을 카탈로그에 올리려면 먼저 이름표를 만들어 주어야 합니다. 이 단계는 모델의 '자리'를 잡아 두는 과정이라고 보면 됩니다.

```python
model = client.create_model(
    name="argus.ml.iris_classifier",
    description="Iris flower classifier",
    owner="ml-team",
)
print(f"Created: {model['name']}")
```

### 모델 조회

이름만 알면 특정 모델의 상세 정보를 곧장 꺼내 볼 수 있습니다.

```python
model = client.get_model("argus.ml.iris_classifier")
print(f"Name: {model['name']}")
print(f"Versions: {model['max_version_number']}")
print(f"Owner: {model['owner']}")
```

### 파일 업로드

이제 실제 모델 파일을 S3(MinIO)에 올려 봅시다. 파일이 작으면 직접 업로드로 충분하지만, 용량이 큰 가중치 파일이라면 서버를 거치지 않고 MinIO로 곧장 전송하는 Presigned URL 방식이 훨씬 빠릅니다.

```python
# 직접 업로드 (소형 파일)
client.upload_file("argus.ml.iris", version=1, filepath="/path/to/model.pkl")

# Presigned URL 업로드 (대형 파일 — 서버 우회, MinIO 직접 전송)
client.upload_via_presigned("argus.ml.bert", version=1, filepath="/path/to/model.safetensors")
```

### 파일 다운로드 / Pull

올렸으면 다시 받아 와야겠죠. 파일 하나만 골라 받을 수도 있고, 한 버전에 속한 파일을 통째로 로컬 디렉토리에 내려받는 Pull도 있습니다.

```python
# 단일 파일 다운로드 URL
url_info = client.get_download_url("argus.ml.iris", version=1, filename="model.pkl")
print(url_info["url"])  # Presigned URL

# 전체 파일 URL 일괄 발급
urls = client.get_download_urls("argus.ml.iris", version=1)
for filename, url in urls["files"].items():
    print(f"{filename}: {url}")

# Pull — 모든 파일을 로컬 디렉토리에 다운로드
downloaded = client.pull("argus.ml.iris", version=1, dest="/tmp/iris-model")
print(f"Downloaded {len(downloaded)} files")
# → ['/tmp/iris-model/model.pkl', '/tmp/iris-model/MLmodel', ...]
```

### Push — 로컬 디렉토리를 모델로 등록

Pull의 반대 방향입니다. 로컬 디렉토리에 모여 있는 모델 파일들을 통째로 새 버전으로 올려 줍니다.

```python
result = client.push(
    local_dir="/path/to/my-model",
    model_name="my-custom-model",
    description="My custom model",
    owner="data-team",
)
print(f"Pushed v{result['version']}: {result['file_count']} files")
```

`push()` 는 **OCI 모델 허브**에 올립니다. 모델이 없으면 로그인한 사용자를 소유자로 만들고, 있으면 다음 버전으로 추가합니다.

- 하위 디렉토리 구조를 그대로 올립니다(`onnx/model.onnx` → 버전 안의 `onnx/model.onnx`). `pull()` 로 받으면 같은 구조로 복원됩니다.
- 숨김 경로(어느 단계든 `.` 으로 시작: `.git/`, `.gitattributes`, `sub/.DS_Store`)는 올리지 않습니다. 올릴 파일이 없으면 `ValueError`.
- 이전 push 가 확정 전에 끊겨 저장소에 파일만 남은 버전 번호는, 남은 파일이 `stale_after` 초(기본 24시간)보다 오래됐으면
  지운 뒤 그 번호를 다시 쓰고(결과 `cleaned_versions`), 최근 것이면 진행 중인 다른 push 일 수 있어 건너뛰고 다음 번호를 씁니다.
  어느 쪽이든 남은 파일이 새 버전에 섞이지 않습니다. 직접 지우려면 `delete_unfinalized_files(name, version)`
  (버전 기록이 있는 번호는 409). 정리 API 가 없는 이전 서버에서는 예전처럼 건너뜁니다.
- 같은 이름의 MLflow 모델이 있으면 `ApiError(409)` 로 멈춥니다.
- 기본은 모든 파일을 서버를 거쳐 올립니다. 서버는 업로드 파일을 메모리에 다 읽으므로, 큰 가중치 파일이 있으면
  `presigned_min_size=100 * 1024**2` 처럼 기준 크기를 주어 그 이상은 presigned URL 로 저장소에 직접 올리세요
  (클라이언트가 서버의 외부용 저장소 주소에 닿아야 함).

`pull()` 은 파일을 조각 단위로 디스크에 바로 써서 대용량도 메모리에 다 올리지 않습니다(받는 동안은 `<파일>.part`). 서버가 준 파일 이름이 `dest` 밖(`..`, 절대 경로)을 가리키면 아무것도 받지 않고 `ValueError` 를 냅니다.
MLflow 클라이언트로 기록한 모델은 서버 디스크에서 받습니다(로그인 사용자 누구나, 그 모델 폴더 안만).

### 오류 처리

서버가 4xx/5xx 로 응답하면 `ApiError` 가 납니다. `RuntimeError` 를 상속하므로 기존 `except RuntimeError` 도 그대로 잡힙니다.

```python
from argus_catalog_sdk import ApiError

try:
    client.oci_get_model("unknown")
except ApiError as e:
    if e.status_code == 404:
        print("없는 모델:", e.detail)
```

### OCI 모델 허브

```python
# 목록·상세·버전 (버전 status: ready / importing / failed)
page = client.oci_list_models(search="bert", task="fill-mask")
model = client.oci_get_model("my-bert")
versions = client.oci_list_versions("my-bert")

# HuggingFace → OCI 허브 (관리자). 서버는 202 로 접수하고 백그라운드에서 받는다.
# wait=True(기본)면 버전이 ready 가 될 때까지 기다리고, failed 면 RuntimeError.
v = client.oci_import_huggingface("prajjwal1/bert-tiny", name="bert-tiny", description="작은 BERT")
print(v["version"], v["file_count"], v["content_digest"])

accepted = client.oci_import_huggingface("bert-base-uncased", wait=False)  # 접수만

# 삭제 (DB + S3 파일, 소유자·생성자·관리자)
client.oci_delete_model("bert-tiny")
```

### HuggingFace 임포트 (MLflow 레지스트리)

아래 `import_huggingface()` 는 MLflow 등록 모델로 가져옵니다. OCI 허브로 가져오려면 위의 `oci_import_huggingface()` 를 쓰세요.

직접 파일을 들고 있지 않아도 됩니다. 모델 ID만 알려 주면 HuggingFace Hub에서 모델을 받아 곧장 S3에 저장해 줍니다.

```python
result = client.import_huggingface(
    hf_model_id="bert-base-uncased",
    model_name="argus.ml.bert",
    revision="main",
    description="BERT base uncased",
    owner="nlp-team",
)
print(f"Imported v{result['version']}: {result['file_count']} files, {result['total_size']:,} bytes")
print(f"Storage: {result['storage_location']}")
```

### 로컬 Import (Airgap)

인터넷이 닿지 않는 폐쇄망이라면 HuggingFace에서 바로 받아 올 수 없습니다. 이럴 때는 USB 같은 매체로 파일을 옮겨 둔 뒤, 서버가 접근 가능한 로컬 디렉토리에서 S3로 임포트하면 됩니다.

```python
result = client.import_local(
    local_dir="/data/transferred/bert-model",
    model_name="argus.ml.bert",
    description="BERT (airgap import)",
)
print(f"Imported v{result['version']}: {result['file_count']} files")
```

### 파일 목록 조회

특정 모델 버전 안에 어떤 파일들이 들어 있는지 궁금하다면 다음처럼 확인할 수 있습니다.

```python
files = client.list_files("argus.ml.iris", version=1)
for f in files:
    print(f"{f['filename']:30s} {f['size']:>10,} bytes")
```

### OCI Manifest 조회

모델이 OCI 형식으로 어떻게 묶여 있는지, 즉 어떤 레이어들로 구성됐는지 들여다보고 싶을 때 쓰는 조회입니다.

```python
manifest = client.get_manifest("argus.ml.iris", version=1)
print(f"Layers: {len(manifest['layers'])}")
for layer in manifest["layers"]:
    title = layer["annotations"]["org.opencontainers.image.title"]
    print(f"  {title}: {layer['size']:,} bytes ({layer['digest'][:20]}...)")
```

### Finalize

파일을 다 올렸다고 끝이 아닙니다. 마지막으로 manifest를 만들고 버전을 확정하는 마무리 단계를 거쳐야 비로소 그 버전이 완성됩니다.

확정 대상은 MLflow 등록 모델의 **이미 만든 버전**입니다(`POST /api/v1/models/{name}/versions`). 확정하면 버전이 READY 가 되고 파일 수·크기와 MLmodel 메타데이터가 카탈로그에 기록됩니다. 모델이나 버전이 없으면 404 가 납니다(OCI 허브 모델은 `push()` 사용).

```python
result = client.finalize(
    "argus.ml.custom",
    version=1,
    annotations={"ai.argus.model.task": "classification"},
)
print(f"Status: {result['status']}, Files: {result['file_count']}")
```

### 모델 삭제

삭제에는 두 단계가 있습니다. Soft delete는 목록에서 잠시 가려 두는 것이고, Hard delete는 DB와 S3·디스크에서 실제로 지워 되돌릴 수 없습니다. 신중하게 골라 쓰세요.

```python
# Soft delete (목록에서 숨김)
client.delete_model("argus.ml.old_model")

# Hard delete (DB + S3/디스크 완전 삭제)
result = client.hard_delete_models(["argus.ml.old_model", "argus.ml.test"])
print(f"Deleted: {result['deleted']}")
```

## CLI (`argus-model`)

Python 코드를 짤 필요 없이 터미널에서 같은 일을 하고 싶을 때는 `argus-model` 명령을 쓰면 됩니다. 앞서 본 SDK 기능 대부분이 명령어 한 줄로 그대로 대응됩니다.

### 인증

모든 명령은 `--server`, `--token` 또는 `--username`/`--password` 를 받습니다. 환경 변수로 지정해 두면 편합니다.

```bash
export ARGUS_SERVER=http://catalog:4600
export ARGUS_TOKEN=<액세스 토큰>        # 또는 ARGUS_USERNAME / ARGUS_PASSWORD
```

### OCI 모델 허브

```bash
argus-model oci-list --search bert
argus-model push /path/to/model my-model --description "직접 학습한 모델"   # 로그인 필요
argus-model push /path/to/llm my-llm --task text-generation --framework pytorch --presigned-min-mb 100
                                                                           # 100MB 이상은 저장소 직접 업로드
argus-model oci-import-hf prajjwal1/bert-tiny --name bert-tiny             # 관리자, 완료까지 대기
argus-model oci-import-hf org/model --task fill-mask --framework pytorch --language ko --owner ml-team
argus-model oci-import-hf bert-base-uncased --no-wait                      # 접수만
argus-model oci-delete bert-tiny                                           # 이름을 다시 입력해 확인 (-y 로 생략)
```

### 모델 목록

가장 먼저 써 보게 될 명령입니다. 저장소에 등록된 모델을 표로 보여 주며, 검색이나 페이지 옵션도 그대로 쓸 수 있습니다.

```bash
argus-model list
argus-model list --search iris
argus-model list --server http://catalog:4600 --page 2 --page-size 10
```

출력 예시:

```
                          Models (6 total)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┓
┃ Name                        ┃ Owner   ┃ Ver ┃ Status ┃ Updated    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━╇━━━━━━━━╇━━━━━━━━━━━━┩
│ argus.ml.iris_classifier    │ -       │ v2  │ READY  │ 2026-03-20 │
│ argus.ml.bert               │ nlp-team│ v1  │ READY  │ 2026-03-22 │
│ iris_rf_model               │ ml-team │ v1  │ READY  │ 2026-03-20 │
└─────────────────────────────┴─────────┴─────┴────────┴────────────┘
```

### 모델 Pull

```bash
# 모든 파일을 로컬에 다운로드
argus-model pull argus.ml.iris_classifier 1 /tmp/iris-model
```

### 모델 Push

```bash
# 로컬 디렉토리를 새 모델 버전으로 업로드
argus-model push /path/to/model argus.ml.custom --description "My custom model"
```

### HuggingFace 임포트 (MLflow 레지스트리)

```bash
argus-model import-hf bert-base-uncased argus.ml.bert
argus-model import-hf bert-base-uncased argus.ml.bert --revision main --description "BERT base"
```

### Airgap Import

```bash
# 서버가 접근 가능한 경로에서 import
argus-model import-local /data/transferred/bert argus.ml.bert
```

### 파일 목록

```bash
argus-model files argus.ml.iris_classifier 1
```

출력 예시:

```
          argus.ml.iris_classifier v1 files
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Filename           ┃      Size ┃ Modified            ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ MLmodel            │   0.5 KB  │ 2026-03-20T19:23:41 │
│ conda.yaml         │   0.2 KB  │ 2026-03-20T19:23:41 │
│ model.pkl          │ 173.1 KB  │ 2026-03-20T19:23:41 │
│ python_env.yaml    │   0.1 KB  │ 2026-03-20T19:23:41 │
│ requirements.txt   │   0.1 KB  │ 2026-03-20T19:23:41 │
└────────────────────┴───────────┴─────────────────────┘
```

### OCI Manifest 조회

```bash
argus-model manifest argus.ml.iris_classifier 1
```

### 모델 삭제

```bash
# 확인 프롬프트 표시 ('모델 삭제' 입력 필요)
argus-model delete argus.ml.old_model argus.ml.test

# 확인 없이 삭제
argus-model delete argus.ml.old_model -y
```

확인 문구를 다르게 입력해 취소하면(`delete`·`oci-delete` 모두) 아무것도 지우지 않고 종료 코드 1 로 끝납니다.

## Airgap 워크플로우

지금까지 본 명령들을 어떻게 엮어 쓰는지, 실제 시나리오 하나로 정리해 보겠습니다. 인터넷이 닿는 곳에서 모델을 받아 폐쇄망으로 옮겨 쓰는 전체 흐름은 다음과 같습니다. 온라인 환경에서 임포트·Pull로 파일을 꺼내고, 매체로 옮긴 뒤, Airgap 환경에서 다시 import하는 순서입니다.

```bash
# ──── 인터넷 환경 ────

# 1. HuggingFace에서 모델 임포트 (온라인 Catalog Server)
argus-model import-hf bert-base-uncased argus.ml.bert \
  --server http://online-catalog:4600

# 2. 모델을 로컬 디렉토리로 Pull
argus-model pull argus.ml.bert 1 /usb/bert-export/ \
  --server http://online-catalog:4600

# 3. USB 또는 SCP로 파일 전송
scp -r /usb/bert-export/ airgap-server:/data/transferred/bert/

# ──── Airgap 환경 ────

# 4. 전송된 파일을 Airgap Catalog Server에 import
argus-model import-local /data/transferred/bert argus.ml.bert \
  --server http://airgap-catalog:4600

# 5. 확인
argus-model files argus.ml.bert 1 --server http://airgap-catalog:4600
```

## AI 에이전트 정의 파일 (`argus-catalog`)

에이전트 정의를 파일(`agent.yaml`)로 소스 저장소에 두고 명령줄에서 카탈로그에 반영합니다. `argus-catalog` 는 `argus-model` 과
같은 프로그램이며(인증·환경 변수 동일), 에이전트 명령은 어느 이름으로 실행해도 됩니다.
정의 파일 형식은 `design/ai-agent-bulk-registration-design.md` §3 을 참고하세요.

```bash
# 미리 보기 — 디렉터리 안(하위 포함) *.yaml·*.yml 전부, 바뀔 내용만 출력
argus-catalog agent-apply -f agents/ --dry-run

# 반영 (여러 위치 가능: -f a/ -f b.yaml)
argus-catalog agent-apply -f agents/

# 반영 후, agents/ 에서 관리되던 에이전트 중 이번 파일에 없는 것을 사용 중단(deprecated)으로 — 삭제는 하지 않음
argus-catalog agent-apply -f agents/ --prune

# 현재 카탈로그 내용을 정의 파일로 내보내기 (화면에서 등록한 에이전트를 파일 관리로 옮길 때)
argus-catalog agent-export cs.refund-helper > agents/cs.refund-helper.yaml
argus-catalog agent-export --department 고객지원팀 -o agents/      # 에이전트별 <이름>.yaml
```

- **지시문 파일**: `spec.release.systemPromptFile: prompts/refund.md` 처럼 쓰면 정의 파일 기준 상대 경로의 파일 내용을
  `systemPrompt` 로 넣어 보냅니다(둘을 함께 쓰면 오류).
- **정의 파일 위치**: 에이전트마다 `<저장소>#<저장소 안 경로>` 가 기록되어 상세 화면에 보이고, `--prune` 범위를 정하는 데 쓰입니다.
  `<저장소>` 는 `--source` 값, 없으면 git 원격 주소(`origin`), git 저장소가 아니면 `file:<현재 위치>` 입니다.
  주소에 든 사용자·토큰(`https://x-access-token:…@github.com/…`)은 빼고 기록합니다.
  같은 에이전트를 늘 같은 방식(같은 `--source`)으로 반영하세요.
- **정리(`--prune`) 안전장치**: 정의 파일을 하나라도 읽지 못하면 정리를 건너뜁니다. 권한이 없는 대상은 '건너뜀'으로만 표시됩니다.
- **종료 코드**: 실패·파일 오류·정리 건너뜀(권한)이 하나라도 있으면 1 — 배포 파이프라인에서 단계 실패로 잡힙니다.
- 200개가 넘는 파일도 200개씩 나눠 보냅니다. `-v` 를 주면 '변경 없음'도 결과 표에 나옵니다.

배포 파이프라인 예(병합 요청 때 미리 보기, 기본 브랜치에 병합되면 반영):

```yaml
# .gitlab-ci.yml
agents-plan:
  rules: [{ if: $CI_PIPELINE_SOURCE == "merge_request_event" }]
  script: argus-catalog agent-apply -f agents/ --prune --dry-run
agents-apply:
  rules: [{ if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH }]
  script: argus-catalog agent-apply -f agents/ --prune
# ARGUS_SERVER, ARGUS_USERNAME/ARGUS_PASSWORD(서비스 계정) 는 파이프라인 비밀 변수로
```

## 환경 변수

CLI 는 다음 환경 변수를 읽습니다. 같은 이름의 플래그가 우선합니다.

| 변수 | 플래그 | 설명 | 기본값 |
|------|------|------|--------|
| `ARGUS_SERVER` | `--server` | 카탈로그 서버 주소 | `http://localhost:4600` |
| `ARGUS_TOKEN` | `--token` | 액세스 토큰 (Bearer) | — |
| `ARGUS_USERNAME` / `ARGUS_PASSWORD` | `--username` / `--password` | 토큰 대신 로그인 | — |

## 의존성

끝으로, 이 SDK가 어떤 라이브러리 위에서 동작하는지 정리해 둡니다.

| 패키지 | 용도 |
|--------|------|
| `PyYAML` | 에이전트 정의 파일 읽기 |
| `httpx` | HTTP 클라이언트 (동기/비동기) |
| `rich` | CLI 테이블 출력, 색상 |
