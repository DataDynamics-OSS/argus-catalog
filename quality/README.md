# Argus Catalog 품질 배치

데이터 품질을 제대로 검사하려면 결국 원본 데이터를 직접 들여다봐야 합니다. 이 배치 스크립트가
바로 그 일을 합니다. 카탈로그 API에서 데이터셋과 품질 규칙을 가져온 다음, 원본 RDBMS를 직접 읽어
**프로파일링·규칙 검증**을 수행하고, 그 결과를 다시 카탈로그로 반입(import)합니다.

| 스크립트 | 엔진 | 용도 |
|---|---|---|
| `python-quality.py` | pandas | 소규모 — 테이블이 메모리에 올라가는 크기(수백만 행 이하) |
| `dataset-quality.py` | PySpark | 대용량 — Spark 클러스터로 분산 처리 |

> 이전 버전의 배치를 스케줄러에 등록해 두었다면 맨 아래 [변경 사항 (마이그레이션)](#변경-사항-마이그레이션)을
> 먼저 확인하세요 — 실행 인자가 바뀌었습니다.

데이터 규모에 따라 둘 중 하나를 고르면 되고, 두 스크립트는 동일한 CLI·흐름·평가 시맨틱을 공유하므로
사용법을 따로 익힐 필요가 없습니다. 공통 로직(API 클라이언트·비밀번호/접속 설정 결정·식별자 인용·
규칙 판정·CUSTOM_SQL 가드·CUSTOM_PYTHON 로더)은 `quality_common.py` 한 곳에 있으며 표준 라이브러리만
사용합니다.

## 서버 내장 검증과의 차이

카탈로그에는 이미 품질 탭의 "검증 실행" 버튼이 있는데, 왜 굳이 배치를 쓸까요? 한마디로 **보는 데이터의 깊이**가
다르기 때문입니다. 서버 검증은 미리 저장해 둔 프로파일 통계를 빠르게 훑는 반면, 이 배치는 원본 전체를 직접 읽어
한 행 한 행 따져 봅니다. 아래 표가 그 차이를 정리한 것입니다.

| | 품질 탭 "검증 실행" (서버) | 이 배치 |
|---|---|---|
| 평가 기준 | 최신 프로파일 스냅숏 통계 | **원본 전체 데이터** |
| ACCEPTED_VALUES / REGEX | 평가하지 않음 — 직전 배치 결과를 이어받고, 없으면 **미평가**(점수 제외) | **위반 행 수 실제 계산** + 위반 샘플 |
| CUSTOM_SQL / CUSTOM_PYTHON | 실행하지 않음(보안) — 위와 같이 이어받기/미평가 | **원본 DB 에서 실행** |
| FRESHNESS(컬럼 미지정) | 마지막 프로파일 이후 경과 시간 | 방금 원본을 읽었으므로 0h(통과) |
| 대용량 처리 | API 서버 자원 | 배치/클러스터 자원 |
| row_count 갱신 | 안 함 | **카탈로그 데이터셋 row_count 갱신** |

배치가 반입한 결과는 이후의 서버 검증(수동·예약)이 덮어쓰지 않습니다. 서버는 자신이 평가할 수 없는
규칙에 새 결과를 남기지 않고, 그 규칙의 **직전 결과를 이어받아**(`carried_forward: true`) 점수에 반영합니다.
배치를 한 번도 돌리지 않은 규칙은 **미평가**(`passed: null`)로 표시되고 점수 분모에서 빠집니다 —
품질 탭 결과 표에는 회색 물음표 아이콘("미평가 — 점수에서 제외")으로 보입니다.

## 동작 흐름

배치가 실행되면 로그인부터 결과 반입까지 다음 순서로 흘러갑니다. 큰 그림을 한눈에 잡아 두면
나중에 문제가 생겼을 때 어느 단계에서 막혔는지 짚어내기 쉽습니다.

```
로그인 → 대상 데이터셋 조회 → 원본 접속 결정(데이터 소스당 1회)
→ (데이터셋마다) 활성 규칙 조회 → 원본 RDBMS 읽기
→ 프로파일 계산(행수·NULL·고유값·min/max·평균·코드성 컬럼 최빈값) → 규칙 평가
→ POST /quality/datasets/{id}/profile/import      (프로파일 반입)
→ PUT  /catalog/datasets/{id}                      (row_count 갱신)
→ POST /quality/datasets/{id}/results/import       (결과 반입 → 점수 산출)
→ 요약표 출력 (실패가 있으면 exit code 1)
```

- 반입 3단계는 관리자 전용 API 입니다. `--dry-run` 이면 반입을 모두 건너뛰고 결과만 출력합니다.
- 활성 규칙이 없는 데이터셋은 프로파일과 row_count 만 반입합니다(서버는 빈 결과 목록을 422 로 거절).
- 결과 반입 점수는 반입한 규칙만이 아니라 데이터셋의 **활성 규칙 전체** 기준입니다(반입에 없는 활성
  규칙은 직전 결과를 이어받고, 없으면 미평가로 제외).

결과를 반입하고 나면(서버 검증도 마찬가지로) 다음 후처리가 자동으로 따라옵니다. 손댈 것 없이
알아서 점수와 상태가 갱신되고, 필요한 알림까지 나갑니다.
- 데이터셋의 **품질 점수/상태**(GOOD·WARN·BAD)가 즉시 갱신됩니다
  (치명 실패 ≥1 또는 점수 < 70 → BAD, 실패 존재 또는 < 90 → WARN)
- 실패한 규칙이 있으면 **거버넌스 > 알림**의 `QUALITY_FAILED` 트리거
  규칙이 평가되어 구독자/소유자에게 알림이 전달됩니다
- 행 단위 위반을 특정할 수 있는 유형(NOT_NULL/UNIQUE/ACCEPTED_VALUES/
  REGEX/MIN·MAX_VALUE)은 **위반 행 샘플(최대 5행)**이 함께 반입되어
  품질 탭 결과에서 "위반 샘플 보기"로 확인할 수 있습니다

### 검증 주기 (서버 스케줄러)

검증을 매번 손으로 돌리고 싶지 않다면 주기를 걸어 둘 수 있습니다.
품질 탭에서 데이터셋별 검증 주기(매시간/매일/매주)를 설정하면 서버가
10분 간격으로 도래 여부를 확인해 **서버 검증(프로파일 기반)**을 자동
실행합니다. 서버 검증은 프로파일을 새로 수집하지 않고(최신 프로파일 재사용), ACCEPTED_VALUES·REGEX·
CUSTOM_* 는 배치 결과를 이어받기만 합니다. 전체 데이터 평가가 필요한 경우에는 이 배치를
cron/Airflow/Kestra 에 등록하세요 — 두 방식은 같은 결과 화면을 공유합니다.

반입된 결과는 데이터셋 상세의 **품질 탭**(점수·결과 표·컬럼 프로파일)에
서버 실행 결과와 동일하게 표시됩니다.

## 설치

어떤 스크립트를 쓸지에 따라 준비물이 조금 다릅니다. pandas 버전은 DB 드라이버만 챙기면 되고,
PySpark 버전은 Spark 환경과 타입별 JDBC 드라이버 jar가 추가로 필요합니다.

```bash
# pandas 버전 — 가상환경 + 전체 DB 드라이버 (드라이버는 지연 임포트라 쓰는 것만 있어도 동작)
python3 -m venv .venv-quality && . .venv-quality/bin/activate
pip install -r quality/requirements.txt

# PySpark 버전 — Spark 환경 + 타입별 JDBC 드라이버 jar 필요.
# 공통 모듈 quality_common.py 를 --py-files 로 함께 배포한다
# (로컬 모드는 스크립트가 자기 디렉터리를 sys.path 에 넣으므로 생략해도 동작).
spark-submit --jars mysql-connector-j-8.4.0.jar --py-files quality/quality_common.py \
    quality/dataset-quality.py ...
```

## 사용법

실제로 어떻게 부르는지 살펴봅시다. 데이터셋 하나만 검사할 수도 있고, 데이터 소스에 속한 전체를
한꺼번에 돌릴 수도 있습니다. 결과만 미리 보고 싶을 땐 `--dry-run` 을 붙이면 됩니다.

```bash
# 비밀번호는 환경변수나 파일로 넘긴다 (명령행 인자는 ps 등 프로세스 목록에 노출됨)
export ARGUS_PASSWORD='<ADMIN_PASSWORD>'               # 카탈로그 계정
export ARGUS_SOURCE_DB_PASSWORD='<SOURCE_DB_PASSWORD>' # 원본 DB 계정

# 단일 데이터셋
python quality/python-quality.py \
    --urn sakila-mysql.sakila.actor.dataset \
    --api-url http://localhost:4600 --username admin

# 데이터 소스 전체 — 등록된 모든 데이터셋을 한 번에 처리
python quality/python-quality.py \
    --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin

# 비밀번호 파일 사용 (첫 줄, 끝 개행 무시)
python quality/python-quality.py --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin \
    --password-file ~/.argus/admin.pw --db-password-file ~/.argus/sakila.pw

# 반입 없이 결과만 확인
python quality/python-quality.py --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin --dry-run

# 일반 계정 — 카탈로그 연결 설정 조회(403)·반입(관리자 전용) 권한이 없으므로
# 원본 접속을 직접 지정하고 --dry-run 으로 실행한다
ARGUS_PASSWORD='<USER_PASSWORD>' python quality/python-quality.py \
    --urn sakila-mysql.sakila.rental.dataset \
    --api-url http://localhost:4600 --username <USER> \
    --db-host 127.0.0.1 --db-port 3306 --db-user sakila --dry-run

# PySpark 버전 (동일 인자 + 분산 읽기 옵션)
spark-submit --jars mysql-connector-j-8.4.0.jar --py-files quality/quality_common.py \
    quality/dataset-quality.py \
    --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin \
    --partition-column rental_id --num-partitions 8
```

`--password` / `--db-password` / `--jdbc-password` 인자도 하위 호환으로 그대로 받지만 권장하지 않습니다.
비밀번호 우선순위는 **명령행 값 > 파일 > 환경변수** 입니다. 카탈로그 계정 비밀번호를 어디서도 찾지 못하면
안내 메시지와 함께 바로 종료합니다(`--password` 는 필수 인자가 아닙니다).

종료 코드: 모든 데이터셋이 처리되면 `0`, 데이터셋 단위 실패가 하나라도 있으면 `1`, 로그인·대상 조회·원본
접속 결정처럼 전체에 영향을 주는 오류는 메시지와 함께 즉시 종료(`1`)합니다.

### 주요 인자

| 인자 | 설명 |
|---|---|
| `--urn` / `--datasource-id` | 둘 중 하나 필수 — 단일 데이터셋 / 데이터 소스 일괄 |
| `--api-url` | 카탈로그 API 서버 (기본 `http://localhost:4600`) |
| `--api-timeout` | API 요청 타임아웃(초, 기본 60) |
| `--username` | 카탈로그 계정 (필수) — 반입 API 가 관리자 전용이라 실제 반입은 관리자 계정, 일반 계정은 `--dry-run` |
| `--password-file` / env `ARGUS_PASSWORD` / `--password` | 카탈로그 계정 비밀번호 (셋 중 하나) |
| `--dry-run` | API 반입 없이 프로파일/검증 결과만 출력 |
| `--checks-dir` | CUSTOM_PYTHON 모듈 디렉터리 (기본: 스크립트 위치/custom_checks) |
| `--source-timezone` / env `ARGUS_SOURCE_TIMEZONE` | 원천 데이터 시간대 — FRESHNESS 에서 시간대 없는 시각 값(MySQL `DATETIME`, PG `timestamp` 등) 해석. IANA 이름(`Asia/Seoul`, `UTC`) 또는 `+09:00`. 기본 `Asia/Seoul`. 서버 설정 `quality_source_timezone`(`GET/PUT /api/v1/quality/settings`, 기본 `Asia/Seoul`)과 같게 둔다 |
| `--db-password-file` / env `ARGUS_SOURCE_DB_PASSWORD` / `--db-password` | (pandas) 원본 DB 비밀번호 |
| `--jdbc-password-file` / env `ARGUS_SOURCE_DB_PASSWORD` / `--jdbc-password` | (PySpark) 원본 DB 비밀번호 |
| `--db-host/--db-port/--db-name/--db-user/--db-ssl` | (pandas) 원본 접속 직접 지정 (`--db-port/--db-name/--db-user/--db-ssl` 은 카탈로그 설정 덮어쓰기에도 사용) |
| `--jdbc-url/--jdbc-user` | (PySpark) 원본 접속 직접 지정 (`--jdbc-user` 는 카탈로그 설정의 계정 덮어쓰기에도 사용) |
| `--jdbc-driver` | (PySpark) JDBC 드라이버 클래스 (기본: 타입별 표준) |
| `--partition-column` / `--num-partitions` | (PySpark) 병렬 JDBC 읽기 (파티션 수 기본 8) — 아래 "대용량 테이블" 참고 |
| `--fetchsize` | (PySpark) JDBC fetchsize (기본 10000) |
| `--approx-distinct` | (PySpark) 프로파일 고유값 수를 `approx_count_distinct` 로 근사 |

### 원본 DB 접속 정보

접속 정보를 매번 손으로 넘길 필요는 없습니다.
기본적으로 **카탈로그의 데이터 소스 연결 설정**(데이터 소스 화면에서 등록한
host/port/database/계정/SSL)을 읽어 자동으로 접속하기 때문입니다. 연결 설정이 없거나 쓸 수 없을 때는
위의 `--db-*` / `--jdbc-*` 인자로 직접 지정하면 됩니다.

- **비밀번호는 카탈로그에서 오지 않습니다.** 연결 설정 API 는 비밀값을 빈 문자열로 마스킹하고
  `secret_fields` 로 "저장돼 있음"만 알려 줍니다. 그래서 원본 DB 비밀번호는 배치 쪽에서
  `--db-password-file`(PySpark `--jdbc-password-file`) 또는 환경변수 `ARGUS_SOURCE_DB_PASSWORD` 로
  넘겨야 합니다. 저장된 비밀번호가 있는데(`secret_fields` 에 `password`) 배치에 주지 않으면
  안내 메시지와 함께 종료합니다. 비밀번호 없이 등록된 계정은 빈 비밀번호로 접속합니다.
- **권한**: 연결 설정 조회는 기능 권한 `datasources.connection`(기본 admin 전용)이 필요합니다.
  403 이면 배치 계정에 권한을 주거나, `--db-host/--db-user`(PySpark `--jdbc-url/--jdbc-user`)와
  비밀번호로 원본 접속을 직접 지정하세요. 일반 계정은 반입 권한도 없으므로 `--dry-run` 과 함께 씁니다.
- `--db-host` 를 주면 카탈로그 연결 설정을 아예 조회하지 않습니다. 포트는 타입별 표준값, DB 명은
  데이터셋 이름의 첫 부분(`sakila.rental` → `sakila`)이 기본이며, SSL 은 `--db-ssl` 로만 켭니다.
  `--db-host` 없이 `--db-port/--db-name/--db-user` 를 주면 카탈로그 설정 값 중 그 항목만 덮어씁니다.
- 타입별 설정 키: RDBMS `host/port/database/username/ssl_enabled`, Trino `catalog`(=database)·`schema`·`use_ssl`,
  Impala `use_ssl`·`auth_type`(LDAP 이면 `ldap_username/ldap_password`), Snowflake `account`(=host)·
  `warehouse/schema/role`. Hive 는 카탈로그 설정에 HiveServer2 호스트가 없으므로 직접 지정해야 합니다.

#### SSL

카탈로그 설정의 SSL 토글(`ssl_enabled`, Trino/Impala 는 `use_ssl`)을 따릅니다. 켜져 있으면 서버의
`connection_ssl` 과 같은 수준으로 **암호화를 강제하되 서버 인증서는 검증하지 않습니다**(카탈로그에 CA
설정 항목이 없음). 꺼져 있거나 설정이 없으면 드라이버 기본값입니다. best-effort 로 다음과 같이 매핑합니다.

| 타입 | pandas 드라이버 | JDBC URL |
|---|---|---|
| mysql/mariadb/starrocks | pymysql `ssl=`(검증 없는 SSLContext) | `?sslMode=REQUIRED` |
| postgresql/greenplum | psycopg2 `sslmode=require` | `?sslmode=require` |
| redshift | `ssl=True, sslmode=require` | `?ssl=true` |
| mssql | pymssql `encryption=require`(미지원 버전은 freetds.conf) | `encrypt=true;trustServerCertificate=true` |
| oracle | `tcps://` DSN, `ssl_server_dn_match=False` | `@tcps://host:port/service` |
| trino | `http_scheme=https` (비밀번호가 있으면 항상 https + Basic 인증) | `?SSL=true` |
| hive/impala | impyla `use_ssl=True` | `;ssl=true` / `;SSL=1` |
| snowflake | 항상 TLS | 항상 TLS |
| tibero | 미지원 | 미지원 |

MSSQL JDBC 는 SSL 이 꺼져 있거나 설정이 없으면 종전대로 `encrypt=false` 를 붙입니다(드라이버 10.x 이상은
기본이 `encrypt=true` 라 명시하지 않으면 인증서 오류가 날 수 있음). pandas 에서 카탈로그 설정 없이 직접
지정할 때는 `--db-ssl` 로 켤 수 있고, PySpark 직접 지정은 `--jdbc-url` 에 파라미터를 직접 넣습니다.

#### 테이블 이름 인용

데이터셋 이름(MySQL 계열 `database.table`, PostgreSQL 계열 `schema.table`)을 점 단위로 나눠 방언별로
인용합니다 — MySQL/MariaDB/StarRocks/Hive/Impala 는 백틱, PostgreSQL/Greenplum/Redshift/Oracle/Tibero/
Trino/Snowflake 는 큰따옴표, MSSQL 은 대괄호(내부의 인용 문자는 두 번 써서 이스케이프). 예약어나
특수문자가 든 이름도 안전하게 읽습니다.

> Oracle/Snowflake 는 인용하면 이름이 **대소문자를 구분**합니다. 데이터셋 이름은 동기화 시 원본
> 카탈로그에 저장된 대소문자(보통 대문자) 그대로 들어오므로 그대로 인용하는 것이 맞습니다. 이름을
> 손으로 바꿔 등록한 데이터셋은 원본의 실제 대소문자와 일치해야 합니다.

지원 타입 (pandas 드라이버 / PySpark JDBC 모두):

| 계열 | 타입 |
|---|---|
| MySQL 프로토콜 | mysql, mariadb, starrocks |
| PostgreSQL 프로토콜 | postgresql, greenplum, redshift |
| 상용 RDBMS | oracle, tibero, mssql(sqlserver) |
| SQL 엔진 | trino, hive, impala |
| 클라우드 DW | snowflake (host 자리에 account 식별자) |

## 규칙 평가 시맨틱

규칙마다 "어떤 조건이면 통과인가"가 정해져 있습니다. 헷갈릴 수 있는 부분은 배치가 서버와
완전히 같은 기준을 쓴다는 점인데, 덕분에 서버 검증과 배치 검증의 점수가 어긋나지 않습니다
(기준 코드는 `app/quality/service.py`).

| 규칙 | 통과 조건 |
|---|---|
| NOT_NULL | 비-NULL 비율 ≥ threshold(%) — `비-NULL 건수 × 100 ≥ threshold × 행 수` 로 **정확한 건수 비교** |
| UNIQUE | 고유값 비율 ≥ threshold(%) — `고유값 수 × 100 ≥ threshold × 행 수` |
| MIN_VALUE / MAX_VALUE | min ≥ 기댓값 / max ≤ 기댓값 |
| ROW_COUNT | 전체 행 수 ≥ 기댓값 |
| ACCEPTED_VALUES | 허용 목록(쉼표 구분) 외 값 0건 — **전체 데이터 검사** |
| REGEX | 패턴 불일치 0건 (부분 일치 시맨틱) — **전체 데이터 검사** |
| FRESHNESS | 컬럼 지정 시: 그 컬럼의 **실제 데이터 최대값**(최신 시각) 이후 경과 시간 ≤ 기댓값(시간, 기본 24). 컬럼 미지정 시: 방금 원본을 읽었으므로 0h 로 통과 |
| CUSTOM_SQL | 기대값의 SELECT 쿼리를 원본에서 실행 — **첫 행 첫 컬럼 = 위반 건수** (0 = 통과). 조인·집계 등 복잡 검증용, **배치 전용** |
| CUSTOM_PYTHON | `custom_checks/` 플러그인 함수의 반환값 `(passed, actual, detail)` 그대로, **배치 전용** |

규칙 1건의 평가 오류(잘못된 임계값·정규식, SQL 실행 오류, 플러그인 오류, 컬럼 없음 등)는 배치를 멈추지 않고
그 규칙의 **실패 결과**(`actual_value: N/A`, `detail` 에 사유)로 기록됩니다.

- **threshold**(NOT_NULL·UNIQUE 만 사용)가 비어 있으면 100%, `0` 은 0% 로 그대로 존중합니다(항상 통과).
  규칙 API 의 기본값도 100 입니다.
- 통과 판정은 반올림 전 정확한 건수로 합니다. 예를 들어 200,000행 중 1행이 NULL(99.9995%)이면 표시값을
  반올림해 100% 로 보이더라도 임계값 100% 는 **실패**이고, 결과 표시는 `99.9995%` 처럼 판정과 어긋나지
  않게 자릿수를 늘립니다. 상세에는 `비-NULL 199,999/200,000행` 처럼 건수도 함께 남깁니다.
- 빈 테이블(0행)은 서버와 같이 NOT_NULL 은 비-NULL 100%(통과), UNIQUE 는 고유 0%(임계값 0 일 때만 통과)로 봅니다.
- **FRESHNESS 시각 해석**: 날짜/타임스탬프 값 또는 ISO 형식 문자열을 받습니다. 시간대 없는 값은 서버
  `_parse_timestamp` 와 같은 규칙으로 **원천 데이터 시간대**(`--source-timezone` / `ARGUS_SOURCE_TIMEZONE`,
  기본 `Asia/Seoul`)의 시각으로 해석합니다 — 배치 호스트의 시간대와 무관합니다. 서버 설정
  `quality_source_timezone`(기본 `Asia/Seoul`)과 같은 값이면 두 엔진의 경과 시간이 일치합니다. 시간대가 있는
  값(PG `timestamptz` 등)은 그 시간대 그대로 씁니다. PySpark 는 타임스탬프 최대값을 Spark 세션 시간대(기본 JVM
  시간대 — JDBC 가 값을 읽을 때 쓴 시간대)의 벽시계 문자열로 받아 원천 값 그대로 해석합니다(`spark.sql.session.timeZone`
  을 JVM 시간대와 다르게 두지 마세요). 해석할 수 없거나 값이 모두 NULL 이면
  실패로 기록합니다. 미래 시각은 0h 로 봅니다. 문자열 컬럼은 사전순 최대값을 쓰므로 ISO 형식이어야 정확합니다.

### CUSTOM_SQL 작성 규약

조인이나 집계가 얽힌 검증은 기본 규칙만으로 표현하기 어렵습니다. 이럴 때 직접 SQL을 써서
"위반 건수"를 세어 내는 것이 CUSTOM_SQL 입니다. 규약은 간단합니다 — 쿼리 결과의 첫 행 첫 컬럼이
위반 건수여야 하고, 그 값이 0이면 통과입니다.

```sql
-- 규약: 결과 첫 행 첫 컬럼이 위반 건수여야 한다 (0 이면 통과)
SELECT count(*)
FROM rental r
LEFT JOIN inventory i ON i.inventory_id = r.inventory_id
WHERE i.inventory_id IS NULL          -- 고아 대여 건
```

- **SELECT/WITH 단일 문장만 허용** — 배치가 주석 제거 후 검사하며
  INSERT/UPDATE/DELETE/DDL 키워드가 있으면 실행을 거부한다 (2차 방어선)
- **1차 방어선은 read-only 계정**: 카탈로그의 데이터 소스 연결 설정에
  조회 전용 계정을 등록할 것을 강력 권장
- 서버의 "검증 실행" 버튼은 보안상 커스텀 SQL 을 실행하지 않는다 — 직전 배치 결과를 이어받아
  점수에 반영하고, 배치를 돌린 적이 없으면 미평가(점수 제외)로 표시한다
- PySpark 버전은 JDBC `query` 옵션으로 실행한다(Spark 가 `(sql) SPARK_GEN_SUBQ_n` 처럼 AS 없는 별칭으로
  감싸므로 Oracle/Tibero 와 `WITH` 쿼리도 동작). 단 MSSQL 은 하위 쿼리 안의 CTE 를 허용하지 않으므로
  MSSQL 에서는 `WITH` 대신 파생 테이블로 작성할 것
- pandas 버전은 실패한 쿼리 뒤 트랜잭션을 정리(autocommit + rollback)하므로 한 규칙의 SQL 오류가
  다음 규칙·데이터셋을 막지 않는다

### CUSTOM_PYTHON 플러그인 규약

SQL로도 손이 닿지 않는 검증이 있습니다. 통계 검정이나 분포 비교, 외부 기준과의 대조처럼
계산이 필요한 로직이 그렇습니다. 이런 경우엔 `quality/custom_checks/` 에 Python 함수로 직접 작성합니다.

```python
# quality/custom_checks/rental_checks.py
def rental_duration_outlier(df, params) -> tuple[bool, str, str]:
    """대여 기간 IQR 이상치 비율 검사."""
    ...
    return passed, f"{pct:.2f}%", "대여 기간 IQR 이상치 ..."
```

규칙의 기대값(JSON):

```json
{"module": "rental_checks", "fn": "rental_duration_outlier",
 "params": {"max_outlier_pct": 1.0}}
```

- **함수 규약**: `fn(df, params) -> (passed: bool, actual: str, detail: str)`
- `df` 는 실행 엔진의 DataFrame — pandas/PySpark 양쪽을 지원하려면
  `hasattr(df, "toPandas")` 로 분기 (예시 모듈 참고)
- 모듈/함수 이름은 식별자만 허용(경로 탈출 차단), 로드는 `--checks-dir`
  (기본: 스크립트 위치/custom_checks) 내부로 한정
- **플러그인은 git 으로 리뷰되는 신뢰 코드라는 전제** — 외부 입력으로
  모듈 이름을 받지 말 것. 서버는 실행하지 않는다(직전 배치 결과 이어받기, 없으면 미평가)
- 모듈 파일·함수가 없거나, 함수가 예외를 내거나 3-튜플이 아닌 값을 돌려주면 그 규칙만 실패로 기록

## 배치 운영

운영 환경에 올려 두고 쓸 때 알아 두면 좋은 동작들입니다. 일괄 처리 중 하나가 넘어져도 전체가
멈추지 않도록 설계되어 있어, 야간 스케줄에 걸어 두기에 부담이 없습니다.

- 일괄 모드는 **한 데이터셋이 실패해도 나머지를 계속** 처리하고, 종료 시
  요약표를 출력합니다. 원본 읽기 오류뿐 아니라 규칙 조회·반입 같은 **API 오류도 해당 데이터셋의
  실패로 기록**하고 다음으로 넘어갑니다. 실패가 있으면 exit code 1 (스케줄러 알림 연동용).
- 로그인 실패, 대상 데이터셋 조회 실패, 원본 접속 정보 결정/접속 실패(비밀번호 미지정·연결 설정 403 포함)처럼
  전체에 영향을 주는 오류만 즉시 종료합니다. 오류 메시지에 비밀번호 값은 싣지 않습니다.
- 모든 API 요청에는 타임아웃(`--api-timeout`, 기본 60초)이 걸립니다. 긴 배치 중 access token 이 만료되어
  401 이 오면 refresh token(`POST /auth/refresh`)으로 1회 재발급하고(실패하면 같은 자격증명으로 재로그인)
  요청을 1회 재시도합니다.
- 같은 데이터 소스 안에서는 접속을 1회만 결정해 재사용합니다. pandas 버전은 커넥션 1개를 재사용하되
  autocommit 과 실패 시 rollback 으로 데이터셋끼리 격리합니다(PostgreSQL 에서 한 쿼리가 실패해도
  트랜잭션 abort 상태가 다음 규칙·데이터셋으로 번지지 않음).
- cron / Airflow / Kestra 등에 등록해 주기 실행하는 것을 권장합니다.
  (데이터셋 상세의 PySpark/Kestra/Airflow 탭에서 적재용 워크플로우 예시 참고)

### 대용량 테이블 (PySpark)

- `--partition-column <컬럼> --num-partitions 8`: 먼저 작은 JDBC 쿼리로 그 컬럼의 min/max 를 구한 뒤
  `partitionColumn/lowerBound/upperBound/numPartitions` 로 읽기를 나눠 **여러 executor 가 병렬로** 원본을
  읽습니다. 컬럼은 숫자/날짜/타임스탬프여야 하며, 테이블에 없거나 분할할 수 없는 타입이면 경고 후 단일
  파티션으로 읽습니다(데이터 소스 일괄 실행 시 테이블마다 컬럼이 다를 수 있으므로). 파티션 수만큼 원본에
  동시 커넥션이 열린다는 점을 고려하세요.
- `--fetchsize`: JDBC 왕복당 행 수 (기본 10000).
- 읽은 데이터는 `persist(MEMORY_AND_DISK)` 로 보관해 메모리가 부족하면 디스크로 넘깁니다.
- `--approx-distinct`: 프로파일의 고유값 수를 `approx_count_distinct`(HyperLogLog++, 상대오차 약 5%)로
  근사해 큰 테이블의 셔플 비용을 줄입니다. **UNIQUE 규칙이 걸린 컬럼은 정확히 다시 세어** 판정합니다.
  기본값은 정확 계산입니다.
- 점(.)·공백 등 특수문자가 든 컬럼 이름도 백틱 인용으로 안전하게 참조합니다.

### 테스트

```bash
cd quality && python -m pytest -q tests   # quality_common.py 단위 테스트 (pandas/pyspark 불필요, pytest 만)
```

비밀번호 결정, 연결 설정 마스킹·403, 식별자 인용, 임계값·정확한 건수 판정, FRESHNESS, CUSTOM_SQL 가드,
CUSTOM_PYTHON 로더, API 타임아웃·401 재발급, 요약 종료 코드를 다룹니다. 통합 테스트 시나리오는
`design/integration-test/24-quality-batch.md` 를 참고하세요.

## 로컬 데모 (Sakila)

글로만 읽기보다 한 번 직접 돌려 보는 편이 빠릅니다. 공개 샘플 데이터베이스인 Sakila로
처음부터 끝까지 따라 해 볼 수 있도록 아래 세 단계를 준비했습니다.

```bash
# 1) Sakila MariaDB 기동 (localhost:3306, sakila/sakila/sakila)
./deploy/download-sakila.sh
docker compose -f deploy/docker-compose.infra.yml up -d mariadb-sakila

# 2) 시드 적용 (데이터셋 16개 + 품질 규칙 51건 + 접속 설정)
psql -U argus -d argus_catalog -f backend/packaging/config/argus-catalog-seed-sakila.sql

# 3) 일괄 실행 (원본 비밀번호는 Sakila 기본값 sakila)
export ARGUS_PASSWORD='...'
export ARGUS_SOURCE_DB_PASSWORD='sakila'
python quality/python-quality.py --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin
```

## 변경 사항 (마이그레이션)

품질 배치 리뷰 반영(2026-11) 이후 실행 방법과 서버 점수 계산이 바뀌었습니다. 기존 cron/Airflow/Kestra
등록을 아래에 맞춰 고쳐 주세요.

| 항목 | 이전 | 지금 | 조치 |
|---|---|---|---|
| spark-submit | 스크립트 하나만 제출 | 공통 모듈 `quality_common.py` 를 함께 배포 | `--py-files quality/quality_common.py` 추가 (클러스터 모드 필수) |
| 원본 DB 비밀번호 | 카탈로그 연결 설정에서 읽음 | 카탈로그는 비밀번호를 내려주지 않음(마스킹) | `ARGUS_SOURCE_DB_PASSWORD` 또는 `--db-password-file`/`--jdbc-password-file` 로 명시. 없으면 안내 후 종료 |
| 카탈로그 계정 비밀번호 | `--password` 필수 | `--password-file` / `ARGUS_PASSWORD` 권장 (`--password` 는 선택) | 명령행에서 비밀번호를 빼고 환경변수·파일로 |
| 일반(비관리자) 계정 | 연결 설정을 읽어 실행 | 연결 설정 조회는 `datasources.connection` 권한 필요(403), 반입은 관리자 전용 | `--db-host/--db-user`(PySpark `--jdbc-url/--jdbc-user`)로 직접 지정 + `--dry-run`, 또는 관리자 계정 사용 |
| Trino | 비밀번호 인증 없이 http | 비밀번호가 있으면 https + BasicAuth | Trino 가 HTTPS 를 받도록 구성하거나 비밀번호 없는 계정 사용 |
| SSL | 무시 | 카탈로그 설정의 SSL 토글 반영, pandas 는 `--db-ssl` | 원본이 SSL 을 지원하지 않는데 토글이 켜져 있으면 접속 실패 — 설정 확인 |
| API 호출 | 타임아웃 없음, 토큰 만료 시 실패 | `--api-timeout`(기본 60초), 401 시 1회 재발급 | 느린 서버면 `--api-timeout` 상향 |
| 데이터셋 단위 API 오류 | 배치 전체 중단 | 그 데이터셋만 실패로 기록하고 계속, exit code 1 | 스케줄러에서 exit code 로 실패 감지 |
| 서버 점수 | 서버가 평가할 수 없는 규칙(ACCEPTED_VALUES·REGEX·CUSTOM_*)을 "평가 제외" **통과**로 집계 — 배치가 찾은 실패를 다음 서버 검증이 덮어씀 | 직전 배치 결과를 이어받아 반영, 배치 결과가 없으면 미평가(점수 제외) | 배치를 주기 실행해야 해당 규칙이 점수에 반영됨. 배치를 돌린 적 없는 데이터셋은 점수 분모가 줄어 점수가 달라질 수 있음 |
| 판정 | threshold 0 이 100 으로 처리, NOT_NULL·UNIQUE 를 반올림 비율로 판정 | threshold 0 존중, 정확한 건수로 판정 | 큰 테이블의 NULL·중복 1건도 임계값 100 에서 실패로 잡힘 |
| FRESHNESS | 컬럼과 무관하게 항상 0h 로 통과 | 컬럼 지정 시 그 컬럼의 실제 최대값(최신 데이터 시각) 기준, 미지정 시 종전대로 0h 통과 | 적재·수정 시각 컬럼을 규칙에 지정 |
