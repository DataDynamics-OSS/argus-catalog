# Argus Catalog 품질 배치

데이터 품질을 제대로 검사하려면 결국 원본 데이터를 직접 들여다봐야 합니다. 이 배치 스크립트가
바로 그 일을 합니다. 카탈로그 API에서 데이터셋과 품질 규칙을 가져온 다음, 원본 RDBMS를 직접 읽어
**프로파일링·규칙 검증**을 수행하고, 그 결과를 다시 카탈로그로 반입(import)합니다.

| 스크립트 | 엔진 | 용도 |
|---|---|---|
| `python-quality.py` | pandas | 소규모 — 테이블이 메모리에 올라가는 크기(수백만 행 이하) |
| `dataset-quality.py` | PySpark | 대용량 — Spark 클러스터로 분산 처리 |

데이터 규모에 따라 둘 중 하나를 고르면 되고, 두 스크립트는 동일한 CLI·흐름·평가 시맨틱을 공유하므로
사용법을 따로 익힐 필요가 없습니다.

## 서버 내장 검증과의 차이

카탈로그에는 이미 품질 탭의 "검증 실행" 버튼이 있는데, 왜 굳이 배치를 쓸까요? 한마디로 **보는 데이터의 깊이**가
다르기 때문입니다. 서버 검증은 미리 저장해 둔 프로파일 통계를 빠르게 훑는 반면, 이 배치는 원본 전체를 직접 읽어
한 행 한 행 따져 봅니다. 아래 표가 그 차이를 정리한 것입니다.

| | 품질 탭 "검증 실행" (서버) | 이 배치 |
|---|---|---|
| 평가 기준 | 프로파일 스냅숏 통계 | **원본 전체 데이터** |
| ACCEPTED_VALUES / REGEX | profile-only (평가 생략) | **위반 행 수 실제 계산** |
| 대용량 처리 | API 서버 자원 | 배치/클러스터 자원 |
| row_count 갱신 | 안 함 | **카탈로그 데이터셋 row_count 갱신** |

## 동작 흐름

배치가 실행되면 로그인부터 결과 반입까지 다음 순서로 흘러갑니다. 큰 그림을 한눈에 잡아 두면
나중에 문제가 생겼을 때 어느 단계에서 막혔는지 짚어내기 쉽습니다.

```
로그인(관리자) → 데이터셋·활성 규칙 조회 → 원본 RDBMS 읽기
→ 프로파일 계산(행수·NULL·고유값·min/max·평균·코드성 컬럼 최빈값) → 규칙 평가
→ POST /quality/datasets/{id}/profile/import      (프로파일 반입)
→ PUT  /catalog/datasets/{id}                      (row_count 갱신)
→ POST /quality/datasets/{id}/results/import       (결과 반입 → 점수 산출)
```

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
실행합니다. 전체 데이터 평가(CUSTOM_* 포함)가 필요한 경우에는 이 배치를
cron/Airflow/Kestra 에 등록하세요 — 두 방식은 같은 결과 화면을 공유합니다.

반입된 결과는 데이터셋 상세의 **품질 탭**(점수·결과 표·컬럼 프로파일)에
서버 실행 결과와 동일하게 표시됩니다.

## 설치

어떤 스크립트를 쓸지에 따라 준비물이 조금 다릅니다. pandas 버전은 DB 드라이버만 챙기면 되고,
PySpark 버전은 Spark 환경과 타입별 JDBC 드라이버 jar가 추가로 필요합니다.

```bash
# pandas 버전 — 전체 DB 드라이버 포함
pip install -r quality/requirements.txt

# PySpark 버전 — Spark 환경 + 타입별 JDBC 드라이버 jar 필요
spark-submit --jars mysql-connector-j-8.4.0.jar ...
```

## 사용법

실제로 어떻게 부르는지 살펴봅시다. 데이터셋 하나만 검사할 수도 있고, 데이터 소스에 속한 전체를
한꺼번에 돌릴 수도 있습니다. 결과만 미리 보고 싶을 땐 `--dry-run` 을 붙이면 됩니다.

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

접속 정보를 매번 손으로 넘길 필요는 없습니다.
기본적으로 **카탈로그의 데이터 소스 연결 설정**(데이터 소스 화면에서 등록한
host/port/database/계정)을 읽어 자동으로 접속하기 때문입니다. 연결 설정이 없을 때만 위의
`--db-*` / `--jdbc-*` 인자로 직접 지정하면 됩니다.

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
| NOT_NULL | 비-NULL 비율 ≥ threshold(%) |
| UNIQUE | 고유값 비율 ≥ threshold(%) |
| MIN_VALUE / MAX_VALUE | min ≥ 기댓값 / max ≤ 기댓값 |
| ROW_COUNT | 전체 행 수 ≥ 기댓값 |
| ACCEPTED_VALUES | 허용 목록(쉼표 구분) 외 값 0건 — **전체 데이터 검사** |
| REGEX | 패턴 불일치 0건 (부분 일치 시맨틱) — **전체 데이터 검사** |
| FRESHNESS | 배치가 방금 원본을 읽었으므로 데이터 나이 0h 로 평가 |
| CUSTOM_SQL | 기대값의 SELECT 쿼리를 원본에서 실행 — **첫 행 첫 컬럼 = 위반 건수** (0 = 통과). 조인·집계 등 복잡 검증용, **배치 전용** |

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
- 서버의 "검증 실행" 버튼은 보안상 커스텀 SQL 을 실행하지 않고
  "평가 제외"로 표시한다 — 점수에는 영향 없음 (배치 실행 시 실평가)

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
  모듈 이름을 받지 말 것. 서버는 실행하지 않고 "평가 제외" 표시

## 배치 운영

운영 환경에 올려 두고 쓸 때 알아 두면 좋은 동작들입니다. 일괄 처리 중 하나가 넘어져도 전체가
멈추지 않도록 설계되어 있어, 야간 스케줄에 걸어 두기에 부담이 없습니다.

- 일괄 모드는 **한 데이터셋이 실패해도 나머지를 계속** 처리하고, 종료 시
  요약표를 출력합니다. 실패가 있으면 exit code 1 (스케줄러 알림 연동용).
- 같은 데이터 소스 안에서는 접속을 1회만 결정해 재사용합니다.
- cron / Airflow / Kestra 등에 등록해 주기 실행하는 것을 권장합니다.
  (데이터셋 상세의 PySpark/Kestra/Airflow 탭에서 적재용 워크플로우 예시 참고)

## 로컬 데모 (Sakila)

글로만 읽기보다 한 번 직접 돌려 보는 편이 빠릅니다. 공개 샘플 데이터베이스인 Sakila로
처음부터 끝까지 따라 해 볼 수 있도록 아래 세 단계를 준비했습니다.

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
