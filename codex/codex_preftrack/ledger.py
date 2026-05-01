from __future__ import annotations

from dataclasses import dataclass
import copy
import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Iterator


class DuplicateEventError(ValueError):
    pass


@dataclass(frozen=True)
class RepairResult:
    repaired: bool
    corrupt_lines: int = 0


SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.I), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"[A-Za-z_][A-Za-z0-9_]*=(sk-|ghp_|[A-Za-z0-9/+]{32,})[A-Za-z0-9/+_=.\-]*"), "[REDACTED_SECRET_ASSIGNMENT]"),
]


def event_id(session_id: str = "codex-current") -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sid = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
    rand = hashlib.sha256(f"{time.time_ns()}-{os.getpid()}".encode("utf-8")).hexdigest()[:8]
    return f"{ts}-{sid}-{rand}"


def _redact_string(value: str) -> str:
    out = value
    for pattern, replacement in SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def sanitize(value):
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    return value


def _events_path(state_root: Path) -> Path:
    return state_root / "events.jsonl"


def read_events(state_root: Path) -> Iterator[dict]:
    path = _events_path(state_root)
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def append_event(state_root: Path, event: dict) -> str:
    state_root.mkdir(parents=True, exist_ok=True)
    path = _events_path(state_root)
    lock_path = state_root / "events.lock"
    event = sanitize(copy.deepcopy(event))
    event.setdefault("schema_version", "codex-pref-v1")
    event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    event.setdefault("event_id", event_id(event.get("session_id", "codex-current")))
    event.setdefault("payload", {})
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = {e.get("event_id") for e in read_events(state_root)}
        if event["event_id"] in existing:
            raise DuplicateEventError(event["event_id"])
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return event["event_id"]


def repair_tail(state_root: Path) -> RepairResult:
    path = _events_path(state_root)
    if not path.is_file():
        return RepairResult(False)
    data = path.read_bytes()
    if not data:
        return RepairResult(False)
    lines = data.splitlines(keepends=True)
    good = []
    corrupt = []
    for i, raw in enumerate(lines):
        text = raw.decode("utf-8", errors="replace")
        if not text.endswith("\n") and i == len(lines) - 1:
            corrupt.append(text)
            break
        try:
            json.loads(text)
            good.append(raw)
        except json.JSONDecodeError:
            corrupt.append(text)
    if not corrupt:
        return RepairResult(False)
    evidence = state_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "events_tail_quarantine.txt").write_text("".join(corrupt), encoding="utf-8")
    return RepairResult(True, len(corrupt))
