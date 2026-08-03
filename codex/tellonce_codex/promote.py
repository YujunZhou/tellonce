from __future__ import annotations

import os
import re
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .ledger import append_event, event_id
from .memory import canonical_key


_VALID_ATOMIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,127}$")


def _scalar(value) -> str:
    """Collapse a candidate field to a single frontmatter-safe line.

    Frontmatter scalars are single-line by contract, but LLM-generated
    candidate text routinely contains newlines. Unescaped, a newline lets a
    candidate smuggle extra frontmatter keys (forging `status:` /
    `applies_when:`) or a premature `---` fence that truncates the block —
    the written file then fails the index's validate/hash pass and is
    silently dropped from active_memories.json while the retrieve hook still
    injects the forged fields. Same treatment for list items (supersedes)."""
    return re.sub(r"[\r\n]+", " ", str(value if value is not None else "")).strip()


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


def promote_candidate(
    state_root: Path,
    candidate: dict,
    dry_run: bool = False,
    *,
    source_text: str | None = None,
) -> PromoteResult:
    """Queue a candidate through the shared semantic upsert core.

    SQLite is the only truth store; this compatibility entry point returns
    immediately after writing the shared inbox request.
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
    if not isinstance(source_text, str) or not source_text.strip():
        return PromoteResult(created=False, reason="needs_original_user_turn")

    shared_lib = Path(__file__).resolve().parents[1] / "shared_lib"
    if str(shared_lib) not in sys.path:
        sys.path.insert(0, str(shared_lib))
    import memory_upsert

    registration_path = state_root / "registration.json"
    default_project_root = (
        state_root.parent.parent
        if state_root.name == "tellonce" and state_root.parent.name == ".codex"
        else state_root.parent
    )
    try:
        project_root = Path(
            json.loads(registration_path.read_text(encoding="utf-8")).get(
                "project_root", default_project_root
            )
        )
    except Exception:
        project_root = default_project_root
    os.environ["PT_PROJECT_ROOT"] = str(project_root)
    os.environ["B5_PROJECT_ROOT"] = str(project_root)
    memory_upsert.path_config.get_project_root.cache_clear()
    memory_upsert.path_config.get_memory_dir.cache_clear()
    context = "Legacy Codex candidate:\n" + json.dumps(candidate, ensure_ascii=False)
    result = memory_upsert.enqueue(
        source_text=source_text,
        turn_key=f"codex-promote-{event_id('upsert')}",
        context=context,
        memory_dir=memory_upsert.path_config.get_memory_dir(),
        spawn_worker=True,
        force=True,
    )
    if result.get("status") not in {"queued", "pending"}:
        return PromoteResult(
            created=False,
            reason=f"enqueue_failed:{result.get('status', 'unknown')}",
        )
    append_event(
        state_root,
        {
            "event_type": "promotion_queued_shared_upsert",
            "session_id": "codex-current",
            "payload": {
                "legacy_atomic_id": atomic_id,
                "canonical_key": key,
                "turn_key": result["turn_key"],
                "request_file": result["request_file"],
            },
        },
    )
    return PromoteResult(
        created=True,
        path=Path(result["request_file"]),
        reason=f"queued:{result['turn_key']}",
    )
