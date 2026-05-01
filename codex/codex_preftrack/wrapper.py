from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from .ledger import append_event, sanitize, secure_mkdir, secure_write_text
from .mode import load_mode, write_mode
from .verify import load_default_whitelist, verify_output


# Subprocess env policy: deny-list anything that looks like a credential or
# session token. CX-4 fix — prior implementation inherited the entire parent
# env, including ANTHROPIC_API_KEY / DATABASE_URL / *_TOKEN / *_SECRET, and
# handed them to the wrapped command (which often runs untrusted-ish 3p
# binaries that may upload telemetry). Allowlist core operational vars and
# inherit; redact the rest.
_ENV_ALLOW_PREFIXES: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "TERM", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_", "PWD", "SHELL", "DISPLAY", "XAUTHORITY",
    "PYTHON",  # PYTHONPATH / PYTHONIOENCODING etc
    "CODEX_PREFTRACK_",  # our own opt-in env
)
_ENV_DENY_SUBSTRINGS: tuple[str, ...] = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY", "API_KEY",
    "ACCESS_KEY", "AUTH",
)


def _filter_env(parent: dict[str, str]) -> dict[str, str]:
    """Build a child-process env that strips obvious credentials.

    Logic: if a var name has any deny substring (TOKEN/SECRET/PASSWORD/...),
    drop it. Otherwise inherit. Plus a small explicit allowlist to ensure
    PATH / HOME / locale survive even if the parent env is unusual.
    """
    out: dict[str, str] = {}
    for k, v in parent.items():
        upper = k.upper()
        if any(deny in upper for deny in _ENV_DENY_SUBSTRINGS):
            continue
        out[k] = v
    # Explicit allowlist guarantees these keep flowing even if a future
    # rule were to filter more aggressively.
    for k, v in parent.items():
        if any(k.startswith(p) for p in _ENV_ALLOW_PREFIXES):
            out[k] = v
    return out


@dataclass(frozen=True)
class WrappedRun:
    run_id: str
    exit_code: int


# Strip cmd-line tokens that look like inline credentials (--password=xxx,
# --token=foo, --api-key=...). We keep the option name (so audit can see
# what was passed) but redact the value.
_CMD_REDACT_RE = re.compile(
    r"(--?(?:password|passwd|secret|token|api[_\-]?key|access[_\-]?key|auth)[=\s])(\S+)",
    re.IGNORECASE,
)


def _sanitize_cmd_for_log(cmd: list[str]) -> list[str]:
    out = []
    for token in cmd:
        m = _CMD_REDACT_RE.match(token)
        if m:
            out.append(f"{m.group(1)}[REDACTED]")
        else:
            out.append(sanitize(token))
    return out


def run_wrapped(
    state_root: Path,
    cmd: list[str],
    timeout_s: int | None = None,
) -> WrappedRun:
    """Run `cmd` as a subprocess, capture+redact stdout/stderr, log the verdict.

    Hardening (publish review):
      - run_id is uuid4 (CX-11) so concurrent wrappers can't collide
      - run_dir is mkdir(exist_ok=False) — collision raises rather than silently
        overwriting another run's evidence
      - state_root + run_dir get 0700; all written files get 0600 (CC parity H10)
      - subprocess env is filtered to drop *_TOKEN / *_SECRET / *PASSWORD* /
        *_API_KEY / *_AUTH (CX-4)
      - stdout / stderr / verdicts go through sanitize() before disk so
        SECRET_PATTERNS catches AWS / Slack / JWT / DB URI / etc (CX-1, CX-14)
      - Timeout reads from arg → env CODEX_PT_TIMEOUT → 600s default (HX-1).
        Was 120s hardcoded which always cut off real LLM sessions.
      - Exception path's stderr no longer dumps cmd argv (HX-12).
    """
    if timeout_s is None:
        try:
            timeout_s = int(os.environ.get("CODEX_PT_TIMEOUT", "600"))
        except ValueError:
            timeout_s = 600

    secure_mkdir(state_root)
    run_id = uuid.uuid4().hex
    run_dir = state_root / "runs" / run_id
    # exist_ok=False — uuid4 collision is astronomically unlikely; raising on
    # collision is safer than silently merging two runs into one dir.
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(run_dir, 0o700)
    except OSError:
        pass

    redacted_cmd = _sanitize_cmd_for_log(cmd)
    meta = {"run_id": run_id, "cmd": redacted_cmd, "mode": "wrapper"}
    secure_write_text(run_dir / "run_meta.json", json.dumps(meta, indent=2) + "\n")
    append_event(
        state_root,
        {"event_type": "wrapper_run_started", "session_id": "codex-current", "payload": meta},
    )

    child_env = _filter_env(os.environ.copy())
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=child_env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # Don't dump full str(exc) — it includes the entire cmd argv.
        stdout = (exc.stdout if isinstance(exc.stdout, str) else "") or ""
        partial_err = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = f"[wrapper] subprocess timed out after {timeout_s}s\n{partial_err or ''}"
        rc = 3
    except FileNotFoundError as exc:
        stdout = ""
        stderr = f"[wrapper] command not found: {exc.filename!r}"
        rc = 3

    # Sanitize before persisting (the central reason all this exists).
    safe_stdout = sanitize(stdout)
    safe_stderr = sanitize(stderr)
    secure_write_text(run_dir / "original_stdout.txt", safe_stdout)
    secure_write_text(run_dir / "original_stderr.txt", safe_stderr)

    verdict = verify_output(safe_stdout, whitelist=load_default_whitelist())
    # Sanitize verdict.violations.evidence too — the inline-token regex can
    # capture sk-/ghp_/etc style tokens (CX-14).
    verdict_dict = sanitize(verdict.__dict__)
    secure_write_text(run_dir / "verdicts.jsonl", json.dumps(verdict_dict) + "\n")

    append_event(
        state_root,
        {
            "event_type": "wrapper_run_completed",
            "session_id": "codex-current",
            "payload": {"run_id": run_id, "exit_code": rc, "verdict": verdict.verdict},
        },
    )

    current_mode = load_mode(state_root)
    write_mode(
        state_root,
        mode="wrapper" if current_mode.mode == "audit_only" else current_mode.mode,
        wrapper_seen=True,
    )
    return WrappedRun(run_id=run_id, exit_code=rc)
