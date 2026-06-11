#!/usr/bin/env python3
"""Tests for path_config.py — central path detection.

Run: python3 test_path_config.py
Expects: 12/12 PASS.

Tests cover the three-layer detection:
  1. Env var (B5_*) highest priority
  2. ~/.tellonce.config.json second priority
  3. Auto-detect fallback
+ escape rules, ensure_dirs idempotency, corrupt config does not crash, etc.
"""
import json
import os
import sys
import tempfile

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config as pc


def _reset_env_and_cache(env_vars: dict = None):
    """test fixture: clear env, set new env, reset cache."""
    for v in ['B5_STATE_DIR', 'B5_MEMORY_DIR', 'B5_OBS_LOG_DIR', 'B5_PROJECT_ROOT']:
        os.environ.pop(v, None)
    if env_vars:
        for k, v in env_vars.items():
            os.environ[k] = v
    pc._clear_cache()


def _write_config(d: dict):
    """test only: write ~/.tellonce.config.json. caller must restore via _reset_config()."""
    pc.CONFIG_PATH = os.path.join(tempfile.gettempdir(), 'test_tellonce.config.json')
    with open(pc.CONFIG_PATH, 'w') as f:
        json.dump(d, f)
    pc._read_config_file.cache_clear()


def _reset_config():
    """restore CONFIG_PATH to default."""
    if pc.CONFIG_PATH != os.path.expanduser('~/.tellonce.config.json'):
        try:
            os.remove(pc.CONFIG_PATH)
        except FileNotFoundError:
            pass
    pc.CONFIG_PATH = os.path.expanduser('~/.tellonce.config.json')
    pc._read_config_file.cache_clear()


# ---------------------------- Tests ----------------------------

