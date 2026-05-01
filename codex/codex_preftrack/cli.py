from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .dashboard import build_dashboard
from .doctor import run_doctor
from .install import install
from .migrate import preview_migration
from .promote import promote_candidate
from .scan import scan_message
from .uninstall import uninstall
from .wrapper import run_wrapped


COMMANDS = ("install", "doctor", "uninstall", "scan", "promote", "dashboard", "exec", "migrate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex_preftrack")
    sub = parser.add_subparsers(dest="command")
    for command in COMMANDS:
        p = sub.add_parser(command)
        if command in {"install", "doctor", "uninstall", "scan", "promote", "dashboard", "exec", "migrate"}:
            p.add_argument("--project-root", default=".")
        if command == "scan":
            p.add_argument("--message", required=False, default="")
        if command == "promote":
            p.add_argument("--dry-run", action="store_true")
        if command == "migrate":
            p.add_argument("--preview", action="store_true")
            p.add_argument("--source", action="append", default=[])
        if command == "exec":
            p.add_argument("cmd", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    if not argv:
        parser.print_usage(sys.stderr)
        return 2
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.command == "install":
        install(Path(args.project_root))
    elif args.command == "doctor":
        print(run_doctor(Path(args.project_root)).status_line)
    elif args.command == "uninstall":
        uninstall(Path(args.project_root))
    elif args.command == "scan":
        state = install(Path(args.project_root)).state_root
        scan_message(state, args.message)
    elif args.command == "dashboard":
        state = install(Path(args.project_root)).state_root
        print(build_dashboard(state))
    elif args.command == "promote":
        if args.dry_run:
            # Placeholder candidate used only for smoke-level command viability.
            state = install(Path(args.project_root)).state_root
            promote_candidate(
                state,
                {
                    "atomic_id": "dry-run",
                    "type": "preference",
                    "domain": "workflow",
                    "scope": "global",
                    "condition": "dry run",
                    "rule_text": "MUST dry run",
                    "applies_when": "dry run",
                    "does_not_apply_when": "(none)",
                    "confidence": "low",
                },
                dry_run=True,
            )
    elif args.command == "migrate":
        if args.preview:
            preview_migration(Path(args.project_root), [Path(p) for p in args.source], write_report=False)
    elif args.command == "exec":
        state = install(Path(args.project_root)).state_root
        cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
        result = run_wrapped(state, cmd)
        return result.exit_code
    return 0
