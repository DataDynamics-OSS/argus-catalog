#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Argus Catalog 릴리스 빌드 — 폐쇄망(EL8/EL9 x86_64) 설치용 tar.gz + RPM
#
#   release/build.sh [옵션] [컴포넌트...]
#
#   컴포넌트: server server-embedding web metadata-sync query-service sdk ai-service collectors (기본: 전부)
#   옵션:
#     --release N            RPM Release 번호 (기본 1)
#     --out DIR              산출물 위치 (기본 release/dist/<VERSION>)
#     --node-major N         내장 Node.js 메이저 버전 (기본 22, LTS)
#     --starrocks-fe-jar F   StarRocks 수집 플러그인도 빌드 (대상 클러스터 FE 의 starrocks-fe.jar —
#                            Maven Central 에 없어 지정할 때만 collectors 에 포함)
#
# 인터넷이 되는 빌드 머신에서 실행한다(docker 필요). 빌드는 Rocky Linux 8 컨테이너에서 하며,
# 산출물은 인터넷 없이 EL8·EL9 에 설치된다. 결과: <out>/*.tar.gz, *.rpm, SHA256SUMS
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
V="$(tr -d '[:space:]' < "$REPO/VERSION")"
RELEASE=1
OUT=""
NODE_MAJOR=22
BUILDER_IMAGE="argus-catalog-release-builder:el8"
CACHE="$HERE/.cache"
COMPONENTS=()
STARROCKS_FE_JAR=""
MAVEN_IMAGE="maven:3.9-eclipse-temurin-25"   # trino-spi 479 가 Java 25 class 라 JDK 25 로 빌드

while [ $# -gt 0 ]; do
  case "$1" in
    --release) RELEASE="${2:?}"; shift ;;
    --out) OUT="${2:?}"; shift ;;
    --node-major) NODE_MAJOR="${2:?}"; shift ;;
    --starrocks-fe-jar) STARROCKS_FE_JAR="$(cd "$(dirname "${2:?}")" && pwd)/$(basename "$2")"; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
    *) COMPONENTS+=("$1") ;;
  esac
  shift
done
OUT="${OUT:-$HERE/dist/$V}"
mkdir -p "$OUT" "$CACHE/node" "$CACHE/pip" "$CACHE/pnpm-store"

echo "==> Argus Catalog $V (release $RELEASE) → $OUT"

# 0) 매니페스트 버전이 VERSION 과 같은지 (다르면 패키지마다 버전이 어긋난다)
python3 "$REPO/tools/sync-version.py" --check >/dev/null \
  || { echo "매니페스트 버전이 VERSION($V)과 다릅니다. 'python tools/sync-version.py' 로 맞춘 뒤 다시 빌드하세요." >&2; exit 1; }

# 1) 빌더 이미지
docker build -q -t "$BUILDER_IMAGE" "$HERE/builder" >/dev/null
echo "==> 빌더 이미지: $BUILDER_IMAGE"

# 2) 내장할 Node.js (web) — 공식 배포본을 받아 SHA256 검증
if [ ${#COMPONENTS[@]} -eq 0 ] || printf '%s\n' "${COMPONENTS[@]}" | grep -qx web; then
  base="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"
  sums="$(curl -fsSL "$base/SHASUMS256.txt")"
  file="$(printf '%s\n' "$sums" | awk '/ node-v[0-9.]+-linux-x64\.tar\.xz$/ {print $2}')"
  [ -n "$file" ] || { echo "Node.js $NODE_MAJOR 배포 파일을 찾지 못했습니다" >&2; exit 1; }
  if [ ! -f "$CACHE/node/$file" ]; then
    rm -f "$CACHE"/node/node-v*-linux-x64.tar.xz
    echo "==> Node.js 다운로드: $file"
    curl -fsSL -o "$CACHE/node/$file" "$base/$file"
  fi
  (cd "$CACHE/node" && printf '%s\n' "$sums" | grep " $file\$" | sha256sum -c --quiet -) \
    || { echo "Node.js 체크섬 불일치: $file" >&2; rm -f "$CACHE/node/$file"; exit 1; }
  echo "==> Node.js: $file (SHA256 확인)"
fi

want() { [ ${#COMPONENTS[@]} -eq 0 ] && return 0; local c; for c in "${COMPONENTS[@]}"; do [ "$c" = "$1" ] && return 0; done; return 1; }

# 2-1) 엔진 수집기 jar (collectors) — Maven 컨테이너(JDK 25)에서 빌드해 $CACHE/collectors 로 모은다
if want collectors; then
  rm -rf "$CACHE/collectors" "$CACHE/javabuild"; mkdir -p "$CACHE/collectors" "$CACHE/javabuild" "$CACHE/m2"
  mvn_build() { # mvn_build <확장 디렉터리> <대상 이름> <산출물 glob> [mvn 추가 인자]
    local src="$1" name="$2" glob="$3"; shift 3
    echo "==> collectors: $name (Maven)"
    cp -a "$REPO/extensions/$src" "$CACHE/javabuild/$src"
    docker run --rm -v "$CACHE/javabuild/$src:/b" -v "$CACHE/m2:/root/.m2" ${STARROCKS_FE_JAR:+-v "$STARROCKS_FE_JAR:/fe/starrocks-fe.jar:ro"} \
      -w /b "$MAVEN_IMAGE" mvn -q -B -DskipTests package "$@" >"$CACHE/javabuild/$src.log" 2>&1 \
      || { echo "Maven 빌드 실패: $src (로그: $CACHE/javabuild/$src.log)" >&2; exit 1; }
    mkdir -p "$CACHE/collectors/$name"
    # shellcheck disable=SC2086
    cp $CACHE/javabuild/$src/target/$glob "$CACHE/collectors/$name/"
    cp "$REPO/extensions/$src/README.md" "$CACHE/collectors/$name/README.md"
  }
  mvn_build hive-query-hook hive '*-jar-with-dependencies.jar'
  mvn_build argus-catalog-impala-query-agent impala 'impala-query-agent-*.jar'
  rm -f "$CACHE"/collectors/impala/original-*.jar
  mvn_build argus-catalog-trino-query-listener trino 'trino-query-listener-*.jar'
  rm -f "$CACHE"/collectors/trino/original-*.jar
  if [ -n "$STARROCKS_FE_JAR" ]; then
    mvn_build argus-catalog-starrocks-audit-plugin starrocks '*-plugin.zip' -Dstarrocks.fe.jar=/fe/starrocks-fe.jar
  else
    echo "==> collectors: StarRocks 플러그인 건너뜀 (--starrocks-fe-jar 미지정)"
  fi
fi

# 3) 컨테이너 안에서 빌드
docker run --rm \
  -v "$REPO:/src:ro" -v "$OUT:/out" -v "$CACHE:/cache" \
  -e RELEASE="$RELEASE" \
  "$BUILDER_IMAGE" bash /src/release/scripts/build-in-container.sh "${COMPONENTS[@]}"

# 4) 체크섬
(cd "$OUT" && shopt -s nullglob && sha256sum -- *.tar.gz *.rpm *.whl > SHA256SUMS)
echo "==> 산출물"
(cd "$OUT" && shopt -s nullglob && ls -lh -- *.tar.gz *.rpm *.whl | awk '{printf "  %-62s %s\n", $9, $5}')
echo "  SHA256SUMS"
