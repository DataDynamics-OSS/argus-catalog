# Argus Catalog 품질 배치

카탈로그 API에서 데이터셋과 품질 규칙을 가져와, 원본 RDBMS를 직접 읽어
**프로파일링·규칙 검증**을 수행하고 결과를 카탈로그로 반입(import)하는 배치 스크립트입니다.

| 스크립트 | 엔진 | 용도 |
|---|---|---|
| `python-quality.py` | pandas | 소규모 — 테이블이 메모리에 올라가는 크기(수백만 행 이하) |
| `dataset-quality.py` | PySpark | 대용량 — Spark 클러스터로 분산 처리 |

두 스크립트는 동일한 CLI·흐름·평가 시맨틱을 공유합니다.

## 서버 내장 검증과의 차이

| | 품질 탭 "검증 실행" (서버) | 이 배치 |
|---|---|---|
| 평가 기준 | 프로파일 스냅숏 통계 | **원본 전체 데이터** |
| ACCEPTED_VALUES / REGEX | profile-only (평가 생략) | **위반 행 수 실제 계산** |
| 대용량 처리 | API 서버 자원 | 배치/클러스터 자원 |
| row_count 갱신 | 안 함 | **카탈로그 데이터셋 row_count 갱신** |

## 동작 흐름

```
로그인(관리자) → 데이터셋·활성 규칙 조회 → 원본 RDBMS 읽기
→ 프로파일 계산(행수·NULL·고유값·min/max·평균·코드성 컬럼 최빈값) → 규칙 평가
→ POST /quality/datasets/{id}/profile/import      (프로파일 반입)
→ PUT  /catalog/datasets/{id}                      (row_count 갱신)
→ POST /quality/datasets/{id}/results/import       (결과 반입 → 점수 산출)
```

반입(또는 서버 검증) 시 자동 후처리:
- 데이터셋의 **품질 점수/상태**(GOOD·WARN·BAD)가 즉시 갱신됩니다
  (치명 실패 ≥1 또는 점수 < 70 → BAD, 실패 존재 또는 < 90 → WARN)
- 실패한 규칙이 있으면 **거버넌스 > 알림**의 `QUALITY_FAILED` 트리거
  규칙이 평가되어 구독자/소유자에게 알림이 전달됩니다
- 행 단위 위반을 특정할 수 있는 유형(NOT_NULL/UNIQUE/ACCEPTED_VALUES/
  REGEX/MIN·MAX_VALUE)은 **위반 행 샘플(최대 5행)**이 함께 반입되어
  품질 탭 결과에서 "위반 샘플 보기"로 확인할 수 있습니다

### 검증 주기 (서버 스케줄러)

품질 탭에서 데이터셋별 검증 주기(매시간/매일/매주)를 설정하면 서버가
10분 간격으로 도래 여부를 확인해 **서버 검증(프로파일 기반)**을 자동
실행합니다. 전체 데이터 평가(CUSTOM_* 포함)가 필요한 경우에는 이 배치를
cron/Airflow/Kestra 에 등록하세요 — 두 방식은 같은 결과 화면을 공유합니다.

반입된 결과는 데이터셋 상세의 **품질 탭**(점수·결과 표·컬럼 프로파일)에
서버 실행 결과와 동일하게 표시됩니다.

## 설치

```bash
# pandas 버전 — 전체 DB 드라이버 포함
pip install -r quality/requirements.txt

# PySpark 버전 — Spark 환경 + 타입별 JDBC 드라이버 jar 필요
spark-submit --jars mysql-connector-j-8.4.0.jar ...
```

## 사용법

```bash
# 단일 데이터셋
python quality/python-quality.py \
    --urn sakila-mysql.sakila.actor.dataset \
    --api-url http://localhost:4600 \
    --username admin --password '<ADMIN_PASSWORD>'

# 데이터 소스 전체 — 등록된 모든 데이터셋을 한 번에 처리
python quality/python-quality.py \
    --datasource-id sakila-mysql \
    --api-url http://localhost:4600 \
    --username admin --password '<ADMIN_PASSWORD>'

# 반입 없이 결과만 확인 (일반 사용자 계정도 가능)
python quality/python-quality.py --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username user1 --password '...' --dry-run

# PySpark 버전 (동일 인자)
spark-submit --jars mysql-connector-j-8.4.0.jar quality/dataset-quality.py \
    --datasource-id sakila-mysql \
    --api-url http://localhost:4600 \
    --username admin --password '<ADMIN_PASSWORD>'
```

