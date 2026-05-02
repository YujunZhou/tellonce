"""In-bundle BibTeX provenance verifier.

Round-7 codex review (P0 Critical fix, 2026-05-02):
The PostToolUse hook used to spawn `<project>/scripts/verify_bib_ledger.py`
as a subprocess via `sys.executable`. That ran USER-CONTROLLED Python
code on every .bib write — opening Codex in a hostile repo would
auto-execute its `scripts/verify_bib_ledger.py` with the user's
privileges. List-arg subprocess defends against shell injection but NOT
against running an attacker-supplied script.

Fix: PT now ships its own verification function in this module. Project
files are read as DATA only (the .bib file content + the ledger jsonl).
We never `exec` anything from the project tree.

The standalone CLI verifier `<paper>/scripts/verify_bib_ledger.py` keeps
working for users who want to run it manually — but the codex hook
imports verify_one() and check_drift() directly from this in-bundle
module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


# Per-call duplicate of the standalone script's verify_one logic. Keeping
# them in sync is intentional — both should agree on what counts as drift.
# The bundled copy is the one PT trusts; the standalone is for human use.


def _normalize_for_loose_match(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def verify_one(ledger_entry: dict, bib_text: str) -> dict:
    """Compare one ledger entry against the .bib text. Returns a dict with
    status in {ok, drift_whitespace, drift_modified, missing, skipped}.
    """
    raw = ledger_entry.get("bibtex_raw") or ""
    title = ledger_entry.get("matched_title") or ledger_entry.get("title_query") or "?"
    doi = ledger_entry.get("doi") or "?"
    if not raw:
        return {"status": "skipped", "reason": "ledger entry has no bibtex_raw", "title": title, "doi": doi}
    if raw.strip() in bib_text:
        return {"status": "ok", "title": title, "doi": doi}
    if _normalize_for_loose_match(raw) in _normalize_for_loose_match(bib_text):
        return {
            "status": "drift_whitespace",
            "reason": "entry exists but with whitespace/case differences",
            "title": title,
            "doi": doi,
        }
    m = re.match(r"\s*@[a-zA-Z]+\s*\{\s*([^,\s]+)", raw)
    if m:
        key = m.group(1)
        if re.search(r"@[a-zA-Z]+\s*\{\s*" + re.escape(key) + r"\b", bib_text):
            return {
                "status": "drift_modified",
                "reason": f"citation key {key!r} present but content differs from ledger",
                "title": title,
                "doi": doi,
                "key": key,
            }
    return {
        "status": "missing",
        "reason": "entry not found in .bib (verbatim or loose) — likely deleted or never appended",
        "title": title,
        "doi": doi,
    }


def _iter_ledger_entries(ledger_path: Path) -> Iterable[dict]:
    """Read a bib_sources.jsonl ledger. Yields dicts. Skips malformed lines."""
    if not ledger_path.is_file():
        return
    try:
        with ledger_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def check_drift(bib_path: Path, ledger_path: Path) -> tuple[list[dict], dict]:
    """Run drift detection for a single .bib + ledger pair.

    Returns (drift_records, summary) where:
      drift_records: list of {status, title, doi, reason, bib_path, ...}
                     for any entry that didn't match ok-verbatim
      summary:       {status -> count}

    Treats ledger-entries-for-other-.bib as filtered out (we only verify
    entries whose appended_to matches our bib_path).
    """
    summary = {"ok": 0, "drift_whitespace": 0, "drift_modified": 0, "missing": 0, "skipped": 0}
    drift_records: list[dict] = []
    if not bib_path.is_file():
        return drift_records, summary
    try:
        bib_text = bib_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return drift_records, summary

    bib_resolved = bib_path.resolve()
    for entry in _iter_ledger_entries(ledger_path):
        # Only verify entries that were appended to this bib (or no
        # appended_to recorded — defensive default = check anyway).
        appended = entry.get("appended_to")
        if appended:
            try:
                if Path(appended).resolve() != bib_resolved:
                    continue
            except OSError:
                continue
        result = verify_one(entry, bib_text)
        summary[result["status"]] = summary.get(result["status"], 0) + 1
        if result["status"] != "ok":
            drift_records.append({**result, "bib_path": str(bib_path)})
    return drift_records, summary


def has_blocking_drift(summary: dict, strict: bool = False) -> bool:
    """drift_modified + missing always block; drift_whitespace only in --strict."""
    if summary.get("drift_modified", 0) or summary.get("missing", 0):
        return True
    if strict and summary.get("drift_whitespace", 0):
        return True
    return False


# ----------------------------------------------------------------------------
# Orphan audit — Round-9 follow-up (2026-05-02)
#
# Drift detection only protects entries that are ALREADY in the ledger. The
# attack vector PT didn't cover until now: an agent (or hand edit) appends a
# new BibTeX entry to references.bib without going through resolve_bib.py,
# producing a ledger-less "orphan" entry. For a paper submission this is how
# phantom citations creep in (fabricated DOI, hallucinated author list, etc.)
# and earns desk reject.
#
# audit_orphans walks references.bib and reports every entry whose citation
# key is NOT in the ledger. The user can then either (a) re-resolve via
# resolve_bib.py to add a proper DOI-backed ledger entry, (b) add the key to
# a TRUSTED list (`bib_trusted_keys.txt`) for entries the user vouches for
# personally (e.g. their own published papers from Google Scholar). The
# trusted list is dumb-text, one key per line, # for comments — easy to audit.
# ----------------------------------------------------------------------------

_BIB_ENTRY_RE = re.compile(r"@[a-zA-Z]+\s*\{\s*([^,\s]+)", re.MULTILINE)


def list_bib_keys(bib_text: str) -> list[str]:
    """Extract every citation key from BibTeX text in source order. Duplicates
    are preserved (caller can dedupe if needed)."""
    return [m.group(1) for m in _BIB_ENTRY_RE.finditer(bib_text)]


def list_ledger_keys(ledger_path: Path) -> set[str]:
    """Pull citation keys out of every ledger entry's bibtex_raw."""
    keys: set[str] = set()
    for entry in _iter_ledger_entries(ledger_path):
        raw = entry.get("bibtex_raw") or ""
        m = _BIB_ENTRY_RE.search(raw)
        if m:
            keys.add(m.group(1))
    return keys


