# Argus Catalog

[![CI](https://github.com/DataDynamics-OSS/argus-catalog/actions/workflows/ci.yml/badge.svg)](https://github.com/DataDynamics-OSS/argus-catalog/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Argus Catalog** 는 DataHub 스타일의 **데이터 카탈로그**와 Unity Catalog OSS 호환
**ML 모델 레지스트리**를 하나로 묶은 오픈소스 메타데이터 플랫폼입니다. 데이터·모델·API·
AI 에이전트를 **하나의 카탈로그에서 거버넌스**하며, **에어갭(폐쇄망) 환경을 적극 지원**합니다.

- **데이터 카탈로그** — 데이터셋·스키마·리니지·데이터 품질·표준·용어집과
  **임베딩 기반 하이브리드 검색**(pgvector — 키워드 + 시맨틱)
- **메타데이터 거버넌스** — OpenAPI 스펙(**API 카탈로그**)과 **AI Agent 카탈로그**(도구/MCP·평가·미터링)
- **카탈로그 페더레이션** — 여러 Argus 인스턴스를 **하나로 연합**해 통합 검색·탐색,
  HARVEST 미러·재임베딩(에어갭 친화), cross-instance 리니지, **로컬 승격(import)**
- **ML 모델 레지스트리** — **MLflow · OCI 호환**, 버전/Stage 관리, 모델 카드, OCI 모델 허브
- **모델 반입 SDK** — 외부(HuggingFace 등) 모델을 **에어갭 사내망으로 반입**하는
  Python SDK + `argus-model` CLI (OCI Airgap 임포트)
- **AI** — LLM(OpenAI/Anthropic/Ollama) 기반 메타데이터 자동 생성과 tool-use AI 어시스턴트

![데이터셋의 메타데이터](docs/modules/ROOT/assets/images/index/dataset.png?w=300 "데이터셋의 메타데이터")

![데이터셋의 PySpark 코드](docs/modules/ROOT/assets/images/index/pyspark.png?w=300 "데이터셋의 PySpark 코드")

이 모든 기능을 단일 저장소에서 제공합니다.

## 주요 기능

- **데이터 카탈로그**
  - 데이터셋 관리
    - 등록 · 검색 · 태그 · 소유자(Technical/Business/Data Steward)
    - 11종 데이터 소스 메타데이터 동기화
  - 리니지 & ERD
    - 이기종 시스템 간 컬럼 수준 데이터 흐름 추적
    - DDL 파싱 기반 ER 다이어그램(Mermaid 내보내기)
  - 데이터 표준 & 용어집
    - 멀티 표준 사전 · 형태소 분석 기반 용어 자동 생성
    - 준수율 측정 · 트리 분류 용어집
  - 시맨틱 검색
    - pgvector 기반 키워드 + 시맨틱 하이브리드 검색
- **데이터 품질**
  - 프로파일링 & 검증
    - 소스 DB 직접 프로파일링(최빈값 포함)
    - 10종 규칙 검증(기본 8종 + CUSTOM_SQL/CUSTOM_PYTHON)
  - 품질 점수 & 전파
    - 점수 자동 동기화(GOOD/WARN/BAD)·추세
    - 리니지 업스트림 품질 전파 경고
- **메타데이터 거버넌스**
  - API 카탈로그
    - OpenAPI/REST API 스펙 등록
    - 버전 diff · 린트
    - URN 기반 메타데이터 관리
  - AI Agent 카탈로그
    - 에이전트 등록 · 도구/MCP
    - 리니지 · 버전 · 평가 · 미터링
- **카탈로그 페더레이션**
  - 인스턴스 연합 · 모드(LIVE/HARVEST/HYBRID)
    - 통합 검색 · 탐색(트리+그리드) · cross-instance 리니지
    - HARVEST 미러·재임베딩·샘플 미러링(에어갭 친화)
  - 로컬 승격(import) · 노출 거버넌스
    - 미러 데이터셋을 로컬 카탈로그로 복사(태그 매칭·"페더레이션" 마커)
    - visibility 정책 · 서비스 토큰 · circuit breaker · 관측성
- **ML 모델 레지스트리**
  - 모델 관리
    - MLflow 연동 · 버전/Stage 관리(STAGING/PRODUCTION)
    - 메트릭 비교 · 모델 카드
  - OCI 모델 허브
    - HuggingFace 스타일 모델 브라우저 · OCI 매니페스트
    - Airgap 임포트
- **AI**
  - AI 메타데이터 생성
    - LLM(OpenAI/Anthropic/Ollama) 기반
    - 설명 · 태그 추천 · PII 감지(미리보기 후 승인)
  - AI 어시스턴트
    - tool-use 채팅 — 카탈로그/스키마/ERD/품질/리니지/SQL 도구 호출
    - 실데이터 근거 응답
- **운영**
  - 알림
    - 스키마 변경 영향 분석
    - 품질 실패 트리거 · Webhook 통지

## 저장소 구성

| 디렉토리 | 역할 |
|----------|------|
| [`backend/`](backend) | FastAPI 기반 카탈로그·모델 레지스트리 백엔드 |
| [`frontend/`](frontend) | Next.js 기반 사용자/관리자 웹 UI |
| [`sdk/`](sdk) | OCI 모델 저장소용 Python SDK + `argus-model` CLI |
| [`agent/`](agent) | AI 메타데이터 생성·AI 어시스턴트 독립 에이전트 (로컬 LLM 연동) |
| [`quality/`](quality) | 외부 품질 배치 (pandas / PySpark) |
| [`tests/`](tests) | 플랫폼별 통합 테스트 (Hive·Trino·StarRocks·PostgreSQL 등) |

## 기술 스택

- **Backend** — Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, PostgreSQL(pgvector)
- **Frontend** — Next.js, React, TypeScript, pnpm
- **Model Registry** — MLflow 호환, OCI 매니페스트, S3/MinIO 호환 오브젝트 스토리지
- **임베딩 / 검색** — pgvector 하이브리드 검색. 임베딩 제공자 선택 가능 —
  **Local**(sentence-transformers, 오프라인) / **OpenAI**(text-embedding-3 등) / **Ollama**(nomic-embed-text 등)
- **AI(LLM)** — OpenAI / Anthropic / Ollama (OpenAI 호환 엔드포인트)

## 외부 의존성

카탈로그 서버가 연동하는 외부 서비스와 용도입니다. **필수**가 아닌 항목은 해당 기능을
쓰지 않으면 없어도 동작합니다.

| 서비스 | 구분 | 용도 |
|--------|------|------|
| **PostgreSQL** (pgvector) | 필수 | 카탈로그 메타데이터 저장 + pgvector 기반 시맨틱 검색 |
| **오브젝트 스토리지** (MinIO / S3 호환) | 필수 | ML 모델 아티팩트 저장 (모델 레지스트리 · OCI Model Hub) |
| **Temporal** | 기본 활성 | 변경관리(change-mgmt) 워크플로 엔진. `temporal.enabled=false` 로 끄면 변경관리 submit 만 비활성 |
| **Keycloak** | 선택 | OIDC/SSO 인증 (`auth.type=keycloak`). 기본은 로컬 인증(`local`)이라 불필요 |
| **OpenLDAP** | 선택 | LDAP 사용자 동기화 (`extensions/ldap-user-sync`) |
| **MLflow** | 선택 | 모델 레지스트리 MLflow 연동 (메트릭 · 버전 관리) |
| **LLM** (OpenAI / Anthropic / Ollama) | 선택 | AI 메타데이터 생성 · AI 어시스턴트 |
| MariaDB (Sakila) | 데모 | 로컬 데모용 샘플 데이터셋 |

> 위 의존성을 로컬에서 한 번에 띄우는 docker-compose 스택과 사용법은 [`deploy/`](deploy) 를 참고하세요.

## 빠른 시작

### Backend

```bash
cd backend
make dev      # pip install -e ".[dev]"
make run      # uvicorn --reload (port 4600)
```

기본 설정으로 PostgreSQL · 오브젝트 스토리지 등 인프라가 필요합니다. 연결 정보는
환경/설정 파일로 주입합니다.

### Frontend

```bash
cd frontend
pnpm install
pnpm dev       # 개발 서버 (Turbopack, port 3000)
```

기본적으로 백엔드 API(`http://localhost:4600`)에 연결됩니다.

### AI 에이전트 (선택)

`agent/` 는 로컬 LLM(ollama 등)과 연동해 메타데이터 자동 생성 / AI 어시스턴트를
제공합니다. 설정 예시는 [`agent/.env.example`](agent/.env.example) 를 참고하세요.

## 운영 환경 보안 설정 ⚠️

이 저장소에는 **로컬 개발용 기본값**이 포함되어 있습니다. 운영 배포 전에 다음을 반드시
변경하세요.

| 항목 | 환경변수 / 위치 | 기본값(개발용) |
|------|-----------------|----------------|
| 로컬 JWT 서명키 | `ARGUS_JWT_SECRET` | 미설정 시 개발용 기본키(경고 로그) |
| 자격증명 암호화 키 | `ARGUS_SECRET_KEY` | 미설정 시 개발용 패스프레이즈(경고 로그) |
| 카탈로그 DB 계정 | 설정 파일 | `argus / argus` |
| 오브젝트 스토리지 | 설정 파일 | `minioadmin / minioadmin` |
| Keycloak client secret | 설정 파일 | `argus-client-secret` |

운영에서는 `ARGUS_JWT_SECRET`, `ARGUS_SECRET_KEY` 를 충분히 긴 무작위 값으로 설정하고
모든 기본 크리덴셜을 교체해야 합니다.

## 버전 관리

[Semantic Versioning](https://semver.org/lang/ko/) 을 따르며, 루트 `VERSION` 파일이
단일 소스입니다. 규칙·릴리스 절차는 [VERSIONING.md](VERSIONING.md) 참고.

## 라이선스

[Apache License 2.0](LICENSE)

## 후원

본 오픈소스는 **[Data Dynamics Inc](https://www.data-dynamics.io/)** 가 후원 및 기여하고 있습니다.
