#!/usr/bin/env bash
# 웹 UI 도움말용 매뉴얼 사이트 빌드 — 결과를 <출력 폴더>에 넣는다(보통 frontend/apps/web/public/manual).
#
#   docs/tools/build-web-manual.sh <출력 폴더>
#
# Antora 는 로컬 콘텐츠 원본이 git 저장소여야 한다. git 워크트리·.git 이 없는 Docker 빌드 컨텍스트에서도
# 빌드되도록 docs/ 를 임시 폴더에 복사해 그 자리에서 git 저장소로 만든 뒤 빌드한다(커밋 안 된 수정도 반영).
# 필요: node(npm), git. UI 번들은 저장소에 고정(docs/ui-bundle)되어 있어 매뉴얼 빌드 자체는 인터넷이 필요 없다
# (node_modules 가 없으면 npm install 만 한 번 받는다).
set -euo pipefail

OUT="${1:?출력 폴더를 지정하세요 (예: frontend/apps/web/public/manual)}"
DOCS="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$DOCS/node_modules/@antora/site-generator" ] && [ -d "$DOCS/node_modules/@antora/lunr-extension" ] \
  || (cd "$DOCS" && npm install --no-audit --no-fund --ignore-scripts)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/docs"
tar -C "$DOCS" --exclude ./node_modules --exclude ./build --exclude ./.antora-cache --exclude ./pdf/fonts -cf - . \
  | tar -C "$TMP/docs" -xf -
git -C "$TMP" init -q
git -C "$TMP" add -A docs
git -C "$TMP" -c user.name=manual -c user.email=manual@localhost commit -q -m manual
ln -s "$DOCS/node_modules" "$TMP/docs/node_modules"

(cd "$TMP/docs" && npx --no-install antora --cache-dir "$TMP/.antora-cache" antora-playbook.yml)

rm -rf "$OUT"
mkdir -p "$OUT"
cp -a "$TMP/docs/build/site/." "$OUT/"
echo "manual → $OUT"