def load_trusted_keys(trusted_path: Path) -> set[str]:
    """Read a `bib_trusted_keys.txt` file. One key per line; blank lines and
    `#` comments ignored. Missing file == empty set (no trust)."""
    if not trusted_path.is_file():
        return set()
    keys: set[str] = set()
    try:
        for raw in trusted_path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                keys.add(line)
    except OSError:
        return set()
    return keys


def audit_orphans(bib_path: Path, ledger_path: Path, trusted_path: Path | None = None) -> dict:
    """Find BibTeX keys present in `.bib` but absent from both the ledger and
    the trusted list.

    Returns:
        {
          "total": int,                # total entries in .bib
          "verified": list[str],       # keys present in ledger (drift-checked)
          "trusted": list[str],        # keys in the trusted list
          "orphans": list[str],        # keys with NO provenance — phantom-citation risk
        }
    """
    if not bib_path.is_file():
        return {"total": 0, "verified": [], "trusted": [], "orphans": []}
    try:
        text = bib_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"total": 0, "verified": [], "trusted": [], "orphans": []}
    all_keys = list_bib_keys(text)
    ledger_keys = list_ledger_keys(ledger_path) if ledger_path else set()
    trusted_keys = load_trusted_keys(trusted_path) if trusted_path else set()
    seen: set[str] = set()
    verified: list[str] = []
    trusted: list[str] = []
    orphans: list[str] = []
    for k in all_keys:
        if k in seen:
            continue
        seen.add(k)
        if k in ledger_keys:
            verified.append(k)
        elif k in trusted_keys:
            trusted.append(k)
        else:
            orphans.append(k)
    return {
        "total": len(seen),
        "verified": verified,
        "trusted": trusted,
        "orphans": orphans,
    }
