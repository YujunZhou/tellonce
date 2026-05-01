from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import load_registration


@dataclass(frozen=True)
class UninstallResult:
    removed_integration: bool
    purged_state: bool = False
    state_root: Path | None = None


def uninstall(project_root: Path, keep_data: bool = True) -> UninstallResult:
    """Remove the install marker; optionally wipe the entire state tree.

    CX-6 fix: previously `keep_data=False` was a documented no-op
    (`pass` with a TODO comment). That made the API lie. Now the flag
    actually deletes the state directory when False, and a refusal
    guard prevents accidental top-level wipes.
    """
    registration = load_registration(project_root)
    state_root = registration.state_root

    marker = state_root / "managed_runtime.txt"
    if marker.exists():
        marker.unlink()

    purged = False
    if not keep_data:
        # Refuse to rmtree obviously-dangerous paths.
        resolved = state_root.resolve()
        if resolved == Path("/") or len(resolved.parts) <= 2:
            raise ValueError(f"refusing to purge suspicious state_root: {resolved}")
        if state_root.is_dir():
            shutil.rmtree(state_root)
            purged = True

    return UninstallResult(
        removed_integration=True,
        purged_state=purged,
        state_root=state_root,
    )
