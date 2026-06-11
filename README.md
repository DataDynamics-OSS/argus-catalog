# Argus Catalog

[![CI](https://github.com/DataDynamics-OSS/argus-catalog/actions/workflows/ci.yml/badge.svg)](https://github.com/DataDynamics-OSS/argus-catalog/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Argus Catalog** 은 DataHub 스타일의 **데이터 카탈로그**와 Unity Catalog OSS 호환
**ML 모델 레지스트리**를 하나로 묶은 오픈소스 메타데이터 플랫폼입니다. 데이터셋·스키마·
리니지·데이터 품질·용어집·데이터 표준은 물론, ML 모델 레지스트리와 LLM 기반 메타데이터
자동 생성 / AI 어시스턴트까지 단일 저장소에서 제공합니다.

## 주요 기능

- **데이터 카탈로그** — 데이터셋 등록/검색/태그/소유자, 11종 데이터 소스 메타데이터 동기화
- **리니지 & ERD** — 이기종 시스템 간 컬럼 수준 데이터 흐름 추적, DDL 파싱 기반 ER 다이어그램
- **데이터 품질** — 소스 DB 직접 프로파일링, 규칙 기반 검증, 품질 점수 추세, 리니지 품질 전파
- **데이터 표준 & 용어집** — 멀티 표준 사전, 형태소 분석 기반 용어 자동 생성, 준수율 측정
- **ML 모델 레지스트리** — MLflow 연동, 버전/Stage 관리, OCI 모델 허브, 모델 카드
- **API 카탈로그** — OpenAPI/REST API 스펙 등록·버전 diff·린트, URN 기반 메타데이터 관리
- **AI Agent 카탈로그** — AI 에이전트 등록·도구/MCP·리니지·버전·평가·미터링 등 에이전트 거버넌스
- **시맨틱 검색** — pgvector 기반 키워드 + 시맨틱 하이브리드 검색
- **AI 메타데이터 / 어시스턴트** — LLM(OpenAI/Anthropic/Ollama) 기반 설명·태그·PII 감지,
  tool-use 채팅으로 실데이터 근거 응답
- **알림** — 스키마 변경 영향 분석 + 품질 실패 트리거, Webhook 통지

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
- **AI** — OpenAI / Anthropic / Ollama (OpenAI 호환 엔드포인트)

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

## 라이선스

[Apache License 2.0](LICENSE)
