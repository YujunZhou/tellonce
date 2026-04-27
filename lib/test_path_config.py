#!/usr/bin/env python3
"""Tests for path_config.py — 路径解耦中央 (Phase 4.1).

Run: python3 test_path_config.py
Expects: 12/12 PASS.

测试覆盖三层 detect:
  1. Env var (B5_*) 最高优先
  2. ~/.preference-tracker.config.json 第二优先
  3. 自动 detect 兜底
+ escape 规则, ensure_dirs 幂等, corrupt config 不 crash 等.
"""
import json
import os
import sys
import tempfile

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config as pc


def _reset_env_and_cache(env_vars: dict = None):
    """test fixture: 清掉 env, 设新 env, 重置 cache."""
    for v in ['B5_STATE_DIR', 'B5_MEMORY_DIR', 'B5_OBS_LOG_DIR', 'B5_PROJECT_ROOT',
              'B5_WHITELIST_USER']:
        os.environ.pop(v, None)
    if env_vars:
        for k, v in env_vars.items():
            os.environ[k] = v
    pc._clear_cache()


def _write_config(d: dict):
    """test only: 写 ~/.preference-tracker.config.json. caller must restore via _reset_config()."""
    pc.CONFIG_PATH = os.path.join(tempfile.gettempdir(), 'test_preference-tracker.config.json')
    with open(pc.CONFIG_PATH, 'w') as f:
        json.dump(d, f)
    pc._read_config_file.cache_clear()


def _reset_config():
    """restore CONFIG_PATH to default."""
    if pc.CONFIG_PATH != os.path.expanduser('~/.preference-tracker.config.json'):
        try:
            os.remove(pc.CONFIG_PATH)
        except FileNotFoundError:
            pass
    pc.CONFIG_PATH = os.path.expanduser('~/.preference-tracker.config.json')
    pc._read_config_file.cache_clear()


# ---------------------------- Tests ----------------------------

def test_env_var_state_dir_highest_priority():
    """B5_STATE_DIR env var 应覆盖 config 跟 default."""
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
    """config file 应优先于 default (env 不 set 时)."""
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
    """env 跟 config 都没 → auto-detect (cwd-based)."""
    _reset_env_and_cache({'B5_PROJECT_ROOT': '/test/proj'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        sd = pc.get_state_dir()
        expected = '/test/proj/.claude/preference-tracker-state/runtime'
        assert sd == expected, f'expected {expected}, got {sd}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_memory_dir_cwd_escape():
    """memory_dir 应按 Claude Code escape 规则: cwd.replace('/', '-')."""
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
    """config 是非法 JSON → 返空 dict, 不 crash."""
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
    """config 文件不存在 → 返空 dict, 不 crash."""
    pc.CONFIG_PATH = '/nonexistent/path/never_exists.json'
    pc._read_config_file.cache_clear()
    try:
        cfg = pc._read_config_file()
        assert cfg == {}, f'expected {{}}, got {cfg}'
    finally:
        _reset_config()
    return True


def test_compliance_log_path_built_from_obs_log_dir():
    """get_compliance_log_path 应是 obs_log_dir/compliance_log.jsonl."""
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
    """get_shadow_log_path 应是 state_dir/b5_shadow_alerts/b5_shadow_log.jsonl."""
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


def test_whitelist_paths_returns_two():
    """get_whitelist_paths 返 [base, user] 长度 2."""
    _reset_env_and_cache()
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        paths = pc.get_whitelist_paths()
        assert isinstance(paths, list) and len(paths) == 2, f'got {paths}'
        assert paths[0].endswith('deterministic_block_whitelist.txt'), f'base path wrong: {paths[0]}'
        assert paths[1].endswith('deterministic_block_whitelist_user.txt'), f'user path wrong: {paths[1]}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_whitelist_user_env_override():
    """B5_WHITELIST_USER env 应覆盖 default user whitelist path."""
    _reset_env_and_cache({'B5_WHITELIST_USER': '/custom/my_whitelist.txt'})
    pc.CONFIG_PATH = '/nonexistent/.config'
    pc._read_config_file.cache_clear()
    try:
        paths = pc.get_whitelist_paths()
        assert paths[1] == '/custom/my_whitelist.txt', f'expected override, got {paths[1]}'
    finally:
        _reset_env_and_cache()
        _reset_config()
    return True


def test_ensure_dirs_idempotent():
    """ensure_dirs 重跑应不 crash, 创全 state subdirs."""
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
        # 重跑不破
        pc.ensure_dirs()
        # 验关键 subdirs 都创了
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


def test_yzhou25_backward_compat_via_real_config():
    """yzhou25 的 ~/.preference-tracker.config.json 应让 state_dir 指向 example-research-project/state/runtime."""
    # 不 mock CONFIG_PATH, 用真 yzhou25 config
    _reset_env_and_cache()
    pc._read_config_file.cache_clear()
    real_config = os.path.expanduser('~/.preference-tracker.config.json')
    if not os.path.exists(real_config):
        # 没 yzhou25 config 跑这 test 没意义 (同学环境)
        return True
    try:
        sd = pc.get_state_dir()
        assert 'example-research-project/state/runtime' in sd, \
            f'expected backward compat (含 example-research-project), got {sd}'
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
        ('whitelist_paths returns two', test_whitelist_paths_returns_two),
        ('whitelist_user env override', test_whitelist_user_env_override),
        ('ensure_dirs idempotent', test_ensure_dirs_idempotent),
        ('yzhou25 backward compat via real config', test_yzhou25_backward_compat_via_real_config),
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
