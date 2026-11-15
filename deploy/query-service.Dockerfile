# Argus Catalog Query Service — 쿼리 수집(저장 전용) + 내장 배치(write-lineage·관계).
# build context 는 저장소 루트:
#   docker build -f deploy/query-service.Dockerfile -t argus-catalog-query-service .
# 설정은 환경변수(ARGUS_DB_*, ARGUS_QS_*) — extensions/argus-catalog-query-service/README.md "설정" 참고.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 헬스체크용 curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 로컬 모노레포 의존 패키지(파서·관계 분석·Impala 전처리) → query-service 순으로 설치
COPY extensions/argus-catalog-sqlglot-impala /src/argus-catalog-sqlglot-impala
COPY extensions/argus-sql-parse /src/argus-sql-parse
COPY extensions/argus-relationship-analyzer /src/argus-relationship-analyzer
COPY extensions/argus-catalog-query-service /src/argus-catalog-query-service
RUN pip install /src/argus-catalog-sqlglot-impala /src/argus-sql-parse \
        /src/argus-relationship-analyzer /src/argus-catalog-query-service

EXPOSE 4620

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${ARGUS_QS_PORT:-4620}/health" || exit 1

CMD ["query-service"]
