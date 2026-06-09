#!/usr/bin/env python3
"""Central path detection — Copilot CLI port.

All lib modules import this; no hard-coded path constants elsewhere.
Uses env / config / auto-detect 3-layer fallback.

Priority (high → low):
  1. Env var (B5_STATE_DIR / B5_MEMORY_DIR / B5_OBS_LOG_DIR / B5_PROJECT_ROOT)
  2. ~/.preference-tracker.config.json (key: state_dir / memory_dir / obs_log_dir /
              project_root)
  3. Auto-detect:
     - skill_dir = Path(__file__).parent.parent (plugin root)
     - project_root = os.getcwd()
     - state_dir = <project_root>/.copilot/preference-tracker-state/runtime
     - obs_log_dir = <project_root>/.copilot/preference-tracker-state/obs_log
     - memory_dir = <project_root>/.copilot/preference-tracker/memory

Ported from Claude Code version. Key changes:
  - .claude/ → .copilot/ in all default paths
  - memory_dir simplified to project-local (no escaped home path)
"""
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pt_platform  # platform-specific values (this variant)


def force_utf8_io():
    """Make stdout/stderr emit UTF-8 regardless of the platform console code page.

    Windows pipes default to cp1252. Hook modules print Chinese/emoji block
    reasons and `additionalContext`; on cp1252 that raises UnicodeEncodeError,
    which the hooks' defensive `except` swallows into a silent exit 0 — so
    blocking and memory injection silently break on native Windows. Forcing
    UTF-8 here fixes both. No-op on Linux/macOS (already UTF-8) and idempotent.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


# Apply at import time: every hook module imports path_config before printing,
# so this guarantees UTF-8 stdout for block decisions / additionalContext.
force_utf8_io()

CONFIG_PATH = os.path.expanduser('~/.preference-tracker.config.json')


_CHMOD_WARN_ONCE = set()


def chmod_or_warn(path, mode, critical=True):
    """chmod best-effort, but warn once-per-path on failure for security-critical
    files so a misconfigured filesystem (NFS no_squash, FAT32) doesn't silently
    leave files world-readable. Set critical=False for hardening hints (e.g. lock
    files). Set env PT_QUIET_CHMOD=1 to silence warnings.
    """
    try:
        os.chmod(path, mode)
    except OSError as e:
        if not critical:
            return
        key = str(path)
        if key in _CHMOD_WARN_ONCE:
            return
        _CHMOD_WARN_ONCE.add(key)
        if os.environ.get('PT_QUIET_CHMOD') == '1':
            return
        try:
            sys.stderr.write(
                f'preference-tracker: warning: chmod {oct(mode)} on {path} '
                f'failed ({e.__class__.__name__}: {e}). File may be '
                f'world-readable; consider remounting on a chmod-capable '
                f'filesystem or set PT_QUIET_CHMOD=1 to silence.\n'
            )
        except Exception:
            pass


def _clear_cache():
    """Test only — reset all lru_cache decorators in this module."""
    for fn in [_read_config_file, get_skill_dir, get_project_root, get_state_dir,
               get_obs_log_dir, get_memory_dir, get_b5_summary_dir,
               get_b5_alerts_threshold_dir]:
        try:
            fn.cache_clear()
        except AttributeError:
            pass


@lru_cache(maxsize=1)
def _read_config_file() -> dict:
    """Read ~/.preference-tracker.config.json. Returns empty dict if missing/corrupt."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return {}


def pt_env(suffix, default=None):
    """Read a user-facing env var by its new PT_ name, falling back to the legacy
    B5_ name, then default. e.g. pt_env('SHADOW_DISABLED')."""
    import os
    v = os.environ.get('PT_' + suffix)
    if v is not None:
        return v
    v = os.environ.get('B5_' + suffix)
    if v is not None:
        return v
    return default


