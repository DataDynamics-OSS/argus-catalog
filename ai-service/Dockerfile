# Argus Catalog AI Service — 단일 스테이지 (의존성이 표준 라이브러리뿐이라 가볍다)
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY argus_catalog_ai ./argus_catalog_ai
RUN pip install --no-cache-dir .

# 기본은 도움말 출력 — 실제 실행은 compose run / docker run 의 인자로 지정
ENTRYPOINT ["argus-catalog-ai"]
CMD ["--help"]
