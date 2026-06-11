# SPDX-License-Identifier: Apache-2.0
"""Argus Model CLI — Argus Catalog 모델 레지스트리용 커맨드라인 인터페이스.

사용 예::

    # 모델 목록
    argus-model list [--search QUERY] [--server URL]

    # 모델을 로컬 디렉터리로 내려받기
    argus-model pull MODEL_NAME VERSION DEST_DIR [--server URL]

    # 로컬 디렉터리를 새 모델 버전으로 푸시
    argus-model push LOCAL_DIR MODEL_NAME [--description DESC] [--server URL]

    # HuggingFace 에서 임포트
    argus-model import-hf HF_MODEL_ID MODEL_NAME [--revision REV] [--server URL]

    # 서버 로컬 디렉터리에서 임포트 (에어갭)
    argus-model import-local LOCAL_DIR MODEL_NAME [--server URL]

    # 모델 버전의 파일 목록
    argus-model files MODEL_NAME VERSION [--server URL]

    # OCI manifest 조회
    argus-model manifest MODEL_NAME VERSION [--server URL]

    # 모델 삭제
    argus-model delete MODEL_NAME [MODEL_NAME ...] [--server URL]
"""

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

from argus_catalog_sdk.client import ModelClient

console = Console()
DEFAULT_SERVER = "http://localhost:4600"


def _get_client(args) -> ModelClient:
    return ModelClient(args.server)


def cmd_list(args):
    client = _get_client(args)
    result = client.list_models(search=args.search, page=args.page, page_size=args.page_size)

    table = Table(title=f"모델 (총 {result['total']}개)")
    table.add_column("이름", style="bold")
    table.add_column("소유자")
    table.add_column("버전", justify="center")
    table.add_column("상태", justify="center")
    table.add_column("수정일")

    for m in result["items"]:
        status = m.get("latest_version_status") or m.get("status", "")
        status_style = (
            "blue" if status == "READY"
            else "red" if "FAILED" in status
            else "yellow" if "PENDING" in status
            else ""
        )
        table.add_row(
            m["name"],
            m.get("owner") or "-",
            f"v{m['max_version_number']}",
            f"[{status_style}]{status}[/]" if status_style else status,
            m.get("updated_at", "")[:10],
        )

    console.print(table)


def cmd_pull(args):
    client = _get_client(args)
    console.print(f"내려받는 중: [bold]{args.model_name}[/] v{args.version} -> {args.dest}")
    files = client.pull(args.model_name, args.version, args.dest)
    console.print(f"{len(files)}개 파일 다운로드 완료:")
    for f in files:
        console.print(f"  {f}")


def cmd_push(args):
    client = _get_client(args)
    console.print(f"푸시하는 중: [bold]{args.local_dir}[/] -> {args.model_name}")
    result = client.push(args.local_dir, args.model_name, description=args.description)
    console.print(f"v{result['version']} 푸시 완료: {result['file_count']}개 파일")


def cmd_import_hf(args):
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
    client = _get_client(args)
    console.print(f"임포트하는 중: [bold]{args.local_dir}[/] -> {args.model_name}")
    result = client.import_local(
        args.local_dir, args.model_name, description=args.description,
    )
    console.print(f"v{result['version']} 임포트 완료: {result['file_count']}개 파일, "
                  f"{result['total_size']:,} 바이트")


def cmd_files(args):
    client = _get_client(args)
    files = client.list_files(args.model_name, args.version)

    table = Table(title=f"{args.model_name} v{args.version} 파일")
    table.add_column("파일명")
    table.add_column("크기", justify="right")
    table.add_column("수정일")

    for f in files:
        size = f["size"]
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / 1024 / 1024:.1f} MB"
        table.add_row(f["filename"], size_str, f.get("last_modified", "")[:19])

    console.print(table)


def cmd_manifest(args):
    client = _get_client(args)
    manifest = client.get_manifest(args.model_name, args.version)
    console.print_json(json.dumps(manifest, indent=2))


def cmd_delete(args):
    client = _get_client(args)
    console.print(f"{len(args.names)}개 모델 삭제: {', '.join(args.names)}")
    if not args.yes:
        confirm = input("확인하려면 '모델 삭제' 를 입력하세요: ")
        if confirm != "모델 삭제":
            console.print("[red]취소됨[/]")
            return
    result = client.hard_delete_models(args.names)
    console.print(f"삭제됨: {result['deleted']}")
    if result.get("not_found"):
        console.print(f"[yellow]찾을 수 없음: {result['not_found']}[/]")


def main():
    parser = argparse.ArgumentParser(
        prog="argus-model",
        description="Argus Catalog 모델 레지스트리 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --server 공통 부모 파서
    server_parent = argparse.ArgumentParser(add_help=False)
    server_parent.add_argument("--server", default=DEFAULT_SERVER, help="카탈로그 서버 URL")

    # list
    p = sub.add_parser("list", help="등록된 모델 목록 조회", parents=[server_parent])
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

    # push
    p = sub.add_parser("push", help="로컬 디렉터리를 모델 버전으로 푸시", parents=[server_parent])
    p.add_argument("local_dir", help="로컬 디렉터리 경로")
    p.add_argument("model_name", help="대상 모델 이름")
    p.add_argument("--description", help="모델 설명")
    p.set_defaults(func=cmd_push)

    # import-hf
    p = sub.add_parser("import-hf", help="HuggingFace Hub 에서 임포트", parents=[server_parent])
    p.add_argument("hf_model_id", help="HuggingFace 모델 ID")
    p.add_argument("model_name", help="대상 모델 이름")
    p.add_argument("--revision", default="main", help="HuggingFace revision")
    p.add_argument("--description", help="모델 설명")
    p.set_defaults(func=cmd_import_hf)

    # import-local
    p = sub.add_parser("import-local", help="서버 로컬 디렉터리에서 임포트 (에어갭)", parents=[server_parent])
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
    p = sub.add_parser("delete", help="모델 영구 삭제", parents=[server_parent])
    p.add_argument("names", nargs="+", help="삭제할 모델 이름")
    p.add_argument("--yes", "-y", action="store_true", help="확인 절차 생략")
    p.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        console.print(f"[red]오류:[/] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
