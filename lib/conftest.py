"""Pytest config: autouse cleanup for the chaos suite.

Background: test_chaos_fault_injection.py mutates B5_STATE_DIR / B5_MEMORY_DIR
env vars and rmtrees the tmp dirs at end. Modules like verify_retry_shadow
snapshot path_config at import time, so a stale env from one chaos test poisons
later tests in the same pytest run (most visibly, test_verify_retry_shadow
losing MEMORY_DIR -> no_rules_loaded -> judge_error). This fixture runs after
every chaos test to drop the env overrides and reload the affected modules
so downstream test files start from a clean slate.
"""
import os
import sys

import pytest


_CHAOS_ENV_KEYS = ('B5_STATE_DIR', 'B5_OBS_LOG_DIR', 'B5_PROJECT_ROOT', 'B5_MEMORY_DIR')
_PATH_SNAPSHOTTING_MODULES = ('verify_retry_shadow', 'verify_compliance', 'deterministic_block')


@pytest.fixture(autouse=True)
def _restore_chaos_env(request):
    yield
    if 'test_chaos_fault_injection' not in str(request.fspath):
        return
    for k in _CHAOS_ENV_KEYS:
        os.environ.pop(k, None)
    import importlib
    if 'path_config' in sys.modules:
        try:
            sys.modules['path_config']._clear_cache()
        except Exception:
            pass
    for mod_name in _PATH_SNAPSHOTTING_MODULES:
        if mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[mod_name])
            except Exception:
                pass
