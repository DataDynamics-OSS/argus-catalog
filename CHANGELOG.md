# Changelog

이 프로젝트의 주요 변경 사항을 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)
를 따르며, 버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/) 을 따릅니다.
버전 관리 규칙은 [VERSIONING.md](VERSIONING.md) 를 참고하세요.

## [Unreleased]

## [0.5.0] - 2026-11-08

### Added
- **MCP Server 카탈로그** — 등록(Phase 1), 거버넌스(승인 정책·finding·감사·manifest·버전·통제 현황, Phase 2),
  정책 집행(정책 번들 API·pull 상태·서버 거부 감사, Phase 3), 에이전트별 리소스 범위 편집, 통합 검색 MCP 서버
- **AI Agent 카탈로그 확장** — 정의 파일(`agent.yaml`) 일괄 반영·정리·내보내기 API와 SDK 명령
  `agent-apply`·`agent-export`, 미등록 에이전트 발견, 외부 에이전트 카드 가져오기, 분류·책임자·정보 완성도
- **API 카탈로그 확장** — 소유자 선택, 스펙 새 버전 등록·버전 비교, 관측 탭(호출 기록 접근 통제·일별 추이),
  즐겨찾기, Monaco 기반 본문·계약 문서 보기
- **모델 허브** — 대시보드·상세 화면 재구성, MLflow 모델 소프트 삭제·복원·영구 삭제, 실패 버전(FAILED_REGISTRATION)
  표시, OCI 모델 허브 관리 UI·비동기 HuggingFace 가져오기·README 편집 미리보기·버전 삭제
- **관리 기능** — 사용자·설정·권한 변경 이력(감사 로그), 설정 비밀값 DB 암호화, 설정 값 출처 표시,
  로그인 잠금·마지막 로그인, 등록 계열 기능 권한과 운영 승격(`models.promote`)·MCP 자격증명 명시 허용 권한
- **플러그인 확장 지점** — 고객사 커스텀 화면용 seam
- **설치 패키지** — 폐쇄망 EL8/EL9 용 tar.gz(systemd) + RPM 빌드(`release/build.sh`)
- 확정되지 않은 버전 파일 정리 API(`DELETE /model-store/{모델}/versions/{n}/files`) — SDK `push` 가 끊긴 업로드를 자동 정리
- **AI 제안 검토 화면** — 데이터셋 상세의 *AI 제안* 에서 AI 서비스(enrich)·미리보기로 생긴 대기 제안을 현재 값과
  비교해 개별·일괄 승인/거절
- **데이터셋 직접 등록 정책** — *설정 › 데이터셋 등록* 옵션(`catalog_dataset_create_by_users`, 기본 꺼짐).
  관리자·동기화 계정(`datasources.sync`)은 항상 허용
- **데모 스택 DB** — `deploy/docker-compose.yml` 의 catalog-postgres 를 `pgvector/pgvector:pg16` 으로, 최초 기동 시
  DDL 자동 적용(`deploy/initdb/catalog/`)
- **사용자 매뉴얼 개편** — 초보자 따라 하기 중심 구조(고급 내용은 고급 사용자 상자·고급 페이지), 사용자/관리자/개발자 가이드
  목차, 다이어그램 그림 교체, PDF(Noto Sans KR·저작권 바닥글·글꼴 검사 `make check-glyphs`)

### Changed
- **로그인 게이트** — `/api/v1/*` 는 조회까지 사용자 토큰이 필요합니다(비로그인 401). 예외: 로그인 경로,
  페더레이션 export·MCP 배포의 서비스 토큰 경로, 다운로드 링크(`?token=`). 로그인 없이 조회하던 클라이언트는
  `ARGUS_TOKEN` 또는 사용자명·비밀번호를 설정해야 합니다
- MLflow 모델 파일(`/filesystem/*`) — 전체 탐색·변경은 관리자 전용, 일반 사용자는 등록 모델 폴더 안만 조회
  (OCI `/model-store/browse/*` 와 같은 규칙)
- AI 서비스 이름 `argus-agent` → `argus-catalog-ai-service` (이전 이름은 한 버전 동안 경고와 함께 동작)
- UI 알림·확인창 일관화, 사이드바 메뉴 검색·그룹 접기·접기 상태 유지
- 엔진 수집기 Java 확장(Hive Hook·Impala Agent·Trino Listener·StarRocks 플러그인)의 버전을 제품 버전에 맞춤
  (1.0.x → 0.5.0, `tools/sync-version.py` 관리 대상). 산출물 이름이 `…-0.5.0.jar` 로 바뀌므로 배포 경로·
  `-javaagent` 인자를 갱신하세요. StarRocks 는 기존 플러그인을 `UNINSTALL PLUGIN` 후 다시 설치합니다
