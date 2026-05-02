from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dashboard import build_dashboard
from .doctor import run_doctor
from .install import install
from .migrate import preview_migration
from .paths import ensure_registered
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
            p.add_argument("--apply", action="store_true",
                           help="actually perform the migration (default is to print help if neither --preview nor --apply set)")
            p.add_argument("--source", action="append", default=[])
        if command == "install":
            # Round-7 codex-review P1-3 fix: forward --no-hooks down so the
            # CLI install does not write ~/.codex/hooks.json. Default
            # remains register_hooks=True (matches install.sh phase 3
            # expectation when bash phase 2 already ran).
            p.add_argument("--no-hooks", action="store_true",
                           help="skip auto-registering hooks in ~/.codex/hooks.json (state init only)")
        if command == "uninstall":
            # CX-6: real --purge-state flag; default keeps data.
            p.add_argument("--purge-state", action="store_true",
                           help="DANGER: rm -rf the entire <project>/.codex/preference-tracker/ state directory.")
        if command == "exec":
            def _positive_int(s: str) -> int:
                v = int(s)
                if v <= 0:
                    raise argparse.ArgumentTypeError("--timeout must be > 0")
                return v
            p.add_argument("--timeout", type=_positive_int, default=None,
                           help="seconds to wait for the wrapped subprocess (default: env CODEX_PT_TIMEOUT or 600)")
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

    project_root = Path(args.project_root)

    if args.command == "install":
        install(project_root, register_hooks=not getattr(args, "no_hooks", False))
    elif args.command == "doctor":
        print(run_doctor(project_root).status_line)
    elif args.command == "uninstall":
        uninstall(project_root, keep_data=not args.purge_state)
    elif args.command == "scan":
        # CX-5: ensure_registered, not install — preserves mode.json across invocations.
        state = ensure_registered(project_root).state_root
        scan_message(state, args.message)
    elif args.command == "dashboard":
        state = ensure_registered(project_root).state_root
        print(build_dashboard(state))
    elif args.command == "promote":
        if args.dry_run:
            # Placeholder candidate used only for smoke-level command viability.
            state = ensure_registered(project_root).state_root
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
        else:
            print(
                "promote: --dry-run is the only supported CLI mode; programmatic callers "
                "should use codex_preftrack.promote.promote_candidate() directly.",
                file=sys.stderr,
            )
            return 2
    elif args.command == "migrate":
        # CX-13: don't silently no-op when neither flag is set.
        if not args.preview and not args.apply:
            print(
                "migrate: choose --preview to inspect decisions, or --apply to actually migrate.\n"
                "        sources may be passed via repeated --source.",
                file=sys.stderr,
            )
            return 2
        # W6 fix: preview_migration's first arg is `state_root` (it writes
        # `<state_root>/evidence/migration_preview.json` when write_report).
        # --preview is read-only — don't register or touch state_root at all.
        # --apply needs a real state_root → register lazily.
        if args.preview:
            preview_migration(Path("/"), [Path(p) for p in args.source], write_report=False)
        else:
            state_root = ensure_registered(project_root).state_root
            preview_migration(state_root, [Path(p) for p in args.source], write_report=True)
            print(
                "migrate --apply: preview written. Programmatic migration is not yet "
                "implemented; the preview report is the audit-only artifact.",
                file=sys.stderr,
            )
    elif args.command == "exec":
        # CX-12: enforce `--` as the separator between codex_preftrack flags
        # and the wrapped command. Without it, argparse will swallow flags
        # like --project-root that the user intended to pass through.
        cmd = list(args.cmd)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        else:
            print(
                "exec: missing `--` separator. Use:\n"
                "    codex_preftrack exec [--project-root PATH] [--timeout SEC] -- <wrapped command...>\n"
                "Without `--`, codex_preftrack flags can intercept arguments meant for the wrapped command.",
                file=sys.stderr,
            )
            return 2
        if not cmd:
            print("exec: no command given after `--`", file=sys.stderr)
            return 2
        state = ensure_registered(project_root).state_root
        result = run_wrapped(state, cmd, timeout_s=args.timeout)
        return result.exit_code
    return 0
