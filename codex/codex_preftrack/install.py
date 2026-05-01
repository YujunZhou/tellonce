from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import Registration, register_project


@dataclass(frozen=True)
class InstallRecord:
    state_root: Path
    registration: Registration


def install(project_root: Path) -> InstallRecord:
    registration = register_project(project_root)
    state = registration.state_root
    (state / "install_record.json").write_text('{"status":"installed","mode":"audit_only"}\n', encoding="utf-8")
    (state / "managed_runtime.txt").write_text("codex_preftrack runtime\n", encoding="utf-8")
    return InstallRecord(state_root=state, registration=registration)
