# Argus Catalog SDK

Argus Catalog Model Registry를 위한 Python SDK 및 CLI 도구입니다.
**OCI 기반 모델 저장소** (S3/MinIO)의 모델 관리를 담당합니다.

> **MLflow와의 관계:** MLflow 모델 등록/추론은 MLflow 자체 클라이언트(`mlflow` 패키지)를 사용합니다.
> 이 SDK는 OCI 모델 저장소 관리 (업로드, 다운로드, HuggingFace 임포트, Airgap 전송)에 사용됩니다.

### SDK 적용 범위

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

```bash
pip install argus-catalog-sdk
```

HuggingFace 임포트를 사용하려면:

```bash
pip install argus-catalog-sdk[huggingface]
```

개발 환경:

```bash
cd sdk
pip install -e ".[dev]"
```

## Python SDK

### 기본 사용

```python
from argus_catalog_sdk import ModelClient

client = ModelClient("http://catalog-server:4600")
```

### 모델 목록 조회

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

```python
model = client.create_model(
    name="argus.ml.iris_classifier",
    description="Iris flower classifier",
    owner="ml-team",
)
print(f"Created: {model['name']}")
```

### 모델 조회

```python
model = client.get_model("argus.ml.iris_classifier")
print(f"Name: {model['name']}")
print(f"Versions: {model['max_version_number']}")
print(f"Owner: {model['owner']}")
```

### 파일 업로드

모델 파일을 S3(MinIO)에 업로드합니다.

```python
# 직접 업로드 (소형 파일)
client.upload_file("argus.ml.iris", version=1, filepath="/path/to/model.pkl")

# Presigned URL 업로드 (대형 파일 — 서버 우회, MinIO 직접 전송)
client.upload_via_presigned("argus.ml.bert", version=1, filepath="/path/to/model.safetensors")
```

### 파일 다운로드 / Pull

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

로컬에 있는 모델 파일들을 새 버전으로 업로드합니다.

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

HuggingFace Hub에서 모델을 다운로드하여 S3에 저장합니다.

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

서버가 접근 가능한 로컬 디렉토리에서 S3로 임포트합니다.
USB 등으로 파일을 전송한 후 사용합니다.

```python
result = client.import_local(
    local_dir="/data/transferred/bert-model",
    model_name="argus.ml.bert",
    description="BERT (airgap import)",
)
print(f"Imported v{result['version']}: {result['file_count']} files")
```

### 파일 목록 조회

```python
files = client.list_files("argus.ml.iris", version=1)
for f in files:
    print(f"{f['filename']:30s} {f['size']:>10,} bytes")
```

### OCI Manifest 조회

```python
manifest = client.get_manifest("argus.ml.iris", version=1)
print(f"Layers: {len(manifest['layers'])}")
for layer in manifest["layers"]:
    title = layer["annotations"]["org.opencontainers.image.title"]
    print(f"  {title}: {layer['size']:,} bytes ({layer['digest'][:20]}...)")
```

### Finalize

업로드 완료 후 manifest를 생성하고 버전을 확정합니다.

```python
result = client.finalize(
    "argus.ml.custom",
    version=1,
    annotations={"ai.argus.model.task": "classification"},
)
print(f"Status: {result['status']}, Files: {result['file_count']}")
```

### 모델 삭제

```python
# Soft delete (목록에서 숨김)
client.delete_model("argus.ml.old_model")

# Hard delete (DB + S3/디스크 완전 삭제)
result = client.hard_delete_models(["argus.ml.old_model", "argus.ml.test"])
print(f"Deleted: {result['deleted']}")
```

## CLI (`argus-model`)

### 모델 목록

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

인터넷이 없는 폐쇄망에서 HuggingFace 모델을 사용하는 전체 흐름:

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

| 변수 | 설명 | 기본값 |
|------|------|--------|
| (없음) | 모든 설정은 `--server` 플래그로 지정 | `http://localhost:4600` |

향후 `ARGUS_CATALOG_SERVER` 환경 변수 지원 예정.

## 의존성

| 패키지 | 용도 |
|--------|------|
| `httpx` | HTTP 클라이언트 (동기/비동기) |
| `rich` | CLI 테이블 출력, 색상 |
| `huggingface_hub` (optional) | HuggingFace 모델 다운로드 |
