# 버전 관리 정책 (Versioning)

Argus Catalog 의 버전 관리 규칙·기준·절차를 정의합니다.

## 버전 체계 — Semantic Versioning

[Semantic Versioning 2.0.0](https://semver.org/lang/ko/) 을 따릅니다.

```
MAJOR.MINOR.PATCH   (예: 1.4.2)
```

- **MAJOR** — 하위 호환이 깨지는 변경
- **MINOR** — 하위 호환을 유지하는 기능 추가
- **PATCH** — 하위 호환을 유지하는 버그 수정

증가 판단의 구체 기준은 [버전 증가 기준](#버전-증가-기준)을 참고하세요.

## 적용 대상 — 공개 표면(Public Surface)

버전 증가는 **사용자가 의존하는 공개 표면**의 변화로 판단합니다. Argus Catalog 의 공개 표면:

- **REST API** (`/api/v1/...`) — 엔드포인트·요청/응답 스키마
- **External API** (`external`) — URN 기반 메타데이터/Avro 스키마
- **Python SDK + `argus-model` CLI** — 공개 함수 시그니처·명령
- **DB 스키마** — 마이그레이션 호환성
- **설정 키** (`config.yml` / `config.properties` / 환경변수)
- **에이전트 연동 인터페이스** (`agent/ serve` ↔ 카탈로그)

내부 모듈·테스트·주석·로그 문구 등 **비공개 표면**의 변경은 버전에 영향을 주지 않습니다.

## 버전 증가 기준

| 증가 | 기준 | 예시 |
|---|---|---|
| **MAJOR** | 공개 표면의 **하위 호환 깨짐** | 엔드포인트 제거/응답 형식 변경, SDK 공개 API 시그니처 변경, 비호환 DB 마이그레이션, 설정 키 제거·의미 변경, 인증 방식 변경 |
| **MINOR** | **하위 호환 유지**하는 기능 추가 | 새 엔드포인트·기능, 새 설정 키(기본값 하위호환), SDK 신규 함수, 하위호환 컬럼 추가 |
| **PATCH** | 하위 호환 유지하는 수정 | 버그 수정, 성능 개선, 내부 리팩터링, 문서, (비호환 아닌) 보안 패치 |

> **0.x 단계 특칙**: `1.0.0` 이전에는 `MINOR`(`0.x.0`)가 호환성 깨짐을 포함할 수 있고,
> `PATCH`(`0.0.x`)는 하위 호환 수정에 씁니다. 공개 API 가 안정화되면 `1.0.0` 으로 승격합니다.

## 사전 릴리스 (Pre-release)

안정 릴리스 전 검증이 필요하면 SemVer 접미사를 사용합니다.

- `X.Y.Z-alpha.N` — 초기, 불안정. 내부/얼리 테스트용
- `X.Y.Z-beta.N` — 기능 동결, 광범위 테스트
- `X.Y.Z-rc.N` — 릴리스 후보. 회귀가 없으면 접미사를 떼어 안정 릴리스로 승격

정렬 순서: `1.0.0-alpha < 1.0.0-beta < 1.0.0-rc.1 < 1.0.0`.

## 호환성 & Deprecation 정책

- 공개 표면을 제거/변경하기 전, 먼저 **deprecated** 로 표시합니다 — 문서·릴리스 노트 명시,
  가능하면 런타임 경고(로그 / 응답 헤더 `Deprecation`).
- 제거는 **다음 MAJOR** 에서만. 최소 **1개 MINOR 주기**(0.x 에서는 1개 `0.x` 주기) 동안 유지합니다.
- 비호환 변경은 릴리스 노트와 [CHANGELOG](#changelog) 의 **Removed / Changed** 에 반드시 기재합니다.

## 지원 & 수명 종료(EOL)

- 현재(0.x): **최신 릴리스만** 보안·버그 패치를 받습니다 ([SECURITY.md](SECURITY.md) 와 연계).
- `1.0.0` 이후: 최신 MAJOR 의 최신 MINOR 를 지원하며, 필요 시 LTS/백포트 정책을 별도 수립합니다.

## 단일 소스 — 루트 `VERSION`

제품 버전의 **단일 소스(single source of truth)** 는 저장소 루트의 [`VERSION`](VERSION)
파일 하나뿐입니다. 각 컴포넌트의 매니페스트(`pyproject.toml`, `__init__.py`,
`package.json`)에 박힌 버전은 모두 이 값에서 **파생**됩니다.

- 절대 매니페스트를 개별 수정하지 마세요. 항상 `VERSION` 을 바꾸고 동기화합니다.
- 서버 시작 배너 / `/health` / OpenAPI 문서도 모두 이 값을 표시합니다.

### 동기화 도구 — `tools/sync-version.py`

| 명령 | 동작 |
|------|------|
| `python tools/sync-version.py` | `VERSION` → 모든 매니페스트로 전파(동기화) |
| `python tools/sync-version.py --check` | 매니페스트가 `VERSION` 과 일치하는지 검증(불일치 시 실패) |
| `python tools/sync-version.py --set X.Y.Z` | `VERSION` 갱신 후 동기화(버전 올릴 때) |

관리 대상: 모든 `pyproject.toml`(백엔드는 dynamic 이라 자동 추종) · `__version__` 을 가진
`__init__.py` · 지정한 `package.json`. 존재하지 않는 파일은 건너뜁니다.
(내부 빌드 설정 패키지 `frontend/packages/*` 는 `private`·`0.0.0` 으로 제외합니다.)

### CI 강제

CI 의 `version-sync` 잡이 `--check` 를 실행해, 매니페스트만 고치고 `VERSION` 을 맞추지
않은 드리프트를 차단합니다.

## 버전 올리는 절차 (Release)

릴리스 시점에 메인테이너가 버전을 올립니다.

```bash
# 1) 버전 결정(SemVer) 후 단일 소스 + 전 매니페스트 일괄 갱신
python tools/sync-version.py --set 0.2.0

# 2) CHANGELOG.md 의 [Unreleased] 를 [0.2.0] - YYYY-MM-DD 로 정리

# 3) 변경 커밋
git commit -am "chore(release): 0.2.0"

# 4) 태그 (vX.Y.Z 컨벤션)
git tag -a v0.2.0 -m "Argus Catalog 0.2.0"
git push origin main --tags

# 5) (공개 반영이 필요하면) 공개 저장소로 publish
#    tools/publish-oss.sh -m "release 0.2.0"
```

릴리스 전 CI(`version-sync` · lint · test) 통과를 필수로 합니다.

## 태그 & 릴리스

- 태그 컨벤션은 **`vX.Y.Z`** 입니다 (예: `v0.2.0`).
- **`v` 접두는 git 태그·GitHub Release 이름에만 붙입니다.** `VERSION` 파일과 매니페스트
  (`pyproject.toml`·`package.json`·`__version__`)의 값에는 `v` 를 붙이지 않습니다 —
  SemVer·npm·PEP 440 모두 버전 값에 `v` 를 허용하지 않습니다 (예: 값은 `0.2.0`, 태그는 `v0.2.0`).
- GitHub Release 를 태그에 연결해 변경 사항(릴리스 노트)을 정리합니다.

> 참고: 초기 태그 `0.1.0` 은 `v` 접두 없이 만들어졌습니다. 이후 태그는 `vX.Y.Z` 를
> 사용하며, 필요하면 `v0.1.0` 으로 재태깅합니다.

## CHANGELOG

- [Keep a Changelog](https://keepachangelog.com/ko/) 형식의 [`CHANGELOG.md`](CHANGELOG.md) 를 유지합니다.
- 변경은 우선 `[Unreleased]` 에 누적하고, 릴리스 시 해당 버전 섹션으로 옮깁니다.
- 분류: `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`.

## 릴리스 책임

- 버전 결정과 릴리스 태깅은 **메인테이너**가 수행합니다.
- 비호환 변경은 릴리스 노트·CHANGELOG 의 **Breaking changes** 로 명확히 알립니다.

## 컴포넌트별 버전

모노레포의 핵심 제품 컴포넌트(backend · frontend · sdk · agent · extensions · tests · docs)는
`VERSION` 으로 **버전을 일원화**합니다. 향후 컴포넌트별로 독립 버전이 필요해지면 이 정책을
개정합니다.
