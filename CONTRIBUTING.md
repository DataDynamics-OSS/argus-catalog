# 기여 가이드 (Contributing to Argus Catalog)

Argus Catalog 에 관심을 가져 주셔서 감사합니다! 버그 리포트, 기능 제안, 문서 개선,
코드 기여 모두 환영합니다. 이 문서는 원활한 협업을 위한 안내입니다.

> 문서·주석·커밋 메시지는 **한국어를 기본**으로 하며, 영어도 무방합니다.

## 목차

- [행동 강령](#행동-강령)
- [기여 방법](#기여-방법)
- [개발 환경 설정](#개발-환경-설정)
- [브랜치 & 커밋 컨벤션](#브랜치--커밋-컨벤션)
- [코드 스타일](#코드-스타일)
- [테스트](#테스트)
- [Pull Request 절차](#pull-request-절차)
- [버전 관리](#버전-관리)
- [보안 취약점 제보](#보안-취약점-제보)
- [라이선스](#라이선스)

## 행동 강령

서로 존중하고 건설적으로 소통해 주세요. 차별·괴롭힘·비방은 허용되지 않습니다.

## 기여 방법

- **버그 리포트** — [이슈](../../issues/new/choose)의 *버그 리포트* 템플릿을 사용해 주세요.
- **기능 제안** — *기능 제안* 템플릿으로 동기와 사용 사례를 설명해 주세요.
- **코드 기여** — 아래 절차에 따라 Fork → 브랜치 → PR 을 보내 주세요.
- 큰 변경(아키텍처·공개 API 변경 등)은 먼저 이슈로 논의해 주시면 좋습니다.

## 개발 환경 설정

모노레포 구성: `backend/`(FastAPI), `frontend/`(Next.js), `sdk/`, `agent/`,
`quality/`, `tests/`. 자세한 구조는 [README](README.md)를 참고하세요.

### Backend (Python 3.11+)

```bash
cd backend
make dev      # 개발 의존성 설치 (pip install -e ".[dev]")
make run      # 개발 서버 실행 (uvicorn --reload, port 4600)
make test     # pytest
make lint     # ruff check
make format   # ruff format
```

PostgreSQL(pgvector)·오브젝트 스토리지 등 인프라가 필요합니다. 연결 정보는
설정 파일/환경변수로 주입합니다.

### Frontend

```bash
cd frontend
pnpm install
pnpm dev       # 개발 서버 (Turbopack, port 3000)
pnpm build     # 프로덕션 빌드
pnpm lint      # ESLint
```

기본적으로 백엔드 API(`http://localhost:4600`)에 연결됩니다.

## 브랜치 & 커밋 컨벤션

### 브랜치

`main` 에 직접 커밋하지 말고 작업용 브랜치를 만들어 주세요.

```
feature/<요약>     # 기능 추가
fix/<요약>         # 버그 수정
docs/<요약>        # 문서
```

### 커밋 메시지 — Conventional Commits

```
<type>(<scope>): <요약>

[본문 — 필요 시 변경 이유/맥락]
```

- **type**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `style`
- **scope**: 영향 영역 (예: `catalog`, `ai`, `frontend`, `sdk`)
- 예: `feat(catalog): 컬럼 수준 리니지 매핑 추가`

## 코드 스타일

- **Python** — [ruff](https://docs.astral.sh/ruff/) 로 lint/format. PR 전 `make lint && make format` 통과.
- **TypeScript** — ESLint. PR 전 `pnpm lint` 통과.
- **주석** — 한국어 기본 (식별자·로그 메시지는 영문 유지).
- 기존 코드의 네이밍·스타일·관용구를 따라 주세요.

## 테스트

- 동작 변경에는 가능한 한 테스트를 추가해 주세요.
- 플랫폼 통합 테스트는 [`tests/`](tests) 참고 (Docker compose 기반).
- PR 전 관련 테스트가 통과하는지 확인해 주세요.

## Pull Request 절차

1. 저장소를 **Fork** 하고 작업 브랜치를 만듭니다.
2. 변경을 커밋합니다(컨벤션 준수, lint/test 통과).
3. PR 을 생성하고 [PR 템플릿](.github/PULL_REQUEST_TEMPLATE.md)을 채웁니다.
4. 관련 이슈가 있으면 `Closes #이슈번호` 로 연결합니다.
5. 리뷰 피드백에 따라 보완합니다. CI 가 통과해야 머지됩니다.

작은 단위로 나눠 주시면 리뷰가 빠릅니다.

## 버전 관리

버전은 [Semantic Versioning](https://semver.org/lang/ko/) 을 따르며, 저장소 루트의
`VERSION` 파일 **하나**가 단일 소스입니다. 매니페스트를 개별 수정하지 말고
`python tools/sync-version.py --set X.Y.Z` 로 일괄 갱신하세요. 자세한 규칙·릴리스 절차는
[VERSIONING.md](VERSIONING.md) 를 참고하세요.

## 보안 취약점 제보

보안 취약점은 **공개 이슈로 올리지 말고** 비공개로 제보해 주세요. 자세한 절차는
[SECURITY.md](SECURITY.md) 를 참고하세요. 배포 시 `ARGUS_JWT_SECRET`,
`ARGUS_SECRET_KEY` 등 운영 시크릿 설정은
[README 의 운영 보안 설정](README.md#운영-환경-보안-설정-️) 섹션을 참고하세요.

## 라이선스

기여하신 내용은 프로젝트와 동일하게 [Apache License 2.0](LICENSE) 으로 배포됩니다.
PR 을 제출함으로써 본인의 기여를 이 라이선스로 제공하는 데 동의하는 것으로 간주합니다.
