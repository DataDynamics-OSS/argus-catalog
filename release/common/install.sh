#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Argus Catalog 컴포넌트 설치 스크립트 (tar.gz 설치 · RPM %install/%post 공용)
#
# 묶음 구조(이 스크립트와 같은 디렉터리):
#   component.env   컴포넌트 정의(이름·경로·서비스·Python 패키지)
#   opt/            → $PREFIX 로 복사 (app, wheels, bin, sql, node ...)
#   conf/           → $CONF_DIR 로 복사 (이미 있으면 덮어쓰지 않고 *.new 로 둔다)
#   systemd/        → systemd unit 디렉터리
#
# 사용:
#   ./install.sh                         전체 설치 (root, 인터넷 불필요)
#   ./install.sh --layout-only --destdir D --unit-dir U
#                                        파일 배치만 (RPM %install 용)
#   $PREFIX/share/installer/install.sh --post-only
#                                        계정·디렉터리·Python venv·systemd 반영만 (RPM %post, 재구성)
#
# 환경변수:
#   ARGUS_PYTHON   사용할 Python 3.11 실행 파일 (기본: python3.11)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/component.env"

SERVICE_USER="${SERVICE_USER:-argus}"
MODE=full
DESTDIR=""
UNIT_DIR=/etc/systemd/system
QUIET=0

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --layout-only) MODE=layout ;;
    --post-only) MODE=post ;;
    --destdir) DESTDIR="${2:?}"; shift ;;
    --unit-dir) UNIT_DIR="${2:?}"; shift ;;
    -q|--quiet) QUIET=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

log() { [ "$QUIET" = 1 ] || echo "[$NAME] $*"; }
die() { echo "[$NAME] 오류: $*" >&2; exit 1; }

