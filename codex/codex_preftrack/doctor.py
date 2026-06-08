from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .ledger import SECRET_PATTERNS
from .mode import load_mode
from .paths import load_registration


# CX-3 fix: previously default empty + module-load-time env read. Now:
#   - default has a small generic-leak heuristic (kicks in even without env)
#   - reads env on every run_doctor() call
#   - SECRET_PATTERNS is an additional always-on safety net
#
# The generic-leak default looks for things that almost certainly mean a
# fork picked up an upstream author's hardcoded path (rather than a runtime-
# detected one): non-user `/scratch*` paths, non-user `/users/<other>/`,
# `/private/var/folders` references that are platform-specific build leaks.
_GENERIC_LEAK_HEURISTIC: tuple[re.Pattern, ...] = (
    # `/Users/<name>/...` (macOS) and `/users/<name>/...` (some Linux installs).
    # Case-insensitive so macOS `/Users/alice/` matches.
    re.compile(r"/[Uu]sers/[A-Za-z][A-Za-z0-9_\-]{1,32}/[A-Za-z]"),
    # Windows user-home path: `C:\Users\<name>\...` (any drive letter).
    # CX-B4 fix: POSIX-only patterns let Windows private-path leaks
    # (`C:\Users\alice\...`) pass undetected. Match a drive letter + the
    # backslash `Users` segment + a username component.
    re.compile(r"[A-Za-z]:\\[Uu]sers\\[A-Za-z][A-Za-z0-9_\-. ]{1,32}\\"),
    # Hardcoded skill-dir paths under someone else's home — almost always a
    # leak from a fork-author's install script that shouldn't be in user state.
    re.compile(r"/home/[A-Za-z][A-Za-z0-9_\-]{1,32}/\.claude/skills"),
    # Note: removed the `/scratch\d*/[a-z]` rule because legitimate HPC users
    # work out of /scratch/<user>/ and would hit the heuristic on every event.
    # If a fork wants to detect a specific cluster path leak, set
    # CODEX_PT_PRIVATE_PATTERNS=/scratch/<your-id> explicitly.
)
_REGISTRATION_FILES = {"registration.json", "install_record.json"}
# Skip very large files (likely captured stdout) — they're already sanitized
# at write time via secure_write_text + sanitize, and reading them all into
# memory would make doctor extremely slow on long-running projects.
_PRIVATE_PATH_MAX_FILE_BYTES = 256 * 1024  # 256KB per file


def _runtime_env_patterns() -> tuple[str, ...]:
    """Read CODEX_PT_PRIVATE_PATTERNS at call time so dynamic env settings work."""
    return tuple(
        p.strip()
        for p in os.environ.get("CODEX_PT_PRIVATE_PATTERNS", "").split(",")
        if p.strip()
    )


# Backwards-compatible module attribute (tests poke at it via importlib.reload).
PRIVATE_PATTERNS: tuple[str, ...] = _runtime_env_patterns()


@dataclass(frozen=True)
class DoctorReport:
    sections: dict
    status_line: str


def _file_has_leak(text: str, env_patterns: tuple[str, ...]) -> bool:
    """A file leaks if it matches any:
      - configured env-driven literal pattern (substring match)
      - generic leak heuristic (regex)
      - a SECRET_PATTERNS hit (catches API keys / DB URIs that slipped past
        the sanitize step at write time)
    """
    for pat in env_patterns:
        if pat and pat in text:
            return True
    for pat in _GENERIC_LEAK_HEURISTIC:
        if pat.search(text):
            return True
    for pat, _replacement in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def _private_path_status(state_root: Path) -> str:
    """Walk state_root files; return PASS if no leak signature found.

    HX-3/HX-10 fix: skip files larger than 256KB (subprocess stdout dumps),
    skip registration files (they legitimately contain absolute paths),
    skip events.lock and *.tmp.
    """
    env_patterns = _runtime_env_patterns()
    for path in state_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _REGISTRATION_FILES:
            continue
        if path.suffix == ".tmp" or path.name.endswith(".lock"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _PRIVATE_PATH_MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _file_has_leak(text, env_patterns):
            return "FAIL"
    return "PASS"


def _hooks_status() -> str:
    """Round-7: report whether codex global hook registration is in place.

    Round-7 codex-review P1-6 fix (Medium, 2026-05-02): compare (event,
    basename) pairs, not basenames alone. Otherwise a hook registered to
    the WRONG event (e.g. posttooluse-deterministic-block.sh under
    UserPromptSubmit) would still report PASS, masking a broken install.

    Returns:
      PASS          — every (event, basename) pair from PT_HOOKS is present
                      and registered to the correct event
      PARTIAL       — some pairs missing OR a basename registered to wrong event
      NOT_INSTALLED — no PT hooks at all
      FAIL          — hooks.json corrupt
    """
    import json
    hooks_json = Path.home() / ".codex" / "hooks.json"
    if not hooks_json.is_file():
        return "NOT_INSTALLED"
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except Exception:
        return "FAIL"
    try:
        from .install_codex_hooks import PT_HOOKS, _is_pt_command
    except Exception:
        return "FAIL"
    expected_pairs = {
        (event, basename)
        for event, lst in PT_HOOKS.items()
        for basename, _ in lst
    }
    found_pairs: set[tuple[str, str]] = set()
    found_any = False
    for event, chain in (data.get("hooks") or {}).items():
        for entry in chain or []:
            for h in entry.get("hooks") or []:
                cmd = h.get("command", "")
                if not _is_pt_command(cmd):
                    continue
                found_any = True
                basename = cmd.replace("\\", "/").rsplit("/", 1)[-1]
                found_pairs.add((event, basename))
    if not found_any:
        return "NOT_INSTALLED"
    if found_pairs == expected_pairs:
        return "PASS"
    return "PARTIAL"


def run_doctor(project_root: Path) -> DoctorReport:
    """Build a DoctorReport. CX-3/HX-4 fix: doctor no longer appends a
    `doctor_run` event to the ledger — that turned doctor into a self-amplifying
    write source on every health check, defeating its read-only intent and
    making append_event slower on each run.
    """
    registration = load_registration(project_root)
    state = registration.state_root
    mode = load_mode(state)
    wrapper_events = False
    try:
        from .ledger import read_events

        wrapper_events = any(e.get("event_type") == "wrapper_run_completed" for e in read_events(state))
    except Exception:
        wrapper_events = False
    sections = {
        "state": "PASS" if state.is_dir() else "FAIL",
        "private_paths": _private_path_status(state) if state.is_dir() else "FAIL",
        "wrapper": "PASS" if (mode.wrapper_seen or wrapper_events) else "NOT_USED",
        "hooks": _hooks_status(),
        "shadow": "DISABLED",
    }
    install_status = "OBSERVE_ONLY" if all(v != "FAIL" for v in sections.values()) else "FAILED"
    # HX-10 UX fix: stop emitting "DEGRADED" / "NOT_USED" by default — they
    # made every fresh install look broken. Use neutral words.
    status_line = (
        "Preference Tracker status: "
        f"state={sections['state']}, "
        f"private_paths={sections['private_paths']}, "
        f"wrapper={sections['wrapper']}, "
        f"hooks={sections['hooks']}, "
        f"shadow={sections['shadow']}, "
        f"install={install_status}"
    )
    return DoctorReport(sections=sections, status_line=status_line)
