# Argus Catalog Web UI (Next.js 16, pnpm/turbo monorepo) — 프로덕션 빌드.
# build context 는 저장소 루트(deploy/docker-compose.yml 의 context: ..).

# ---- manual stage ---- 헤더 도움말(?)이 여는 사용자 매뉴얼 사이트 → apps/web/public/manual (/manual/)
FROM node:22-slim AS manual
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY docs/ ./docs/
RUN docs/tools/build-web-manual.sh /manual

# ---- build stage ----
FROM node:22-slim AS build
RUN corepack enable
WORKDIR /app

# 모노레포 전체 복사 후 워크스페이스 설치 + 빌드 (turbo build → apps/web 포함)
COPY frontend/ ./
COPY --from=manual /manual ./apps/web/public/manual
RUN pnpm install --frozen-lockfile
RUN pnpm build

# ---- runtime stage ----
FROM node:22-slim AS run
RUN corepack enable
ENV NODE_ENV=production \
    PORT=3000
WORKDIR /app
COPY --from=build /app ./

WORKDIR /app/apps/web
EXPOSE 3000

# next start — /api/v1/* 는 proxy.ts 가 API_BASE_URL(=http://backend:4600) 로 서버사이드 프록시
CMD ["pnpm", "start"]
