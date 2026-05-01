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


# SECRET_PATTERNS — applied via sanitize() to anything we serialize into events
# or write to disk. Order matters: SSH private key block is multi-line so it
# goes through re.sub with re.DOTALL. Other patterns are line-local.
#
# Coverage targets:
#   - OpenAI / Anthropic legacy + project keys (sk-, sk-ant-, sk-proj-)
#   - GitHub PAT (classic ghp_, fine-grained github_pat_, OAuth gho_/ghu_/ghs_/ghr_)
#   - Slack tokens (xoxb-, xoxp-, xoxa-, xoxs-, xoxr-)
#   - Stripe (sk_live_, rk_live_, pk_live_)
#   - Google API key (AIza...)
#   - HuggingFace (hf_...)
#   - AWS Access Key ID (AKIA...) and SK (40-char base64-ish, contextual)
#   - JWT (eyJ...eyJ...)
#   - Bearer / Authorization headers
#   - SSH / GCP private key PEM blocks
#   - Database URIs with embedded credentials
#   - Common assignment forms: password=, api_key=, secret=, token=
#
# Previous pattern `[VAR]=...{32+}` was too broad (caught long paths / hashes).
# Removed; replaced with explicit token-prefix alternations.
SECRET_PATTERNS = [
    # SSH / GCP private-key PEM block (multi-line; must run before line-local rules)
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
        "[REDACTED_PRIVATE_KEY_BLOCK]",
    ),
    # OpenAI / Anthropic / project keys
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"), "[REDACTED_API_KEY]"),
    # GitHub: classic + fine-grained + OAuth flow tokens
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_PAT]"),
    (re.compile(r"\bgh[ousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    # Slack
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    # Stripe
    (re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"), "[REDACTED_STRIPE_KEY]"),
    # Google API key
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "[REDACTED_GOOGLE_API_KEY]"),
    # HuggingFace
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[REDACTED_HF_TOKEN]"),
    # AWS Access Key ID (AKIA / ASIA / temporary creds)
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    # JWT (header.payload.sig — both header and payload start with eyJ)
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"), "[REDACTED_JWT]"),
    # Bearer / Authorization headers
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-+=/]{20,}", re.I), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\bAuthorization:\s*[A-Za-z]+\s+[A-Za-z0-9._\-+=/]{20,}", re.I), "Authorization: [REDACTED]"),
    # Database URIs with embedded credentials (postgres://, mysql://, mongodb://, mongodb+srv://, redis://)
    (
        re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp|amqps)://[^\s:@]+:[^\s@]*@[^\s\"'`<>]+", re.I),
        "[REDACTED_DB_URI]",
    ),
    # Common assignment forms (after the prefix-specific patterns above so we
    # don't double-redact). Catch password=, secret=, token=, api[_-]?key=,
    # access[_-]?key=, even short values, because these patterns mean intent.
    (
        re.compile(
            r"\b(?:password|passwd|pwd|secret|api[_\-]?key|access[_\-]?key|auth[_\-]?token|client[_\-]?secret|private[_\-]?key)\s*[:=]\s*[^\s,;'\"`)]+",
            re.IGNORECASE,
        ),
        "[REDACTED_SECRET_ASSIGNMENT]",
    ),
]


def event_id(session_id: str = "codex-current") -> str:
    """Generate a globally unique event id.

    Format: ``<UTC_TS>-<sid8>-<uuid32>``. Uses uuid4 (122 bits of randomness)
    instead of an 8-hex prng-of-pid+ns digest, so we don't hit a 32-bit
    birthday collision after ~65k events. Was ledger CX-9 in the publish review.
    """
    import uuid as _uuid

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sid = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
    rand = _uuid.uuid4().hex
    return f"{ts}-{sid}-{rand}"


