#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# 원천 직접 진단 — 실제 DB 교차 검증(tests/test_quality_direct_dialects.py)을 임시 컨테이너로 돌린다.
#
# PostgreSQL · MySQL · Oracle Free · SQL Server 컨테이너를 127.0.0.1 에만 띄우고(이름 dqv-*), 같은 시드 데이터를 넣어
# 품질 워커 코드로 모든 규칙 · 프로파일 · 위반 샘플을 돌린 뒤 판정이 Python 기대값 · 서로와 같은지 본다. 끝나면 컨테이너와
# 볼륨을 지운다(KEEP=1 이면 남긴다 — 다시 돌릴 때 빠르다).
#
#   cd backend
#   PYTHON=.venv/bin/python scripts/quality-direct-dialects.sh            # 네 DB 모두
#   DIALECTS="oracle mssql" scripts/quality-direct-dialects.sh            # 일부만
#   KEEP=1 scripts/quality-direct-dialects.sh -k profile                   # pytest 인자는 그대로 넘긴다
#
# 필요: docker, 그리고 PYTHON 에 백엔드 의존성 + asyncpg · aiomysql · oracledb · pymssql · pandas
#       (pip install asyncpg aiomysql oracledb pymssql pandas). Oracle 이미지는 크다(약 1.3GB, 처음 기동 1~2분).
#
# 포트(127.0.0.1): PG 46743 · MySQL 46746 · Oracle 46721 · SQL Server 46733 — DQV_PORT_<PG|MYSQL|ORACLE|MSSQL> 로 바꾼다.

set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
DIALECTS="${DIALECTS:-postgresql mysql oracle mssql}"
PG_PORT="${DQV_PORT_PG:-46743}"
MYSQL_PORT="${DQV_PORT_MYSQL:-46746}"
ORACLE_PORT="${DQV_PORT_ORACLE:-46721}"
MSSQL_PORT="${DQV_PORT_MSSQL:-46733}"
PASSWORD='DqvPass123!'          # 검증용 임시 컨테이너의 계정 — 외부에 열지 않는다(127.0.0.1)
ORACLE_PASSWORD='DqvPass123'    # Oracle 은 비밀번호에 ! 를 받지 않는다

ORACLE_IMAGE="${DQV_ORACLE_IMAGE:-gvenzl/oracle-free:23-slim-faststart}"
MSSQL_IMAGE="${DQV_MSSQL_IMAGE:-mcr.microsoft.com/mssql/server:2022-latest}"
PG_IMAGE="${DQV_PG_IMAGE:-postgres:16-alpine}"
MYSQL_IMAGE="${DQV_MYSQL_IMAGE:-mysql:8.0}"

started=()

cleanup() {
  if [[ "${KEEP:-0}" == "1" ]]; then
    echo "컨테이너를 남깁니다: ${started[*]:-(없음)} — 지우려면 docker rm -f -v <이름>"
    return
  fi
  for name in "${started[@]}"; do
    docker rm -f -v "$name" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

run() {  # run <이름> <docker run 인자…> — 같은 이름의 컨테이너가 있으면 그대로 쓴다
  local name="$1"; shift
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    docker run -d --name "$name" "$@" >/dev/null
  fi
  started+=("$name")
}

wait_for() {  # wait_for <설명> <초> <명령…>
  local what="$1" seconds="$2"; shift 2
  for _ in $(seq "$seconds"); do
    if "$@" >/dev/null 2>&1; then
      echo "준비됨: $what"
      return 0
    fi
    sleep 1
  done
  echo "준비되지 않음: $what" >&2
  return 1
}

for d in $DIALECTS; do
  case "$d" in
    postgresql)
      run dqv-postgres -p "127.0.0.1:${PG_PORT}:5432" -e POSTGRES_USER=dqv -e POSTGRES_PASSWORD="$PASSWORD" \
        -e POSTGRES_DB=dqv "$PG_IMAGE"
      export ARGUS_TEST_PG_DSN="postgresql://dqv:DqvPass123%21@127.0.0.1:${PG_PORT}/dqv" ;;
    mysql)
      run dqv-mysql -p "127.0.0.1:${MYSQL_PORT}:3306" -e MYSQL_ROOT_PASSWORD="$PASSWORD" -e MYSQL_DATABASE=dqv \
        -e MYSQL_USER=dqv -e MYSQL_PASSWORD="$PASSWORD" "$MYSQL_IMAGE"
      export ARGUS_TEST_MYSQL_DSN="mysql://dqv:DqvPass123%21@127.0.0.1:${MYSQL_PORT}/dqv" ;;
    oracle)
      run dqv-oracle -p "127.0.0.1:${ORACLE_PORT}:1521" -e ORACLE_PASSWORD="$ORACLE_PASSWORD" -e APP_USER=dqv \
        -e APP_USER_PASSWORD="$ORACLE_PASSWORD" "$ORACLE_IMAGE"
      export ARGUS_TEST_ORACLE_DSN="oracle://dqv:${ORACLE_PASSWORD}@127.0.0.1:${ORACLE_PORT}/FREEPDB1" ;;
    mssql)
      run dqv-mssql -p "127.0.0.1:${MSSQL_PORT}:1433" -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD="$PASSWORD" "$MSSQL_IMAGE"
      export ARGUS_TEST_MSSQL_DSN="mssql://sa:DqvPass123%21@127.0.0.1:${MSSQL_PORT}/dqv" ;;
    *) echo "모르는 DB: $d (postgresql mysql oracle mssql)" >&2; exit 2 ;;
  esac
done

for d in $DIALECTS; do
  case "$d" in
    postgresql) wait_for PostgreSQL 120 docker exec dqv-postgres pg_isready -h 127.0.0.1 -U dqv -d dqv ;;  # 초기화 중의 임시 서버는 TCP 를 열지 않는다
    mysql) wait_for MySQL 180 docker exec dqv-mysql mysql -h 127.0.0.1 --protocol=TCP -udqv "-p$PASSWORD" -e "SELECT 1" dqv ;;
    oracle) wait_for Oracle 600 docker exec dqv-oracle healthcheck.sh ;;
    mssql) wait_for "SQL Server" 180 bash -c "docker logs dqv-mssql 2>&1 | grep -q 'ready for client connections'" ;;
  esac
done

PYTHONPATH="$(pwd)" "$PYTHON" -m pytest -q tests/test_quality_direct_dialects.py "$@"
