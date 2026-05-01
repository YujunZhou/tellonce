from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from .ledger import secure_mkdir, secure_write_text
from .mode import load_mode, write_mode


class ProjectRootError(ValueError):
    pass


@dataclass(frozen=True)
class Registration:
    project_root: Path
    state_root: Path
    state_location: str


def project_id_for(path: Path) -> str:
    resolved = str(path.resolve())
    name = path.resolve().name or "project"
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    return f"{name}-{digest}"


_UNSAFE_TMP_PREFIXES = ("/tmp", "/private/tmp", "/var/tmp")


def resolve_project_root(path: Path, allow_unsafe: bool = False) -> Path:
    """Reject unsafe project roots (root, HOME, /tmp variants).

    HX-11 fix: macOS `/tmp` is a symlink → `/private/tmp`. The original
    `startswith("/tmp")` check passed `/private/tmp/...` through. Now we
    check the resolved path against an explicit list of tmp prefixes plus
    `/var/tmp` (some distros).
    """
    root = Path(path).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    allow_unsafe = allow_unsafe or os.environ.get("CODEX_PREFTRACK_ALLOW_TEMP") == "1"
    root_str = str(root)
    is_tmp = any(root_str == p or root_str.startswith(p + "/") for p in _UNSAFE_TMP_PREFIXES)
    if not allow_unsafe and (root == Path("/") or root == home or is_tmp):
        raise ProjectRootError(f"unsafe project root: {root}")
    return root


def default_state_root(project_root: Path) -> Path:
    return project_root / ".codex" / "preference-tracker"


def fallback_state_root(project_root: Path) -> Path:
    return Path(os.path.expanduser("~")) / ".codex" / "projects" / project_id_for(project_root) / "preference-tracker"


def register_project(project_root: Path, allow_unsafe: bool = False) -> Registration:
    """Idempotent registration.

    CX-5 fix: do NOT call write_mode unconditionally — that resets
    mode.json back to `audit_only`/`wrapper_seen=False` on every CLI
    invocation, breaking the wrapper-mode state machine's monotonicity.
    Now we only write the default mode if mode.json doesn't already exist.
    """
    root = resolve_project_root(project_root, allow_unsafe=allow_unsafe)
    state_root = default_state_root(root)
    location = "project"
    try:
        secure_mkdir(state_root)
        probe = state_root / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        state_root = fallback_state_root(root)
        secure_mkdir(state_root)
        location = "global_project_fallback"

    import json as _json

    registration = {
        "project_root": str(root),
        "install_cwd": str(Path.cwd().resolve()),
        "state_root": str(state_root),
        "state_location": location,
        "package_version": "0.1.0",
        "mode": "audit_only",
    }
    secure_write_text(
        state_root / "registration.json",
        _json.dumps(registration, indent=2) + "\n",
        atomic=True,
    )
    # Only initialize mode.json on first install — preserve later mode/wrapper_seen.
    if not (state_root / "mode.json").is_file():
        write_mode(state_root)
    return Registration(project_root=root, state_root=state_root, state_location=location)


def load_registration(project_root: Path) -> Registration:
    """Locate or create the project's registration.

    CX-10 fix: the fallback "create on miss" path used `allow_unsafe=True`,
    which silently bypassed the safe-root check that install rejects. Now
    creation honors the same safety policy as install — if neither location
    holds a registration AND the root is unsafe (HOME / `/` / `/tmp`),
    raise rather than auto-create.
    """
    import json as _json

    # Load is permissive: we may need to read a registration for a project
    # whose root happens to equal HOME (legacy install). But CREATE goes
    # through the strict path.
    root = resolve_project_root(project_root, allow_unsafe=True)
    state = default_state_root(root)
    path = state / "registration.json"
    if not path.is_file():
        fallback = fallback_state_root(root)
        path = fallback / "registration.json"
        state = fallback
    if not path.is_file():
        # Auto-register, but use strict policy (allow_unsafe=False).
        return register_project(root, allow_unsafe=False)
    data = _json.loads(path.read_text(encoding="utf-8"))
    return Registration(
        project_root=Path(data.get("project_root", str(root))),
        state_root=path.parent,
        state_location=data.get("state_location", "project"),
    )


def ensure_registered(project_root: Path) -> Registration:
    """Get the Registration without re-running write_mode side effects.

    CX-5 helper used by cli.py for scan/dashboard/exec/promote/migrate so
    those commands don't reset mode.json. Falls back to register_project
    on first run, which is fine because that goes through the same
    monotone path now.
    """
    return load_registration(project_root)