def secure_write_text(path: Path, data: str, *, atomic: bool = False) -> None:
    """Write `data` to `path` and chmod 0o600. If atomic=True, write to .tmp
    then rename and fsync parent dir.

    All on-disk codex_preftrack artifacts go through this helper rather than
    Path.write_text, because the package writes user prompts / subprocess
    stdout / preference rule_text — anything an attacker on a shared host
    might want to read. POSIX umask defaults to 0022 → world-readable
    without an explicit chmod.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        with tmp.open("r+", encoding="utf-8") as f:
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
        # Persist the rename in the parent directory.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    else:
        path.write_text(data, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def secure_mkdir(path: Path) -> None:
    """mkdir -p `path` with mode 0o700 (user-only). Best-effort chmod on existing dirs."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


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
    secure_mkdir(state_root)
    path = _events_path(state_root)
    lock_path = state_root / "events.lock"
    event = sanitize(copy.deepcopy(event))
    event.setdefault("schema_version", "codex-pref-v1")
    event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    event.setdefault("event_id", event_id(event.get("session_id", "codex-current")))
    event.setdefault("payload", {})
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        # Tighten lockfile perms (best-effort).
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        # Tail-only dedup: only check the last 1000 events. event_id is now
        # uuid4-backed (122 bits) so collisions across full history are
        # negligible, and a tail window is enough to catch caller-side
        # explicit-id reuse without paying O(N) per append. (HX-2)
        existing = _recent_event_ids(state_root, limit=1000)
        if event["event_id"] in existing:
            raise DuplicateEventError(event["event_id"])
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return event["event_id"]


def _recent_event_ids(state_root: Path, limit: int = 1000) -> set:
    """Read the last `limit` lines from events.jsonl, return their event_id set.

    O(limit) instead of O(N). Approximates dup detection: if a caller passes
    an explicit event_id that's older than `limit` events ago, dedup misses
    it — acceptable because uuid4 makes accidental reuse near-impossible and
    the only legitimate reuse path is intra-promote intent/commit which is
    same-second.
    """
    path = _events_path(state_root)
    if not path.is_file():
        return set()
    try:
        with path.open(encoding="utf-8") as f:
            # Read the file once; for a 1000-line tail this is cheap.
            # For very large files, skip ahead by seeking to end - 256KB.
            try:
                size = path.stat().st_size
                if size > 256 * 1024:
                    f.seek(size - 256 * 1024)
                    f.readline()  # discard partial first line after seek
            except OSError:
                pass
            tail = []
            for line in f:
                tail.append(line)
                if len(tail) > limit:
                    tail.pop(0)
    except OSError:
        return set()
    out = set()
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = obj.get("event_id")
        if eid:
            out.add(eid)
    return out


def repair_tail(state_root: Path) -> RepairResult:
    """Quarantine corrupt lines AND rewrite events.jsonl to remove them.

    HX-5 fix: the original implementation only copied corrupt content to
    `evidence/events_tail_quarantine.txt` but never modified events.jsonl,
    so subsequent reads still skipped corrupt lines and the next repair
    ran into the same data forever. Now we atomically rewrite events.jsonl
    to contain only valid lines after quarantining the bad ones.
    """
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
            # Last line missing newline → check if it's a complete JSON anyway.
            try:
                json.loads(text)
                # Valid JSON, just missing the trailing newline. Keep but add \n.
                good.append((text + "\n").encode("utf-8"))
            except json.JSONDecodeError:
                corrupt.append(text)
            break
        try:
            json.loads(text)
            good.append(raw)
        except json.JSONDecodeError:
            corrupt.append(text)
    if not corrupt:
        return RepairResult(False)

    # Quarantine bad lines.
    evidence = state_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    quarantine_path = evidence / "events_tail_quarantine.txt"
    # Append (don't overwrite — preserve history of corruption events).
    with quarantine_path.open("a", encoding="utf-8") as f:
        f.write(f"\n# repair_tail at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write("".join(corrupt))
    try:
        os.chmod(quarantine_path, 0o600)
    except OSError:
        pass

    # Atomically rewrite events.jsonl to contain only good lines.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        for raw in good:
            f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass

    return RepairResult(True, len(corrupt))