# ---------------------------------------------------------------- 파일 배치
layout() {
  local dst="$DESTDIR$PREFIX" f name target
  mkdir -p "$dst"
  # 업그레이드: 이전 버전의 같은 이름 디렉터리를 비워 남은 파일이 섞이지 않게 한다 (venv·addons 는 유지)
  if [ -z "$DESTDIR" ]; then
    for f in "$HERE"/opt/*; do rm -rf "${dst:?}/$(basename "$f")"; done
  fi
  cp -a "$HERE"/opt/. "$dst"/
  # 설치 스크립트 보관 — RPM %post, 재구성(--post-only), 제거에 쓴다
  mkdir -p "$dst/share/installer"
  cp -a "$HERE/component.env" "$HERE/install.sh" "$HERE/uninstall.sh" "$HERE/VERSION" "$dst/share/installer/"
  [ -f "$HERE/README-INSTALL.md" ] && cp -a "$HERE/README-INSTALL.md" "$dst/share/installer/"

  if [ -d "$HERE/conf" ] && [ -n "${CONF_DIR:-}" ]; then
    mkdir -p "$DESTDIR$CONF_DIR"
    for f in "$HERE"/conf/*; do
      name="$(basename "$f")"; target="$DESTDIR$CONF_DIR/$name"
      if [ -z "$DESTDIR" ] && [ -e "$target" ]; then
        if ! cmp -s "$f" "$target"; then
          install -m 0640 "$f" "$target.new"
          log "기존 설정 유지: $target (새 기본값은 $target.new)"
        fi
      else
        install -m 0640 "$f" "$target"
      fi
    done
  fi

  if [ -d "$HERE/systemd" ]; then
    mkdir -p "$DESTDIR$UNIT_DIR"
    for f in "$HERE"/systemd/*; do install -m 0644 "$f" "$DESTDIR$UNIT_DIR/"; done
  fi

  # 명령 링크(CLI): RPM 은 /usr/bin, tar.gz 설치는 /usr/local/bin
  if [ -n "${LINKS:-}" ]; then
    local linkdir=/usr/local/bin l
    [ -n "$DESTDIR" ] && linkdir=/usr/bin
    mkdir -p "$DESTDIR$linkdir"
    for l in $LINKS; do ln -sfn "$PREFIX/venv/bin/$l" "$DESTDIR$linkdir/$l"; done
  fi
}

# ---------------------------------------------------------------- 설치 후 구성
ensure_user() {
  getent group "$SERVICE_USER" >/dev/null || groupadd -r "$SERVICE_USER"
  getent passwd "$SERVICE_USER" >/dev/null || \
    useradd -r -g "$SERVICE_USER" -d "/var/lib/$SERVICE_USER" -s /sbin/nologin \
      -c "Argus Catalog service account" "$SERVICE_USER"
}

find_python() {
  local py="${ARGUS_PYTHON:-python3.11}" ver
  command -v "$py" >/dev/null 2>&1 || die "$py 가 없습니다. 'dnf install python3.11' 로 설치하세요 (EL8.8+/EL9.2+ AppStream)."
  ver="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  # 포함된 wheel 은 CPython 3.11(cp311) 용이다
  [ "$ver" = "3.11" ] || die "$py 는 Python $ver 입니다. 이 패키지는 Python 3.11 용입니다 (ARGUS_PYTHON 으로 지정)."
  command -v "$py"
}

pip_offline() { # pip_offline <venv> <pip install 인자...> — 인터넷 없이 묶음 wheel 만 사용
  local venv="$1"; shift
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$venv/bin/python" -m pip install -q --no-index "$@"
}

setup_venv() {
  local py venv a
  if [ -n "${ADDON_OF_PREFIX:-}" ]; then
    # 애드온: 본체 venv 에 추가 설치
    venv="$ADDON_OF_PREFIX/venv"
    [ -x "$venv/bin/python" ] || die "$ADDON_OF_PREFIX/venv 가 없습니다. 본체 패키지를 먼저 설치하세요."
    log "본체 venv 에 애드온 설치 (오프라인)"
    pip_offline "$venv" --find-links "$PREFIX/wheels" --find-links "$ADDON_OF_PREFIX/wheels" \
      -r "$PREFIX/requirements.txt"
    return
  fi
  py="$(find_python)"
  venv="$PREFIX/venv"
  log "Python venv 구성: $venv ($py, 오프라인)"
  "$py" -m venv --clear "$venv"
  pip_offline "$venv" --find-links "$PREFIX/wheels" --upgrade pip
  # shellcheck disable=SC2086
  pip_offline "$venv" --find-links "$PREFIX/wheels" $PIP_INSTALL
  # 설치된 애드온(예: 로컬 임베딩)을 다시 넣는다 — 본체 업그레이드 때 venv 를 새로 만들기 때문
  for a in "$PREFIX"/addons/*/requirements.txt; do
    [ -f "$a" ] || continue
    log "애드온 재설치: $(basename "$(dirname "$a")")"
    pip_offline "$venv" --find-links "$(dirname "$a")/wheels" --find-links "$PREFIX/wheels" -r "$a"
  done
}

post() {
  local d
  [ "$(id -u)" = 0 ] || die "root 로 실행하세요."
  ensure_user
  for d in ${STATE_DIRS:-}; do
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$d"
  done
  if [ -n "${CONF_DIR:-}" ] && [ -d "$CONF_DIR" ]; then
    chown root:"$SERVICE_USER" "$CONF_DIR"; chmod 0750 "$CONF_DIR"
    find "$CONF_DIR" -maxdepth 1 -type f -exec chown root:"$SERVICE_USER" {} + -exec chmod 0640 {} +
  fi
  if [ "${PYTHON_VENV:-no}" = yes ]; then setup_venv; fi
  if [ -d /run/systemd/system ]; then systemctl daemon-reload || true; fi
}

next_steps() {
  [ "$QUIET" = 1 ] && return 0
  echo
  echo "[$NAME] 설치 완료 — $(cat "$HERE/VERSION")"
  [ -n "${NEXT_STEPS:-}" ] && printf '%s\n' "$NEXT_STEPS"
  return 0
}

case "$MODE" in
  layout) layout ;;
  post) post ;;
  full)
    [ "$(id -u)" = 0 ] || die "root 로 실행하세요."
    if [ -n "${ADDON_OF_PREFIX:-}" ] && [ ! -d "$ADDON_OF_PREFIX" ]; then
      die "$ADDON_OF_PREFIX 가 없습니다. 본체 패키지를 먼저 설치하세요."
    fi
    layout
    post
    next_steps
    ;;
esac
