# Argus Catalog Quality Worker — 카탈로그의 품질 진단 대기열을 처리한다(Trino 에 쿼리, 또는 원본 DB 를 직접 읽기).
# build context 는 저장소 루트:
#   docker build -f deploy/quality-worker.Dockerfile -t argus-catalog-quality-worker .
# 설정은 환경변수(ARGUS_CATALOG_BASE_URL, ARGUS_CATALOG_API_KEY) 또는 /etc/argus-catalog-quality-worker 에 마운트한
# config.yml·config.properties — extensions/argus-catalog-quality-worker/README.md "설정" 참고.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY extensions/argus-catalog-quality-worker /src/argus-catalog-quality-worker
# [direct] — 원천 직접 진단 드라이버(asyncpg·aiomysql)와 커스텀 Python 의 pandas
RUN pip install "/src/argus-catalog-quality-worker[direct]"
# 커스텀 Python 플러그인 — 기본 direct.checks_dir(custom_checks, WORKDIR 기준). 운영에서는 이 디렉터리를 마운트해 바꾼다
COPY extensions/argus-catalog-quality-worker/custom_checks /app/custom_checks

# 상태 확인(/health) — 컨테이너 안에서만 부른다. 설정 파일이 없으면 로그는 stdout 으로 간다.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4630/health', timeout=3).status == 200 else 1)"

CMD ["quality-worker"]
