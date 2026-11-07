# Argus Catalog 설치 안내 (폐쇄망 · Rocky Linux / RHEL 8·9 x86_64)

모든 패키지는 **인터넷 없이** 설치됩니다. Python 의존성은 패키지 안의 wheel 로, 웹 UI 는 패키지에 들어 있는 Node.js 로 동작합니다.

## 패키지 구성

| 패키지 | 내용 | 서비스(systemd) | 기본 포트 | OS 요구 |
|---|---|---|---|---|
| `argus-catalog-server` | 카탈로그 서버(REST API) + 변경관리 워커 | `argus-catalog-server`, `argus-catalog-worker` | 4600 | `python3.11` |
| `argus-catalog-server-embedding` | (선택) 로컬 임베딩 — sentence-transformers + CPU torch | — (서버에 추가) | — | `argus-catalog-server` |
| `argus-catalog-web` | 웹 UI (Node.js 포함) | `argus-catalog-web` | 3000 | — |
| `argus-catalog-query-service` | 쿼리 수집(collector 수신) + lineage·관계 배치 | `argus-catalog-query-service` | 4620 | `python3.11` |
| `argus-catalog-metadata-sync` | 데이터 소스 메타데이터 동기화 배치 | `argus-catalog-metadata-sync.timer` | — | `python3.11`, `krb5-libs` |
| `argus-catalog-sdk` | Python SDK·CLI (`argus-model`, `argus-catalog`) | — | — | `python3.11` |
| `argus-catalog-ai-service` | (선택) AI 어시스턴트 서버 + 메타데이터 보강 워커·CLI (`argus-catalog-ai`) | `argus-catalog-ai-assistant`, `argus-catalog-ai-enrich` | 8930 | `python3.11` |
| `argus-catalog-collectors` | 엔진 쿼리 수집기 jar (Hive hook, Impala agent, Trino listener) | — (엔진에 등록) | — | 각 엔진의 JVM |

각 패키지는 RPM(`*.x86_64.rpm`)과 tar.gz(`*-x86_64.tar.gz`) 두 형태로 제공됩니다. 단 수집기(`argus-catalog-collectors`)는 jar 만 담은 아키텍처 무관 패키지라 `*.noarch.rpm` · `*-noarch.tar.gz` 입니다. 같은 내용이며 **한 서버에서는 한 형태만** 쓰세요.

외부에 따로 준비할 것:

- **PostgreSQL 16 + pgvector** (또는 MariaDB) — 카탈로그 DB
- (선택) **Temporal** — 변경관리 워크플로, (선택) **Keycloak** — SSO, (선택) **S3 호환 스토리지** — 모델 파일
- (선택) **OpenAI 호환 LLM** (ollama·vLLM 등) — AI 서비스용. 모델 파일은 폐쇄망 반입이 필요합니다

## 0. 준비 — 내려받은 파일 확인

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

Python 3.11 은 OS 의 AppStream 저장소에 있습니다(EL 8.8+ / 9.2+). 사내 미러 또는 설치 DVD 로 설치합니다.

```bash
dnf install -y python3.11
```

## 1. RPM 으로 설치 (권장)

```bash
dnf install -y ./argus-catalog-server-<버전>-1.x86_64.rpm ./argus-catalog-web-<버전>-1.x86_64.rpm
# 필요 시
dnf install -y ./argus-catalog-query-service-<버전>-1.x86_64.rpm ./argus-catalog-metadata-sync-<버전>-1.x86_64.rpm
dnf install -y ./argus-catalog-server-embedding-<버전>-1.x86_64.rpm
```

`dnf` 가 `python3.11` 등 OS 패키지를 사내 저장소에서 함께 설치합니다. 저장소가 전혀 없으면 해당 OS 패키지를 먼저 설치한 뒤 `rpm -ivh` 를 쓰세요.

