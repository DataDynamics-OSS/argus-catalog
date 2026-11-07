#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# 릴리스 빌더 컨테이너(release/builder, Rocky Linux 8) 안에서 실행된다 — release/build.sh 가 호출.
#   /src    저장소 (읽기 전용)
#   /out    산출물 디렉터리 (tar.gz, rpm)
#   /cache  pip·pnpm 캐시와 Node.js 배포 파일
#
# 사용: build-in-container.sh [컴포넌트...]
#   server server-embedding web metadata-sync query-service sdk collectors (기본: 전부)
set -euo pipefail

SRC=/src
OUT=/out
CACHE=/cache
WORK=/work
PY=python3.11
V="$(tr -d '[:space:]' < "$SRC/VERSION")"
RELEASE="${RELEASE:-1}"
COMPONENTS=("$@")
[ ${#COMPONENTS[@]} -gt 0 ] || COMPONENTS=(server server-embedding web metadata-sync query-service sdk ai-service collectors)

export PIP_CACHE_DIR="$CACHE/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$OUT" "$WORK/stage" "$WORK/rpmbuild"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

log() { echo "==> [$(date +%H:%M:%S)] $*"; }

# 확장 패키지는 빌드 중 egg-info·build/ 를 쓰므로 쓰기 가능한 사본에서 빌드한다
ext_copy() {
  if [ ! -d "$WORK/ext" ]; then
    mkdir -p "$WORK/ext"
    rsync -a --exclude '__pycache__' --exclude '*.egg-info' --exclude build --exclude .venv \
      "$SRC/extensions/" "$WORK/ext/"
  fi
}

# 묶음 공통 파일: 설치·제거 스크립트, component.env, VERSION, 설치 안내, systemd, conf
stage_common() { # stage_common <component> <stage dir>
  local comp="$1" st="$2" cdir="$SRC/release/components/$1"
  rm -rf "$st"; mkdir -p "$st/opt"
  install -m 0755 "$SRC/release/common/install.sh" "$SRC/release/common/uninstall.sh" "$st/"
  sed "s/@VERSION@/$V/g" "$cdir/component.env" > "$st/component.env"
  echo "$V" > "$st/VERSION"
  cp "$SRC/release/README-INSTALL.md" "$st/README-INSTALL.md"
  [ -d "$cdir/systemd" ] && cp -a "$cdir/systemd" "$st/systemd"
  [ -d "$cdir/conf" ] && cp -a "$cdir/conf" "$st/conf"
  return 0
}

wheelhouse() { # wheelhouse <dir> <pip wheel 인자...> — 대상 Python(3.11)·플랫폼용 wheel 을 의존성까지 모은다
  local dir="$1"; shift
  mkdir -p "$dir"
  "$PY" -m pip wheel -q --wheel-dir "$dir" "$@"
}

# ------------------------------------------------------------------ server
build_server() {
  local st="$WORK/stage/argus-catalog-server-$V"
  log "server: 묶음 구성"
  stage_common server "$st"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' "$SRC/backend/app/" "$st/opt/app/"
  # 로컬 임베딩(sentence-transformers → torch, 수백 MB)은 애드온으로 분리 — 지연 import 라 없어도 기동된다
  grep -viE '^\s*(sentence-transformers|transformers|torch)\b' "$SRC/backend/requirements.txt" > "$st/opt/requirements.txt"
  log "server: wheel 수집"
  wheelhouse "$st/opt/wheels" -r "$st/opt/requirements.txt" pip
  mkdir -p "$st/opt/sql"
  cp "$SRC"/backend/packaging/config/*.sql "$st/opt/sql/"
  mkdir -p "$st/conf"
  cp "$SRC/backend/packaging/config/config.yml" "$st/conf/"
  # 개발용 상대 경로(logs, data)를 운영 경로로
  sed -e 's#^log\.dir=.*#log.dir=/var/log/argus-catalog-server#' \
      -e 's#^data\.dir=.*#data.dir=/var/lib/argus-catalog-server#' \
      -e '/개발용/d' \
      "$SRC/backend/packaging/config/config.properties" > "$st/conf/config.properties"
}

# ------------------------------------------------------------------ server-embedding (애드온)
build_server_embedding() {
  local st="$WORK/stage/argus-catalog-server-embedding-$V" w torch_whl torch_ver core
  log "server-embedding: 묶음 구성"
  stage_common server-embedding "$st"
  w="$st/opt/wheels"
  # CUDA 휠(수 GB) 대신 CPU 전용 torch — PyTorch CPU 인덱스에서 먼저 받는다
  log "server-embedding: CPU torch wheel"
  wheelhouse "$w" --index-url https://download.pytorch.org/whl/cpu torch
  torch_whl="$(ls "$w"/torch-*.whl | head -1)"
  torch_ver="$(basename "$torch_whl" | cut -d- -f2)"
  {
    echo "torch==$torch_ver"
    grep -iE '^\s*(sentence-transformers|transformers)\b' "$SRC/backend/requirements.txt"
  } > "$st/opt/requirements.txt"
  log "server-embedding: sentence-transformers wheel (torch $torch_ver 고정)"
  wheelhouse "$w" --find-links "$w" -r "$st/opt/requirements.txt"
  # 본체 묶음에 이미 있는 wheel 은 빼서 크기를 줄인다 (설치 때 본체 wheels 도 함께 찾는다)
  core="$WORK/stage/argus-catalog-server-$V/opt/wheels"
  if [ -d "$core" ]; then
    for f in "$w"/*.whl; do [ -e "$core/$(basename "$f")" ] && rm -f "$f"; done
  fi
  return 0
}

# ------------------------------------------------------------------ web
build_web() {
  local st="$WORK/stage/argus-catalog-web-$V" nodetar fe="$WORK/frontend" sa
  log "web: 묶음 구성"
  stage_common web "$st"
  nodetar="$(ls "$CACHE"/node/node-v*-linux-x64.tar.xz | head -1)"
  [ -f "$nodetar" ] || { echo "Node.js 배포 파일이 없습니다 ($CACHE/node)" >&2; exit 1; }
  rm -rf "$WORK/node"; mkdir -p "$WORK/node"
  tar -xJf "$nodetar" -C "$WORK/node" --strip-components=1
  export PATH="$WORK/node/bin:$PATH" COREPACK_ENABLE_DOWNLOAD_PROMPT=0 NEXT_TELEMETRY_DISABLED=1
  corepack enable
  rm -rf "$fe"
  rsync -a --exclude node_modules --exclude .next --exclude .turbo "$SRC/frontend/" "$fe/"
  log "web: pnpm install ($(node --version))"
  (cd "$fe" && pnpm config set store-dir "$CACHE/pnpm-store" >/dev/null && pnpm install --frozen-lockfile --reporter=silent)
  log "web: next build (standalone)"
  (cd "$fe/apps/web" && NEXT_OUTPUT=standalone pnpm build)
  sa="$fe/apps/web/.next/standalone"
  [ -f "$sa/apps/web/server.js" ] || { echo "standalone 산출물이 없습니다" >&2; exit 1; }
  # standalone 은 정적 파일을 포함하지 않는다 — 직접 넣는다
  mkdir -p "$sa/apps/web/.next"
  cp -a "$fe/apps/web/.next/static" "$sa/apps/web/.next/static"
  [ -d "$fe/apps/web/public" ] && cp -a "$fe/apps/web/public" "$sa/apps/web/public"
  cp -a "$sa" "$st/opt/app"
  # Node.js 런타임: 실행 파일과 라이선스만
  mkdir -p "$st/opt/node/bin"
  cp -a "$WORK/node/bin/node" "$st/opt/node/bin/node"
  cp -a "$WORK/node/LICENSE" "$st/opt/node/LICENSE"
  node --version > "$st/opt/node/VERSION"
}

# ------------------------------------------------------------------ metadata-sync
build_metadata_sync() {
  local st="$WORK/stage/argus-catalog-metadata-sync-$V" e="$WORK/ext"
  log "metadata-sync: 묶음 구성"
  stage_common metadata-sync "$st"
  ext_copy
  log "metadata-sync: wheel 수집"
  wheelhouse "$st/opt/wheels" pip \
    "$e/argus-catalog-sqlglot-impala" "$e/argus-sql-parse" "$e/argus-relationship-analyzer" \
    "$e/argus-catalog-metadata-sync[trino,oracle,mssql,iceberg,kerberos]"
  cp "$SRC"/extensions/argus-catalog-metadata-sync/packaging/config/config.yml \
     "$SRC"/extensions/argus-catalog-metadata-sync/packaging/config/config.properties "$st/conf/"
}

# ------------------------------------------------------------------ query-service
build_query_service() {
  local st="$WORK/stage/argus-catalog-query-service-$V" e="$WORK/ext"
  log "query-service: 묶음 구성"
  stage_common query-service "$st"
  ext_copy
  log "query-service: wheel 수집"
  wheelhouse "$st/opt/wheels" pip \
    "$e/argus-catalog-sqlglot-impala" "$e/argus-sql-parse" "$e/argus-relationship-analyzer" \
    "$e/argus-catalog-query-service"
}

# ------------------------------------------------------------------ sdk (CLI: argus-model, argus-catalog)
build_sdk() {
  local st="$WORK/stage/argus-catalog-sdk-$V"
  log "sdk: 묶음 구성"
  stage_common sdk "$st"
  rsync -a --exclude '__pycache__' --exclude '*.egg-info' --exclude build "$SRC/sdk/" "$WORK/sdk/"
  log "sdk: wheel 수집"
  wheelhouse "$st/opt/wheels" pip "$WORK/sdk"
  # pip 로 바로 설치할 사용자를 위한 순수 wheel (OS·Python 3.11+ 무관)
  cp "$st"/opt/wheels/argus_catalog_sdk-*.whl "$OUT/"
}

# ------------------------------------------------------------------ collectors (엔진 수집기 jar)
# jar 는 release/build.sh 가 Maven(JDK 25) 컨테이너로 빌드해 /cache/collectors 에 둔다
build_ai_service() {
  local st="$WORK/stage/argus-catalog-ai-service-$V"
  log "ai-service: 묶음 구성"
  stage_common ai-service "$st"
  rsync -a --exclude '__pycache__' --exclude '*.egg-info' --exclude build --exclude .venv --exclude .env \
    "$SRC/ai-service/" "$WORK/ai-service/"
  log "ai-service: wheel 수집"
  wheelhouse "$st/opt/wheels" pip "$WORK/ai-service"
}

build_collectors() {
  local st="$WORK/stage/argus-catalog-collectors-$V" d n
  log "collectors: 묶음 구성"
  stage_common collectors "$st"
  [ -d "$CACHE/collectors" ] || { echo "collector jar 가 없습니다 ($CACHE/collectors)" >&2; exit 1; }
  for d in "$CACHE"/collectors/*/; do
    n="$(basename "$d")"
    mkdir -p "$st/opt/$n"
    cp -a "$d"/. "$st/opt/$n/"
  done
}

# ------------------------------------------------------------------ 패키징 (tar.gz + rpm)
package() { # package <component>
  local comp="$1" name st tgz spec files="" f units prefix conf requires postun
  # shellcheck source=/dev/null
  name="$(. "$SRC/release/components/$comp/component.env"; echo "$NAME")"
  st="$WORK/stage/$name-$V"
  # 묶음 안 component.env(버전 치환됨) 기준으로 값을 읽는다
  # shellcheck source=/dev/null
  ( . "$st/component.env"
    printf '%s\n' "$PREFIX" "${CONF_DIR:-}" "${UNITS:-}" "${RPM_REQUIRES:-}" "${ADDON_OF_PREFIX:-}" "$SUMMARY" "$DESCRIPTION" \
      "${RPM_ARCH:-x86_64}" "${LINKS:-}"
  ) > "$WORK/meta.$comp"
  { read -r prefix; read -r conf; read -r units; read -r requires; read -r addon_of; read -r summary; read -r description
    read -r arch; read -r links; } < "$WORK/meta.$comp"

  tgz="$OUT/$name-$V-$arch.tar.gz"
  log "$comp: tar.gz → $(basename "$tgz")"
  tar -C "$WORK/stage" --owner=0 --group=0 -czf "$tgz" "$name-$V"

  # %files: 설정 파일(noreplace)과 unit
  if [ -n "$conf" ] && [ -d "$st/conf" ]; then
    files+="%dir %attr(0750,root,argus) $conf"$'\n'
    for f in "$st"/conf/*; do
      files+="%config(noreplace) %attr(0640,root,argus) $conf/$(basename "$f")"$'\n'
    done
  fi
  if [ -d "$st/systemd" ]; then
    for f in "$st"/systemd/*; do files+="%{_unitdir}/$(basename "$f")"$'\n'; done
  fi
  for f in $links; do files+="/usr/bin/$f"$'\n'; done
  postun=":"
  [ -n "$addon_of" ] && postun="  $addon_of/share/installer/install.sh --post-only -q || :"

  cp "$tgz" "$WORK/rpmbuild/SOURCES/$name-$V.tar.gz"
  spec="$WORK/rpmbuild/SPECS/$name.spec"
  NAME="$name" VERSION="$V" RELEASE="$RELEASE" SUMMARY="$summary" DESCRIPTION="$description" \
  PREFIX="$prefix" UNITS="$units" REQUIRES="${requires:+Requires:       $requires}" \
  FILES_EXTRA="$files" POSTUN_ERASE="$postun" ARCH="$arch" \
  CHANGELOG_DATE="$(LC_ALL=C date '+%a %b %d %Y')" \
  "$PY" - "$SRC/release/common/component.spec.in" "$spec" <<'PYEOF'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()
for k in ("NAME", "VERSION", "RELEASE", "SUMMARY", "DESCRIPTION", "PREFIX", "UNITS", "REQUIRES",
          "FILES_EXTRA", "POSTUN_ERASE", "ARCH", "CHANGELOG_DATE"):
    s = s.replace(f"@{k}@", os.environ[k])
open(dst, "w", encoding="utf-8").write(s)
PYEOF
  log "$comp: rpmbuild"
  rpmbuild -bb --quiet \
    --define "_topdir $WORK/rpmbuild" \
    --define "dist %{nil}" \
    --define "_binary_payload w6.gzdio" \
    "$spec" >/dev/null
  cp "$WORK/rpmbuild/RPMS/$arch/$name-$V-$RELEASE.$arch.rpm" "$OUT/"
  log "$comp: rpm → $name-$V-$RELEASE.$arch.rpm"
}

for c in "${COMPONENTS[@]}"; do
  case "$c" in
    server) build_server ;;
    server-embedding) build_server_embedding ;;
    web) build_web ;;
    metadata-sync) build_metadata_sync ;;
    query-service) build_query_service ;;
    sdk) build_sdk ;;
    ai-service) build_ai_service ;;
    collectors) build_collectors ;;
    *) echo "알 수 없는 컴포넌트: $c" >&2; exit 2 ;;
  esac
  package "$c"
done
log "완료: $OUT"
