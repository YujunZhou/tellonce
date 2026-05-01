from __future__ import annotations

from pathlib import Path

from .ledger import read_events
from .mode import load_mode


def build_dashboard(state_root: Path) -> str:
    events = list(read_events(state_root))
    scans = [e for e in events if e.get("event_type") == "scan_recorded"]
    wrapped = [e for e in events if e.get("event_type") == "wrapper_run_completed"]
    mode = load_mode(state_root)
    return "\n".join(
        [
            f"mode: {mode.mode}",
            f"hooks: {mode.hooks}",
            f"blocking: {mode.blocking}",
            f"scan_count: {len(scans)}",
            f"wrapped_turns: {len(wrapped)}",
        ]
    )
