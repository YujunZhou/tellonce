#!/usr/bin/env python3
"""SQLite-backed source of truth for tellonce memories.

Semantic decisions are supplied by an LLM plan. This module only performs
schema validation, optimistic concurrency checks, transactional updates, and
deterministic Markdown/JSON projection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager


SCHEMA_VERSION = 1
DB_FILENAME = ".tellonce.sqlite3"
ACTIVE_INDEX_FILENAME = ".tellonce-active.json"
VALID_OPERATIONS = {"NOOP", "UPDATE", "SUPERSEDE", "NEW", "NEEDS_USER"}
VALID_ATOMIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
VALID_EXTRA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
VALID_CONFIDENCE = {"high", "medium", "low"}

DOMAIN_ABBREVIATIONS = {
    "formatting": "fmt",
    "language": "lang",
    "workflow": "wf",
    "coding": "code",
    "tools": "tool",
    "experiment": "exp",
    "writing": "wrt",
    "communication": "comm",
    "other": "oth",
}
TYPE_ABBREVIATIONS = {
    "preference": "pref",
    "pitfall": "pit",
    "friction": "fric",
    "user": "usr",
    "project": "proj",
    "reference": "ref",
}
DOMAIN_HEADINGS = {
    "formatting": "Formatting",
    "language": "Language",
    "workflow": "Workflow",
    "coding": "Coding",
    "tools": "Tools",
    "experiment": "Experiment",
    "writing": "Writing",
    "communication": "Communication",
    "other": "Other",
}
RECORD_FIELDS = (
    "name",
    "description",
    "type",
    "domain",
    "scope",
    "condition",
    "confidence",
    "rule_text",
    "applies_when",
    "does_not_apply_when",
    "body",
)


class MemoryStoreError(RuntimeError):
    pass


class StaleSnapshotError(MemoryStoreError):
    pass


class InvalidPlanError(MemoryStoreError):
    pass


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_scalar(value) -> str:
    return re.sub(r"[\r\n]+", " ", str(value if value is not None else "")).strip()


def _strip_balanced_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def parse_frontmatter(text: str):
    """Parse the simple scalar/list YAML subset used by tellonce rule files."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    raw = text[4:end]
    body = text[end + 5 :]
    data = {}
    current_key = None
    for raw_line in raw.splitlines():
        if not raw_line.strip():
            continue
        stripped = raw_line.strip()
        if stripped.startswith("- ") and current_key:
            current = data.setdefault(current_key, [])
            if current == "":
                current = []
                data[current_key] = current
            if isinstance(current, list):
                current.append(_strip_balanced_quotes(stripped[2:].strip()))
            continue
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        value = _strip_balanced_quotes(value.strip())
        current_key = key
        if value == "[]":
            data[key] = []
        elif value == "":
            data[key] = ""
        else:
            data[key] = value
    return data, body


def _render_frontmatter(data: dict, body: str) -> str:
    order = (
        "schema_version",
        "atomic_id",
        "name",
        "description",
        "type",
        "domain",
        "scope",
        "condition",
        "confidence",
        "status",
        "revision",
        "rule_text",
        "applies_when",
        "does_not_apply_when",
        "supersedes",
        "superseded_by",
        "created",
        "updated",
        "content_sha256",
    )
    lines = ["---"]
    emitted = set()
    for key in order:
        if key not in data:
            continue
        emitted.add(key)
        value = data[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_safe_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_safe_scalar(value)}")
    for key in sorted(set(data) - emitted):
        value = data[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_safe_scalar(item)}" for item in value)
        elif isinstance(value, (str, int, float)):
            lines.append(f"{key}: {_safe_scalar(value)}")
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)


