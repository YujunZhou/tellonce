from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from .mode import write_mode


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


def resolve_project_root(path: Path, allow_unsafe: bool = False) -> Path:
    root = Path(path).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    allow_unsafe = allow_unsafe or os.environ.get("CODEX_PREFTRACK_ALLOW_TEMP") == "1"
    if not allow_unsafe and (root == Path("/") or root == home or str(root).startswith("/tmp")):
        raise ProjectRootError(f"unsafe project root: {root}")
    return root


def default_state_root(project_root: Path) -> Path:
    return project_root / ".codex" / "preference-tracker"


def fallback_state_root(project_root: Path) -> Path:
    return Path(os.path.expanduser("~")) / ".codex" / "projects" / project_id_for(project_root) / "preference-tracker"


def register_project(project_root: Path, allow_unsafe: bool = False) -> Registration:
    root = resolve_project_root(project_root, allow_unsafe=allow_unsafe)
    state_root = default_state_root(root)
    location = "project"
    try:
        state_root.mkdir(parents=True, exist_ok=True)
        probe = state_root / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        state_root = fallback_state_root(root)
        state_root.mkdir(parents=True, exist_ok=True)
        location = "global_project_fallback"

    registration = {
        "project_root": str(root),
        "install_cwd": str(Path.cwd().resolve()),
        "state_root": str(state_root),
        "state_location": location,
        "package_version": "0.1.0",
        "mode": "audit_only",
    }
    (state_root / "registration.json").write_text(json.dumps(registration, indent=2) + "\n", encoding="utf-8")
    write_mode(state_root)
    return Registration(project_root=root, state_root=state_root, state_location=location)


def load_registration(project_root: Path) -> Registration:
    root = resolve_project_root(project_root, allow_unsafe=True)
    state = default_state_root(root)
    path = state / "registration.json"
    if not path.is_file():
        fallback = fallback_state_root(root)
        path = fallback / "registration.json"
        state = fallback
    if not path.is_file():
        return register_project(root, allow_unsafe=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    # The registration file is the authority for identity, but the state root is
    # the directory that contains the registration. This prevents a corrupted or
    # machine-specific absolute state_root value from redirecting doctor writes.
    return Registration(
        project_root=Path(data.get("project_root", str(root))),
        state_root=path.parent,
        state_location=data.get("state_location", "project"),
    )
