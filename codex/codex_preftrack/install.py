from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ledger import secure_write_text
from .paths import Registration, register_project


@dataclass(frozen=True)
class InstallRecord:
    state_root: Path
    registration: Registration


def install(project_root: Path) -> InstallRecord:
    registration = register_project(project_root)
    state = registration.state_root
    secure_write_text(state / "install_record.json", '{"status":"installed"}\n')
    secure_write_text(state / "managed_runtime.txt", "codex_preftrack runtime\n")
    return InstallRecord(state_root=state, registration=registration)
