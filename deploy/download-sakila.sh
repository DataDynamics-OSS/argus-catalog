#!/usr/bin/env bash
# Sakila 샘플 DB 준비 — 공식 배포본을 받아 MariaDB 호환 처리 후 initdb 디렉터리에 배치.
#
# 사용:
#   ./deploy/download-sakila.sh
#   docker compose -f deploy/docker-compose.infra.yml up -d mariadb-sakila
#
# 생성 파일 (gitignore 대상 — 재생성 가능):
#   deploy/sakila/01-sakila-schema.sql
#   deploy/sakila/02-sakila-data.sql
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)/sakila"
mkdir -p "$DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[sakila] downloading official sakila-db.tar.gz ..."
curl -sL -o "$TMP/sakila-db.tar.gz" https://downloads.mysql.com/docs/sakila-db.tar.gz
tar xzf "$TMP/sakila-db.tar.gz" -C "$TMP"

# MariaDB 호환 처리:
#  - /*!80003 SRID 0 */ : MySQL 8 전용 컬럼 속성. MariaDB 는 버전 조건 주석을
#    실행해 버리므로(110400 >= 80003) 구문 오류가 난다 → 제거.
sed 's|/\*!80003 SRID 0 \*/||' "$TMP/sakila-db/sakila-schema.sql" > "$DIR/01-sakila-schema.sql"
cp "$TMP/sakila-db/sakila-data.sql" "$DIR/02-sakila-data.sql"

echo "[sakila] ready:"
ls -lh "$DIR"/0*.sql
