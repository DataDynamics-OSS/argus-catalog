# Argus Catalog Integration Tests
#
# 모든 타깃은 호스트에 argus-catalog-server (4600) 와 argus-catalog-metadata-sync
# (4610) 가 이미 실행 중이라고 가정한다. 플랫폼별 compose 는 외부 의존성만 띄움.

ICEBERG_PROJECT := argus-test-iceberg
ICEBERG_COMPOSE := -f platforms/iceberg_rest/compose.yml -p $(ICEBERG_PROJECT)

.PHONY: install
install:
	pip install -e '.[dev]'

# ----------------------------------------------------------------------------
# Iceberg REST Catalog
# ----------------------------------------------------------------------------

.PHONY: up-iceberg-rest
up-iceberg-rest:
	docker compose $(ICEBERG_COMPOSE) up -d

.PHONY: down-iceberg-rest
down-iceberg-rest:
	docker compose $(ICEBERG_COMPOSE) down -v

.PHONY: logs-iceberg-rest
logs-iceberg-rest:
	docker compose $(ICEBERG_COMPOSE) logs -f

.PHONY: test-iceberg-rest
test-iceberg-rest:
	pytest platforms/iceberg_rest -v

# ----------------------------------------------------------------------------
# PostgreSQL
# ----------------------------------------------------------------------------

PG_PROJECT := argus-test-postgresql
PG_COMPOSE := -f platforms/postgresql/compose.yml -p $(PG_PROJECT)

.PHONY: up-postgresql
up-postgresql:
	docker compose $(PG_COMPOSE) up -d

.PHONY: down-postgresql
down-postgresql:
	docker compose $(PG_COMPOSE) down -v

.PHONY: logs-postgresql
logs-postgresql:
	docker compose $(PG_COMPOSE) logs -f

.PHONY: test-postgresql
test-postgresql:
	pytest platforms/postgresql -v

# ----------------------------------------------------------------------------
# MySQL / MariaDB
# ----------------------------------------------------------------------------

MY_PROJECT := argus-test-mysql
MY_COMPOSE := -f platforms/mysql/compose.yml -p $(MY_PROJECT)

.PHONY: up-mysql
up-mysql:
	docker compose $(MY_COMPOSE) up -d

.PHONY: down-mysql
down-mysql:
	docker compose $(MY_COMPOSE) down -v

.PHONY: test-mysql
test-mysql:
	pytest platforms/mysql -v

# ----------------------------------------------------------------------------
# Oracle
# ----------------------------------------------------------------------------

ORA_PROJECT := argus-test-oracle
ORA_COMPOSE := -f platforms/oracle/compose.yml -p $(ORA_PROJECT)

.PHONY: up-oracle
up-oracle:
	docker compose $(ORA_COMPOSE) up -d

.PHONY: down-oracle
down-oracle:
	docker compose $(ORA_COMPOSE) down -v

.PHONY: test-oracle
test-oracle:
	pytest platforms/oracle -v

# ----------------------------------------------------------------------------
# StarRocks
# ----------------------------------------------------------------------------

SR_PROJECT := argus-test-starrocks
SR_COMPOSE := -f platforms/starrocks/compose.yml -p $(SR_PROJECT)

.PHONY: up-starrocks
up-starrocks:
	docker compose $(SR_COMPOSE) up -d

.PHONY: down-starrocks
down-starrocks:
	docker compose $(SR_COMPOSE) down -v

.PHONY: test-starrocks
test-starrocks:
	pytest platforms/starrocks -v

# ----------------------------------------------------------------------------
# Hive Metastore
# ----------------------------------------------------------------------------

HIVE_PROJECT := argus-test-hive
HIVE_COMPOSE := -f platforms/hive/compose.yml -p $(HIVE_PROJECT)

.PHONY: up-hive
up-hive:
	docker compose $(HIVE_COMPOSE) up -d

.PHONY: down-hive
down-hive:
	docker compose $(HIVE_COMPOSE) down -v

.PHONY: test-hive
test-hive:
	pytest platforms/hive -v

# ----------------------------------------------------------------------------
# Trino (Hive Metastore + MinIO)
# ----------------------------------------------------------------------------

TRINO_PROJECT := argus-test-trino
TRINO_COMPOSE := -f platforms/trino/compose.yml -p $(TRINO_PROJECT)

.PHONY: up-trino
up-trino:
	docker compose $(TRINO_COMPOSE) up -d

.PHONY: down-trino
down-trino:
	docker compose $(TRINO_COMPOSE) down -v

.PHONY: test-trino
test-trino:
	pytest platforms/trino -v

# ----------------------------------------------------------------------------
# Kudu
# ----------------------------------------------------------------------------

KUDU_PROJECT := argus-test-kudu
KUDU_COMPOSE := -f platforms/kudu/compose.yml -p $(KUDU_PROJECT)

.PHONY: up-kudu
up-kudu:
	docker compose $(KUDU_COMPOSE) up -d

.PHONY: down-kudu
down-kudu:
	docker compose $(KUDU_COMPOSE) down -v

.PHONY: test-kudu
test-kudu:
	pytest platforms/kudu -v

# 소스 빌드한 self-contained kudu wheel(libkudu_client 번들)을 양쪽 venv 에 재설치.
# 빌드 결과물 재사용 — 매번 libkudu_client 를 다시 빌드할 필요가 없다.
# 다른 경로/버전이면 `make install-kudu-driver KUDU_WHEEL=/path/to.whl`.
KUDU_WHEEL ?= $(firstword $(wildcard /opt/kudu-client/wheelhouse/kudu_python-*.whl))
SYNC_VENV := ../extensions/argus-catalog-metadata-sync/.venv

.PHONY: install-kudu-driver
install-kudu-driver:
	@test -n "$(KUDU_WHEEL)" || { echo "KUDU_WHEEL 가 비었습니다. 빌드 wheel 경로를 지정하세요."; exit 1; }
	.venv/bin/python -m pip install --force-reinstall --no-deps "$(KUDU_WHEEL)"
	.venv/bin/python -m pip install pytz six
	$(SYNC_VENV)/bin/python -m pip install --force-reinstall --no-deps "$(KUDU_WHEEL)"
	$(SYNC_VENV)/bin/python -m pip install pytz six

# ----------------------------------------------------------------------------
# Aggregate
# ----------------------------------------------------------------------------

.PHONY: test-all
test-all: test-iceberg-rest test-postgresql test-mysql test-starrocks test-oracle test-hive test-trino test-kudu