- 업그레이드: `dnf install -y ./<새 버전>.rpm` — 설정 파일은 유지되고(바뀐 기본값은 `*.rpmnew`), 실행 중인 서비스는 재시작됩니다.
- 제거: `dnf remove argus-catalog-server` — 설정(`/etc/argus-*`)·데이터·로그는 남습니다.

## 2. tar.gz 로 설치

```bash
tar xzf argus-catalog-server-<버전>-x86_64.tar.gz
sudo ./argus-catalog-server-<버전>/install.sh
```

- 업그레이드: 새 버전 tar.gz 를 풀어 같은 방법으로 `install.sh` 실행 — 설정은 유지되고 바뀐 기본값은 `*.new` 로 생깁니다. 이후 서비스를 재시작하세요.
- 제거: `sudo /opt/<패키지>/share/installer/uninstall.sh` (설정·데이터까지 지우려면 `--purge`)
- Python 실행 파일이 `python3.11` 이 아니면 `ARGUS_PYTHON=/경로/python3.11 ./install.sh`

## 3. 설치 위치

| 항목 | 위치 |
|---|---|
| 프로그램 | `/opt/argus-catalog-<이름>/` (Python 은 그 아래 `venv/`) |
| 설정 | `/etc/argus-catalog-<이름>/` (root:argus, 0640) |
| 데이터 / 로그 | `/var/lib/argus-catalog-server/`, `/var/log/argus-catalog-*/` |
| DB 스키마 | `/opt/argus-catalog-server/sql/` |
| systemd unit | RPM: `/usr/lib/systemd/system/`, tar.gz: `/etc/systemd/system/` |
| 실행 계정 | `argus` (설치 시 자동 생성, 로그인 불가) |

## 4. 카탈로그 서버

```bash
# 1) DB 와 스키마 (최초 1회)
sudo -u postgres psql -c "CREATE USER argus PASSWORD '<비밀번호>'" -c "CREATE DATABASE argus_catalog OWNER argus"
sudo -u postgres psql -d argus_catalog -c "CREATE EXTENSION IF NOT EXISTS vector"
psql -h <DB> -U argus -d argus_catalog -f /opt/argus-catalog-server/sql/argus-catalog-postgresql.sql

# 2) 설정 — db.* 는 반드시, 그 외 auth.* / os.* / temporal.* 는 환경에 맞게
vi /etc/argus-catalog-server/config.properties

# 3) 시작
systemctl enable --now argus-catalog-server
systemctl enable --now argus-catalog-worker      # 변경관리(Temporal) 사용 시
curl -s http://127.0.0.1:4600/health
```

- 로컬 인증(`auth.type=local`)이면 첫 기동 때 관리자 계정 `admin` / `admin` 이 만들어지고, 첫 로그인에서 비밀번호 변경을 요구합니다.
- 업그레이드 시 DB 스키마는 같은 SQL 을 다시 적용합니다(이미 있는 객체는 건너뜀). 릴리스 노트의 업그레이드 항목을 함께 확인하세요.
- 설정 파일은 최초 기동 때 DB 에 없는 항목만 채우고, 이후에는 화면(설정 메뉴)에서 바꾼 값이 우선합니다.
- 프록시·`HF_HOME` 같은 추가 환경변수는 `/etc/sysconfig/argus-catalog-server` 에 `KEY=VALUE` 로 둡니다.

### 로컬 임베딩 모델 (server-embedding 사용 시)

폐쇄망에서는 Hugging Face 에서 모델을 받을 수 없습니다. 인터넷이 되는 곳에서 모델 디렉터리를 받아 옮기고,
설정 › 임베딩의 모델 값을 **디렉터리 경로**로 지정합니다.

