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


# Rank for monotonic transitions.
# Mode must only progress forward; downgrade is opt-in via allow_downgrade=True
# (e.g. test fixtures). wrapper_seen is also latch-only (True stays True).
_MODE_RANK = {"audit_only": 0, "wrapper": 1, "blocking": 2}


class ModeDowngradeError(ValueError):
    """Raised when write_mode would downgrade the persisted mode."""


def write_mode(
    state_root: Path,
    mode: str = "audit_only",
    wrapper_seen: bool = False,
    hooks: str = "disabled",
    allow_downgrade: bool = False,
) -> Mode:
    # Lazy import to break circular dep with ledger (which imports mode for tests).
    from .ledger import secure_mkdir, secure_write_text

    secure_mkdir(state_root)
    if not allow_downgrade and (state_root / "mode.json").is_file():
        try:
            prior = load_mode(state_root)
        except Exception:
            prior = None
        if prior is not None:
            prior_rank = _MODE_RANK.get(prior.mode, -1)
            new_rank = _MODE_RANK.get(mode, -1)
            if prior_rank >= 0 and new_rank >= 0 and new_rank < prior_rank:
                raise ModeDowngradeError(
                    f"refusing to downgrade mode {prior.mode!r} -> {mode!r}; "
                    f"pass allow_downgrade=True if intentional (e.g. test reset)"
                )
            # wrapper_seen latches True.
            if prior.wrapper_seen and not wrapper_seen:
                wrapper_seen = True

    data = Mode(mode=mode, hooks=hooks, blocking=False, wrapper_seen=wrapper_seen)
    secure_write_text(state_root / "mode.json", json.dumps(data.__dict__, indent=2) + "\n", atomic=True)
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
