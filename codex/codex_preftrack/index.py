from __future__ import annotations

import json
from pathlib import Path

from .ledger import read_events
from .memory import canonical_key, parse_memory, validate_memory_data


def build_active_index(state_root: Path) -> dict:
    commits = {}
    for event in read_events(state_root):
        if event.get("event_type") == "promotion_committed":
            payload = event.get("payload", {})
            commits[payload.get("atomic_id")] = payload
    active = []
    active_dir = state_root / "memories" / "active"
    if active_dir.is_dir():
        for path in sorted(active_dir.glob("*.md")):
            try:
                parsed = parse_memory(path)
            except Exception:
                continue
            data = parsed.data
            commit = commits.get(data.get("atomic_id"))
            if data.get("status") != "active" or validate_memory_data(data) or not commit:
                continue
            if commit.get("content_sha256") != data.get("content_sha256"):
                continue
            active.append(
                {
                    "atomic_id": data["atomic_id"],
                    "canonical_key": canonical_key(data),
                    "path": str(path),
                    "type": data.get("type"),
                    "domain": data.get("domain"),
                    "scope": data.get("scope"),
                    "condition": data.get("condition"),
                    "rule_text": data.get("rule_text"),
                    "confidence": data.get("confidence"),
                }
            )
    out = {"active": active}
    index_dir = state_root / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "active_memories.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
