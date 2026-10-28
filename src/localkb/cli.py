"""Command-line interface for localkb."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from localkb import __version__
from localkb.clipboard import copy_text
from localkb.embed import DEFAULT_MODEL
from localkb.errors import LocalKBError
from localkb.install import CLIENTS, install_client
from localkb.models import Hit
from localkb.pipeline import (
    check_dir,
    format_status,
    init_kb,
    search_kb,
    status_kb,
    update_kb,
)
from localkb.prompt import build_prompt


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except LocalKBError as exc:
        print(f"localkb: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("localkb: interrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localkb",
        description="Private, local-first knowledge base for any AI.",
    )
    parser.add_argument("--version", action="version", version=f"localkb {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="index a folder into <folder>/.localkb")
    init.add_argument("folder", help="folder of documents")
    init.add_argument("--force", action="store_true", help="replace an existing knowledge base")
    init.add_argument(
        "--embedder",
        choices=("fastembed", "hash", "ollama"),
        default=None,
        help="embedding backend (default: fastembed, or LOCALKB_EMBEDDER)",
    )
    init.add_argument(
        "--model",
        default=None,
        help=f"fastembed model id (default: {DEFAULT_MODEL})",
    )
    init.set_defaults(handler=_cmd_init)

    update = commands.add_parser(
        "update", help="index only new, changed, deleted, and renamed files"
    )
    update.add_argument(
        "folder", nargs="?", help="folder of documents (default: current directory)"
    )
    update.set_defaults(handler=_cmd_update)

    search = commands.add_parser("search", help="print matching excerpts and citations")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5, help="number of excerpts (default: 5)")
    search.add_argument("--json", action="store_true", help="print JSON instead of text")
    search.add_argument("--folder", help="folder of documents (default: current directory)")
    search.set_defaults(handler=_cmd_search)

    ask = commands.add_parser("ask", help="build a cited prompt for any assistant")
    ask.add_argument("query")
    ask.add_argument("--copy", action="store_true", help="copy the prompt to the clipboard")
    ask.add_argument("-k", type=int, default=6, help="number of excerpts (default: 6)")
    ask.add_argument("--folder", help="folder of documents (default: current directory)")
    ask.set_defaults(handler=_cmd_ask)

    serve = commands.add_parser("serve", help="serve the knowledge base over MCP")
    serve.add_argument("--folder", help="folder of documents (default: current directory)")
    serve.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument(
        "--token",
        default=None,
        help="bearer token for HTTP mode (or set LOCALKB_TOKEN)",
    )
    serve.set_defaults(handler=_cmd_serve)

    install = commands.add_parser("install", help="add localkb to an MCP client config")
    install.add_argument("client", choices=CLIENTS)
    install.add_argument("--folder", help="folder of documents (default: current directory)")
    install.add_argument("--config", help="write this config file instead of the client default")
    install.set_defaults(handler=_cmd_install)

    status = commands.add_parser("status", help="show knowledge-base size and model")
    status.add_argument(
        "folder", nargs="?", help="folder of documents (default: current directory)"
    )
    status.set_defaults(handler=_cmd_status)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    import os

    embedder = args.embedder or os.environ.get("LOCALKB_EMBEDDER") or "fastembed"
    report = init_kb(
        Path(args.folder),
        embedder=embedder,
        model=args.model,
        force=args.force,
    )
    print(report.format("Indexed"))
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    folder = Path(args.folder) if args.folder else Path.cwd()
    report = update_kb(folder)
    print(report.format("Updated"))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    folder = _optional_folder(args.folder)
    hits = search_kb(folder, args.query, k=args.k)
    if args.json:
        sys.stdout.write(_hits_json(hits))
    else:
        sys.stdout.write(_render_hits(hits))
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    folder = _optional_folder(args.folder)
    hits = search_kb(folder, args.query, k=args.k)
    prompt = build_prompt(args.query, hits)
    if args.copy and copy_text(prompt):
        print(
            f"Copied a prompt with {len(hits)} excerpts to the clipboard.",
            file=sys.stderr,
        )
        for index, hit in enumerate(hits, start=1):
            print(f"[{index}] {hit.path}  {hit.location}")
        return 0
    if args.copy:
        print(
            "No clipboard tool found (tried pbcopy, wl-copy, xclip, xsel, clip). Prompt follows.",
            file=sys.stderr,
        )
    sys.stdout.write(prompt)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import os

    from localkb.mcp_server import serve_http, serve_stdio

    folder = _optional_folder(args.folder)
    if args.http:
        if args.port < 1 or args.port > 65535:
            raise LocalKBError("port must be between 1 and 65535")
        token = args.token if args.token is not None else os.environ.get("LOCALKB_TOKEN")
        serve_http(folder, host=args.host, port=args.port, token=token or None)
        return 0
    if args.token:
        raise LocalKBError("--token is only used with --http")
    serve_stdio(folder)
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    folder = _optional_folder(args.folder)
    config = Path(args.config).expanduser() if args.config else None
    result = install_client(args.client, folder, config_path=config)
    print(result.format())
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    folder = Path(args.folder) if args.folder else Path.cwd()
    print(format_status(status_kb(folder)))
    return 0


def _optional_folder(value: str | None) -> Path:
    return check_dir(Path(value) if value else Path.cwd())


def _render_hits(hits: list[Hit]) -> str:
    if not hits:
        return "No matching excerpts.\n"
    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] {hit.score:.4f}  {hit.path}  {hit.location}")
        for line in hit.text.strip().splitlines() or [""]:
            lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _hits_json(hits: list[Hit]) -> str:
    payload = {"results": [hit.as_dict() for hit in hits]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
