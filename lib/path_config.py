#!/usr/bin/env python3
"""Central path detection — Phase 4.1 解耦中央 (per `code-pref-287`).

所有 lib `.py` import 这个模块, 不写硬编码常量. 装包用 env / config / auto-detect
三层兜底.

优先级 (高 → 低):
  1. Env var (B5_STATE_DIR / B5_MEMORY_DIR / B5_OBS_LOG_DIR / B5_PROJECT_ROOT /
              B5_WHITELIST_USER)
  2. ~/.preference-tracker.config.json (key: state_dir / memory_dir / obs_log_dir /
              project_root / whitelist_user)
  3. 自动 detect:
     - skill_dir = Path(__file__).parent.parent (preference-tracker 包根)
     - project_root = os.getcwd()
     - state_dir = <project_root>/.claude/preference-tracker-state/runtime
     - obs_log_dir = <project_root>/.claude/preference-tracker-state/obs_log
     - memory_dir = ~/.claude/projects/<cwd_escaped>/memory
                    cwd_escaped = cwd.replace('/', '-')

Per `wf-pref-027` versioned 备份 — 此文件 additive 新文件, 不动现有.
Per `tool-pit-130` state 走 .claude/preference-tracker-state/, 不 /tmp.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = os.path.expanduser('~/.preference-tracker.config.json')


def _clear_cache():
    """test only — reset all lru_cache decorators in this module.

    用于测试 (修改 env / config 后 re-eval); 不在 production hot path 用.
    """
    for fn in [_read_config_file, get_skill_dir, get_project_root, get_state_dir,
               get_obs_log_dir, get_memory_dir, get_b5_summary_dir,
               get_b5_alerts_threshold_dir]:
        try:
            fn.cache_clear()
        except AttributeError:
            pass


@lru_cache(maxsize=1)
def _read_config_file() -> dict:
    """读 ~/.preference-tracker.config.json. 不存在或 corrupt 返空 dict."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        # corrupt JSON / 权限错都返空; 不 crash
        return {}


def _resolve(env_var: str, config_key: str, default_func):
    """三层优先级 detect: env > config file > default_func()."""
    v = os.environ.get(env_var)
    if v:
        return v
    cfg = _read_config_file()
    if config_key in cfg and cfg[config_key]:
        return cfg[config_key]
    return default_func()


@lru_cache(maxsize=1)
def get_skill_dir() -> str:
    """preference-tracker 包根. Path(__file__).parent.parent.

    e.g. /users/<user>/.claude/skills/preference-tracker/
    """
    return str(Path(__file__).resolve().parent.parent)


@lru_cache(maxsize=1)
def get_project_root() -> str:
    """项目根 (用户调用 hook 时的 cwd).

    Stop hook fire 时 process cwd = 用户 Claude Code session 的 cwd.
    """
    return _resolve('B5_PROJECT_ROOT', 'project_root', os.getcwd)


@lru_cache(maxsize=1)
def get_state_dir() -> str:
    """state runtime 根. 默认 <cwd>/.claude/preference-tracker-state/runtime."""
    return _resolve(
        'B5_STATE_DIR', 'state_dir',
        lambda: os.path.join(get_project_root(), '.claude', 'preference-tracker-state', 'runtime')
    )


@lru_cache(maxsize=1)
def get_obs_log_dir() -> str:
    """observation log + compliance log + pending queue 根.

    默认 <cwd>/.claude/preference-tracker-state/obs_log.
    """
    return _resolve(
        'B5_OBS_LOG_DIR', 'obs_log_dir',
        lambda: os.path.join(get_project_root(), '.claude', 'preference-tracker-state', 'obs_log')
    )


@lru_cache(maxsize=1)
def get_memory_dir() -> str:
    """memory rules 目录. Claude Code 标准: ~/.claude/projects/<cwd_escaped>/memory.

    cwd_escaped = cwd.replace('/', '-')
    e.g. /home/alice/projects/foo → -home-alice-projects-foo
    """
    def default():
        cwd = get_project_root()
        escaped = cwd.replace('/', '-')
        return os.path.expanduser(f'~/.claude/projects/{escaped}/memory')
    return _resolve('B5_MEMORY_DIR', 'memory_dir', default)


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


def get_whitelist_paths() -> list:
    """返 [全局基础, per-user 增量]. lib 加载 whitelist 合并两者.

    全局: <skill_dir>/lib/deterministic_block_whitelist.txt (装时一致)
    per-user: <skill_dir>/lib/deterministic_block_whitelist_user.txt
              (env B5_WHITELIST_USER 可 override)
    """
    skill_dir = get_skill_dir()
    base = os.path.join(skill_dir, 'lib', 'deterministic_block_whitelist.txt')
    user = _resolve(
        'B5_WHITELIST_USER', 'whitelist_user',
        lambda: os.path.join(skill_dir, 'lib', 'deterministic_block_whitelist_user.txt')
    )
    return [base, user]


def ensure_dirs():
    """install.sh 跑时 + lib 自检时调一次, 创全 state subdirs.

    幂等 (mkdir -p), 重跑不破.
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
            # 权限 / 磁盘满 等失败 → defensive skip, 由调用方处理
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
    print(f'whitelist_paths = {get_whitelist_paths()}')
    print()
    print(f'Config file: {CONFIG_PATH} (exists: {os.path.exists(CONFIG_PATH)})')
    print(f'Config loaded: {_read_config_file()}')
