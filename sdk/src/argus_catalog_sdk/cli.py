# SPDX-License-Identifier: Apache-2.0
"""Argus Model CLI — Argus Catalog 모델 레지스트리용 커맨드라인 인터페이스.

인증: 내려받기(pull)와 쓰기 명령(push·import·delete·agent-apply)은 로그인이 필요하다.
    --token TOKEN 또는 환경변수 ARGUS_TOKEN, 혹은 --username/--password (ARGUS_USERNAME/ARGUS_PASSWORD)
서버: --server URL 또는 환경변수 ARGUS_SERVER (웹 UI 주소도 사용 가능 — /api/v1 을 서버로 넘겨 줌)
종료 코드: 성공 0, 오류(서버 오류는 "오류: API error <코드>: <내용>")·삭제 확인 취소·agent-apply 실패는 1.

OCI 모델 허브::

    argus-model oci-list [--search Q]
    argus-model oci-import-hf HF_MODEL_ID [--name NAME] [--task T] [--framework F] [--no-wait]
    # 로컬 디렉터리를 OCI 모델 새 버전으로 (큰 파일은 --presigned-min-mb 로 저장소 직접 업로드)
    argus-model push LOCAL_DIR NAME [--description D] [--owner O] [--task T] [--framework F] [--presigned-min-mb MB]
    argus-model oci-delete NAME [-y]

MLflow 모델 레지스트리 / 공통::

    # 모델 목록
    argus-model list [--search QUERY] [--server URL]

    # 모델을 로컬 디렉터리로 내려받기 (OCI·MLflow 공통)
    argus-model pull MODEL_NAME VERSION DEST_DIR [--server URL]

    # HuggingFace 에서 임포트
    argus-model import-hf HF_MODEL_ID MODEL_NAME [--revision REV] [--server URL]

    # 서버(컨테이너) 안의 디렉터리에서 임포트 (에어갭 — 실행하는 PC 의 경로가 아님)
    argus-model import-local SERVER_DIR MODEL_NAME [--server URL]

    # 모델 버전의 파일 목록
    argus-model files MODEL_NAME VERSION [--server URL]

    # OCI manifest 조회
    argus-model manifest MODEL_NAME VERSION [--server URL]

    # 모델 영구 삭제 (관리자, '모델 삭제' 입력으로 확인 — -y 면 생략)
    argus-model delete MODEL_NAME [MODEL_NAME ...] [-y] [--server URL]

AI 에이전트 정의 파일 (``argus-catalog`` 으로도 실행 가능)::

    # 정의 파일·디렉터리 반영 (미리 보기 → 반영)
    argus-catalog agent-apply -f agents/ --dry-run
    argus-catalog agent-apply -f agents/ [--source REPO] [--prune]

    # 정의 파일로 내보내기 (이름 지정 또는 조건)
    argus-catalog agent-export cs.refund-helper > cs.refund-helper.yaml
    argus-catalog agent-export --department 고객지원팀 -o agents/
"""

import argparse
import json
import os
import sys

from rich.console import Console
from rich.table import Table

from argus_catalog_sdk.client import ModelClient

console = Console()
DEFAULT_SERVER = os.environ.get("ARGUS_SERVER", "http://localhost:4600")


def _get_client(args) -> ModelClient:
    """공통 옵션(``--server``·``--token``·``--username``·``--password``, 없으면 환경 변수)으로 클라이언트를 만든다.

    사용자명·비밀번호가 있으면 여기서 로그인하므로, 비밀번호가 틀리면 명령을 시작하기 전에 401 로 끝난다."""
    return ModelClient(
        args.server,
        token=args.token or None,
        username=args.username or None,
        password=args.password or None,
    )