def test_env_var_state_dir_highest_priority():
    """B5_STATE_DIR env var should override config and default."""
    _write_config({'state_dir': '/from/config'})
    _reset_env_and_cache({'B5_STATE_DIR': '/from/env'})
    try:
        assert pc.get_state_dir() == '/from/env', \
            f'expected /from/env (env var win), got {pc.get_state_dir()}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_config_file_second_priority():
    """config file should take priority over default (when env is not set)."""
    _write_config({'state_dir': '/from/config/state'})
    _reset_env_and_cache()
    try:
        assert pc.get_state_dir() == '/from/config/state', \
            f'expected /from/config/state (config win), got {pc.get_state_dir()}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_auto_detect_default_when_no_env_no_config():
    """neither env nor config set → auto-detect (cwd-based)."""
    _reset_env_and_cache({'B5_PROJECT_ROOT': '/test/proj'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        sd = pc.get_state_dir()
        expected = '/test/proj/.claude/tellonce-state/runtime'
        assert sd == expected, f'expected {expected}, got {sd}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_memory_dir_cwd_escape():
    """memory_dir should follow the Claude Code escape rule: cwd.replace('/', '-')."""
    _reset_env_and_cache({'B5_PROJECT_ROOT': '/foo/bar/baz'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        md = pc.get_memory_dir()
        expected = os.path.expanduser('~/.claude/projects/-foo-bar-baz/memory')
        assert md == expected, f'expected {expected}, got {md}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_corrupt_config_not_crash():
    """config is invalid JSON → return empty dict, do not crash."""
    pc.CONFIG_PATH = os.path.join(tempfile.gettempdir(), 'test_corrupt_pt.config.json')
    with open(pc.CONFIG_PATH, 'w') as f:
        f.write('{ this is invalid json ]]]')
    pc._read_config_file.cache_clear()
    try:
        cfg = pc._read_config_file()
        assert cfg == {}, f'expected {{}}, got {cfg}'
    finally:
        try:
            os.remove(pc.CONFIG_PATH)
        except FileNotFoundError:
            pass
        _reset_config()
    return True


def test_missing_config_not_crash():
    """config file does not exist → return empty dict, do not crash."""
    pc.CONFIG_PATH = '/nonexistent/path/never_exists.json'
    pc._read_config_file.cache_clear()
    try:
        cfg = pc._read_config_file()
        assert cfg == {}, f'expected {{}}, got {cfg}'
    finally:
        _reset_config()
    return True


def test_compliance_log_path_built_from_obs_log_dir():
    """get_compliance_log_path should be obs_log_dir/compliance_log.jsonl."""
    _reset_env_and_cache({'B5_OBS_LOG_DIR': '/test/obs'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        clp = pc.get_compliance_log_path()
        assert clp == '/test/obs/compliance_log.jsonl', f'got {clp}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_shadow_log_path_built_from_state_dir():
    """get_shadow_log_path should be state_dir/b5_shadow_alerts/b5_shadow_log.jsonl."""
    _reset_env_and_cache({'B5_STATE_DIR': '/test/state'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        slp = pc.get_shadow_log_path()
        assert slp == '/test/state/b5_shadow_alerts/b5_shadow_log.jsonl', f'got {slp}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_ensure_dirs_idempotent():
    """ensure_dirs should not crash on re-run, and creates all state subdirs."""
    tmp = tempfile.mkdtemp()
    _reset_env_and_cache({
        'B5_STATE_DIR': os.path.join(tmp, 'state'),
        'B5_OBS_LOG_DIR': os.path.join(tmp, 'obs'),
        'B5_PROJECT_ROOT': tmp,
        'B5_MEMORY_DIR': os.path.join(tmp, 'mem'),
    })
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        pc.ensure_dirs()
        # re-run does not break
        pc.ensure_dirs()
        # verify all key subdirs were created
        for subdir in ['state', 'obs', 'mem',
                        os.path.join('state', 'b5_cost'),
                        os.path.join('state', 'b5_deterministic_streak'),
                        os.path.join('state', 'b5_shadow_alerts')]:
            full = os.path.join(tmp, subdir)
            assert os.path.isdir(full), f'expected dir {full}'
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        _reset_env_and_cache()
        _reset_config()
    return True


def test_real_user_config_honored():
    """If ~/.tellonce.config.json exists with state_dir set, get_state_dir
    must reflect that value (env > config > default precedence)."""
    _reset_env_and_cache()
    pc._read_config_file.cache_clear()
    real_config = os.path.expanduser('~/.tellonce.config.json')
    if not os.path.exists(real_config):
        # No user config installed → test is a no-op on fresh installs.
        return True
    try:
        with open(real_config) as f:
            cfg = json.load(f)
        configured = cfg.get('state_dir')
        if not configured:
            return True  # config exists but didn't pin state_dir — nothing to verify
        sd = pc.get_state_dir()
        assert sd == configured, \
            f'real config state_dir not honored: expected {configured!r}, got {sd!r}'
    finally:
        _reset_env_and_cache()
    return True


# ---------------------------- Main ----------------------------

def main():
    tests = [
        ('env var state_dir highest priority', test_env_var_state_dir_highest_priority),
        ('config file second priority', test_config_file_second_priority),
        ('auto detect default when no env no config', test_auto_detect_default_when_no_env_no_config),
        ('memory_dir cwd escape', test_memory_dir_cwd_escape),
        ('corrupt config not crash', test_corrupt_config_not_crash),
        ('missing config not crash', test_missing_config_not_crash),
        ('compliance_log built from obs_log_dir', test_compliance_log_path_built_from_obs_log_dir),
        ('shadow_log built from state_dir', test_shadow_log_path_built_from_state_dir),
        ('ensure_dirs idempotent', test_ensure_dirs_idempotent),
        ('real user config (if any) honored end-to-end', test_real_user_config_honored),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            ok = fn()
            if ok:
                print(f'  PASS  {name}')
                passed += 1
            else:
                print(f'  FAIL  {name} (returned False)')
                failed.append(name)
        except AssertionError as e:
            print(f'  FAIL  {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  ERR   {name}: {type(e).__name__}: {e}')
            failed.append(name)
    print(f'\n{passed}/{len(tests)} PASS, {len(failed)} FAIL')
    if failed:
        print(f'Failed: {failed}')
        sys.exit(1)


if __name__ == '__main__':
    main()
