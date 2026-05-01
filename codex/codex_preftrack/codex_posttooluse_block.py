#!/usr/bin/env python3
"""Codex PostToolUse adapter for deterministic_block.

Codex doesn't have a Stop hook. PostToolUse fires after every tool call. This
adapter:

  1. Reads codex hook stdin JSON (tool_name, tool_input, tool_response, cwd).
  2. Reconstructs a "response_text" from tool_input + tool_response.output so
     CC's evaluate_rules() can scan it for violations (active /tmp/ in code,
     etc.).
  3. Runs CC's evaluate_rules() and emits the codex hook output protocol:
       - exit 2 + JSON {decision: "block", reason: ...} when mode == 'blocking'
       - exit 0 + advisory log when mode == 'audit_only' or 'wrapper'
  4. Always logs the decision to <state>/runtime/posttooluse_log.jsonl.

The hook intentionally scans tool_input first — agent-submitted text (e.g. a
Write tool's content) is the most common source of rule violations we can
detect at this surface. tool_response.output is scanned secondarily but
filtered (build error lines often contain "stub" / "/tmp" legitimately).

Defensive: any failure -> exit 0 with empty stdout (never block codex turns).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _load_stdin() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _extract_agent_text(payload: dict) -> str:
    """Pull agent-authored text content out of a codex PostToolUse payload.

    Priority order:
      1. tool_input.content (Write/Edit-style tools) — what the agent wrote
      2. tool_input.command (Bash/Shell) — the command itself
      3. tool_input.* string fields concatenated — fallback
      4. tool_input as JSON — last-resort fallback so SECRET / /tmp inside any
         field still scannable

    NOTE: We avoid scanning tool_response.output because that's environment
    output (build logs, command stdout) — not agent-authored. False positives
    there are common (e.g. "build using stub for X" is informational).
    """
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    if isinstance(tool_input.get("content"), str):
        return tool_input["content"]
    if isinstance(tool_input.get("new_string"), str) and isinstance(tool_input.get("old_string"), str):
        # Edit-style: agent's new content
        return tool_input["new_string"]
    if isinstance(tool_input.get("command"), str):
        return tool_input["command"]
    parts = [
        v for v in tool_input.values()
        if isinstance(v, str) and len(v) < 100_000
    ]
    if parts:
        return "\n".join(parts)
    if not tool_input:
        return ""
    try:
        return json.dumps(tool_input, ensure_ascii=False)
    except Exception:
        return ""


def _resolve_state_root(payload: dict) -> Path | None:
    """Find the project's codex preference-tracker state root.

    Prefer `cwd` from payload, fall back to env CODEX_PROJECT_ROOT, else PWD.
    """
    cwd = payload.get("cwd") or os.environ.get("CODEX_PROJECT_ROOT") or os.getcwd()
    candidate = Path(cwd) / ".codex" / "preference-tracker"
    if (candidate / "registration.json").is_file() or (candidate / "mode.json").is_file():
        return candidate
    # Also try home-fallback layout (codex_preftrack.paths.fallback_state_root)
    try:
        from codex_preftrack.paths import project_id_for, fallback_state_root
        fb = fallback_state_root(Path(cwd))
        if (fb / "registration.json").is_file():
            return fb
    except Exception:
        pass
    return None


def _read_mode(state_root: Path | None) -> str:
    if not state_root:
        return "audit_only"
    p = state_root / "mode.json"
    if not p.is_file():
        return "audit_only"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("mode", "audit_only")
    except Exception:
        return "audit_only"


def _log_decision(state_root: Path | None, record: dict) -> None:
    if not state_root:
        return
    log_dir = state_root / "runtime"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "posttooluse_log.jsonl"
        # POSIX: a single line write to an O_APPEND fd is atomic up to PIPE_BUF
        # (typically 4096 bytes). Our records are well under that, so flock is
        # belt-and-suspenders here, but it ALSO blocks tmp races between
        # concurrent codex sessions on the same project.
        try:
            import fcntl
            with log_path.open("a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            # No fcntl (Windows) — fall back to bare append; record may
            # interleave on rare contention but won't corrupt single lines.
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            os.chmod(log_path, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def main() -> int:
    payload = _load_stdin()
    if not payload:
        return 0

    state_root = _resolve_state_root(payload)
    mode = _read_mode(state_root)

    # Lazy import shared CC lib. Try multiple layouts:
    #   1. ~/.codex/skills/preference-tracker/shared_lib/ (installed layout)
    #   2. <repo>/lib/ (development layout when running from repo)
    candidates = [
        Path(__file__).parent.parent / "shared_lib",            # installed
        Path(__file__).parent.parent.parent / "lib",            # repo dev
    ]
    for cand in candidates:
        if cand.is_dir() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
            break
    try:
        import deterministic_block as db  # type: ignore
    except Exception:
        return 0

    text = _extract_agent_text(payload)
    if not text or len(text) < 30:
        # Small inputs are usually metadata or short shell commands; skip to
        # avoid noise.
        return 0

    try:
        violations = db.evaluate_rules(text, [])
    except Exception:
        return 0

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "tool_name": payload.get("tool_name"),
        "mode": mode,
        "violations": [v.get("rule_id") for v in violations],
    }
    _log_decision(state_root, record)

    if not violations:
        return 0

    # Build feedback text using CC's helper if available.
    try:
        reason = db.build_block_reason(violations)
    except Exception:
        reason = "preference-tracker: deterministic violations: " + ", ".join(
            v.get("rule_id", "?") for v in violations
        )

    if mode != "blocking":
        # Advisory only — print to stderr (codex shows it to user/agent without
        # blocking the tool call).
        sys.stderr.write(
            "[preference-tracker advisory] "
            + ", ".join(v.get("rule_id", "?") for v in violations)
            + "\n"
        )
        return 0

    # Blocking mode: emit codex PostToolUse-block JSON + exit 2.
    # If JSON serialization fails (shouldn't happen, but defensive), don't
    # exit 2 — codex would log "invalid JSON output" and the user wouldn't
    # see the reason. Better: fail open (advisory only, exit 0).
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reason,
        },
        "decision": "block",
        "reason": reason,
    }
    try:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception:
        sys.stderr.write("[preference-tracker] JSON serialize failed; demoting to advisory\n")
        sys.stderr.write(reason + "\n")
        return 0
    sys.stderr.write(reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