def cmd_oci_list(args):
    """``oci-list`` — OCI 모델 허브 목록을 표로. 버전이 없는 모델은 '최신 버전' 이 ``-``."""
    client = _get_client(args)
    result = client.oci_list_models(search=args.search, page=args.page, page_size=args.page_size)
    table = Table(title=f"OCI 모델 (총 {result['total']}개)")
    table.add_column("이름", style="bold")
    table.add_column("출처")
    table.add_column("최신 버전", justify="center")
    table.add_column("상태", justify="center")
    table.add_column("수정일")
    for m in result["items"]:
        table.add_row(
            m["name"], m.get("source_type") or "-", f"v{m['version_count']}" if m.get('version_count') else "-",
            m.get("status") or "", (m.get("updated_at") or "")[:10],
        )
    console.print(table)


def cmd_oci_import_hf(args):
    """``oci-import-hf`` — 서버가 HuggingFace 에서 받아 OCI 허브에 넣는다. 기본은 끝날 때까지 기다리고,
    ``--no-wait`` 면 접수만 하고 돌아온다(진행은 웹 UI 버전 탭)."""
    client = _get_client(args)
    console.print(f"OCI 허브로 가져오는 중: [bold]{args.hf_model_id}[/] (revision={args.revision})")
    with console.status("HuggingFace 에서 받아 저장소로 올리는 중..."):
        result = client.oci_import_huggingface(
            args.hf_model_id, name=args.name, revision=args.revision,
            description=args.description, owner=args.owner, task=args.task,
            framework=args.framework, language=args.language, wait=not args.no_wait,
        )
    if result.get("status") == "importing":
        console.print(f"접수됨: {result['name']} v{result['version']} — 웹 UI 버전 탭에서 진행 상태를 확인하세요.")
    else:
        console.print(f"{result['name']} v{result['version']} 가져오기 완료: {result['file_count']}개 파일, "
                      f"{result['total_size']:,} 바이트")


def cmd_oci_delete(args):
    """``oci-delete`` — 모델 이름을 다시 입력받아 확인한 뒤 DB 기록과 저장소 파일을 지운다. ``-y`` 면 확인 생략.

    취소하면 1 을 돌려줘 종료 코드로 구분할 수 있게 한다."""
    client = _get_client(args)
    if not args.yes:
        confirm = input(f"OCI 모델 '{args.name}' 과 저장된 파일을 삭제합니다. 모델 이름을 입력하세요: ")
        if confirm != args.name:
            console.print("[red]취소됨[/]")
            return 1  # 스크립트에서 취소를 구분할 수 있게 0 이 아닌 종료 코드
    client.oci_delete_model(args.name)
    console.print(f"삭제됨: {args.name}")


def cmd_list(args):
    """``list`` — MLflow 등록 모델 목록. 최신 버전 상태를 색으로 구분한다(READY 파랑, FAILED 빨강, PENDING 노랑)."""
    client = _get_client(args)
    result = client.list_models(search=args.search, page=args.page, page_size=args.page_size)

    table = Table(title=f"모델 (총 {result['total']}개)")
    table.add_column("이름", style="bold")
    table.add_column("소유자")
    table.add_column("버전", justify="center")
    table.add_column("상태", justify="center")
    table.add_column("수정일")

    for m in result["items"]:
        status = m.get("latest_version_status") or m.get("status") or ""
        status_style = (
            "blue" if status == "READY"
            else "red" if "FAILED" in status
            else "yellow" if "PENDING" in status
            else ""
        )
        table.add_row(
            m["name"],
            m.get("owner") or "-",
            f"v{m['max_version_number']}" if m.get('max_version_number') else "-",
            f"[{status_style}]{status}[/]" if status_style else status,
            (m.get("updated_at") or "")[:10],
        )

    console.print(table)


def cmd_pull(args):
    """``pull`` — 버전의 파일 전체를 받아 받은 경로를 출력한다(하위 폴더 유지, MLflow 기록 모델은 서버 디스크에서)."""
    client = _get_client(args)
    console.print(f"내려받는 중: [bold]{args.model_name}[/] v{args.version} -> {args.dest}")
    files = client.pull(args.model_name, args.version, args.dest)
    console.print(f"{len(files)}개 파일 다운로드 완료:")
    for f in files:
        console.print(f"  {f}")


