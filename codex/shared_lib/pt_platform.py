#!/usr/bin/env python3
"""Platform-specific values for the Codex Tellonce variant."""
import os

STATE_DIR_NAME = '.codex'
CLI_COMMAND = 'codex'
PREFER_MODEL_DEFAULT = ''
RETRIEVE_CLI_DEFAULT = 'codex'
JUDGE_MODEL_DEFAULT = ''
INJECT_CADENCE = 'turns'


def _prefer_existing(new_path: str, legacy_path: str) -> str:
    if os.path.exists(legacy_path) and not os.path.exists(new_path):
        return legacy_path
    return new_path


def default_state_dir(project_root: str) -> str:
    return _prefer_existing(
        os.path.join(project_root, '.codex', 'tellonce-state', 'runtime'),
        os.path.join(project_root, '.claude', 'tellonce-state', 'runtime'),
    )


def default_obs_log_dir(project_root: str) -> str:
    return _prefer_existing(
        os.path.join(project_root, '.codex', 'tellonce-state', 'obs_log'),
        os.path.join(project_root, '.claude', 'tellonce-state', 'obs_log'),
    )


def default_memory_dir(project_root: str) -> str:
    return os.path.join(project_root, '.tellonce', 'memory')


def stop_block_exit_code() -> int:
    value = os.environ.get('PT_STOP_BLOCK_EXIT')
    return int(value) if value and value.strip().isdigit() else 0


def is_child_session() -> bool:
    return os.environ.get('PT_CHILD_SESSION', '').strip().lower() in (
        '1', 'true', 'yes', 'on'
    )
