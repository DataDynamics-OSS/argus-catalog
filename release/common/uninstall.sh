#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Argus Catalog 컴포넌트 제거 (tar.gz 로 설치한 경우. RPM 은 'dnf remove <이름>')
#
#   $PREFIX/share/installer/uninstall.sh            서비스 중지 + 프로그램 제거 (설정·데이터·로그 유지)
#   $PREFIX/share/installer/uninstall.sh --purge    설정·데이터·로그까지 삭제
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/component.env"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" = 0 ] || { echo "root 로 실행하세요." >&2; exit 1; }

if command -v rpm >/dev/null 2>&1 && rpm -q "$NAME" >/dev/null 2>&1; then
  echo "[$NAME] RPM 으로 설치돼 있습니다. 'dnf remove $NAME' 을 사용하세요." >&2
  exit 1
fi

for u in ${UNITS:-}; do
  if [ -d /run/systemd/system ]; then systemctl disable --now "$u" 2>/dev/null || true; fi
  rm -f "/etc/systemd/system/$u"
done
[ -d /run/systemd/system ] && systemctl daemon-reload || true
for l in ${LINKS:-}; do rm -f "/usr/local/bin/$l"; done

# 설치 스크립트가 PREFIX 안에 있으므로 지우기 전에 필요한 값을 잡아 둔다
prefix="$PREFIX"
cd /
rm -rf "${prefix:?}"
echo "[$NAME] 프로그램 제거: $prefix"

if [ -n "${ADDON_OF_PREFIX:-}" ] && [ -x "$ADDON_OF_PREFIX/share/installer/install.sh" ]; then
  # 애드온 제거 후 본체 venv 를 애드온 없이 다시 구성
  "$ADDON_OF_PREFIX/share/installer/install.sh" --post-only
fi

if [ "$PURGE" = 1 ]; then
  for d in ${CONF_DIR:-} ${STATE_DIRS:-}; do rm -rf "${d:?}"; echo "[$NAME] 삭제: $d"; done
elif [ -n "${CONF_DIR:-}${STATE_DIRS:-}" ]; then
  echo "[$NAME] 설정·데이터·로그는 남겨 두었습니다: ${CONF_DIR:-} ${STATE_DIRS:-}  (모두 지우려면 --purge)"
fi
