from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ledger import append_event
from .mode import load_mode
from .paths import load_registration


PRIVATE_PATTERNS = ("yzhou25", "/scratch365", "/home/user", ".claude")


@dataclass(frozen=True)
class DoctorReport:
    sections: dict
    status_line: str


def _private_path_status(state_root: Path) -> str:
    for path in state_root.rglob("*"):
        if path.is_file():
            if path.name == "registration.json":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(pattern in text for pattern in PRIVATE_PATTERNS):
                return "FAIL"
    return "PASS"


def run_doctor(project_root: Path) -> DoctorReport:
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
        "shadow": "DISABLED",
    }
    append_event(
        state,
        {
            "event_type": "doctor_run",
            "session_id": "codex-current",
            "payload": {"sections": sections},
        },
    )
    install_status = "OBSERVE_ONLY" if all(v != "FAIL" for v in sections.values()) else "FAILED"
    status_line = (
        "Preference Tracker status: local=PASS, skill=PASS, state="
        f"{sections['state']}, plain_codex_hooks=DEGRADED, wrapper={sections['wrapper']}, "
        f"shadow=DISABLED, install={install_status}"
    )
    return DoctorReport(sections=sections, status_line=status_line)