def _read_env(env_var: str):
    """Read an env var honoring the PT_/B5_ alias pair. When env_var starts with
    'B5_', both PT_<X> and B5_<X> are honored (PT_ wins); otherwise the name is
    read as-is (so PT_-named vars like PT_ENFORCE keep working)."""
    if env_var.startswith('B5_'):
        return pt_env(env_var[3:])
    return os.environ.get(env_var)


def _resolve(env_var: str, config_key: str, default_func):
    """3-layer priority: env > config file > default_func()."""
    v = _read_env(env_var)
    if v:
        return v
    cfg = _read_config_file()
    if config_key in cfg and cfg[config_key]:
        return cfg[config_key]
    return default_func()


def _bool_setting(env_var: str, config_key: str, default: bool) -> bool:
    """3-layer boolean: env > config file > default. Truthy = 1/true/yes/on."""
    v = _read_env(env_var)
    if v is not None:
        return v.strip().lower() in ('1', 'true', 'yes', 'on')
    cfg = _read_config_file()
    if config_key in cfg:
        cv = cfg[config_key]
        if isinstance(cv, bool):
            return cv
        # Tolerate string/int forms a user might hand-edit ("true", "1", 1).
        if isinstance(cv, (str, int)):
            return str(cv).strip().lower() in ('1', 'true', 'yes', 'on')
    return default


def stop_block_exit_code() -> int:
    """Exit code a Stop hook uses when it BLOCKs. Delegates to the platform layer
    (Copilot: 0 = block via stdout JSON + exit 0; override via env
    PT_STOP_BLOCK_EXIT)."""
    return pt_platform.stop_block_exit_code()


def is_child_session() -> bool:
    """True inside a nested CLI subprocess this skill spawned (e.g. the shadow
    judge); hook entry points early-exit when set. Delegates to the platform
    layer (env PT_CHILD_SESSION)."""
    return pt_platform.is_child_session()


def enforcement_enabled() -> bool:
    """Master opt-in for hard-blocking gates (deterministic block, B4 pending-
    finalize gate, observation-log gate).

    PUBLIC DEFAULT = False (observe-only). A freshly installed skill records
    preferences and surfaces them, but NEVER hard-blocks a session — so a
    stranger can't be locked out or have replies rejected by the author's
    personal rules. Turn full enforcement on with env `PT_ENFORCE=1` or
    config {"enforce": true}.
    """
    return _bool_setting('PT_ENFORCE', 'enforce', False)


def shadow_enabled() -> bool:
    """Opt-in for the shadow LLM judge, which sends the last user message +
    assistant reply to an LLM (`copilot -p`).

    PUBLIC DEFAULT = False (privacy + cost). Turn on with env `PT_SHADOW=1`
    or config {"shadow": true}.
    """
    return _bool_setting('PT_SHADOW', 'shadow', False)


@lru_cache(maxsize=1)
def get_skill_dir() -> str:
    """Plugin root directory. Path(__file__).parent.parent.

    e.g. ~/.copilot/installed-plugins/_direct/preference-tracker/copilot/
    """
    return str(Path(__file__).resolve().parent.parent)


@lru_cache(maxsize=1)
def get_project_root() -> str:
    """Project root (cwd when hook fires).

    Stop/SessionStart hooks inherit the Copilot CLI session's cwd.
    """
    return _resolve('B5_PROJECT_ROOT', 'project_root', os.getcwd)


@lru_cache(maxsize=1)
def get_state_dir() -> str:
    """State runtime root. Default: <cwd>/.copilot/preference-tracker-state/runtime."""
    return _resolve(
        'B5_STATE_DIR', 'state_dir',
        lambda: pt_platform.default_state_dir(get_project_root())
    )


@lru_cache(maxsize=1)
def get_obs_log_dir() -> str:
    """Observation log + compliance log + pending queue root.

    Default: <cwd>/.copilot/preference-tracker-state/obs_log.
    """
    return _resolve(
        'B5_OBS_LOG_DIR', 'obs_log_dir',
        lambda: pt_platform.default_obs_log_dir(get_project_root())
    )


