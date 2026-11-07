# 릴리스 패키지 빌드 (폐쇄망 · EL8/EL9)

운영 환경(Rocky Linux / RHEL 8·9, x86_64, 인터넷 차단)에 설치할 **tar.gz + systemd** 묶음과 **RPM** 을 만든다.
설치 방법은 [README-INSTALL.md](README-INSTALL.md) — 모든 묶음에 같이 들어간다.

## 빌드

인터넷이 되는 빌드 머신(docker 필요)에서:

```bash
release/build.sh                       # 전부
release/build.sh server web            # 일부만
release/build.sh --release 2           # 같은 버전 재배포 시 RPM Release 증가
release/build.sh --starrocks-fe-jar /path/starrocks-fe.jar   # StarRocks 수집 플러그인 포함
```

산출물: `release/dist/<VERSION>/` (git 에서 제외)

| 산출물 | 설명 |
|---|---|
| `<패키지>-<버전>-x86_64.tar.gz` | 압축을 풀고 `install.sh` 로 설치 (collectors 는 `-noarch.tar.gz`) |
| `<패키지>-<버전>-<릴리스>.x86_64.rpm` | `dnf install ./…rpm` |
| `argus-catalog-collectors-<버전>-<릴리스>.noarch.rpm` | 수집기 jar — 아키텍처 무관(`BuildArch: noarch`). `dnf install ./argus-catalog-collectors-<버전>-1.noarch.rpm` |
| `argus_catalog_sdk-<버전>-py3-none-any.whl` | 다른 OS 용 SDK |
| `SHA256SUMS` | 전체 체크섬 |

버전은 루트 `VERSION` 을 따른다 ([VERSIONING.md](../VERSIONING.md)).

## 패키지

| 컴포넌트 | 패키지 | 내용 |
|---|---|---|
| `server` | `argus-catalog-server` | `backend/app` + Python 3.11 wheel(오프라인) + DDL + 서버·워커 unit |
| `server-embedding` | `argus-catalog-server-embedding` | 로컬 임베딩 애드온: CPU torch + sentence-transformers (본체에 이미 있는 wheel 은 제외) |
| `web` | `argus-catalog-web` | Next.js standalone(`NEXT_OUTPUT=standalone`) + Node.js LTS 런타임 |
| `query-service` | `argus-catalog-query-service` | 쿼리 수집·lineage 배치 상시 서비스 |
| `metadata-sync` | `argus-catalog-metadata-sync` | 메타데이터 동기화 배치 + systemd timer (extras: trino·oracle·mssql·iceberg·kerberos) |
| `sdk` | `argus-catalog-sdk` | SDK·CLI (`argus-model`, `argus-catalog`) |
| `ai-service` | `argus-catalog-ai-service` | AI 어시스턴트 서버 + 메타데이터 보강(enrich) 워커·CLI (`argus-catalog-ai`) |
| `collectors` | `argus-catalog-collectors` | Hive hook·Impala agent·Trino listener jar (StarRocks 는 선택) |

## 구조와 원칙

```
release/
  build.sh                     빌드 진입점 (호스트)
  builder/Dockerfile           Rocky Linux 8 빌더 — python3.11, rpm-build, gcc, krb5-devel
  scripts/build-in-container.sh  컴포넌트별 묶음 구성 → tar.gz → rpmbuild
  common/install.sh            설치 스크립트 (tar.gz 설치, RPM %install/%post 공용)
  common/uninstall.sh          제거 스크립트 (tar.gz 설치용)
  common/component.spec.in     공용 RPM spec 템플릿
  components/<이름>/           component.env (경로·서비스·pip 대상), systemd/, conf/
  README-INSTALL.md            운영자 설치 안내 (묶음에 포함)
```

- **EL8 에서 빌드 → EL8·EL9 공용.** wheel 의 네이티브 확장이 glibc 2.28 기준이라 EL9 에서도 동작한다.
- **Python 은 OS 의 `python3.11`** (EL8.8+/EL9.2+ AppStream). wheel 이 cp311 용이라 정확히 3.11 이어야 한다.
  venv 는 설치 시 묶음 wheel 로만 만든다(`pip --no-index`) — 인터넷을 쓰지 않는다.
- **Node.js 는 패키지에 포함** — EL 의 nodejs 모듈 스트림 버전 차이를 피한다. 공식 배포본을 SHA256 으로 검증한다.
- **설정은 덮어쓰지 않는다** — RPM `%config(noreplace)`, tar.gz 는 `*.new` 로 새 기본값을 둔다.
- **업그레이드 때 venv 를 새로 만든다** — 설치된 애드온(`addons/*`)은 자동으로 다시 넣는다.
- 수집기 jar 는 Maven + JDK 25 컨테이너에서 빌드한다(`trino-spi` 479 가 Java 25 class).
- StarRocks 플러그인은 대상 FE 의 `starrocks-fe.jar` 가 있어야 컴파일된다(Maven Central 에 없음).

## 새 컴포넌트 추가

1. `components/<이름>/component.env` — `NAME`, `PREFIX`, `CONF_DIR`, `STATE_DIRS`, `UNITS`, `PYTHON_VENV`,
   `PIP_INSTALL`, `RPM_REQUIRES` (필요 시 `RPM_ARCH`, `LINKS`, `ADDON_OF_PREFIX`)
2. `components/<이름>/systemd/`, `conf/` (있으면)
3. `scripts/build-in-container.sh` 에 `build_<이름>()` 과 디스패치 추가 — `$st/opt/` 에 설치될 파일을 둔다

## 검증 방법

인터넷이 막힌 docker 네트워크(`docker network create --internal`)에 `rockylinux:8`/`rockylinux:9` +
`python3.11` 컨테이너와 PostgreSQL(pgvector)을 두고, `dnf --disablerepo='*' install ./*.rpm` 또는 `install.sh` 로
설치한 뒤 서버·웹·query-service 의 `/health`, 웹 경유 로그인, 업그레이드(설정 유지)·제거를 확인한다.
