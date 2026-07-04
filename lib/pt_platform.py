#!/usr/bin/env python3
"""Platform-specific values — Claude Code variant.

The shared core (path_config and the hook modules) imports this module instead
of hard-coding the values that differ between runtimes (Claude Code / Copilot
CLI). Each variant ships its own ``pt_platform.py`` with the same interface but
different values, so the rest of the code stays identical across variants.

Named ``pt_platform`` (not ``platform``) to avoid shadowing the Python stdlib
``platform`` module on ``sys.path``.

Interface (must stay in sync with copilot/lib/pt_platform.py):
  STATE_DIR_NAME            : str
  default_state_dir(root)   : str
  default_obs_log_dir(root) : str
  default_memory_dir(root)  : str
  stop_block_exit_code()    : int
  is_child_session()        : bool
"""
import os

# Directory name under the project root where this runtime keeps its state.
STATE_DIR_NAME = '.claude'

# CLI used for subscription-mode subprocess calls (preference classifier, etc.).
CLI_COMMAND = 'claude'

# Default model for the CLI preference classifier (detect_user_prefer). Empty
# means "omit --model and let the CLI pick its own" (for runtimes that reject an
# explicit Claude model name).
PREFER_MODEL_DEFAULT = 'claude-haiku-4-5'

# Default CLI for the session-rule retriever (retrieve_inject / B5_RETRIEVE_CLI).
RETRIEVE_CLI_DEFAULT = 'claude'

# Default model for the shadow LLM judge (verify_retry_shadow). Empty means
# "omit --model and let the CLI pick its own" (for runtimes that reject an
# explicit Claude model name).
JUDGE_MODEL_DEFAULT = 'claude-haiku-4-5'

# How often the rule index is injected on this platform — used in user-facing
# wording (the progressive backend's truncation note says rules "rotate in on
# later <cadence>"). Claude Code / Codex inject per UserPromptSubmit ('turns');
# Copilot only injects at SessionStart ('sessions').
INJECT_CADENCE = 'turns'


def default_state_dir(project_root: str) -> str:
    """Default runtime state dir: <project_root>/.claude/tellonce-state/runtime."""
    return os.path.join(project_root, '.claude', 'tellonce-state', 'runtime')


def default_obs_log_dir(project_root: str) -> str:
    """Default observation/compliance/pending root: <project_root>/.claude/tellonce-state/obs_log."""
    return os.path.join(project_root, '.claude', 'tellonce-state', 'obs_log')


def default_memory_dir(project_root: str) -> str:
    """Default memory-rules dir: ~/.claude/projects/<cwd_escaped>/memory.

    Claude Code escapes EVERY non-alphanumeric character to '-' (verified
    against a live installation: /home/u/zyj/paper_v2_template ->
    -home-u-zyj-paper-v2-template). An earlier tellonce (and possibly older
    CC builds) escaped only '/', so for any project path containing '_' or
    '.' the two spellings diverge and rules silently split-brain between
    "the dir tellonce reads" and "the dir Claude Code loads". Prefer the
    current-CC spelling; fall back to the legacy spelling only when that
    exact dir already exists and the new one doesn't (data written by older
    installs stays visible)."""
    import re
    new_escaped = re.sub(r'[^A-Za-z0-9]', '-', project_root)
    new_dir = os.path.expanduser(f'~/.claude/projects/{new_escaped}/memory')
    legacy_escaped = project_root.replace('/', '-')
    if legacy_escaped != new_escaped:
        legacy_dir = os.path.expanduser(f'~/.claude/projects/{legacy_escaped}/memory')
        if os.path.isdir(legacy_dir) and not os.path.isdir(new_dir):
            return legacy_dir
    return new_dir


def stop_block_exit_code() -> int:
    """Exit code a Stop hook uses when it decides to BLOCK.

    Claude Code honors ``{"decision":"block"}`` on stdout together with a non-zero
    exit, so the Claude variant blocks with exit 2 (the historical behavior).
    Override with env ``PT_STOP_BLOCK_EXIT`` if a runtime needs a different code.
    """
    v = os.environ.get('PT_STOP_BLOCK_EXIT')
    if v and v.strip().isdigit():
        return int(v.strip())
    return 2


def is_child_session() -> bool:
    """True inside a nested CLI subprocess this skill spawned (e.g. the shadow
    judge). Hook entry points early-exit when set so a nested agent session can't
    re-fire the gates, re-inject context, promote the pending queue, or pollute
    the compliance log. Set via env ``PT_CHILD_SESSION=1`` on the child (see
    verify_retry_shadow)."""
    return os.environ.get('PT_CHILD_SESSION', '').strip().lower() in ('1', 'true', 'yes', 'on')
