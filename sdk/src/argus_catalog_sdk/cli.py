"""Argus Model CLI — Command-line interface for Argus Catalog Model Registry.

Usage::

    # List models
    argus-model list [--search QUERY] [--server URL]

    # Pull model to local directory
    argus-model pull MODEL_NAME VERSION DEST_DIR [--server URL]

    # Push local directory as new model version
    argus-model push LOCAL_DIR MODEL_NAME [--description DESC] [--server URL]

    # Import from HuggingFace
    argus-model import-hf HF_MODEL_ID MODEL_NAME [--revision REV] [--server URL]

    # Import from server-local directory (airgap)
    argus-model import-local LOCAL_DIR MODEL_NAME [--server URL]

    # List files in a model version
    argus-model files MODEL_NAME VERSION [--server URL]

    # Get OCI manifest
    argus-model manifest MODEL_NAME VERSION [--server URL]

    # Delete models
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

    table = Table(title=f"Models ({result['total']} total)")
    table.add_column("Name", style="bold")
    table.add_column("Owner")
    table.add_column("Ver", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Updated")

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
    console.print(f"Pulling [bold]{args.model_name}[/] v{args.version} -> {args.dest}")
    files = client.pull(args.model_name, args.version, args.dest)
    console.print(f"Downloaded {len(files)} files:")
    for f in files:
        console.print(f"  {f}")


def cmd_push(args):
    client = _get_client(args)
    console.print(f"Pushing [bold]{args.local_dir}[/] -> {args.model_name}")
    result = client.push(args.local_dir, args.model_name, description=args.description)
    console.print(f"Pushed v{result['version']}: {result['file_count']} files")


def cmd_import_hf(args):
    client = _get_client(args)
    console.print(f"Importing [bold]{args.hf_model_id}[/] -> {args.model_name} (revision={args.revision})")
    with console.status("Downloading from HuggingFace..."):
        result = client.import_huggingface(
            args.hf_model_id, args.model_name,
            revision=args.revision, description=args.description,
        )
    console.print(f"Imported v{result['version']}: {result['file_count']} files, "
                  f"{result['total_size']:,} bytes")
    console.print(f"Storage: {result['storage_location']}")


def cmd_import_local(args):
    client = _get_client(args)
    console.print(f"Importing [bold]{args.local_dir}[/] -> {args.model_name}")
    result = client.import_local(
        args.local_dir, args.model_name, description=args.description,
    )
    console.print(f"Imported v{result['version']}: {result['file_count']} files, "
                  f"{result['total_size']:,} bytes")


def cmd_files(args):
    client = _get_client(args)
    files = client.list_files(args.model_name, args.version)

    table = Table(title=f"{args.model_name} v{args.version} files")
    table.add_column("Filename")
    table.add_column("Size", justify="right")
    table.add_column("Modified")

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
    console.print(f"Deleting {len(args.names)} model(s): {', '.join(args.names)}")
    if not args.yes:
        confirm = input("Type DELETE MODELS to confirm: ")
        if confirm != "DELETE MODELS":
            console.print("[red]Cancelled[/]")
            return
    result = client.hard_delete_models(args.names)
    console.print(f"Deleted: {result['deleted']}")
    if result.get("not_found"):
        console.print(f"[yellow]Not found: {result['not_found']}[/]")


def main():
    parser = argparse.ArgumentParser(
        prog="argus-model",
        description="Argus Catalog Model Registry CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Common parent for --server
    server_parent = argparse.ArgumentParser(add_help=False)
    server_parent.add_argument("--server", default=DEFAULT_SERVER, help="Catalog server URL")

    # list
    p = sub.add_parser("list", help="List registered models", parents=[server_parent])
    p.add_argument("--search", help="Search by name")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=cmd_list)

    # pull
    p = sub.add_parser("pull", help="Pull model files to local directory", parents=[server_parent])
    p.add_argument("model_name", help="Model name")
    p.add_argument("version", type=int, help="Version number")
    p.add_argument("dest", help="Destination directory")
    p.set_defaults(func=cmd_pull)

    # push
    p = sub.add_parser("push", help="Push local directory as model version", parents=[server_parent])
    p.add_argument("local_dir", help="Local directory path")
    p.add_argument("model_name", help="Target model name")
    p.add_argument("--description", help="Model description")
    p.set_defaults(func=cmd_push)

    # import-hf
    p = sub.add_parser("import-hf", help="Import from HuggingFace Hub", parents=[server_parent])
    p.add_argument("hf_model_id", help="HuggingFace model ID")
    p.add_argument("model_name", help="Target model name")
    p.add_argument("--revision", default="main", help="HuggingFace revision")
    p.add_argument("--description", help="Model description")
    p.set_defaults(func=cmd_import_hf)

    # import-local
    p = sub.add_parser("import-local", help="Import from server-local directory (airgap)", parents=[server_parent])
    p.add_argument("local_dir", help="Server-local directory path")
    p.add_argument("model_name", help="Target model name")
    p.add_argument("--description", help="Model description")
    p.set_defaults(func=cmd_import_local)

    # files
    p = sub.add_parser("files", help="List files in a model version", parents=[server_parent])
    p.add_argument("model_name", help="Model name")
    p.add_argument("version", type=int, help="Version number")
    p.set_defaults(func=cmd_files)

    # manifest
    p = sub.add_parser("manifest", help="Get OCI manifest for a model version", parents=[server_parent])
    p.add_argument("model_name", help="Model name")
    p.add_argument("version", type=int, help="Version number")
    p.set_defaults(func=cmd_manifest)

    # delete
    p = sub.add_parser("delete", help="Permanently delete models", parents=[server_parent])
    p.add_argument("names", nargs="+", help="Model names to delete")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
