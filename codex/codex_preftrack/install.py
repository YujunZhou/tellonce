from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .ledger import secure_write_text
from .paths import Registration, register_project


@dataclass(frozen=True)
class InstallRecord:
    state_root: Path
    registration: Registration
    hooks_registered: bool = False


def _try_register_global_hooks() -> tuple[bool, str]:
    """Round-7: auto-register PT hooks in ~/.codex/hooks.json if the global
    runtime layout exists. Idempotent — re-running is safe (skipped =
    "already registered").

    Returns (registered, message). registered=True iff the call ran without
    error AND the hooks dir exists. message is for the caller to surface.
    """
    skill_dir = Path.home() / ".codex" / "skills" / "preference-tracker"
    hooks_dir = skill_dir / "hooks"
    hooks_json = Path.home() / ".codex" / "hooks.json"
    if not hooks_dir.is_dir():
        return False, (
            f"⚠ ~/.codex/skills/preference-tracker/hooks/ not present — global "
            f"runtime not installed yet. Run `bash codex/install.sh` once to "
            f"deploy the runtime + hooks. Current call only initialized "
            f"per-project state."
        )
    try:
        from .install_codex_hooks import cmd_add
        cmd_add(str(hooks_json), str(hooks_dir))
        return True, f"✓ hooks registered in {hooks_json}"
    except Exception as e:
        return False, f"⚠ hook registration failed: {type(e).__name__}: {e}"


def install(project_root: Path, register_hooks: bool = True) -> InstallRecord:
    registration = register_project(project_root)
    state = registration.state_root
    secure_write_text(state / "install_record.json", '{"status":"installed"}\n')
    secure_write_text(state / "managed_runtime.txt", "codex_preftrack runtime\n")

    hooks_registered = False
    if register_hooks:
        hooks_registered, msg = _try_register_global_hooks()
        # Surface the message so users running `codex_preftrack install`
        # bare (without bash install.sh wrapping it) see what happened.
        try:
            sys.stderr.write(msg + "\n")
        except Exception:
            pass

    return InstallRecord(
        state_root=state,
        registration=registration,
        hooks_registered=hooks_registered,
    )
