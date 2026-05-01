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


# Subprocess env policy.
#
# CX-4 + post-fix-review fix: we want to strip obvious leak vectors
# (random *_TOKEN/*_SECRET/*PASSWORD* env vars from ambient shell), but
# the whole point of the wrapper is to run user-chosen binaries — most of
# which are LLM CLIs (claude / codex / gh) that REQUIRE a credential env
# to function. A pure deny-list would brick the most common workflow.
#
# Resolution: deny by substring → then punch back specific names that the
# wrapped binary likely needs. Allowlist takes precedence over denylist.
# Strict mode (CODEX_PT_STRICT_ENV=1) drops the punch-through and goes pure
# deny-list — for security-sensitive review workflows that don't need to
# call an LLM CLI from inside the wrapper.
_ENV_ALLOW_PREFIXES: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "TERM", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_", "PWD", "SHELL", "DISPLAY", "XAUTHORITY",
    "PYTHON",  # PYTHONPATH / PYTHONIOENCODING etc
    "CODEX_PREFTRACK_",  # our own opt-in env
    "XDG_",  # runtime / config / data dirs (e.g. XDG_RUNTIME_DIR for gh / ssh)
)

# Explicit names that survive the deny pass even though they contain
# AUTH/TOKEN/KEY substrings — these are the standard credential envs
# for the LLM CLIs that user_facing_command-passers most commonly pass to
# `codex_preftrack exec`. If you want to forbid one, set
# CODEX_PT_STRICT_ENV=1.
_ENV_ALLOW_NAMES: frozenset[str] = frozenset({
    # LLM vendors
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "DEEPINFRA_API_KEY", "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY", "GROQ_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",  # Claude Code subscription
    # Common dev tooling that the wrapped command may invoke
    "GH_TOKEN", "GITHUB_TOKEN",  # gh / git
    "SSH_AUTH_SOCK", "SSH_AGENT_PID",  # ssh
    "GIT_ASKPASS", "GIT_SSH",
    "GPG_AGENT_INFO", "GNUPGHOME",
})

_ENV_DENY_SUBSTRINGS: tuple[str, ...] = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY", "API_KEY",
    "ACCESS_KEY", "AUTH",
)


def _filter_env(parent: dict[str, str]) -> dict[str, str]:
    """Build a child-process env per the policy above.

    Order: start empty → deny-pass (drop anything matching deny substring)
    → allow-prefix pass (re-add anything starting with PATH/HOME/PYTHON/...)
    → allow-name pass (re-add specific LLM/dev tool credential vars by name).
    Strict mode skips the third pass.
    """
    strict = os.environ.get("CODEX_PT_STRICT_ENV", "").lower() in ("1", "true", "yes")
    out: dict[str, str] = {}
    for k, v in parent.items():
        upper = k.upper()
        if any(deny in upper for deny in _ENV_DENY_SUBSTRINGS):
            continue
        out[k] = v
    # Allow-prefix pass.
    for k, v in parent.items():
        if any(k.startswith(p) for p in _ENV_ALLOW_PREFIXES):
            out[k] = v
    # Allow-name pass (skip in strict mode).
    if not strict:
        for name in _ENV_ALLOW_NAMES:
            if name in parent:
                out[name] = parent[name]
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
    secure_write_text(run_dir / "run_meta.json", json.dumps(meta, indent=2) + "\n", atomic=True)
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
    secure_write_text(run_dir / "original_stdout.txt", safe_stdout, atomic=True)
    secure_write_text(run_dir / "original_stderr.txt", safe_stderr, atomic=True)

    verdict = verify_output(safe_stdout, whitelist=load_default_whitelist())
    # Sanitize verdict.violations.evidence too — the inline-token regex can
    # capture sk-/ghp_/etc style tokens (CX-14).
    verdict_dict = sanitize(verdict.__dict__)
    secure_write_text(run_dir / "verdicts.jsonl", json.dumps(verdict_dict) + "\n", atomic=True)

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
