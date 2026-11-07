#!/bin/bash
# 카탈로그 앱 DB 초기화 — catalog-postgres 최초 기동 시 1회 실행(데이터 볼륨이 비어 있을 때만).
#
# 서버는 테이블을 만들지 않으므로(스키마는 SQL DDL 로만 관리) 여기서 DDL 을 적용한다.
# DDL 원본은 backend/packaging/config 를 /argus-sql 로 마운트해 그대로 쓴다.
# 관리자 계정(admin/admin, 첫 로그인 시 비밀번호 변경)은 서버가 기동할 때 만든다.
# 적용이 끝나면 $PGDATA/.argus-init-done 을 남겨 healthcheck 가 그 뒤에만 healthy 가 되게 한다.
set -euo pipefail

echo "[argus-init] apply DDL -> ${POSTGRES_DB}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -f /argus-sql/argus-catalog-postgresql.sql

touch "$PGDATA/.argus-init-done"
echo "[argus-init] done"
