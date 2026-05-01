from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import load_registration


@dataclass(frozen=True)
class UninstallResult:
    removed_integration: bool


def uninstall(project_root: Path, keep_data: bool = True) -> UninstallResult:
    registration = load_registration(project_root)
    marker = registration.state_root / "managed_runtime.txt"
    if marker.exists():
        marker.unlink()
    if not keep_data:
        # Deliberately conservative: caller must explicitly remove state in a future purge implementation.
        pass
    return UninstallResult(removed_integration=True)