def cmd_push(args):
    """``push`` — 로컬 디렉터리를 OCI 모델 새 버전으로. ``--presigned-min-mb`` 는 바이트로 바꿔 넘긴다.

    이전에 끊긴 push 의 남은 파일을 정리했으면 그 번호도 알려 준다."""
    client = _get_client(args)
    console.print(f"푸시하는 중: [bold]{args.local_dir}[/] -> {args.model_name}")
    min_size = int(args.presigned_min_mb * 1024 * 1024) if args.presigned_min_mb is not None else None
    result = client.push(args.local_dir, args.model_name, description=args.description, owner=args.owner,
                         task=args.task, framework=args.framework, presigned_min_size=min_size)
    for v in result.get("cleaned_versions") or []:
        console.print(f"[yellow]이전에 끊긴 push 가 v{v} 에 남긴 파일을 정리했습니다.[/]")
    console.print(f"v{result['version']} 푸시 완료: {result['file_count']}개 파일")


def cmd_import_hf(args):
    """``import-hf`` — HuggingFace → MLflow 레지스트리 반입(관리자). 서버가 다 받을 때까지 기다린다."""
    client = _get_client(args)
    console.print(f"임포트하는 중: [bold]{args.hf_model_id}[/] -> {args.model_name} (revision={args.revision})")
    with console.status("HuggingFace 에서 다운로드 중..."):
        result = client.import_huggingface(
            args.hf_model_id, args.model_name,
            revision=args.revision, description=args.description,
        )
    console.print(f"v{result['version']} 임포트 완료: {result['file_count']}개 파일, "
                  f"{result['total_size']:,} 바이트")
    console.print(f"저장 위치: {result['storage_location']}")


def cmd_import_local(args):
    """``import-local`` — **서버 안의** 디렉터리 → MLflow 레지스트리 반입(관리자, 에어갭)."""
    client = _get_client(args)
    console.print(f"임포트하는 중: [bold]{args.local_dir}[/] -> {args.model_name}")
    result = client.import_local(
        args.local_dir, args.model_name, description=args.description,
    )
    console.print(f"v{result['version']} 임포트 완료: {result['file_count']}개 파일, "
                  f"{result['total_size']:,} 바이트")


def cmd_files(args):
    """``files`` — 버전의 파일 이름·크기·수정일 표. 크기는 B/KB/MB 로 줄여 보여 준다."""
    client = _get_client(args)
    files = client.list_files(args.model_name, args.version)

    table = Table(title=f"{args.model_name} v{args.version} 파일")
    table.add_column("파일명")
    table.add_column("크기", justify="right")
    table.add_column("수정일")

    for f in files:
        size = f.get("size") or 0
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / 1024 / 1024:.1f} MB"
        table.add_row(f["filename"], size_str, (f.get("last_modified") or "")[:19])

    console.print(table)


def cmd_manifest(args):
    """``manifest`` — 저장소 버전의 OCI manifest(JSON) 출력. MLflow 클라이언트로 기록한 버전에는 없다(404)."""
    client = _get_client(args)
    manifest = client.get_manifest(args.model_name, args.version)
    console.print_json(json.dumps(manifest, indent=2))


def cmd_delete(args):
    """``delete`` — MLflow 모델 영구 삭제(관리자). '모델 삭제' 를 입력받아 확인하고, ``-y`` 면 생략한다.

    취소하면 1. 없는 이름은 오류 대신 '찾을 수 없음' 으로 따로 알려 준다."""
    client = _get_client(args)
    console.print(f"{len(args.names)}개 모델 삭제: {', '.join(args.names)}")
    if not args.yes:
        confirm = input("확인하려면 '모델 삭제' 를 입력하세요: ")
        if confirm != "모델 삭제":
            console.print("[red]취소됨[/]")
            return 1  # 스크립트에서 취소를 구분할 수 있게 0 이 아닌 종료 코드
    result = client.hard_delete_models(args.names)
    console.print(f"삭제됨: {result['deleted']}")
    if result.get("not_found"):
        console.print(f"[yellow]찾을 수 없음: {result['not_found']}[/]")


# ── AI 에이전트 정의 파일 ──

