# Argus Catalog — 로컬 개발/데모/운영 배포

Docker Compose 기반의 **로컬 개발·데모/운영 스택**입니다. 카탈로그 서버가 의존하는 인프라
(MinIO·Temporal·Keycloak·OpenLDAP·Sakila)와 풀스택(backend·frontend)을 한 번에 띄울 수
있습니다.

> ⚠️ **로컬/데모 전용입니다.** 아래의 모든 계정·비밀번호는 개발 편의를 위한 기본값이며,
> 운영에 그대로 사용하면 안 됩니다. 운영 배포 시 반드시 교체하세요
> (루트 [README 의 운영 보안 설정](../README.md#운영-환경-보안-설정-️) 참고).

## 구성 파일

| 파일 | 용도 |
|------|------|
| `docker-compose.yml` | **풀스택(데모)** — backend·frontend·Temporal·의존 서비스 일괄 기동 |
| `docker-compose.prod.yml` | **운영 오버라이드** — 시크릿 주입·`restart: always`·이미지 핀 ([운영 배포](#운영-배포)) |
| `.env.example` | **운영 시크릿/크리덴셜 템플릿** — `deploy/.env` 로 복사해 사용 |
| `docker-compose.infra.yml` | **인프라만** — MinIO·Temporal·Keycloak(+Sakila). 백엔드를 로컬에서 직접 띄울 때 |
| `docker-compose.infra.openldap.seed.yml` | (선택) OpenLDAP + 시드 데이터 |
| `docker-compose.infra.openldap.changed.yml` | (선택) OpenLDAP 속성 변경 시나리오 |
| `config/` | 카탈로그 서버 설정(`config.yml`·`config.properties`)과 Keycloak realm |
| `download-sakila.sh` | 샘플 데이터셋(Sakila) 다운로드 |

## 빠른 시작

```bash
# 풀스택 (UI + API + 인프라)
docker compose -f deploy/docker-compose.yml up -d --build

# 또는 인프라만 (백엔드는 로컬에서 make run 으로)
docker compose -f deploy/docker-compose.infra.yml up -d
```

종료:

```bash
docker compose -f deploy/docker-compose.yml down       # 컨테이너만
docker compose -f deploy/docker-compose.yml down -v    # 볼륨까지 삭제(데이터 초기화)
```

## 엔드포인트 & 기본 계정 (로컬 전용)

| 서비스 | URL | 기본 계정 |
|--------|-----|-----------|
| Web UI | http://localhost:3000 | (카탈로그 admin 으로 로그인) |
| API (Swagger) | http://localhost:4600/docs | — |
| Temporal UI | http://localhost:8233 | — |
| MinIO Console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Keycloak | http://localhost:8180 | `admin` / `admin` |
| MariaDB (Sakila) | localhost:3306 | — |

카탈로그 앱 DB(PostgreSQL)·오브젝트 스토리지 기본값은 `config/config.properties` 에 있습니다(`argus`/`argus`, `minioadmin`/`minioadmin`).

Keycloak `argus` realm 의 데모 사용자(`config/keycloak/argus-realm.json`):

| 사용자 | 비밀번호 | 역할 |
|--------|----------|------|
| `argus-admin` | `argus-admin` | argus-admin |
| `argus-superuser` | `argus-superuser` | argus-superuser |
| `argus-user` | `argus-user` | argus-user |

## 샘플 데이터 (Sakila)

데이터 카탈로그의 데이터셋 샘플로 사용하는 데이터입니다.

```bash
deploy/download-sakila.sh    # deploy/sakila/*.sql 생성(.gitignore 대상)
```

## 운영 배포

> 위 `docker-compose.yml` 단독은 **데모용**입니다. 운영에는 아래 오버라이드와 외부 시크릿을
> 사용하고, 가능하면 외부 관리형 DB/S3/Temporal 로 전환하세요.

### 1) 시크릿 준비

```bash
cp deploy/.env.example deploy/.env       # .gitignore 대상 — 절대 커밋 금지
# 강한 무작위 값 생성
openssl rand -base64 36                   # ARGUS_SECRET_KEY / ARGUS_JWT_SECRET 등에 사용
```

`deploy/.env` 에 채울 값:

| 변수 | 용도 | 주입 경로 |
|------|------|-----------|
| `ARGUS_SECRET_KEY` | 외부 연동 자격증명 암호화 키 | 백엔드 **환경변수** |
| `ARGUS_JWT_SECRET` | 로컬 JWT 서명키 | 백엔드 **환경변수** |
| `DB_PASSWORD` | catalog-postgres 비밀번호 | 컨테이너 env |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO 키 | 컨테이너 env |
| `TEMPORAL_DB_PASSWORD` | Temporal 메타 DB | 컨테이너 env |
| `DATA_DIR` | DB·MinIO 데이터 보관 호스트 경로 | bind mount |
| `MINIO_IMAGE` | MinIO 이미지 핀 | 컨테이너 image |

### 2) config 정합 (중요)

백엔드는 **DB/오브젝트 스토리지 접속정보를 `config/config.properties` 에서 읽습니다**
(환경변수가 아님). 따라서 `.env` 의 `DB_PASSWORD`·`MINIO_*` 와
`config/config.properties` 의 `db.password`·`os.access_key`·`os.secret_key` 를
**반드시 일치**시켜야 합니다. (`ARGUS_SECRET_KEY`·`ARGUS_JWT_SECRET` 은 환경변수로만 주입)

### 3) 기동

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  --env-file deploy/.env up -d --build
```

`docker-compose.prod.yml` 가 추가하는 것: `.env` 기반 크리덴셜 주입, 백엔드/워커에
`ARGUS_SECRET_KEY`·`ARGUS_JWT_SECRET` 주입, `restart: always`, MinIO 이미지 핀.
미설정 시크릿이 있으면 기동이 **실패**합니다(`:?` 가드).

### 4) 데이터 보존 & 백업

운영 오버라이드는 상태 데이터를 **명시적 호스트 경로(bind mount)** 에 보관하므로,
컨테이너를 삭제·재생성해도(`down`, 이미지 교체, 재빌드) 데이터가 보존됩니다.

| 데이터 | 위치 |
|--------|------|
| 앱 DB(PostgreSQL) | `${DATA_DIR}/catalog-postgres` |
| 모델 아티팩트/샘플(MinIO) | `${DATA_DIR}/minio` |
| Temporal 메타 DB | `${DATA_DIR}/temporal-postgres` |
| 환경설정 파일 | `deploy/config/` (호스트에 그대로 존재 — 컨테이너에 read-only 마운트) |
| 샘플 데이터(Sakila) | `deploy/sakila/` (호스트, `download-sakila.sh` 산출물) |

- `DATA_DIR` 은 백업 가능한 **절대 경로**를 권장합니다(예: `/var/lib/argus-catalog/data`).
  상대 경로는 `deploy/` 기준으로 해석됩니다.
- ⚠️ `docker compose ... down -v` 는 named volume 을 지웁니다. bind mount(호스트 경로)는
  영향받지 않지만, 운영에서는 `-v` 사용을 피하세요.
- **정기 백업** (단일 호스트는 필수):
  ```bash
  # 앱 DB 논리 백업
  docker exec argus-catalog-postgres pg_dump -U argus argus_catalog > backup-$(date +%F).sql
  # 데이터 디렉터리 전체 백업(DB 파일 + MinIO 오브젝트)
  tar czf argus-data-$(date +%F).tgz -C "$DATA_DIR" .
  ```

### 5) 권장 사항

- **외부 관리형 서비스** — 운영에서는 컨테이너 DB/MinIO/Temporal 대신 관리형 PostgreSQL·S3·
  Temporal 사용을 권장. 해당 컨테이너를 제거하고 `config/config.properties` 를 외부
  엔드포인트로 지정합니다.
- **TLS / 리버스 프록시** — 외부 노출은 리버스 프록시(예: nginx/Traefik) 뒤에서 TLS 종단.
  내부 포트(`5432`·`9000`·`7233`)는 호스트로 노출하지 마세요.
- **이미지** — `:latest` 금지, 검증된 버전으로 고정. 백엔드/프론트는 사전 빌드 후 레지스트리 push 권장.
- **대규모/오케스트레이션** — Kubernetes(Helm) 배포를 권장.
- 전체 보안 체크리스트는 루트 [README 운영 환경 보안 설정](../README.md#운영-환경-보안-설정-️) 참고.
