from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .index import build_active_index
from .ledger import append_event, event_id
from .memory import canonical_key, write_memory_atomic


@dataclass(frozen=True)
class PromoteResult:
    created: bool
    path: Path | None = None
    reason: str = ""


def promote_candidate(state_root: Path, candidate: dict, dry_run: bool = False) -> PromoteResult:
    key = canonical_key(candidate)
    if dry_run:
        return PromoteResult(created=False, reason=f"dry_run:{key}")

    atomic_id = candidate["atomic_id"]
    intent_id = event_id("promote")
    commit_id = event_id("promote")
    append_event(
        state_root,
        {
            "event_id": intent_id,
            "event_type": "promotion_intent",
            "session_id": "codex-current",
            "payload": {"atomic_id": atomic_id, "canonical_key": key},
        },
    )
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
        "supersedes": [],
        "created": time.strftime("%Y-%m-%d", time.gmtime()),
        "updated": time.strftime("%Y-%m-%d", time.gmtime()),
    }
    staging_path = state_root / "memories" / "staging" / f"{atomic_id}.{intent_id}.md"
    active_path = state_root / "memories" / "active" / f"{atomic_id}.md"
    content_hash = write_memory_atomic(staging_path, data, candidate.get("body", ""))
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
            },
        },
    )
    active_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.replace(active_path)
    build_active_index(state_root)
    return PromoteResult(created=True, path=active_path)