```bash
# 인터넷 되는 곳
python3 -c "from huggingface_hub import snapshot_download as d; d('sentence-transformers/all-MiniLM-L6-v2', local_dir='all-MiniLM-L6-v2')"
# 설치 서버
mkdir -p /var/lib/argus-catalog-server/models && cp -r all-MiniLM-L6-v2 /var/lib/argus-catalog-server/models/
chown -R argus:argus /var/lib/argus-catalog-server/models
echo HF_HUB_OFFLINE=1 >> /etc/sysconfig/argus-catalog-server
systemctl restart argus-catalog-server
# 설정 › 임베딩 › 모델: /var/lib/argus-catalog-server/models/all-MiniLM-L6-v2
```

## 5. 웹 UI

```bash
vi /etc/argus-catalog-web/web.env      # API_BASE_URL=http://<카탈로그 서버>:4600, PORT
systemctl enable --now argus-catalog-web
```

브라우저는 웹 UI 주소(`http://<서버>:3000`)만 열면 됩니다. API 요청은 웹 UI 가 카탈로그 서버로 전달합니다.

## 6. Query Service

```bash
vi /etc/argus-catalog-query-service/query-service.env   # ARGUS_DB_*, ARGUS_QS_COLLECTOR_TOKEN
systemctl enable --now argus-catalog-query-service
curl -s http://127.0.0.1:4620/health
```

Hive·Impala·Trino·StarRocks collector 의 전송 주소를 `http://<이 서버>:4620` 으로 지정합니다.

## 7. Metadata Sync

```bash
vi /etc/argus-catalog-metadata-sync/config.properties   # DB, catalog.base_url, 데이터 소스
vi /etc/argus-catalog-metadata-sync/metadata-sync.env   # 카탈로그 로그인 계정 또는 토큰
systemctl start argus-catalog-metadata-sync && journalctl -u argus-catalog-metadata-sync -n 50   # 1회 실행 확인
systemctl enable --now argus-catalog-metadata-sync.timer   # 주기 실행 (기본 매시)
```

주기 변경: `systemctl edit argus-catalog-metadata-sync.timer` 에서 `[Timer]` 아래 `OnCalendar=` 를 비우고 새 값을 지정합니다.
Kudu 데이터 소스는 OS 의 Kudu 클라이언트 라이브러리가 필요해 이 패키지에 포함하지 않습니다.

## 8. SDK · CLI

학습·배포 서버(Airflow 워커 등)에 설치합니다. RPM 은 `/usr/bin`, tar.gz 는 `/usr/local/bin` 에 명령이 생깁니다.

```bash
dnf install -y ./argus-catalog-sdk-<버전>-1.x86_64.rpm
export ARGUS_SERVER=http://<카탈로그 서버>:4600 ARGUS_TOKEN=<토큰>   # 또는 ARGUS_USERNAME / ARGUS_PASSWORD
argus-model --help
```

다른 OS·Python(3.11+) 환경에서는 함께 제공되는 `argus_catalog_sdk-<버전>-py3-none-any.whl` 을 `pip install` 하세요
(의존성 httpx·rich·PyYAML 이 필요합니다).

## 9. AI 서비스 (선택)

카탈로그의 AI 어시스턴트(도구 기반 채팅)와 데이터셋 설명·요약·컬럼·태그·PII 자동 생성을 담당합니다.
OpenAI 호환 LLM 서버(ollama·vLLM 등)가 따로 있어야 합니다.

```bash
dnf install -y ./argus-catalog-ai-service-<버전>-1.x86_64.rpm
vi /etc/argus-catalog-ai-service/ai-service.env    # ARGUS_API_URL, ARGUS_AI_LLM_URL, ARGUS_AI_MODEL

# AI 어시스턴트 서버
systemctl enable --now argus-catalog-ai-assistant
curl -s http://127.0.0.1:8930/health
```

카탈로그 화면 **설정 › AI 어시스턴트** 에서 "AI 서비스 기반 어시스턴트"를 켜고 URL 에 `http://<이 서버>:8930` 을
넣은 뒤 연결 테스트 → 저장합니다(재시작 불필요). 설정 파일로 정하려면 카탈로그 서버의 `assistant.service.url`.

