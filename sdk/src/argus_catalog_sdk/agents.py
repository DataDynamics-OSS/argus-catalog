# SPDX-License-Identifier: Apache-2.0
"""AI 에이전트 정의 파일(``agent.yaml``) 반영 · 정리 · 내보내기 (명령줄 도구 ``agent-apply``·``agent-export``).

설계: design/ai-agent-bulk-registration-design.md §4.6

반영 흐름
1. ``-f`` 로 받은 파일·디렉터리에서 ``*.yaml``/``*.yml`` 을 모은다(디렉터리는 하위까지, 이름순).
2. 파일마다 문서를 읽고 ``spec.release.systemPromptFile`` 을 그 파일 기준 상대 경로로 읽어 ``systemPrompt`` 로 바꾼다.
3. 파일마다 정의 파일 위치(``source_ref`` = ``<저장소>#<저장소 안 경로>``)를 붙여 200개씩 나눠 서버에 반영한다.
4. ``prune`` 이면 ``-f`` 로 준 위치 범위에서, 이번에 읽은 이름에 없는 정의 파일 관리 에이전트를 사용 중단으로 바꾼다.
   파일을 하나라도 읽지 못하면(이름 목록이 불완전) 정리는 하지 않는다.

``<저장소>`` 는 ``--source`` 로 줄 수 있고, 없으면 git 원격 주소(``origin``), git 저장소가 아니면 ``file:<절대 경로>``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CHUNK = 200
SUFFIXES = (".yaml", ".yml")
AGENT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,254}")  # 서버 app/agents/manifest.py NAME_PATTERN 과 같은 규칙


class ManifestFileError(ValueError):
    """정의 파일을 읽거나 해석할 수 없음(경로 없음, YAML 오류, 지시문 파일 없음 등).

    ``apply_paths`` 는 파일 하나의 오류를 ``ApplyReport.file_errors`` 에 모으고 나머지 파일은 계속 반영한다.
    반영할 파일이 하나도 없을 때만 이 예외를 그대로 올린다."""


# ---------------------------------------------------------------------------
# 파일 수집 · 해석
# ---------------------------------------------------------------------------


def collect_files(paths: list[str | Path]) -> list[Path]:
    """파일·디렉터리 목록 → 정의 파일 목록(중복 제거, 순서 고정).

    디렉터리를 훑을 때 숨김 경로(``.github/workflows/*.yml``, ``.hidden.yaml`` 처럼 디렉터리 기준 상대 경로의
    어느 조각이든 ``.`` 으로 시작)는 건너뛴다. 숨김 파일도 ``-f`` 로 직접 주면 읽는다.
    """
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out.extend(sorted(
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in SUFFIXES
                and not any(part.startswith(".") for part in f.relative_to(p).parts)
            ))
        elif p.is_file():
            out.append(p)
        else:
            raise ManifestFileError(f"경로가 없습니다: {p}")
    seen: set[Path] = set()
    uniq = []
    for f in out:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(f)
    return uniq


def load_file(path: Path) -> list[dict]:
    """정의 파일 하나의 문서 목록. ``systemPromptFile`` 은 본문으로 바꿔 넣는다."""
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d is not None]
    except (OSError, yaml.YAMLError) as e:
        raise ManifestFileError(f"{path}: 읽을 수 없습니다 — {e}") from e
    for i, doc in enumerate(docs, 1):
        if not isinstance(doc, dict):
            raise ManifestFileError(f"{path}: {i}번째 문서가 객체 형식이 아닙니다.")
        release = (doc.get("spec") or {}).get("release")
        if isinstance(release, dict) and "systemPromptFile" in release:
            if "systemPrompt" in release:
                raise ManifestFileError(f"{path}: systemPrompt 와 systemPromptFile 은 함께 쓸 수 없습니다.")
            prompt_path = (path.parent / str(release.pop("systemPromptFile"))).resolve()
            try:
                release["systemPrompt"] = prompt_path.read_text(encoding="utf-8")
            except OSError as e:
                raise ManifestFileError(f"{path}: 지시문 파일을 읽을 수 없습니다 — {prompt_path}") from e
    return docs


def doc_name(doc: dict) -> str | None:
    """문서의 ``metadata.name`` (없거나 문자열이 아니면 None). ``--prune`` 의 남길 이름 목록을 만들 때 쓴다."""
    name = (doc.get("metadata") or {}).get("name")
    return name if isinstance(name, str) and name else None


# ---------------------------------------------------------------------------
# 정의 파일 위치 (source_ref)
# ---------------------------------------------------------------------------


def strip_credentials(url: str) -> str:
    """원격 주소에서 사용자·비밀번호(토큰)를 뺀다 — ``https://x-access-token:TOKEN@github.com/o/r`` → ``https://github.com/o/r``.

    ``source_ref`` 는 카탈로그 DB·로그·변경 내역에 그대로 남으므로 CI 가 origin 에 넣어 둔 토큰이 새지 않게 한다.
    scp 형식(``git@github.com:o/r.git``)은 비밀이 없으므로 그대로 둔다."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if not parts.scheme or "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _git(cwd: Path, *args: str) -> str | None:
    """``git -C cwd <args>`` 의 출력(앞뒤 공백 제거). git 이 없거나 실패·빈 출력이면 None — 예외를 내지 않는다."""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


@dataclass
class SourceResolver:
    """경로 → ``<저장소>#<저장소 안 경로>``. 저장소 이름은 ``source`` > git origin > ``file:<루트>``."""

    source: str | None = None
    _cache: dict = field(default_factory=dict)

    def _root(self, path: Path) -> tuple[str, Path]:
        """경로가 속한 ``(저장소 이름, 기준 디렉터리)`` — 디렉터리별로 한 번만 git 을 부른다.

        기준 디렉터리는 git 최상위, git 밖이면 현재 작업 위치(그 밖의 경로면 파일 시스템 최상위)다.
        저장소 이름은 ``--source`` > git origin 주소 > ``file:<기준 디렉터리>`` 순이며, 어느 쪽이든
        ``strip_credentials`` 로 사용자·토큰을 뺀다."""
        d = path if path.is_dir() else path.parent
        d = d.resolve()
        if d in self._cache:
            return self._cache[d]
        top = _git(d, "rev-parse", "--show-toplevel")
        # git 저장소가 아니면 현재 작업 위치를 기준으로(그 밖의 경로면 파일 시스템 최상위 기준) 삼는다
        root = Path(top).resolve() if top else Path.cwd().resolve()
        if not top and not d.is_relative_to(root):
            root = Path(d.anchor)
        origin = _git(d, "remote", "get-url", "origin") if top and not self.source else None
        base = strip_credentials(self.source or origin or "") or f"file:{root.as_posix()}"
        self._cache[d] = (base, root)
        return base, root

    def ref(self, path: Path) -> str:
        """``<저장소>#<기준 디렉터리 기준 상대 경로>`` — 에이전트의 ``source_ref`` 로 서버에 기록된다."""
        base, root = self._root(path)
        rel = Path(os.path.relpath(path.resolve(), root)).as_posix()
        return f"{base}#{'' if rel == '.' else rel}"

    def prefix(self, path: Path) -> str:
        """정리 범위 — 디렉터리면 그 아래 전체, 파일이면 그 파일."""
        r = self.ref(path)
        return r if path.is_file() or r.endswith("#") else r + "/"


# ---------------------------------------------------------------------------
# 반영 · 정리
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    """``apply_paths`` 결과 — 명령줄 표와 종료 코드를 만드는 데 쓴다.

    ``summary`` 는 서버가 센 생성·갱신·변경 없음·실패 수의 합, ``results`` 는 에이전트별 결과에 파일 경로를 더한 것,
    ``file_errors`` 는 읽지 못한 정의 파일, ``pruned`` 는 정리 결과(``--prune``), ``prune_skipped`` 는 정리를
    건너뛴 이유다."""

    dry_run: bool
    summary: dict = field(default_factory=lambda: {"created": 0, "updated": 0, "unchanged": 0, "failed": 0})
    results: list[dict] = field(default_factory=list)  # {file, name, action, changes?, error?}
    file_errors: list[str] = field(default_factory=list)
    pruned: list[dict] = field(default_factory=list)  # {prefix, name, action, ...}
    prune_skipped: str | None = None

    @property
    def ok(self) -> bool:
        """반영 실패도 파일 오류도 없음. 정리 건너뜀(권한)은 명령줄에서 따로 실패로 친다."""
        return not self.file_errors and self.summary["failed"] == 0


def apply_paths(client, paths: list[str | Path], *, dry_run: bool = False, source: str | None = None,
                prune: bool = False) -> ApplyReport:
    """정의 파일을 반영하고(필요하면 정리까지) 결과를 돌려준다. 서버 오류(인증 등)는 예외로 올린다."""
    report = ApplyReport(dry_run=dry_run)
    resolver = SourceResolver(source)
    inputs = [Path(p) for p in paths]
    files = collect_files(inputs)
    if not files:
        raise ManifestFileError("반영할 정의 파일(*.yaml, *.yml)이 없습니다.")

    names: list[str] = []
    for f in files:
        try:
            docs = load_file(f)
        except ManifestFileError as e:
            report.file_errors.append(str(e))
            continue
        names.extend(n for n in map(doc_name, docs) if n)
        ref = resolver.ref(f)
        for i in range(0, len(docs), CHUNK):
            res = client.apply_agents(docs[i:i + CHUNK], dry_run=dry_run, source_ref=ref)
            for k in report.summary:
                report.summary[k] += res["summary"].get(k, 0)
            report.results.extend({"file": str(f), **r} for r in res["results"])

    if prune:
        if report.file_errors:
            report.prune_skipped = "읽지 못한 정의 파일이 있어 정리를 건너뜀(이름 목록이 불완전)"
        else:
            for prefix in dict.fromkeys(resolver.prefix(p) for p in inputs):
                res = client.prune_agents(prefix, names, dry_run=dry_run)
                report.pruned.extend({"prefix": prefix, **r} for r in res["results"])
    return report


# ---------------------------------------------------------------------------
# 내보내기
# ---------------------------------------------------------------------------


def select_agents(client, names: list[str] | None = None, *, search: str | None = None, status: str | None = None,
                  category: str | None = None, department: str | None = None) -> list[str]:
    """내보낼 에이전트 이름. 이름을 주면 그대로, 아니면 목록 조건으로(부서는 받은 목록에서 거른다)."""
    if names:
        return list(names)
    out, page = [], 1
    while True:
        data = client.list_agents(search=search, status=status, category=category, page=page, page_size=100)
        items = data.get("items", [])
        out.extend(a["name"] for a in items if not department or a.get("department") == department)
        if page * 100 >= data.get("total", 0) or not items:
            return out
        page += 1


def export_agents(client, names: list[str], out_dir: str | Path | None = None) -> list[tuple[str, str]]:
    """에이전트별 정의 파일 원문. ``out_dir`` 이면 ``<이름>.yaml`` 로 저장한다.

    이름은 서버 규칙(소문자·숫자·``.``·``_``·``-``)이어야 한다 — ``../x`` 같은 이름이 요청 경로나 저장 위치를 벗어나지 않게
    받기 전에 모두 검사한다."""
    bad = [n for n in names if not AGENT_NAME.fullmatch(n)]
    if bad:
        raise ValueError(f"에이전트 이름이 올바르지 않습니다: {', '.join(map(repr, bad))}")
    texts = [(n, client.export_agent(n)) for n in names]
    if out_dir is not None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        for n, t in texts:
            (d / f"{n}.yaml").write_text(t, encoding="utf-8")
    return texts
