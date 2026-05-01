from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path


REQUIRED = {"atomic_id", "type", "domain", "scope", "rule_text", "applies_when", "does_not_apply_when"}


@dataclass(frozen=True)
class ParsedMemory:
    path: Path
    data: dict
    body: str


def _normalize(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def canonical_key(data: dict) -> str:
    parts = [
        data.get("type", ""),
        data.get("domain", ""),
        data.get("scope", ""),
        data.get("applies_when", ""),
        data.get("rule_text", ""),
    ]
    normalized = "|".join(_normalize(p) for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_hash_for_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_balanced_quotes(value: str) -> str:
    """Strip a single matched pair of leading/trailing quotes (' or ").
    Handles values like `"foo"`, `'bar'`. Doesn't strip mismatched / nested.
    HX-7 fix: prior implementation used `.strip('"')` which removes any
    number of leading/trailing `"` chars and broke on values like `"a"b"`.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, object] = {}
    current_key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            item = _strip_balanced_quotes(line[4:].strip())
            data[current_key].append(item)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = _strip_balanced_quotes(value.strip())
            current_key = key
            if value == "":
                data[key] = []
            elif value == "[]":
                data[key] = []
            else:
                data[key] = value
    return data, body


def parse_memory(path: Path) -> ParsedMemory:
    text = path.read_text(encoding="utf-8")
    data, body = parse_frontmatter(text)
    return ParsedMemory(path=path, data=data, body=body)


def render_memory(data: dict, body: str) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)


def validate_memory_data(data: dict) -> list[str]:
    errors = []
    missing = sorted(REQUIRED - set(data))
    if missing:
        errors.append("missing:" + ",".join(missing))
    if data.get("status") == "active" and not data.get("source_event_ids"):
        errors.append("missing:source_event_ids")
    return errors


def write_memory_atomic(path: Path, data: dict, body: str) -> str:
    """Atomic write with parent-dir fsync + 0600 perms.

    HX-8 fix: previously fsync'd only the file, leaving the rename's
    directory entry in page cache; a crash between rename and dirent
    flush could revert the change. Now also fsyncs the parent dir.
    """
    from .ledger import secure_mkdir  # lazy: avoid circular at import

    secure_mkdir(path.parent)
    data = dict(data)
    data["canonical_key"] = canonical_key(data)
    text_without_hash = render_memory({k: v for k, v in data.items() if k != "content_sha256"}, body)
    data["content_sha256"] = content_hash_for_text(text_without_hash)
    text = render_memory(data, body)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    with tmp.open("r+", encoding="utf-8") as f:
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # Persist the rename in the parent directory.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return data["content_sha256"]