메타데이터 보강은 1회 실행 또는 상시 워커로 돌립니다. 결과를 카탈로그에 반입하므로 `ARGUS_USERNAME`/`ARGUS_PASSWORD`
에 관리자 권한 계정이 필요합니다. 기본 설정은 서비스 계정 `argus` 이니, 먼저 카탈로그 **관리 › 사용자 관리** 에서
`argus` 계정을 관리자(argus-admin) 역할로 만들고 그 비밀번호를 `ai-service.env` 의 `ARGUS_PASSWORD` 에 적으세요
(임시 비밀번호로 만들면 최초 비밀번호 변경 전까지 API 호출이 막히니, 한 번 로그인해 바꾼 값을 넣습니다). 그리고 기본(`ARGUS_AI_MODE=suggest`)은 사람이 승인할 제안으로 들어갑니다.

```bash
# 1회 — 데이터 소스 전체 (작업: describe summarize columns tags pii all)
set -a; . /etc/argus-catalog-ai-service/ai-service.env; set +a
argus-catalog-ai enrich all --datasource-id <데이터 소스 ID>

# 상시 워커 — ai-service.env 에 ARGUS_AI_DATASOURCE_ID 지정 후
systemctl enable --now argus-catalog-ai-enrich
```

워커는 데이터 소스 하나를 맡습니다. 여러 개라면 unit 을 복사해 `ARGUS_AI_DATASOURCE_ID` 만 바꿔 띄우세요.
이전 이름(`argus-agent`, 환경변수 `AGENT_*`)은 한 버전 동안 경고와 함께 동작합니다.

## 10. 엔진 수집기 (collectors)

수집기 패키지는 아키텍처 무관(`noarch`)입니다 — 엔진 노드의 CPU 와 상관없이 같은 파일을 씁니다.

```bash
dnf install -y ./argus-catalog-collectors-<버전>-1.noarch.rpm
# 또는 tar.gz
tar xzf argus-catalog-collectors-<버전>-noarch.tar.gz
sudo ./argus-catalog-collectors-<버전>/install.sh
```

각 엔진 노드에 설치하면 `/opt/argus-catalog-collectors/<엔진>/` 에 jar 와 README 가 놓입니다. 엔진 설정에 jar 를
등록하고 전송 주소를 Query Service(`http://<서버>:4620`)로 지정하는 방법은 각 README 를 따릅니다.

| 엔진 | 위치 | 등록 방식 |
|---|---|---|
| Hive | `hive/hive-query-hook-*-jar-with-dependencies.jar` | `hive.aux.jars.path` + `hive.exec.post.hooks` |
| Impala | `impala/impala-query-agent-*.jar` | Impala daemon JVM `-javaagent:` |
| Trino | `trino/trino-query-listener-*.jar` | `plugin/<이름>/` 에 복사 + `event-listener.properties` |
| StarRocks | (빌드 시 FE jar 를 지정한 경우만 포함) | `INSTALL PLUGIN` |

## 11. 방화벽 · SELinux

```bash
firewall-cmd --permanent --add-port=3000/tcp     # 웹 UI
firewall-cmd --permanent --add-port=4600/tcp     # API (웹 UI 와 다른 서버이거나 SDK·외부 연동 시)
firewall-cmd --permanent --add-port=4620/tcp     # Query Service (collector 수신)
firewall-cmd --permanent --add-port=8930/tcp     # AI 서비스 assistant (카탈로그 서버에서만 접근하면 그 서버만 허용)
firewall-cmd --reload
```

서비스는 SELinux enforcing 기본 정책(unconfined_service_t)으로 동작합니다. 포트를 1024 미만으로 바꾸지 마세요.

## 12. 문제 확인

```bash
systemctl status argus-catalog-server
journalctl -u argus-catalog-server -f
tail -f /var/log/argus-catalog-server/argus-catalog-server.log
```