- **데이터셋 직접 등록** — 일반 사용자는 기본 거부(403). 비관리자 계정으로 돌던 외부 metadata-sync 는 그 계정을
  관리자로 바꾸거나 역할에 `datasources.sync` 를 허용해야 합니다
- **Keycloak client** — Direct Access Grants 만 쓰는 최소 구성으로 자동 초기화·realm 예시·문서 통일
  (Standard Flow·Service Accounts 불필요, redirect URI·Web Origins 비움)
- AI 서비스 설치 묶음 `ai-service.env` 의 `ARGUS_USERNAME` 기본값 `argus`(관리자 역할 서비스 계정을 미리 생성)
- 반도체 표준 시드 스크립트 — 로그인 토큰 인증(`--username`/`ARGUS_PASSWORD` 또는 `--token`)
- 서버 Python 코드 한글 주석 보강·레벨별 로그

### Fixed
- 모델 버전 삭제 시 저장소 파일·다운로드 기록 정리, 실패 버전 확정 거부(409), 저장소 장애 시 503 안내 등
  MLflow·OCI 모델 허브 통합 테스트에서 찾은 결함
- API·변경 관리·데이터 소스·표준·용어집·검색·페더레이션·확장 모듈 등 다수의 버그 수정
- **보안** — 파일 미리보기 docx HTML sanitizer(저장형 XSS)·pptx 이스케이프, 파일 업로드 경로 탈출 차단,
  MLflow 모델 이름 변경 시 OCI 이름 충돌 409·영구 삭제 범위를 버전 단위로
- AI 요약 제안 승인 시 요약 미반영, AI 서비스 태그 제안 승인 시 태그 미부착
- 리니지 LIKE 와일드카드·MariaDB 방언, 스케줄러 시각 혼용 TypeError, 다단계 답글 삭제, 샘플 API 400,
  LLM 연결 테스트가 전역 제공자를 바꾸던 문제, OpenAPI·외부 메타데이터 캐시 등 코드 리뷰 확인 결함
- 데이터셋 상세 Avro 가져오기 가이드 예시에 인증 헤더, Avro 스키마 편집기 테마를 뷰어와 같게

## [0.1.2] - 2026-06-13

### Added
- **카탈로그 페더레이션** — 여러 Argus 인스턴스를 하나로 연합해 한 화면에서 통합
  검색·탐색. `LIVE`/`HARVEST`/`HYBRID` 모드, HARVEST 미러·허브 모델 재임베딩·샘플
  미러링(에어갭 친화), cross-instance 리니지, 미러 데이터셋 **로컬 승격(import)**,
  노출 거버넌스(visibility·서비스 토큰·circuit breaker)·관측성
- 분류 체계 화면의 데이터셋 목록에 페이지네이션 추가

### Changed
- **삭제 안정성·안내 개선** — 하위 의존(FK)으로 삭제가 막히는 경우(용어집·분류·조직
  등)를 500 대신 친절한 409 메시지로 안내하고, 미처리 무결성 오류에 대한 전역 안전망 추가
- **API 오류 메시지 개선** — 변경·액션 실패 시 상태코드 대신 백엔드 상세 메시지(detail)를 노출

## [0.1.1] - 2026-06-11

### Added
- **로컬 개발/데모 배포 공개** — `deploy/` (MinIO·Temporal·Keycloak·OpenLDAP·Sakila
  인프라 + 풀스택 docker-compose)와 사용 안내(`deploy/README.md`)를 공개 배포에 포함
- **운영 배포 산출물** — `deploy/docker-compose.prod.yml`(오버라이드: 시크릿 주입·
  `restart: always`·이미지 핀·**데이터 bind mount 영속**) + `deploy/.env.example`(시크릿/
  `DATA_DIR` 템플릿) + README 운영 배포·데이터 보존/백업 섹션

### Changed
- `deploy/docker-compose.yml` 의 예시 엔드포인트를 문서용 IP(`192.0.2.10`)로 정리

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

[Unreleased]: https://github.com/DataDynamics-OSS/argus-catalog/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/DataDynamics-OSS/argus-catalog/compare/v0.1.2...v0.5.0
[0.1.2]: https://github.com/DataDynamics-OSS/argus-catalog/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/DataDynamics-OSS/argus-catalog/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/DataDynamics-OSS/argus-catalog/releases/tag/v0.1.0
