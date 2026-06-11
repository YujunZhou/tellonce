from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import tempfile
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


def _norm_for_compare(p: str) -> str:
    """Normalize a path string for prefix comparison across platforms.

    Lowercases on case-insensitive filesystems (Windows), collapses `.`/`..`,
    and unifies separators so both `/` and `\\` forms compare equal.
    """
    return os.path.normcase(os.path.normpath(p))


def _unsafe_tmp_prefixes() -> tuple[str, ...]:
    """All temp-dir prefixes to reject, normalized for the current platform.

    Includes the fixed POSIX list (so behavior on Linux/macOS is unchanged)
    plus the platform temp dir reported by `tempfile.gettempdir()`, which on
    Windows resolves to `%TEMP%` (e.g. C:\\Users\\<name>\\AppData\\Local\\Temp).

    The platform temp dir is run through `Path.resolve()` so a Windows 8.3
    short name (e.g. `C:\\Users\\T-YUJU~1\\...`) is canonicalized to the same
    long form a resolved project root produces; otherwise the prefix compare
    would miss.
    """
    prefixes = list(_UNSAFE_TMP_PREFIXES)
    try:
        prefixes.append(str(Path(tempfile.gettempdir()).resolve()))
    except Exception:
        pass
    return tuple(_norm_for_compare(p) for p in prefixes)


def resolve_project_root(path: Path, allow_unsafe: bool = False) -> Path:
    """Reject unsafe project roots (root, HOME, /tmp variants).

    HX-11 fix: macOS `/tmp` is a symlink → `/private/tmp`. The original
    `startswith("/tmp")` check passed `/private/tmp/...` through. Now we
    check the resolved path against an explicit list of tmp prefixes plus
    `/var/tmp` (some distros).

    CX-B3 fix: the prefix list was POSIX-only and compared with a forward-
    slash separator, so on Windows `%TEMP%` was never blacklisted and
    backslash paths never matched. We now include `tempfile.gettempdir()`
    and normalize separators/case (via os.path.normcase/normpath) so both
    `/` and `\\` forms are caught on every platform.
    """
    root = Path(path).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    allow_unsafe = allow_unsafe or os.environ.get("TELLONCE_CODEX_ALLOW_TEMP") == "1"
    root_norm = _norm_for_compare(str(root))
    is_tmp = any(
        root_norm == p or root_norm.startswith(p + os.sep)
        for p in _unsafe_tmp_prefixes()
    )
    if not allow_unsafe and (root == Path("/") or root == home or is_tmp):
        raise ProjectRootError(f"unsafe project root: {root}")
    return root


def default_state_root(project_root: Path) -> Path:
    return project_root / ".codex" / "tellonce"


def fallback_state_root(project_root: Path) -> Path:
    return Path(os.path.expanduser("~")) / ".codex" / "projects" / project_id_for(project_root) / "tellonce"


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

    CX-10 + post-review fix: the fallback "create on miss" path used to be
    `allow_unsafe=True` (silently bypassed safety) — that was wrong. We then
    flipped it to `allow_unsafe=False`, which broke legacy installs whose
    project_root happens to be HOME or under /tmp (e.g. someone who already
    ran the package against `~`). Now we forward the same `allow_unsafe`
    policy that the caller asked for: load is permissive (so we can find
    legacy installs), but create-on-miss inherits the original caller's
    safety preference. The test fixture path that uses
    `TELLONCE_CODEX_ALLOW_TEMP=1` continues to work; cli.py paths that
    don't set that env still get strict semantics on first install.
    """
    import json as _json

    # If the user explicitly opted into unsafe roots (via env), let load and
    # create both honor that. Otherwise: load is lax (find legacy state),
    # create is strict (don't silently provision under HOME/`/`/`/tmp`).
    env_allow = os.environ.get("TELLONCE_CODEX_ALLOW_TEMP") == "1"
    root = resolve_project_root(project_root, allow_unsafe=True)
    state = default_state_root(root)
    path = state / "registration.json"
    if not path.is_file():
        fallback = fallback_state_root(root)
        path = fallback / "registration.json"
        state = fallback
    if not path.is_file():
        # No prior install — create. Use env_allow so test mode and
        # explicit-opt-in users can register against /tmp / HOME.
        return register_project(root, allow_unsafe=env_allow)
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
