from __future__ import annotations

from pathlib import Path
import json
import os
import sqlite3
import sys

from .ledger import read_events
from .mode import load_mode


def _upsert_counts(state_root: Path) -> dict[str, int]:
    try:
        registration = json.loads(
            (state_root / "registration.json").read_text(encoding="utf-8")
        )
        project_root = Path(registration["project_root"])
        shared_lib = Path(__file__).resolve().parents[1] / "shared_lib"
        if str(shared_lib) not in sys.path:
            sys.path.insert(0, str(shared_lib))
        import path_config

        os.environ["PT_PROJECT_ROOT"] = str(project_root)
        os.environ["B5_PROJECT_ROOT"] = str(project_root)
        path_config.get_project_root.cache_clear()
        path_config.get_memory_dir.cache_clear()
        db_path = Path(path_config.get_memory_dir()) / ".tellonce.sqlite3"
        if not db_path.is_file():
            return {}
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM turns GROUP BY status"
            ).fetchall()
            plans = conn.execute(
                "SELECT result_json FROM turns WHERE result_json IS NOT NULL"
            ).fetchall()
        counts = {str(status): int(count) for status, count in rows}

        def count_rejects(mutations):
            total = 0
            for mutation in mutations or []:
                if not isinstance(mutation, dict):
                    continue
                total += str(mutation.get("operation", "")).upper() == "REJECT"
                total += count_rejects(mutation.get("children"))
            return total

        counts["rejected_mutations"] = sum(
            count_rejects(json.loads(row[0] or "{}").get("mutations", []))
            for row in plans
        )
        return counts
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
        return {}


def build_dashboard(state_root: Path) -> str:
    events = list(read_events(state_root))
    scans = [e for e in events if e.get("event_type") == "scan_recorded"]
    wrapped = [e for e in events if e.get("event_type") == "wrapper_run_completed"]
    mode = load_mode(state_root)
    upsert = _upsert_counts(state_root)
    # Read hooks status from the ground truth (~/.codex/hooks.json)
    # at query time, not the stale `mode.hooks` field that was written once at
    # install. Otherwise dashboard drifts and reports `hooks: disabled` even
    # after install_codex_hooks --add succeeded.
    try:
        from .doctor import _hooks_status
        hooks_state = _hooks_status()
    except Exception:
        hooks_state = mode.hooks  # fallback to legacy field on any error
    return "\n".join(
        [
            f"mode: {mode.mode}",
            f"hooks: {hooks_state}",
            f"blocking: {mode.blocking}",
            f"scan_count: {len(scans)}",
            f"wrapped_turns: {len(wrapped)}",
            "upsert_turns: "
            f"pending={upsert.get('pending', 0)}, "
            f"resolving={upsert.get('resolving', 0)}, "
            f"needs_user={upsert.get('needs_user', 0)}, "
            f"rejected={upsert.get('rejected', 0)}, "
            f"rejected_mutations={upsert.get('rejected_mutations', 0)}, "
            f"failed={upsert.get('failed', 0)}",
        ]
    )
