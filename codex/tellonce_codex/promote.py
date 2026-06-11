from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .index import build_active_index
from .ledger import append_event, event_id, secure_mkdir
from .memory import canonical_key, parse_memory, write_memory_atomic


_VALID_ATOMIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,127}$")


class InvalidAtomicIdError(ValueError):
    """Raised when a candidate's atomic_id contains characters that could
    enable path traversal or filename-shell-meta abuse. The id is used
    directly as a filename (`<atomic_id>.md`) so it must be tightly
    constrained.
    """


@dataclass(frozen=True)
class PromoteResult:
    created: bool
    path: Path | None = None
    reason: str = ""


def _read_existing_supersedes(active_path: Path) -> tuple[list[str], str | None]:
    """If active_path exists, return (its current supersedes list, its atomic_id)."""
    if not active_path.is_file():
        return [], None
    try:
        existing = parse_memory(active_path)
    except Exception:
        return [], None
    sup = existing.data.get("supersedes") or []
    if not isinstance(sup, list):
        sup = []
    return [str(x) for x in sup], existing.data.get("atomic_id")


def _fsync_dir(path: Path) -> None:
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def promote_candidate(state_root: Path, candidate: dict, dry_run: bool = False) -> PromoteResult:
    """Promote a candidate to memories/active, with proper supersedes tracking
    and a consistent ledger / file ordering.

    CX-7 fix (consistency window): previously the order was
        write staging → append `promotion_committed` → rename.
    A crash between commit and rename left the ledger claiming the file
    existed when it didn't. Now we rename + fsync(parent) BEFORE writing
    the commit event, so the ledger only records what's already on disk.

    CX-8 fix (silent overwrite): previously a second promote of the same
    atomic_id silently overwrote the active file with no audit trail. Now
    we read the prior file's atomic_id and append it to the new record's
    `supersedes` list (preserving the chain), and emit a separate
    `promotion_superseded` event so the ledger reflects the displacement.
    """
    key = canonical_key(candidate)
    atomic_id = candidate["atomic_id"]

    # Reject path-traversal / filename-meta atomic_ids before they reach
    # the filesystem. atomic_id flows from LLM / user input and is used
    # directly as a filename, so any `..` / `/` / `\0` etc. would let an
    # attacker write outside `<state>/memories/active/`.
    if not isinstance(atomic_id, str) or not _VALID_ATOMIC_ID.match(atomic_id):
        raise InvalidAtomicIdError(
            f"atomic_id must match [A-Za-z0-9][A-Za-z0-9_-]{{0,127}}, got {atomic_id!r}"
        )

    if dry_run:
        return PromoteResult(created=False, reason=f"dry_run:{key}")

    staging_path = state_root / "memories" / "staging" / f"{atomic_id}.intent.md"
    active_path = state_root / "memories" / "active" / f"{atomic_id}.md"

    # Intent event first — records that we're about to write.
    intent_id = event_id("promote")
    append_event(
        state_root,
        {
            "event_id": intent_id,
            "event_type": "promotion_intent",
            "session_id": "codex-current",
            "payload": {"atomic_id": atomic_id, "canonical_key": key},
        },
    )

    # Maintain supersedes chain for repeated promotions of the same atomic_id.
    # Dedupe while preserving order — a chain that loops the same id (re-promote
    # of the same atomic_id 3+ times) used to grow N entries with the same
    # value. dict.fromkeys() collapses duplicates without breaking order.
    prior_supersedes, prior_atomic_id = _read_existing_supersedes(active_path)
    new_supersedes = list(candidate.get("supersedes") or [])
    if prior_atomic_id:
        merged = [*prior_supersedes, prior_atomic_id, *new_supersedes]
        new_supersedes = list(dict.fromkeys(merged))

    # We need both the intent_id and the commit_id in source_event_ids before
    # we hash content. Generate the commit_id here, but only emit the event
    # AFTER the rename succeeds.
    commit_id = event_id("promote")

    data = {
        "schema_version": "codex-memory-v1",
        "atomic_id": atomic_id,
        "type": candidate["type"],
        "domain": candidate["domain"],
        "scope": candidate["scope"],
        "condition": candidate.get("condition", ""),
        "rule_text": candidate["rule_text"],
        "applies_when": candidate["applies_when"],
        "does_not_apply_when": candidate["does_not_apply_when"],
        "confidence": candidate.get("confidence", "medium"),
        "status": "active",
        "source_event_ids": [intent_id, commit_id],
        "supersedes": new_supersedes,
        "created": time.strftime("%Y-%m-%d", time.gmtime()),
        "updated": time.strftime("%Y-%m-%d", time.gmtime()),
    }

    content_hash = write_memory_atomic(staging_path, data, candidate.get("body", ""))

    # Rename + fsync the active dir BEFORE emitting commit (CX-7).
    secure_mkdir(active_path.parent)
    staging_path.replace(active_path)
    _fsync_dir(active_path.parent)

    # Now record the commit — at this point the file is definitively on disk.
    append_event(
        state_root,
        {
            "event_id": commit_id,
            "event_type": "promotion_committed",
            "session_id": "codex-current",
            "payload": {
                "atomic_id": atomic_id,
                "canonical_key": key,
                "path": str(active_path),
                "content_sha256": content_hash,
                "supersedes": new_supersedes,
            },
        },
    )

    # If we displaced a prior version of the same atomic_id, log that too.
    if prior_atomic_id:
        append_event(
            state_root,
            {
                "event_type": "promotion_superseded",
                "session_id": "codex-current",
                "payload": {
                    "atomic_id": atomic_id,
                    "previous_supersedes": prior_supersedes,
                    "new_supersedes": new_supersedes,
                },
            },
        )

    try:
        build_active_index(state_root)
    except Exception:
        # Index build failure should not unwind a successful promote — the
        # file is on disk and the commit event is in the ledger; index can
        # be rebuilt on the next promote / doctor run.
        pass

    return PromoteResult(created=True, path=active_path)
