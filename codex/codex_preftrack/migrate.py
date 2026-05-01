from __future__ import annotations

import json
from pathlib import Path

from .ledger import secure_mkdir, secure_write_text


def preview_migration(state_root: Path, source_paths: list[Path], write_report: bool = False) -> dict:
    items = []
    for path in source_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "applies_when:" in text and "does_not_apply_when:" in text:
            decision = "pending_review"
            reason = "preview_only"
        elif "rule_text:" in text:
            decision = "archive_legacy"
            reason = "missing applicability"
        else:
            decision = "quarantine"
            reason = "schema incomplete"
        items.append({"source_path": str(path), "decision": decision, "reason": reason})
    report = {"items": items}
    if write_report:
        evidence = state_root / "evidence"
        secure_mkdir(evidence)
        secure_write_text(evidence / "migration_preview.json", json.dumps(report, indent=2) + "\n")
    return report