### 주요 인자

| 인자 | 설명 |
|---|---|
| `--urn` / `--datasource-id` | 둘 중 하나 필수 — 단일 데이터셋 / 데이터 소스 일괄 |
| `--api-url` | 카탈로그 API 서버 (기본 `http://localhost:4600`) |
| `--username` / `--password` | 관리자 계정 — 반입 API 가 admin 전용 (`--dry-run` 은 일반 계정 가능) |
| `--dry-run` | API 반입 없이 프로파일/검증 결과만 출력 |
| `--db-host/--db-port/--db-name/--db-user/--db-password` | (pandas) 원본 접속 직접 지정 |
| `--jdbc-url/--jdbc-user/--jdbc-password/--jdbc-driver` | (PySpark) 원본 접속 직접 지정 |

### 원본 DB 접속 정보

기본적으로 **카탈로그의 데이터 소스 연결 설정**(데이터 소스 화면에서 등록한
host/port/database/계정)을 읽어 자동 접속합니다. 연결 설정이 없으면 위의
`--db-*` / `--jdbc-*` 인자로 직접 지정합니다.

지원 타입 (pandas 드라이버 / PySpark JDBC 모두):

| 계열 | 타입 |
|---|---|
| MySQL 프로토콜 | mysql, mariadb, starrocks |
| PostgreSQL 프로토콜 | postgresql, greenplum, redshift |
| 상용 RDBMS | oracle, tibero, mssql(sqlserver) |
| SQL 엔진 | trino, hive, impala |
| 클라우드 DW | snowflake (host 자리에 account 식별자) |

## 규칙 평가 시맨틱

서버(`app/quality/service.py`)와 동일한 기준입니다.

| 규칙 | 통과 조건 |
|---|---|
| NOT_NULL | 비-NULL 비율 ≥ threshold(%) |
| UNIQUE | 고유값 비율 ≥ threshold(%) |
| MIN_VALUE / MAX_VALUE | min ≥ 기댓값 / max ≤ 기댓값 |
| ROW_COUNT | 전체 행 수 ≥ 기댓값 |
| ACCEPTED_VALUES | 허용 목록(쉼표 구분) 외 값 0건 — **전체 데이터 검사** |
| REGEX | 패턴 불일치 0건 (부분 일치 시맨틱) — **전체 데이터 검사** |
| FRESHNESS | 배치가 방금 원본을 읽었으므로 데이터 나이 0h 로 평가 |
| CUSTOM_SQL | 기대값의 SELECT 쿼리를 원본에서 실행 — **첫 행 첫 컬럼 = 위반 건수** (0 = 통과). 조인·집계 등 복잡 검증용, **배치 전용** |

### CUSTOM_SQL 작성 규약

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
- 서버의 "검증 실행" 버튼은 보안상 커스텀 SQL 을 실행하지 않고
  "평가 제외"로 표시한다 — 점수에는 영향 없음 (배치 실행 시 실평가)

### CUSTOM_PYTHON 플러그인 규약

SQL 로 표현하기 어려운 로직(통계 검정, 분포 비교, 외부 기준 대조)은
`quality/custom_checks/` 에 Python 함수로 작성한다.

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
  모듈 이름을 받지 말 것. 서버는 실행하지 않고 "평가 제외" 표시

## 배치 운영

- 일괄 모드는 **한 데이터셋이 실패해도 나머지를 계속** 처리하고, 종료 시
  요약표를 출력합니다. 실패가 있으면 exit code 1 (스케줄러 알림 연동용).
- 같은 데이터 소스 안에서는 접속을 1회만 결정해 재사용합니다.
- cron / Airflow / Kestra 등에 등록해 주기 실행하는 것을 권장합니다.
  (데이터셋 상세의 PySpark/Kestra/Airflow 탭에서 적재용 워크플로우 예시 참고)

## 로컬 데모 (Sakila)

```bash
# 1) Sakila MariaDB 기동 (localhost:3306, sakila/sakila/sakila)
./deploy/download-sakila.sh
docker compose -f deploy/docker-compose.infra.yml up -d mariadb-sakila

# 2) 시드 적용 (데이터셋 16개 + 품질 규칙 46건 + 접속 설정)
psql -U argus -d argus_catalog -f backend/packaging/config/argus-catalog-seed-sakila.sql

# 3) 일괄 실행
python quality/python-quality.py --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin --password '...'
```