_ACTION_LABEL = {"created": "생성", "updated": "갱신", "unchanged": "변경 없음", "failed": "실패",
                 "deprecated": "사용 중단", "skipped": "건너뜀"}
_ACTION_STYLE = {"created": "green", "updated": "cyan", "unchanged": "dim", "failed": "red",
                 "deprecated": "yellow", "skipped": "red"}


def _label(action: str) -> str:
    """반영·정리 결과 코드(``created`` 등)를 한글 이름 + 색 표시(rich 마크업)로."""
    return f"[{_ACTION_STYLE.get(action, '')}]{_ACTION_LABEL.get(action, action)}[/]"


def cmd_agent_apply(args) -> int:
    """``agent-apply`` — 정의 파일을 반영하고(``--prune`` 이면 정리까지) 결과를 표로 보여 준다.

    '변경 없음' 은 ``-v`` 일 때만 표에 넣는다. 실패·파일 오류·정리 건너뜀(권한)이 하나라도 있으면 1 을 돌려줘
    CI 단계가 실패하게 한다."""
    from argus_catalog_sdk.agents import apply_paths

    client = _get_client(args)
    report = apply_paths(client, args.files, dry_run=args.dry_run, source=args.source, prune=args.prune)
    title = "반영 미리 보기" if report.dry_run else "반영 결과"

    for err in report.file_errors:
        console.print(f"[red]파일 오류:[/] {err}")

    show = report.results if args.verbose else [r for r in report.results if r["action"] != "unchanged"]
    if show:
        table = Table(title=title)
        table.add_column("이름", style="bold")
        table.add_column("결과")
        table.add_column("내용")
        table.add_column("파일", style="dim")
        for r in show:
            detail = r.get("error") or "\n".join(r.get("changes") or []) or "—"
            table.add_row(r["name"], _label(r["action"]), detail, r["file"])
        console.print(table)

    s = report.summary
    console.print(f"{title}: 생성 {s['created']} · 갱신 {s['updated']} · 변경 없음 {s['unchanged']} · 실패 {s['failed']}"
                  + (f" · 파일 오류 {len(report.file_errors)}" if report.file_errors else ""))

    if args.prune:
        if report.prune_skipped:
            console.print(f"[yellow]정리 건너뜀:[/] {report.prune_skipped}")
        elif report.pruned:
            table = Table(title="정리 미리 보기" if report.dry_run else "정리 결과")
            table.add_column("이름", style="bold")
            table.add_column("결과")
            table.add_column("내용")
            for r in report.pruned:
                detail = r.get("error") or (f"{r['from_status']} → deprecated" if r.get("from_status") else "—")
                table.add_row(r["name"], _label(r["action"]), detail)
            console.print(table)
        else:
            console.print("정리 대상 없음")
    if report.dry_run:
        console.print("[dim]미리 보기입니다 — 실제로 바뀐 것은 없습니다. --dry-run 없이 다시 실행하면 반영됩니다.[/]")
    failed = not report.ok or any(r["action"] == "skipped" for r in report.pruned)
    return 1 if failed else 0


def cmd_agent_export(args) -> int:
    """``agent-export`` — 에이전트를 정의 파일로. ``-o`` 면 ``<이름>.yaml`` 로 저장하고, 없으면 원문을 표준 출력으로
    (여러 개면 ``---`` 로 구분) 내보낸다. 안내 문구는 표준 오류로 보내 파이프·리다이렉트를 더럽히지 않는다."""
    from argus_catalog_sdk.agents import export_agents, select_agents

    client = _get_client(args)
    names = select_agents(client, args.names, search=args.search, status=args.status,
                          category=args.category, department=args.department)
    if not names:
        Console(stderr=True).print("[yellow]내보낼 에이전트가 없습니다.[/]")
        return 0
    texts = export_agents(client, names, args.output)
    if args.output:
        Console(stderr=True).print(f"{len(texts)}개 정의 파일을 {args.output} 에 저장했습니다.")
    else:
        # 표준 출력은 정의 파일 원문만(여러 개면 '---' 로 구분) — 파일로 바로 저장할 수 있게
        sys.stdout.write("---\n".join(t if t.endswith("\n") else t + "\n" for _, t in texts))
    return 0


