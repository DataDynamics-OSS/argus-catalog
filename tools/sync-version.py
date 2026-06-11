#!/usr/bin/env python3
"""제품 버전 단일 소스 동기화 도구.

루트 ``VERSION`` 파일을 단일 소스(single source of truth)로 삼아, 모노레포 전반의
매니페스트/패키지 메타데이터에 동일한 버전을 전파한다. 빌드 도구는 매니페스트의
리터럴 버전을 요구하므로(런타임 읽기는 패키징에서 취약) 값을 VERSION 에서 생성하고
드리프트는 ``--check`` 로 CI 에서 검증한다.

관리 대상(존재하지 않는 파일은 조용히 건너뛴다 — 공개 repo 처럼 일부 디렉터리가
제외된 트리에서도 동일 스크립트가 그대로 동작한다):
  - 모든 ``pyproject.toml`` 의 ``[project].version`` (단, dynamic 버전은 제외)
  - ``__version__ = "..."`` 를 가진 모든 ``__init__.py``
  - 명시한 ``package.json`` (내부 빌드 설정 패키지는 제외 — private·0.0.0 유지)

사용법:
  tools/sync-version.py            # VERSION → 매니페스트 동기화
  tools/sync-version.py --check    # 일치 검증(불일치 시 exit 1) — CI 게이트
  tools/sync-version.py --set X.Y.Z  # VERSION 갱신 후 동기화
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# 탐색에서 제외할 디렉터리 이름
_PRUNE = {".git", ".venv", "node_modules", ".next", ".turbo", "build", "dist",
          "__pycache__", ".pytest_cache"}

# 명시 관리하는 package.json (내부 빌드 설정 패키지 frontend/packages/* 는 제외)
_PACKAGE_JSON = [
    "frontend/package.json",
    "frontend/apps/web/package.json",
    "docs/package.json",
]

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-.+][0-9A-Za-z.-]+)?$")


def _read_version() -> str:
    if not VERSION_FILE.exists():
        sys.exit(f"[sync-version] VERSION 파일이 없습니다: {VERSION_FILE}")
    v = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not _SEMVER_RE.match(v):
        sys.exit(f"[sync-version] VERSION 형식이 올바르지 않습니다: {v!r} (예: 0.1.0)")
    return v


def _iter_files(filename: str):
    """_PRUNE 를 건너뛰며 주어진 파일명을 재귀 탐색한다."""
    for path in ROOT.rglob(filename):
        if any(part in _PRUNE for part in path.relative_to(ROOT).parts):
            continue
        yield path


def _pyproject_section_span(text: str) -> tuple[int, int] | None:
    """``[project]`` 테이블의 (시작, 끝) 오프셋. 없으면 None."""
    m = re.search(r"(?m)^\[project\]\s*$", text)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    return start, end


# (현재 버전 추출 정규식, 치환 함수) — 파일 유형별 핸들러
def _pyproject_get(text: str) -> str | None:
    span = _pyproject_section_span(text)
    if not span:
        return None
    body = text[span[0]:span[1]]
    m = re.search(r'(?m)^version\s*=\s*"([^"]*)"', body)
    return m.group(1) if m else None  # dynamic 버전이면 None → 건너뜀


def _pyproject_set(text: str, version: str) -> str:
    span = _pyproject_section_span(text)
    s, e = span
    body = text[s:e]
    new_body = re.sub(r'(?m)^(version\s*=\s*)"[^"]*"', rf'\1"{version}"', body, count=1)
    return text[:s] + new_body + text[e:]


def _init_get(text: str) -> str | None:
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else None


def _init_set(text: str, version: str) -> str:
    return re.sub(r'(?m)^(__version__\s*=\s*)"[^"]*"', rf'\1"{version}"', text, count=1)


def _pkgjson_get(text: str) -> str | None:
    m = re.search(r'"version"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else None


def _pkgjson_set(text: str, version: str) -> str:
    return re.sub(r'("version"\s*:\s*)"[^"]*"', rf'\1"{version}"', text, count=1)


def _targets():
    """(경로, getter, setter) 목록. 존재하는 파일만."""
    out = []
    for path in _iter_files("pyproject.toml"):
        out.append((path, _pyproject_get, _pyproject_set))
    for path in _iter_files("__init__.py"):
        if _init_get(path.read_text(encoding="utf-8")) is not None:
            out.append((path, _init_get, _init_set))
    for rel in _PACKAGE_JSON:
        path = ROOT / rel
        if path.exists():
            out.append((path, _pkgjson_get, _pkgjson_set))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="제품 버전 단일 소스 동기화")
    ap.add_argument("--check", action="store_true", help="일치 검증만(불일치 시 exit 1)")
    ap.add_argument("--set", metavar="X.Y.Z", help="VERSION 갱신 후 동기화")
    args = ap.parse_args()

    if args.set:
        if not _SEMVER_RE.match(args.set):
            sys.exit(f"[sync-version] 잘못된 버전: {args.set!r} (예: 1.2.3)")
        VERSION_FILE.write_text(args.set + "\n", encoding="utf-8")
        print(f"[sync-version] VERSION → {args.set}")

    version = _read_version()
    targets = _targets()

    drift, changed = [], []
    for path, get, setter in targets:
        text = path.read_text(encoding="utf-8")
        cur = get(text)
        if cur is None or cur == version:
            continue
        rel = path.relative_to(ROOT)
        if args.check:
            drift.append((rel, cur))
        else:
            path.write_text(setter(text, version), encoding="utf-8")
            changed.append(rel)

    if args.check:
        if drift:
            print(f"[sync-version] ✗ VERSION={version} 과 불일치 {len(drift)}건:")
            for rel, cur in drift:
                print(f"    {rel}: {cur}")
            print("  → `tools/sync-version.py` 로 동기화하세요.")
            return 1
        print(f"[sync-version] ✅ 모든 매니페스트가 VERSION={version} 과 일치 "
              f"({len(targets)}개 검사)")
        return 0

    if changed:
        print(f"[sync-version] VERSION={version} 동기화 — {len(changed)}개 파일 갱신:")
        for rel in changed:
            print(f"    {rel}")
    else:
        print(f"[sync-version] 이미 모두 VERSION={version} 과 일치 (변경 없음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
