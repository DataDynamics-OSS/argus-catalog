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
| 모델 파일 Push/Pull (S3) | **Argus SDK** (`client.push()`, `client.pull()`) | `/api/v1/model-store/*` |
| Airgap 환경 모델 전송 | **Argus SDK** (`client.import_local()`) | `/api/v1/model-store/*` |
| 모델 목록 조회 / 검색 | **Argus SDK** (`client.list_models()`) | `/api/v1/models` |
| 모델 삭제 (DB + S3/디스크) | **Argus SDK** (`client.hard_delete_models()`) | `/api/v1/models/hard-delete` |
| OCI Manifest 조회 | **Argus SDK** (`client.get_manifest()`) | `/api/v1/model-store/*` |

> MLflow로 등록한 모델도 SDK의 `list_models()`, `get_model()`, `hard_delete_models()`로 조회/삭제가 가능합니다.
> 단, MLflow 모델의 아티팩트는 로컬 디스크(`file://`)에 저장되므로 SDK의 `pull()`로는 다운로드할 수 없습니다.

## 설치

먼저 SDK부터 설치해 봅시다. 기본 기능은 한 줄이면 충분합니다.

```bash
pip install argus-catalog-sdk
```

HuggingFace 임포트까지 쓰려면 관련 의존성을 함께 받아야 합니다.

```bash
pip install argus-catalog-sdk[huggingface]
```

SDK 자체를 고치거나 기여하려는 개발 환경이라면 편집 가능(editable) 모드로 설치합니다.

```bash
cd sdk
pip install -e ".[dev]"
```

## Python SDK

### 기본 사용

모든 작업은 `ModelClient` 하나에서 출발합니다. 카탈로그 서버 주소만 알려 주면, 이 클라이언트가 나머지 호출을 대신 처리해 줍니다.

```python
from argus_catalog_sdk import ModelClient

client = ModelClient("http://catalog-server:4600")
```

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
    model_name="argus.ml.custom_model",
    description="My custom model",
    owner="data-team",
)
print(f"Pushed v{result['version']}: {result['file_count']} files")
```

### HuggingFace 임포트

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

### HuggingFace 임포트

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

## 환경 변수

설정과 관련해서는 아직 단출합니다. 지금은 서버 주소를 `--server` 플래그로 직접 넘기는 방식 하나뿐입니다.

| 변수 | 설명 | 기본값 |
|------|------|--------|
| (없음) | 모든 설정은 `--server` 플래그로 지정 | `http://localhost:4600` |

향후 `ARGUS_CATALOG_SERVER` 환경 변수 지원 예정.

## 의존성

끝으로, 이 SDK가 어떤 라이브러리 위에서 동작하는지 정리해 둡니다. HuggingFace 임포트를 쓰지 않는다면 마지막 항목은 선택 사항입니다.

| 패키지 | 용도 |
|--------|------|
| `httpx` | HTTP 클라이언트 (동기/비동기) |
| `rich` | CLI 테이블 출력, 색상 |
| `huggingface_hub` (optional) | HuggingFace 모델 다운로드 |
