from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Mode:
    mode: str = "audit_only"
    hooks: str = "disabled"
    blocking: bool = False
    wrapper_seen: bool = False


def write_mode(state_root: Path, mode: str = "audit_only", wrapper_seen: bool = False, hooks: str = "disabled") -> Mode:
    state_root.mkdir(parents=True, exist_ok=True)
    data = Mode(mode=mode, hooks=hooks, blocking=False, wrapper_seen=wrapper_seen)
    (state_root / "mode.json").write_text(json.dumps(data.__dict__, indent=2) + "\n", encoding="utf-8")
    return data


def load_mode(state_root: Path) -> Mode:
    path = state_root / "mode.json"
    if not path.is_file():
        return Mode()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Mode(
        mode=data.get("mode", "audit_only"),
        hooks=data.get("hooks", "disabled"),
        blocking=bool(data.get("blocking", False)),
        wrapper_seen=bool(data.get("wrapper_seen", False)),
    )