def _atomic_write(path: Path, content: str, retries: int = 6) -> None:
    """Write and replace in the target directory, retrying Windows share errors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        delay = 0.05
        for attempt in range(retries):
            try:
                os.replace(str(tmp), str(path))
                break
            except PermissionError:
                if attempt + 1 >= retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.8)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


class MemoryStore:
    def __init__(self, memory_dir, db_path=None, legacy_dirs=None):
        self.memory_dir = Path(memory_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve() if db_path else self.memory_dir / DB_FILENAME
        self.legacy_dirs = [
            Path(path).expanduser().resolve()
            for path in (legacy_dirs or [])
            if Path(path).expanduser().resolve() != self.memory_dir
        ]

    def _tighten_permissions(self) -> None:
        try:
            os.chmod(self.memory_dir, 0o700)
        except OSError:
            pass
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if not path.exists():
                continue
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def connect(self, timeout_seconds: float = 30.0):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._tighten_permissions()
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=max(0.0, timeout_seconds),
            isolation_level=None,
        )
        self._tighten_permissions()
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={int(max(0.0, timeout_seconds) * 1000)}")
            conn.execute("PRAGMA synchronous=FULL")
            return conn
        except Exception:
            conn.close()
            raise

    @contextmanager
    def connection(self, timeout_seconds: float = 30.0):
        conn = self.connect(timeout_seconds=timeout_seconds)
        try:
            yield conn
        finally:
            self._tighten_permissions()
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    turn_key TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    context_text TEXT NOT NULL DEFAULT '',
                    judge_cli TEXT NOT NULL DEFAULT '',
                    clarification_candidates_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    plan_json TEXT,
                    result_json TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rules (
                    atomic_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_revision INTEGER NOT NULL,
                    superseded_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rule_versions (
                    atomic_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    applies_when TEXT NOT NULL,
                    does_not_apply_when TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    extra_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (atomic_id, revision),
                    FOREIGN KEY (atomic_id) REFERENCES rules(atomic_id)
                );
                CREATE TABLE IF NOT EXISTS rule_relations (
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    turn_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (from_id, to_id, relation)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    txn_id TEXT PRIMARY KEY,
                    turn_key TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    base_generation INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    result_json TEXT,
                    committed_generation INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    committed_at TEXT,
                    projected_at TEXT
                );
                CREATE TABLE IF NOT EXISTS id_counters (
                    prefix TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('generation', '0')")
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(turns)").fetchall()
            }
            if "context_text" not in columns:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN context_text TEXT NOT NULL DEFAULT ''"
                )
            if "judge_cli" not in columns:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN judge_cli TEXT NOT NULL DEFAULT ''"
                )
            if "clarification_candidates_json" not in columns:
                conn.execute(
                    """
                    ALTER TABLE turns
                    ADD COLUMN clarification_candidates_json TEXT NOT NULL DEFAULT '[]'
                    """
                )
            if "lease_owner" not in columns:
                conn.execute("ALTER TABLE turns ADD COLUMN lease_owner TEXT")
            if "lease_expires_at" not in columns:
                conn.execute("ALTER TABLE turns ADD COLUMN lease_expires_at REAL")
            if "attempt_count" not in columns:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            transaction_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()
            }
            if "committed_generation" not in transaction_columns:
                conn.execute(
                    "ALTER TABLE transactions ADD COLUMN committed_generation INTEGER"
                )
            conn.execute(
                """
                UPDATE transactions
                SET committed_generation=CAST(
                    (SELECT value FROM meta WHERE key='generation') AS INTEGER
                )
                WHERE status='committed' AND committed_generation IS NULL
                """
            )
        if self._import_legacy_if_empty():
            self.project()

    def generation(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
            return int(row["value"]) if row else 0

    def _record_from_row(self, row) -> dict:
        record = {
            "atomic_id": row["atomic_id"],
            "revision": int(row["current_revision"]),
            "status": row["status"],
            "superseded_by": row["superseded_by"] or "",
            "created": row["created_at"],
            "updated": row["updated_at"],
            "type": row["type"],
            "domain": row["domain"],
            "scope": row["scope"],
            "name": row["name"],
            "description": row["description"],
            "condition": row["condition"],
            "confidence": row["confidence"],
            "rule_text": row["rule_text"],
            "applies_when": row["applies_when"],
            "does_not_apply_when": row["does_not_apply_when"],
            "body": row["body"],
            "source_text": row["source_text"],
            "content_hash": row["content_hash"],
        }
        try:
            record["extra"] = json.loads(row["extra_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            record["extra"] = {}
        return record

    def _current_records(self, conn, active_only=False):
        where = "WHERE r.status='active' AND r.superseded_by IS NULL" if active_only else ""
        rows = conn.execute(
            f"""
            SELECT r.*, v.name, v.description, v.condition, v.confidence,
                   v.rule_text, v.applies_when, v.does_not_apply_when,
                   v.body, v.source_text, v.extra_json, v.content_hash
            FROM rules r
            JOIN rule_versions v
              ON v.atomic_id=r.atomic_id AND v.revision=r.current_revision
            {where}
            ORDER BY r.domain, r.type, r.atomic_id
            """
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def snapshot(self):
        with self.connection() as conn:
            generation_row = conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
            generation = int(generation_row["value"]) if generation_row else 0
            return generation, self._current_records(conn, active_only=True)

    def all_records(self):
        with self.connection() as conn:
            return self._current_records(conn, active_only=False)

    def ensure_turn(
        self,
        turn_key: str,
        source_text: str,
        context_text: str = "",
        judge_cli: str = "",
        clarification_candidates=None,
    ):
        source_hash = _sha256(source_text)
        now = _utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM turns WHERE turn_key=?", (turn_key,)).fetchone()
            if row:
                if row["source_hash"] != source_hash:
                    conn.execute("ROLLBACK")
                    raise MemoryStoreError("turn_key already exists with different source text")
                conn.execute("COMMIT")
                result = json.loads(row["result_json"]) if row["result_json"] else None
                return {"status": row["status"], "result": result}
            conn.execute(
                """
                INSERT INTO turns(
                    turn_key, source_hash, source_text, context_text, judge_cli,
                    clarification_candidates_json,
                    status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    turn_key,
                    source_hash,
                    source_text,
                    context_text,
                    _safe_scalar(judge_cli).lower(),
                    _json(
                        list(
                            dict.fromkeys(
                                str(item).strip()
                                for item in (clarification_candidates or [])
                                if str(item).strip()
                            )
                        )
                    ),
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
        return {"status": "pending", "result": None}

    def get_turn(self, turn_key: str):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM turns WHERE turn_key=?", (turn_key,)).fetchone()
            return dict(row) if row else None

    def claim_turn(self, turn_key: str, lease_seconds: int = 900):
        """Claim a pending turn for one worker, reclaiming stale workers."""
        now_epoch = time.time()
        owner = f"worker-{os.getpid()}-{uuid.uuid4().hex}"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, lease_expires_at FROM turns WHERE turn_key=?",
                (turn_key,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            status = row["status"]
            if status in {
                "committed", "projected", "noop", "needs_user", "clarified", "dismissed"
            }:
                conn.execute("COMMIT")
                return None
            lease_expires_at = row["lease_expires_at"]
            if status == "resolving" and lease_expires_at and float(lease_expires_at) > now_epoch:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE turns
                SET status='resolving', lease_owner=?, lease_expires_at=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE turn_key=?
                """,
                (owner, now_epoch + max(30, lease_seconds), _utc_now(), turn_key),
            )
            conn.execute("COMMIT")
            return owner

    def pending_turn_keys(self, limit: int = 20):
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT turn_key FROM turns
                WHERE status IN ('pending', 'resolving')
                ORDER BY created_at
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [row["turn_key"] for row in rows]

    def needs_user_turns(self, limit: int = 20, exclude_turn_key: str = ""):
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT turn_key, source_text, result_json, created_at
                FROM turns
                WHERE status='needs_user' AND turn_key != ?
                ORDER BY created_at
                LIMIT ?
                """,
                (exclude_turn_key, max(1, int(limit))),
            ).fetchall()
        result = []
        for row in rows:
            parsed = {}
            try:
                parsed = json.loads(row["result_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                pass
            result.append(
                {
                    "turn_key": row["turn_key"],
                    "source_text": str(row["source_text"] or "")[:500],
                    "reason": _safe_scalar(parsed.get("reason", "")),
                    "created_at": row["created_at"],
                }
            )
        return result

    def mark_clarifications_resolved(
        self,
        turn_keys: list[str],
        resolved_by: str,
    ) -> list[str]:
        unique = list(dict.fromkeys(str(item).strip() for item in turn_keys if str(item).strip()))
        if not unique:
            return []
        resolved = []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for turn_key in unique:
                row = conn.execute(
                    "SELECT result_json FROM turns WHERE turn_key=? AND status='needs_user'",
                    (turn_key,),
                ).fetchone()
                if not row:
                    continue
                try:
                    result = json.loads(row["result_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    result = {}
                result["status"] = "clarified"
                result["resolved_by"] = resolved_by
                conn.execute(
                    """
                    UPDATE turns
                    SET status='clarified', result_json=?, updated_at=?
                    WHERE turn_key=? AND status='needs_user'
                    """,
                    (_json(result), _utc_now(), turn_key),
                )
                resolved.append(turn_key)
            conn.execute("COMMIT")
        return resolved

    def dismiss_clarification(self, turn_key: str) -> dict:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT result_json FROM turns WHERE turn_key=? AND status='needs_user'",
                (turn_key,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise MemoryStoreError(
                    f"turn is not an unresolved clarification: {turn_key}"
                )
            try:
                result = json.loads(row["result_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                result = {}
            result["status"] = "dismissed"
            result["turn_key"] = turn_key
            conn.execute(
                """
                UPDATE turns
                SET status='dismissed', result_json=?, updated_at=?
                WHERE turn_key=? AND status='needs_user'
                """,
                (_json(result), _utc_now(), turn_key),
            )
            conn.execute("COMMIT")
        return result

    def retry_delay_seconds(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(attempt_count), 0) AS attempts
                FROM turns
                WHERE status IN ('pending', 'resolving')
                """
            ).fetchone()
        attempts = int(row["attempts"]) if row else 0
        return min(900, 15 * (2 ** min(attempts, 6)))

    def mark_turn_error(self, turn_key: str, error: str, lease_owner: str = "") -> None:
        with self.connection() as conn:
            if lease_owner:
                conn.execute(
                    """
                    UPDATE turns
                    SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                        last_error=?, updated_at=?
                    WHERE turn_key=? AND status='resolving' AND lease_owner=?
                    """,
                    (_safe_scalar(error)[:1000], _utc_now(), turn_key, lease_owner),
                )
            else:
                conn.execute(
                    """
                    UPDATE turns
                    SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                        last_error=?, updated_at=?
                    WHERE turn_key=? AND status NOT IN (
                        'committed','projected','noop','needs_user','clarified','dismissed'
                    )
                    """,
                    (_safe_scalar(error)[:1000], _utc_now(), turn_key),
                )

    def mark_needs_user(self, turn_key: str, plan: dict, lease_owner: str = "") -> dict:
        result = {
            "status": "needs_user",
            "turn_key": turn_key,
            "reason": _safe_scalar(plan.get("reason", "semantic decision needs user input")),
            "plan": plan,
        }
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sql = """
                UPDATE turns
                SET status='needs_user', lease_owner=NULL, lease_expires_at=NULL,
                    plan_json=?, result_json=?, updated_at=?
                WHERE turn_key=?
            """
            params = [_json(plan), _json(result), _utc_now(), turn_key]
            if lease_owner:
                sql += " AND status='resolving' AND lease_owner=?"
                params.append(lease_owner)
            updated = conn.execute(sql, tuple(params))
            if updated.rowcount != 1:
                conn.execute("ROLLBACK")
                raise StaleSnapshotError("worker lease no longer owns the turn")
            conn.execute("COMMIT")
        return result

    def _active_record(self, conn, atomic_id: str):
        row = conn.execute(
            """
            SELECT r.*, v.name, v.description, v.condition, v.confidence,
                   v.rule_text, v.applies_when, v.does_not_apply_when,
                   v.body, v.source_text, v.extra_json, v.content_hash
            FROM rules r
            JOIN rule_versions v
              ON v.atomic_id=r.atomic_id AND v.revision=r.current_revision
            WHERE r.atomic_id=? AND r.status='active' AND r.superseded_by IS NULL
            """,
            (atomic_id,),
        ).fetchone()
        return self._record_from_row(row) if row else None

    def _allocate_atomic_id(self, conn, domain: str, rule_type: str) -> str:
        domain_key = DOMAIN_ABBREVIATIONS.get(domain, "oth")
        type_key = TYPE_ABBREVIATIONS.get(rule_type, "ref")
        prefix = f"{domain_key}-{type_key}"
        row = conn.execute("SELECT next_value FROM id_counters WHERE prefix=?", (prefix,)).fetchone()
        if row:
            value = int(row["next_value"])
        else:
            maximum = 0
            pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
            for existing in conn.execute("SELECT atomic_id FROM rules").fetchall():
                match = pattern.match(existing["atomic_id"])
                if match:
                    maximum = max(maximum, int(match.group(1)))
            value = maximum + 1
        while True:
            atomic_id = f"{prefix}-{value:03d}"
            exists = conn.execute("SELECT 1 FROM rules WHERE atomic_id=?", (atomic_id,)).fetchone()
            if not exists:
                break
            value += 1
        conn.execute(
            """
            INSERT INTO id_counters(prefix, next_value) VALUES(?, ?)
            ON CONFLICT(prefix) DO UPDATE SET next_value=excluded.next_value
            """,
            (prefix, value + 1),
        )
        return atomic_id

    def _normalize_record(self, record: dict, base=None) -> dict:
        if not isinstance(record, dict):
            raise InvalidPlanError("record must be an object")
        base = base or {}
        out = {}
        for field in RECORD_FIELDS:
            incoming = record[field] if field in record else base.get(field, "")
            if incoming is None:
                incoming = ""
            out[field] = _safe_scalar(incoming) if field != "body" else str(incoming or "").strip()
        out["type"] = out["type"] or "preference"
        out["domain"] = out["domain"] or "other"
        out["scope"] = out["scope"] or "global"
        out["confidence"] = out["confidence"] or "medium"
        out["rule_text"] = out["rule_text"] or out["description"]
        out["description"] = out["description"] or out["rule_text"]
        out["name"] = out["name"] or re.sub(r"[^A-Za-z0-9_-]+", "-", out["description"].lower())[:60].strip("-")
        out["applies_when"] = out["applies_when"] or out["condition"]
        out["does_not_apply_when"] = out["does_not_apply_when"] or "(none)"
        if not out["rule_text"]:
            raise InvalidPlanError("record.rule_text is required")
        if out["domain"] not in DOMAIN_ABBREVIATIONS:
            raise InvalidPlanError(f"unsupported record.domain: {out['domain']}")
        if out["type"] not in TYPE_ABBREVIATIONS:
            raise InvalidPlanError(f"unsupported record.type: {out['type']}")
        if out["confidence"] not in VALID_CONFIDENCE:
            raise InvalidPlanError(f"unsupported record.confidence: {out['confidence']}")
        if out["scope"] != "global":
            project_scope = out["scope"][len("project:"):].strip() if out["scope"].startswith("project:") else ""
            if not project_scope:
                raise InvalidPlanError(
                    "record.scope must be 'global' or 'project:<non-empty-name>'"
                )
        reserved = set(RECORD_FIELDS) | {
            "schema_version",
            "atomic_id",
            "status",
            "revision",
            "supersedes",
            "superseded_by",
            "created",
            "updated",
            "content_sha256",
            "content_hash",
            "priority_tier",
        }
        extra = {}
        if isinstance(base.get("extra"), dict):
            extra.update(
                {
                    key: value
                    for key, value in base["extra"].items()
                    if isinstance(key, str)
                    and VALID_EXTRA_KEY.fullmatch(key)
                    and key not in reserved
                }
            )
        if isinstance(record.get("extra"), dict):
            extra.update(
                {
                    key: value
                    for key, value in record["extra"].items()
                    if isinstance(key, str)
                    and VALID_EXTRA_KEY.fullmatch(key)
                    and key not in reserved
                }
            )
        out["extra"] = extra
        return out

    def _insert_rule(self, conn, atomic_id: str, record: dict, source_text: str) -> None:
        now = _today()
        conn.execute(
            """
            INSERT INTO rules(
                atomic_id, type, domain, scope, status, current_revision,
                superseded_by, created_at, updated_at
            ) VALUES(?, ?, ?, ?, 'active', 1, NULL, ?, ?)
            """,
            (atomic_id, record["type"], record["domain"], record["scope"], now, now),
        )
        self._insert_version(conn, atomic_id, 1, record, source_text, now)

    def _insert_version(
        self,
        conn,
        atomic_id: str,
        revision: int,
        record: dict,
        source_text: str,
        created_at: str,
    ) -> None:
        material = {field: record.get(field, "") for field in RECORD_FIELDS}
        material["extra"] = record.get("extra", {})
        content_hash = _sha256(_json(material))
        conn.execute(
            """
            INSERT INTO rule_versions(
                atomic_id, revision, name, description, condition, confidence,
                rule_text, applies_when, does_not_apply_when, body, source_text,
                extra_json, content_hash, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atomic_id,
                revision,
                record["name"],
                record["description"],
                record["condition"],
                record["confidence"],
                record["rule_text"],
                record["applies_when"],
                record["does_not_apply_when"],
                record["body"],
                source_text,
                _json(record.get("extra", {})),
                content_hash,
                created_at,
            ),
        )

    def _retire_rule(self, conn, old_id: str, new_id: str, relation: str, turn_key: str) -> None:
        if old_id == new_id:
            raise InvalidPlanError("a rule cannot supersede itself")
        updated = conn.execute(
            """
            UPDATE rules
            SET status='superseded', superseded_by=?, updated_at=?
            WHERE atomic_id=? AND status='active' AND superseded_by IS NULL
            """,
            (new_id, _today(), old_id),
        )
        if updated.rowcount != 1:
            raise InvalidPlanError(f"target rule is not active: {old_id}")
        conn.execute(
            """
            INSERT OR IGNORE INTO rule_relations(from_id, to_id, relation, turn_key, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (old_id, new_id, relation, turn_key, _utc_now()),
        )

    def _validate_plan(self, conn, plan: dict):
        mutations = plan.get("mutations")
        if not isinstance(mutations, list):
            raise InvalidPlanError("plan.mutations must be a list")
        claimed_targets = set()
        normalized = []
        for index, mutation in enumerate(mutations):
            if not isinstance(mutation, dict):
                raise InvalidPlanError(f"mutation {index} must be an object")
            operation = str(mutation.get("operation", "")).upper()
            if operation not in VALID_OPERATIONS:
                raise InvalidPlanError(f"unsupported operation: {operation}")
            target_ids = mutation.get("target_ids") or []
            if not isinstance(target_ids, list) or not all(isinstance(item, str) for item in target_ids):
                raise InvalidPlanError(f"mutation {index}.target_ids must be string list")
            if len(target_ids) != len(set(target_ids)):
                raise InvalidPlanError(f"mutation {index} repeats a target")
            overlap = claimed_targets.intersection(target_ids)
            if overlap:
                raise InvalidPlanError(f"target appears in multiple mutations: {sorted(overlap)}")
            claimed_targets.update(target_ids)
            if operation in {"UPDATE", "SUPERSEDE"} and not target_ids:
                raise InvalidPlanError(f"{operation} requires target_ids")
            if operation == "NOOP" and not target_ids:
                raise InvalidPlanError("NOOP must identify the active rule that entails the turn")
            if operation in {"NEW", "NOOP", "NEEDS_USER"} and target_ids and operation == "NEW":
                raise InvalidPlanError("NEW cannot target an existing rule")
            for atomic_id in target_ids:
                if not VALID_ATOMIC_ID.match(atomic_id):
                    raise InvalidPlanError(f"invalid target atomic_id: {atomic_id}")
                if operation in {"NOOP", "UPDATE", "SUPERSEDE"} and not self._active_record(conn, atomic_id):
                    raise InvalidPlanError(f"target rule is not active: {atomic_id}")
            record = mutation.get("record") or {}
            if operation in {"NEW", "UPDATE", "SUPERSEDE"} and not isinstance(record, dict):
                raise InvalidPlanError(f"{operation} requires a record object")
            normalized.append(
                {
                    "operation": operation,
                    "target_ids": target_ids,
                    "record": record,
                    "reason": _safe_scalar(mutation.get("reason", "")),
                }
            )
        return normalized

    def commit_plan(
        self,
        turn_key: str,
        source_text: str,
        plan: dict,
        expected_generation: int,
        lease_owner: str = "",
    ) -> dict:
        now = _utc_now()
        txn_id = f"txn-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:12]}"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            generation_row = conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
            generation = int(generation_row["value"]) if generation_row else 0
            if generation != expected_generation:
                conn.execute("ROLLBACK")
                raise StaleSnapshotError(
                    f"memory changed while judging: expected generation {expected_generation}, got {generation}"
                )
            turn = conn.execute("SELECT status, result_json FROM turns WHERE turn_key=?", (turn_key,)).fetchone()
            if not turn:
                conn.execute("ROLLBACK")
                raise MemoryStoreError("turn must be registered before commit")
            if turn["status"] in {"committed", "projected", "noop", "needs_user"}:
                conn.execute("COMMIT")
                return json.loads(turn["result_json"]) if turn["result_json"] else {
                    "status": turn["status"],
                    "turn_key": turn_key,
                }
            if lease_owner:
                ownership = conn.execute(
                    """
                    SELECT 1 FROM turns
                    WHERE turn_key=? AND status='resolving' AND lease_owner=?
                      AND lease_expires_at>?
                    """,
                    (turn_key, lease_owner, time.time()),
                ).fetchone()
                if not ownership:
                    conn.execute("ROLLBACK")
                    raise StaleSnapshotError("worker lease no longer owns the turn")
            normalized = self._validate_plan(conn, plan)
            if any(item["operation"] == "NEEDS_USER" for item in normalized):
                conn.execute("ROLLBACK")
                return self.mark_needs_user(turn_key, plan, lease_owner=lease_owner)

            conn.execute(
                """
                INSERT INTO transactions(
                    txn_id, turn_key, status, base_generation, plan_json, created_at
                ) VALUES(?, ?, 'applying', ?, ?, ?)
                """,
                (txn_id, turn_key, generation, _json(plan), now),
            )
            results = []
            changed = False
            for mutation in normalized:
                operation = mutation["operation"]
                target_ids = mutation["target_ids"]
                if operation == "NOOP":
                    results.append(
                        {
                            "operation": operation,
                            "atomic_id": target_ids[0] if target_ids else "",
                            "reason": mutation["reason"],
                        }
                    )
                    continue
                if operation == "NEW":
                    record = self._normalize_record(mutation["record"])
                    atomic_id = self._allocate_atomic_id(conn, record["domain"], record["type"])
                    self._insert_rule(conn, atomic_id, record, source_text)
                    results.append({"operation": operation, "atomic_id": atomic_id})
                    changed = True
                    continue
                if operation == "UPDATE":
                    primary_id = target_ids[0]
                    primary = self._active_record(conn, primary_id)
                    record = self._normalize_record(mutation["record"], primary)
                    revision = int(primary["revision"]) + 1
                    self._insert_version(conn, primary_id, revision, record, source_text, _utc_now())
                    conn.execute(
                        """
                        UPDATE rules
                        SET type=?, domain=?, scope=?, current_revision=?, updated_at=?
                        WHERE atomic_id=?
                        """,
                        (
                            record["type"],
                            record["domain"],
                            record["scope"],
                            revision,
                            _today(),
                            primary_id,
                        ),
                    )
                    for old_id in target_ids[1:]:
                        self._retire_rule(conn, old_id, primary_id, "consolidated_into", turn_key)
                    results.append(
                        {
                            "operation": operation,
                            "atomic_id": primary_id,
                            "revision": revision,
                            "consolidated_ids": target_ids[1:],
                        }
                    )
                    changed = True
                    continue
                if operation == "SUPERSEDE":
                    record = self._normalize_record(mutation["record"])
                    new_id = self._allocate_atomic_id(conn, record["domain"], record["type"])
                    self._insert_rule(conn, new_id, record, source_text)
                    for old_id in target_ids:
                        self._retire_rule(conn, old_id, new_id, "superseded_by", turn_key)
                    results.append(
                        {
                            "operation": operation,
                            "atomic_id": new_id,
                            "superseded_ids": target_ids,
                        }
                    )
                    changed = True

            new_generation = generation + 1 if changed else generation
            if changed:
                conn.execute(
                    "UPDATE meta SET value=? WHERE key='generation'",
                    (str(new_generation),),
                )
            status = "committed" if changed else "noop"
            result = {
                "status": status,
                "turn_key": turn_key,
                "txn_id": txn_id,
                "generation": new_generation,
                "mutations": results,
            }
            conn.execute(
                """
                UPDATE transactions
                SET status='committed', result_json=?, committed_at=?,
                    committed_generation=?
                WHERE txn_id=?
                """,
                (_json(result), _utc_now(), new_generation, txn_id),
            )
            conn.execute(
                """
                UPDATE turns
                SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    plan_json=?, result_json=?, last_error=NULL, updated_at=?
                WHERE turn_key=?
                """,
                (status, _json(plan), _json(result), _utc_now(), turn_key),
            )
            conn.execute("COMMIT")
        return result

    def _relations_for_projection(self, conn):
        supersedes = {}
        for row in conn.execute(
            "SELECT from_id, to_id FROM rule_relations WHERE relation IN ('superseded_by','consolidated_into')"
        ).fetchall():
            supersedes.setdefault(row["to_id"], []).append(row["from_id"])
        for value in supersedes.values():
            value.sort()
        return supersedes

    def project(self) -> dict:
        """Rebuild all Markdown/JSON projections from committed SQLite state."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                generation_row = conn.execute(
                    "SELECT value FROM meta WHERE key='generation'"
                ).fetchone()
                generation = int(generation_row["value"]) if generation_row else 0
                records = self._current_records(conn, active_only=False)
                supersedes = self._relations_for_projection(conn)
                active = [
                    record
                    for record in records
                    if record["status"] == "active" and not record["superseded_by"]
                ]

                written = []
                for record in records:
                    frontmatter = {
                        "schema_version": "tellonce-memory-v2",
                        "atomic_id": record["atomic_id"],
                        "name": record["name"],
                        "description": record["description"],
                        "type": record["type"],
                        "domain": record["domain"],
                        "scope": record["scope"],
                        "condition": record["condition"],
                        "confidence": record["confidence"],
                        "status": record["status"],
                        "revision": record["revision"],
                        "rule_text": record["rule_text"],
                        "applies_when": record["applies_when"],
                        "does_not_apply_when": record["does_not_apply_when"],
                        "supersedes": supersedes.get(record["atomic_id"], []),
                        "superseded_by": record["superseded_by"],
                        "created": record["created"],
                        "updated": record["updated"],
                    }
                    frontmatter.update(
                        {
                            key: value
                            for key, value in (record.get("extra") or {}).items()
                            if isinstance(key, str)
                            and VALID_EXTRA_KEY.fullmatch(key)
                            and key not in set(RECORD_FIELDS)
                            and key not in frontmatter
                            and key not in {"content_hash", "content_sha256"}
                            and key != "priority_tier"
                        }
                    )
                    without_hash = _render_frontmatter(frontmatter, record["body"])
                    frontmatter["content_sha256"] = _sha256(without_hash)
                    content = _render_frontmatter(frontmatter, record["body"])
                    target = self.memory_dir / f"{record['atomic_id']}.md"
                    _atomic_write(target, content)
                    written.append(str(target))

                grouped = {}
                for record in active:
                    grouped.setdefault(record["domain"], []).append(record)
                index_lines = ["# Memory", ""]
                ordered_domains = list(DOMAIN_ABBREVIATIONS)
                for domain in ordered_domains:
                    items = sorted(
                        grouped.get(domain, []),
                        key=lambda item: (item["type"], item["atomic_id"]),
                    )
                    if not items:
                        continue
                    index_lines.extend(
                        [f"## {DOMAIN_HEADINGS.get(domain, domain.title())}", ""]
                    )
                    for item in items:
                        description = _safe_scalar(
                            item["description"] or item["rule_text"]
                        )[:150]
                        index_lines.append(
                            f"- [{item['atomic_id']}]({item['atomic_id']}.md) — {description}"
                        )
                    index_lines.append("")
                _atomic_write(
                    self.memory_dir / "MEMORY.md",
                    "\n".join(index_lines).rstrip() + "\n",
                )

                active_index = {
                    "schema_version": SCHEMA_VERSION,
                    "generation": generation,
                    "active": [
                        {
                            "atomic_id": item["atomic_id"],
                            "revision": item["revision"],
                            "type": item["type"],
                            "domain": item["domain"],
                            "scope": item["scope"],
                            "description": item["description"],
                            "rule_text": item["rule_text"],
                            "condition": item["condition"],
                            "confidence": item["confidence"],
                            "applies_when": item["applies_when"],
                            "does_not_apply_when": item["does_not_apply_when"],
                            "content_hash": item["content_hash"],
                        }
                        for item in active
                    ],
                }
                _atomic_write(
                    self.memory_dir / ACTIVE_INDEX_FILENAME,
                    json.dumps(active_index, ensure_ascii=False, indent=2) + "\n",
                )

                now = _utc_now()
                projected_turns = [
                    row["turn_key"]
                    for row in conn.execute(
                        """
                        SELECT turn_key FROM transactions
                        WHERE status='committed'
                          AND committed_generation IS NOT NULL
                          AND committed_generation<=?
                        """,
                        (generation,),
                    ).fetchall()
                ]
                conn.execute(
                    """
                    UPDATE transactions
                    SET status='projected', projected_at=?
                    WHERE status='committed'
                      AND committed_generation IS NOT NULL
                      AND committed_generation<=?
                    """,
                    (now, generation),
                )
                for turn_key in projected_turns:
                    conn.execute(
                        """
                        UPDATE turns
                        SET status='projected', updated_at=?
                        WHERE turn_key=? AND status='committed'
                        """,
                        (now, turn_key),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {
            "status": "projected",
            "generation": active_index["generation"],
            "active_count": len(active),
            "files_written": written,
        }

    def projection_pending(self) -> bool:
        with self.connection() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM transactions WHERE status='committed' LIMIT 1"
                ).fetchone()
            )

    def recover(self) -> dict:
        """SQLite commits are atomic; recovery only needs to rebuild projections."""
        self.initialize()
        return self.project()

    def _legacy_record(self, data: dict, body: str) -> dict:
        known = set(RECORD_FIELDS) | {
            "schema_version",
            "atomic_id",
            "status",
            "revision",
            "supersedes",
            "superseded_by",
            "created",
            "updated",
            "content_sha256",
            "canonical_key",
            "source_event_ids",
            "priority_tier",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        legacy_type = str(data.get("type", "preference")).strip()
        legacy_domain = str(data.get("domain", "other")).strip()
        legacy_confidence = str(data.get("confidence", "medium")).strip()
        legacy_scope = str(data.get("scope", "global")).strip()
        if legacy_type not in TYPE_ABBREVIATIONS:
            legacy_type = "preference"
        if legacy_domain not in DOMAIN_ABBREVIATIONS:
            legacy_domain = "other"
        if legacy_confidence not in VALID_CONFIDENCE:
            legacy_confidence = "medium"
        if legacy_scope == "project":
            project_name = (
                self.memory_dir.parent.parent.name
                if self.memory_dir.parent.name == ".tellonce"
                else "legacy"
            )
            legacy_scope = f"project:{project_name}"
        elif legacy_scope != "global" and not (
            legacy_scope.startswith("project:")
            and legacy_scope[len("project:"):].strip()
        ):
            legacy_scope = "global"
        record = {
            "name": data.get("name", ""),
            "description": data.get("description") or data.get("rule_text", ""),
            "type": legacy_type,
            "domain": legacy_domain,
            "scope": legacy_scope,
            "condition": data.get("condition", ""),
            "confidence": legacy_confidence,
            "rule_text": data.get("rule_text") or data.get("description", ""),
            "applies_when": data.get("applies_when") or data.get("condition", ""),
            "does_not_apply_when": data.get("does_not_apply_when", "(none)"),
            "body": body.strip(),
            "extra": extra,
        }
        return self._normalize_record(record)

    def _import_legacy_if_empty(self) -> bool:
        with self.connection() as conn:
            if conn.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
                return False
        candidates = {}
        source_dirs = [self.memory_dir, *self.legacy_dirs]
        for source_rank, source_dir in enumerate(source_dirs):
            if not source_dir.is_dir():
                continue
            for path in sorted(source_dir.glob("*.md")):
                if path.name == "MEMORY.md" or path.name.startswith("_archived_"):
                    continue
                try:
                    data, body = parse_frontmatter(
                        path.read_text(encoding="utf-8-sig", errors="replace")
                    )
                except Exception:
                    continue
                atomic_id = str(data.get("atomic_id", "")).strip()
                if not VALID_ATOMIC_ID.match(atomic_id):
                    continue
                revision_raw = data.get("revision", 0)
                try:
                    revision = int(revision_raw)
                except (TypeError, ValueError):
                    revision = 0
                priority = (
                    revision,
                    1 if path.name == f"{atomic_id}.md" else 0,
                    -source_rank,
                )
                fingerprint = _sha256(_json(data) + "\n" + body)
                candidate_key = atomic_id
                prior = candidates.get(candidate_key)
                if prior is not None and prior[1] != fingerprint:
                    candidate_key = f"{atomic_id}#{fingerprint}"
                    prior = candidates.get(candidate_key)
                if prior is None or priority > prior[0]:
                    candidates[candidate_key] = (
                        priority,
                        fingerprint,
                        atomic_id,
                        data,
                        body,
                        str(path),
                    )
        if not candidates:
            return False
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
                conn.execute("ROLLBACK")
                return False
            now = _utc_now()
            pending_relations = []
            for _candidate_key, (
                _priority,
                _fingerprint,
                legacy_atomic_id,
                data,
                body,
                source_path,
            ) in sorted(candidates.items()):
                try:
                    record = self._legacy_record(data, body)
                except InvalidPlanError:
                    continue
                atomic_id = legacy_atomic_id
                if conn.execute(
                    "SELECT 1 FROM rules WHERE atomic_id=?",
                    (atomic_id,),
                ).fetchone():
                    record["extra"]["legacy_atomic_id"] = legacy_atomic_id
                    record["extra"]["legacy_source_path"] = source_path
                    atomic_id = self._allocate_atomic_id(
                        conn,
                        record["domain"],
                        record["type"],
                    )
                created = _safe_scalar(data.get("created", "")) or _today()
                updated = _safe_scalar(data.get("updated", "")) or created
                superseded_by = _safe_scalar(data.get("superseded_by", ""))
                status = _safe_scalar(data.get("status", ""))
                if status not in {"active", "superseded"}:
                    status = "superseded" if superseded_by else "active"
                conn.execute(
                    """
                    INSERT INTO rules(
                        atomic_id, type, domain, scope, status, current_revision,
                        superseded_by, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        atomic_id,
                        record["type"],
                        record["domain"],
                        record["scope"],
                        status,
                        superseded_by or None,
                        created,
                        updated,
                    ),
                )
                self._insert_version(conn, atomic_id, 1, record, "legacy import", now)
                supersedes = data.get("supersedes") or []
                if isinstance(supersedes, str):
                    supersedes = [supersedes]
                for old_id in supersedes:
                    if isinstance(old_id, str) and VALID_ATOMIC_ID.match(old_id):
                        pending_relations.append((old_id, atomic_id, "superseded_by"))
                if superseded_by and VALID_ATOMIC_ID.match(superseded_by):
                    pending_relations.append((atomic_id, superseded_by, "superseded_by"))
            for old_id, new_id, relation in pending_relations:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO rule_relations(from_id, to_id, relation, turn_key, created_at)
                    VALUES(?, ?, ?, 'legacy-import', ?)
                    """,
                    (old_id, new_id, relation, now),
                )
            conn.execute("UPDATE meta SET value='1' WHERE key='generation'")
            conn.execute("COMMIT")
        return True