def main(argv: list[str] | None = None):
    """명령줄 진입점(``argus-model``·``argus-catalog`` 공통).

    ``argv`` 를 주면 그것을 해석한다(테스트용). 명령 함수가 0 이 아닌 값을 돌려주면 그 값으로, 예외가 나면
    ``오류: <내용>`` 을 찍고 1 로 종료한다 — 서버 오류는 ``API error <코드>: <내용>`` 형식이다."""
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]) or "argus-model",
        description="Argus Catalog CLI — 모델 레지스트리 · AI 에이전트 정의 파일",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --server 공통 부모 파서
    server_parent = argparse.ArgumentParser(add_help=False)
    server_parent.add_argument("--server", default=DEFAULT_SERVER, help="카탈로그 서버 URL (env ARGUS_SERVER)")
    server_parent.add_argument("--token", default=os.environ.get("ARGUS_TOKEN"),
                               help="액세스 토큰 (env ARGUS_TOKEN)")
    server_parent.add_argument("--username", default=os.environ.get("ARGUS_USERNAME"),
                               help="로그인 사용자 (env ARGUS_USERNAME)")
    server_parent.add_argument("--password", default=os.environ.get("ARGUS_PASSWORD"),
                               help="로그인 비밀번호 (env ARGUS_PASSWORD)")

    # ── OCI 모델 허브 ──
    p = sub.add_parser("oci-list", help="OCI 모델 허브 목록", parents=[server_parent])
    p.add_argument("--search", help="이름·설명 검색")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=12)
    p.set_defaults(func=cmd_oci_list)

    p = sub.add_parser("oci-import-hf", help="HuggingFace 모델을 OCI 허브로 가져오기 (관리자)",
                       parents=[server_parent])
    p.add_argument("hf_model_id", help="HuggingFace 모델 ID (예: bert-base-uncased)")
    p.add_argument("--name", help="OCI 모델 이름 (기본: hf_model_id 의 '/' 를 '-' 로)")
    p.add_argument("--revision", default="main")
    p.add_argument("--description")
    p.add_argument("--owner", help="소유자")
    p.add_argument("--task", help="태스크 (예: text-classification)")
    p.add_argument("--framework", help="프레임워크 (예: pytorch)")
    p.add_argument("--language", help="언어 (예: ko)")
    p.add_argument("--no-wait", action="store_true", help="접수만 하고 완료를 기다리지 않음")
    p.set_defaults(func=cmd_oci_import_hf)

    p = sub.add_parser("oci-delete", help="OCI 모델 삭제 (파일 포함)", parents=[server_parent])
    p.add_argument("name")
    p.add_argument("--yes", "-y", action="store_true", help="확인 절차 생략")
    p.set_defaults(func=cmd_oci_delete)

    # ── MLflow 모델 레지스트리 ──

    # list
    p = sub.add_parser("list", help="MLflow 모델 목록 조회", parents=[server_parent])
    p.add_argument("--search", help="이름으로 검색")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=cmd_list)

    # pull
    p = sub.add_parser("pull", help="모델 파일을 로컬 디렉터리로 내려받기", parents=[server_parent])
    p.add_argument("model_name", help="모델 이름")
    p.add_argument("version", type=int, help="버전 번호")
    p.add_argument("dest", help="대상 디렉터리")
    p.set_defaults(func=cmd_pull)

    # push (OCI 모델 허브)
    p = sub.add_parser("push", help="로컬 디렉터리를 OCI 모델 새 버전으로 푸시", parents=[server_parent])
    p.add_argument("local_dir", help="로컬 디렉터리 경로")
    p.add_argument("model_name", help="대상 모델 이름")
    p.add_argument("--description", help="모델 설명 (새 모델일 때)")
    p.add_argument("--owner", help="소유자 (새 모델일 때)")
    p.add_argument("--task", help="태스크 (새 모델일 때, 예: text-classification)")
    p.add_argument("--framework", help="프레임워크 (새 모델일 때, 예: pytorch)")
    p.add_argument("--presigned-min-mb", type=float, metavar="MB",
                   help="이 크기(MB) 이상 파일은 서버를 거치지 않고 저장소로 직접 업로드(presigned URL)")
    p.set_defaults(func=cmd_push)

    # import-hf
    p = sub.add_parser("import-hf", help="HuggingFace 모델을 MLflow 레지스트리로 임포트 (관리자)", parents=[server_parent])
    p.add_argument("hf_model_id", help="HuggingFace 모델 ID")
    p.add_argument("model_name", help="대상 모델 이름")
    p.add_argument("--revision", default="main", help="HuggingFace revision")
    p.add_argument("--description", help="모델 설명")
    p.set_defaults(func=cmd_import_hf)

    # import-local
    p = sub.add_parser("import-local", help="서버 로컬 디렉터리를 MLflow 레지스트리로 임포트 (관리자·에어갭)", parents=[server_parent])
    p.add_argument("local_dir", help="서버 로컬 디렉터리 경로")
    p.add_argument("model_name", help="대상 모델 이름")
    p.add_argument("--description", help="모델 설명")
    p.set_defaults(func=cmd_import_local)

    # files
    p = sub.add_parser("files", help="모델 버전의 파일 목록", parents=[server_parent])
    p.add_argument("model_name", help="모델 이름")
    p.add_argument("version", type=int, help="버전 번호")
    p.set_defaults(func=cmd_files)

    # manifest
    p = sub.add_parser("manifest", help="모델 버전의 OCI manifest 조회", parents=[server_parent])
    p.add_argument("model_name", help="모델 이름")
    p.add_argument("version", type=int, help="버전 번호")
    p.set_defaults(func=cmd_manifest)

    # delete
    p = sub.add_parser("delete", help="MLflow 모델 영구 삭제 (관리자)", parents=[server_parent])
    p.add_argument("names", nargs="+", help="삭제할 모델 이름")
    p.add_argument("--yes", "-y", action="store_true", help="확인 절차 생략")
    p.set_defaults(func=cmd_delete)

    # ── AI 에이전트 정의 파일 ──
    p = sub.add_parser("agent-apply", help="AI 에이전트 정의 파일(agent.yaml) 반영", parents=[server_parent])
    p.add_argument("-f", "--file", dest="files", action="append", required=True, metavar="PATH",
                   help="정의 파일 또는 디렉터리(하위의 *.yaml·*.yml 전부). 여러 번 지정 가능")
    p.add_argument("--dry-run", action="store_true", help="바뀔 내용만 보여 주고 반영하지 않음")
    p.add_argument("--source", help="정의 파일 저장소 이름(기본: git 원격 주소 origin, 없으면 file:<경로>)")
    p.add_argument("--prune", action="store_true",
                   help="-f 로 준 위치에서 관리되던 에이전트 중 이번 파일에 없는 것을 사용 중단으로 바꿈")
    p.add_argument("-v", "--verbose", action="store_true", help="변경 없음 결과도 표에 표시")
    p.set_defaults(func=cmd_agent_apply)

    p = sub.add_parser("agent-export", help="AI 에이전트를 정의 파일(agent.yaml)로 내보내기", parents=[server_parent])
    p.add_argument("names", nargs="*", help="에이전트 이름(없으면 조건에 맞는 전체)")
    p.add_argument("--search", help="이름·설명 검색")
    p.add_argument("--status", help="상태 (draft, active, deprecated, blocked ...)")
    p.add_argument("--category", help="분류")
    p.add_argument("--department", help="부서")
    p.add_argument("-o", "--output", metavar="DIR", help="디렉터리에 <이름>.yaml 로 저장(없으면 표준 출력)")
    p.set_defaults(func=cmd_agent_export)

    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except Exception as e:
        console.print(f"[red]오류:[/] {e}")
        sys.exit(1)
    if rc:
        sys.exit(rc)


if __name__ == "__main__":
    main()