@lru_cache(maxsize=1)
def get_memory_dir() -> str:
    """Memory rules directory.

    Copilot port: project-local at <cwd>/.copilot/preference-tracker/memory.
    Migration fallback: if the new path has no .md files, check the legacy
    Claude Code path (~/.claude/projects/<cwd_escaped>/memory) and use it
    if it has content. This ensures existing rules aren't invisible after
    switching from Claude Code to Copilot CLI.
    """
    return _resolve('B5_MEMORY_DIR', 'memory_dir',
                    lambda: pt_platform.default_memory_dir(get_project_root()))


def get_compliance_log_path() -> str:
    return os.path.join(get_obs_log_dir(), 'compliance_log.jsonl')


def get_observations_log_path() -> str:
    return os.path.join(get_obs_log_dir(), 'observations.jsonl')


def get_pending_queue_path() -> str:
    return os.path.join(get_obs_log_dir(), 'pending_queue.jsonl')


def get_pending_alert_path() -> str:
    return os.path.join(get_obs_log_dir(), 'PENDING_ALERT.md')


def get_pending_error_log_path() -> str:
    return os.path.join(get_obs_log_dir(), 'pending_queue_errors.jsonl')


def get_shadow_log_path() -> str:
    return os.path.join(get_state_dir(), 'b5_shadow_alerts', 'b5_shadow_log.jsonl')


def get_shadow_alert_md_path() -> str:
    return os.path.join(get_state_dir(), 'b5_shadow_alerts', 'B5_SHADOW_ALERT.md')


def get_cost_log_dir() -> str:
    return os.path.join(get_state_dir(), 'b5_cost')


def get_streak_dir() -> str:
    return os.path.join(get_state_dir(), 'b5_deterministic_streak')


def get_retire_log_path() -> str:
    return os.path.join(get_state_dir(), 'b5_retire_log', 'retire_log.jsonl')


def get_b4_retry_dir() -> str:
    return os.path.join(get_state_dir(), 'b4_retry')


def get_b4_alert_dir() -> str:
    return os.path.join(get_state_dir(), 'b4_alerts')


@lru_cache(maxsize=1)
def get_b5_summary_dir() -> str:
    return os.path.join(get_state_dir(), 'b5_daily_summary')


@lru_cache(maxsize=1)
def get_b5_alerts_threshold_dir() -> str:
    return os.path.join(get_state_dir(), 'b5_alerts_threshold')


def ensure_dirs():
    """Create all state subdirectories. Called by install script + lib self-check.

    Idempotent (mkdir -p equivalent).
    """
    for d in [
        get_state_dir(),
        get_obs_log_dir(),
        get_cost_log_dir(),
        get_streak_dir(),
        get_b5_summary_dir(),
        get_b5_alerts_threshold_dir(),
        os.path.dirname(get_shadow_log_path()),
        os.path.dirname(get_retire_log_path()),
        get_b4_retry_dir(),
        get_b4_alert_dir(),
        get_memory_dir(),
    ]:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


if __name__ == '__main__':
    """Debug: print all paths."""
    print(f'skill_dir       = {get_skill_dir()}')
    print(f'project_root    = {get_project_root()}')
    print(f'state_dir       = {get_state_dir()}')
    print(f'obs_log_dir     = {get_obs_log_dir()}')
    print(f'memory_dir      = {get_memory_dir()}')
    print(f'compliance_log  = {get_compliance_log_path()}')
    print(f'shadow_log      = {get_shadow_log_path()}')
    print(f'shadow_alert_md = {get_shadow_alert_md_path()}')
    print(f'cost_log_dir    = {get_cost_log_dir()}')
    print(f'streak_dir      = {get_streak_dir()}')
    print(f'retire_log      = {get_retire_log_path()}')
    print(f'b5_summary_dir  = {get_b5_summary_dir()}')
    print()
    print(f'Config file: {CONFIG_PATH} (exists: {os.path.exists(CONFIG_PATH)})')
    print(f'Config loaded: {_read_config_file()}')
