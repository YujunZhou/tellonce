#!/usr/bin/env python3
"""Platform-specific values — GitHub Copilot CLI variant.

The shared core (path_config and the hook modules) imports this module instead
of hard-coding the values that differ between runtimes (Claude Code / Copilot
CLI). Each variant ships its own ``pt_platform.py`` with the same interface but
different values, so the rest of the code stays identical across variants.

Named ``pt_platform`` (not ``platform``) to avoid shadowing the Python stdlib
``platform`` module on ``sys.path``.

Interface (must stay in sync with lib/pt_platform.py):
  STATE_DIR_NAME            : str
  default_state_dir(root)   : str
  default_obs_log_dir(root) : str
  default_memory_dir(root)  : str
  stop_block_exit_code()    : int
  is_child_session()        : bool
"""
import os

# Directory name under the project root where this runtime keeps its state.
STATE_DIR_NAME = '.copilot'

# CLI used for subscription-mode subprocess calls (preference classifier, etc.).
CLI_COMMAND = 'copilot'

# Default model for the CLI preference classifier (detect_user_prefer). Empty
# means "omit --model and let copilot pick its own" (copilot rejects an explicit
# Claude model name like claude-haiku-4-5).
PREFER_MODEL_DEFAULT = ''


def default_state_dir(project_root: str) -> str:
    """Default runtime state dir: <project_root>/.copilot/preference-tracker-state/runtime."""
    return os.path.join(project_root, '.copilot', 'preference-tracker-state', 'runtime')


def default_obs_log_dir(project_root: str) -> str:
    """Default observation/compliance/pending root: <project_root>/.copilot/preference-tracker-state/obs_log."""
    return os.path.join(project_root, '.copilot', 'preference-tracker-state', 'obs_log')


def default_memory_dir(project_root: str) -> str:
    """Default memory-rules dir for Copilot: project-local at
    <project_root>/.copilot/preference-tracker/memory.

    Migration fallback: if the new path has no .md files, check the legacy Claude
    Code path (~/.claude/projects/<cwd_escaped>/memory) and use it if it has
    content, so rules recorded before switching from Claude Code aren't invisible.
    """
    new_dir = os.path.join(project_root, '.copilot', 'preference-tracker', 'memory')
    try:
        if os.path.isdir(new_dir) and any(f.endswith('.md') for f in os.listdir(new_dir)):
            return new_dir
    except Exception:
        pass
    try:
        escaped = project_root.replace('/', '-').replace('\\', '-').replace(':', '-')
        legacy_dir = os.path.expanduser(f'~/.claude/projects/{escaped}/memory')
        if os.path.isdir(legacy_dir) and any(f.endswith('.md') for f in os.listdir(legacy_dir)):
            return legacy_dir
    except Exception:
        pass
    return new_dir


def stop_block_exit_code() -> int:
    """Exit code a Stop hook uses when it decides to BLOCK.

    The Copilot CLI treats a Stop hook's ``exit 2`` as a warning only; to actually
    block it wants ``{"decision":"block"}`` on stdout plus ``exit 0``. We therefore
    default to 0 so the Windows (PowerShell-direct python) path and the bash-wrapper
    path behave identically and per-spec. Override with env ``PT_STOP_BLOCK_EXIT=2``
    if a runtime is found to honor exit-2 blocking.

    REVERSE RISK: if a runtime honors ``exit 2`` but IGNORES stdout JSON, defaulting
    to 0 lets a real violation through silently on the python-direct path; set
    ``PT_STOP_BLOCK_EXIT=2`` there.
    """
    v = os.environ.get('PT_STOP_BLOCK_EXIT')
    if v and v.strip().isdigit():
        return int(v.strip())
    return 0


def is_child_session() -> bool:
    """True inside a nested ``copilot -p`` subprocess this skill spawned (e.g. the
    shadow judge). Hook entry points early-exit when set so a nested agent session
    can't re-fire the gates, re-inject context, promote the pending queue, or
    pollute the compliance log. Set via env ``PT_CHILD_SESSION=1`` on the child
    (see verify_retry_shadow)."""
    return os.environ.get('PT_CHILD_SESSION', '').strip().lower() in ('1', 'true', 'yes', 'on')
