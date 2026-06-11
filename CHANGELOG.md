# Changelog

이 프로젝트의 주요 변경 사항을 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)
를 따르며, 버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/) 을 따릅니다.
버전 관리 규칙은 [VERSIONING.md](VERSIONING.md) 를 참고하세요.

## [Unreleased]

### Added
- (다음 릴리스의 변경 사항을 여기에 누적합니다)

## [0.1.0] - 2026-05-27

최초 릴리스.

### Added
- **데이터 카탈로그** — 데이터셋 등록/검색/태그/소유자, 11종 데이터 소스 메타데이터 동기화
- **리니지 & ERD** — 이기종 시스템 간 컬럼 수준 데이터 흐름 추적, DDL 파싱 기반 ER 다이어그램
- **데이터 품질** — 소스 DB 직접 프로파일링, 규칙 기반 검증, 품질 점수 추세, 리니지 품질 전파
- **데이터 표준 & 용어집** — 멀티 표준 사전, 형태소 분석 기반 용어 자동 생성, 준수율 측정
- **ML 모델 레지스트리** — MLflow 연동, 버전/Stage 관리, OCI 모델 허브, 모델 카드
- **API 카탈로그** — OpenAPI/REST API 스펙 등록·버전 diff·린트, URN 기반 메타데이터 관리
- **AI Agent 카탈로그** — AI 에이전트 등록·도구/MCP·리니지·버전·평가·미터링 거버넌스
- **시맨틱 검색** — pgvector 기반 키워드 + 시맨틱 하이브리드 검색
- **AI 메타데이터 / 어시스턴트** — LLM(OpenAI/Anthropic/Ollama) 기반 설명·태그·PII 감지, tool-use 채팅
- **알림** — 스키마 변경 영향 분석 + 품질 실패 트리거, Webhook 통지
- 백엔드(FastAPI) · 프론트엔드(Next.js) · Python SDK(`argus-model`) · AI 에이전트 구성

[Unreleased]: https://github.com/DataDynamics-OSS/argus-catalog/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DataDynamics-OSS/argus-catalog/releases/tag/v0.1.0
