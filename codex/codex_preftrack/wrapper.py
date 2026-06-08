from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .ledger import append_event, sanitize, secure_mkdir, secure_write_text
from .mode import load_mode, write_mode
from .verify import load_default_whitelist, verify_output


class _StringSink:
    """Adapter so the byte-tee can write to sinks that lack `.buffer` (e.g.
    pytest's CaptureIO replacement for sys.stdout). Decodes with errors='replace'
    and writes via the text-mode write()/flush() API."""
    def __init__(self, text_stream):
        self._stream = text_stream

    def write(self, chunk_bytes):
        try:
            self._stream.write(chunk_bytes.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass


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
    from .ledger import _chmod_or_warn
    _chmod_or_warn(run_dir, 0o700)

    redacted_cmd = _sanitize_cmd_for_log(cmd)
    meta = {"run_id": run_id, "cmd": redacted_cmd, "mode": "wrapper"}
    secure_write_text(run_dir / "run_meta.json", json.dumps(meta, indent=2) + "\n", atomic=True)
    append_event(
        state_root,
        {"event_type": "wrapper_run_started", "session_id": "codex-current", "payload": meta},
    )

    child_env = _filter_env(os.environ.copy())
    # Byte-mode I/O handling:
    # 1. Byte-mode Popen — text=True would crash a tee thread with
    #    UnicodeDecodeError when the child writes non-UTF-8 bytes (compilers,
    #    binaries, locale-mismatched logs).
    # 2. Tee to sys.stdout.buffer / sys.stderr.buffer so non-UTF-8 bytes pass
    #    through to the user's terminal unchanged.
    # 3. Decode-for-persist with errors='replace' so sanitize() / verify_output
    #    still see strings, but garbled bytes don't poison the captured copy.
    # 4. Convert negative returncode (signal kill) to shell convention 128+sig
    #    so wrappers behave like a normal shell (e.g. SIGTERM → 143, not -15).
    import threading as _threading
    stdout = ""
    stderr = ""
    rc = 0
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )

        def _tee_bytes(src, sink_buffer, buf_list):
            try:
                while True:
                    chunk = src.readline()
                    if not chunk:
                        break
                    buf_list.append(chunk)
                    try:
                        sink_buffer.write(chunk)
                        sink_buffer.flush()
                    except Exception:
                        # Sink failures (e.g. parent's stdout closed) shouldn't
                        # crash the tee — keep capturing so we can still persist.
                        pass
            finally:
                try:
                    src.close()
                except Exception:
                    pass

        out_chunks: list = []
        err_chunks: list = []
        # sys.stdout/sys.stderr may be replaced by pytest with non-buffer wrappers;
        # fall back to writing the chunk via str(errors=replace) in that case.
        out_sink = getattr(sys.stdout, "buffer", None) or _StringSink(sys.stdout)
        err_sink = getattr(sys.stderr, "buffer", None) or _StringSink(sys.stderr)
        t_out = _threading.Thread(target=_tee_bytes, args=(proc.stdout, out_sink, out_chunks), daemon=True)
        t_err = _threading.Thread(target=_tee_bytes, args=(proc.stderr, err_sink, err_chunks), daemon=True)
        t_out.start()
        t_err.start()
        try:
            raw_rc = proc.wait(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                raw_rc = proc.wait(timeout=5)
            except Exception:
                raw_rc = -9
            timed_out = True
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        if timed_out:
            stderr += f"\n[wrapper] subprocess timed out after {timeout_s}s\n"
            rc = 3
        elif raw_rc < 0:
            # Negative returncode means killed by signal -raw_rc. Shell convention
            # is 128 + signal_number so wrappers compose with `set -e` etc.
            rc = 128 + (-raw_rc)
        else:
            rc = raw_rc
    except FileNotFoundError as exc:
        msg = f"[wrapper] command not found: {exc.filename!r}\n"
        try:
            sys.stderr.write(msg)
        except Exception:
            pass
        stdout = ""
        stderr = msg
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
