# Argus Catalog Server (FastAPI) — API 서버 / change-mgmt 워커 공용 이미지.
# build context 는 저장소 루트(deploy/docker-compose.yml 의 context: ..).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ARGUS_CATALOG_SERVER_CONFIG_DIR=/etc/argus-catalog-server

WORKDIR /app

# 일부 패키지(빌드 필요 시)와 헬스체크용 curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 레이어 캐시 — requirements 먼저 설치
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# 애플리케이션 코드
COPY backend/ ./

# 로그/데이터 디렉터리 (config.properties 의 경로와 일치)
RUN mkdir -p /var/log/argus-catalog-server /var/lib/argus-catalog-server

EXPOSE 4600

# 기본은 API 서버. 워커는 compose 에서 command 로 덮어쓴다.
#   python -m app.change_mgmt.worker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4600"]
