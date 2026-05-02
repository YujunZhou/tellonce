from __future__ import annotations

from pathlib import Path

from .ledger import read_events
from .mode import load_mode


def build_dashboard(state_root: Path) -> str:
    events = list(read_events(state_root))
    scans = [e for e in events if e.get("event_type") == "scan_recorded"]
    wrapped = [e for e in events if e.get("event_type") == "wrapper_run_completed"]
    mode = load_mode(state_root)
    # Round-7 fix: read hooks status from the ground truth (~/.codex/hooks.json)
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
        ]
    )
