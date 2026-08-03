#!/usr/bin/env python3
"""dashboard — one-glance status of the tellonce install.

Read-only: never writes, never blocks, never crashes. Mirrors the codex
variant's `build_dashboard` (mode / hooks / blocking / counts) but reports the
Copilot-specific facts: effective mode (from path_config, the same source the
hooks read), whether the plugin is registered in Copilot's config (so hooks
actually load), and counts of rules / memory files / observations / pending
promotions / compliance entries.

Usage:
    python <plugin>/lib/dashboard.py
"""
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _LIB_DIR)

import path_config  # single source of truth

try:
    path_config.force_utf8_io()
except Exception:
    pass


def _count_lines(path):
    """Non-empty line count of a file, or 0 if missing/unreadable."""
    try:
        if not os.path.exists(path):
            return 0
        n = 0
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                if line.strip():
                    n += 1
        return n
    except Exception:
        return None  # unreadable — distinguish from 0


def _mode_label():
    try:
        enforce = path_config.enforcement_enabled()
    except Exception:
        enforce = False
    try:
        shadow = path_config.shadow_enabled()
    except Exception:
        shadow = False
    if enforce and shadow:
        return 'full     (hard block + LLM judge)', enforce, shadow
    if enforce:
        return 'enforce  (hard block, no LLM judge)', enforce, shadow
    if shadow:
        return 'shadow   (LLM judge, no hard block)', enforce, shadow
    return 'observe  (no hard block or shadow judge)', enforce, shadow


def _memory_upsert_enabled():
    value = os.environ.get(
        'PT_MEMORY_UPSERT_ENABLED',
        os.environ.get('B5_MEMORY_UPSERT_ENABLED'),
    )
    if value is not None:
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}
    try:
        with open(path_config.CONFIG_PATH, encoding='utf-8-sig') as f:
            return bool(json.load(f).get('memory_upsert_enabled', False))
    except Exception:
        return False


def _registered():
    """True/False if plugin is in Copilot config installedPlugins; None if unknown."""
    try:
        import register_plugin
        _header, data = register_plugin._load()
        return register_plugin._is_registered(data)
    except Exception:
        return None


def _rule_counts():
    """(fingerprint_rules, memory_only_rules, memory_md_files)."""
    fp = memory_only = md_files = None
    try:
        import retrieve_inject
        fps = retrieve_inject._load_fingerprints() or {}
        fp = len(fps)
        try:
            idx = retrieve_inject._build_index() or {}
            memory_only = len([k for k in idx if k not in fps])
        except Exception:
            memory_only = None
    except Exception:
        fp = None
    try:
        mem_dir = path_config.get_memory_dir()
        md_files = len([p for p in glob.glob(os.path.join(mem_dir, '*.md'))
                        if os.path.basename(p) != 'MEMORY.md'])
    except Exception:
        md_files = None
    return fp, memory_only, md_files


def _upsert_counts():
    db_path = os.path.join(path_config.get_memory_dir(), '.tellonce.sqlite3')
    if not os.path.isfile(db_path):
        return {}
    try:
        with tempfile.TemporaryDirectory(prefix='tellonce-dashboard-') as td:
            snapshot = os.path.join(td, '.tellonce.sqlite3')
            shutil.copy2(db_path, snapshot)
            for suffix in ('-wal', '-shm'):
                sidecar = db_path + suffix
                if os.path.isfile(sidecar):
                    shutil.copy2(sidecar, snapshot + suffix)
            conn = sqlite3.connect(snapshot)
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM turns GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
            return {str(status): int(count) for status, count in rows}
    except Exception:
        return {}


def _fmt(v):
    return '?' if v is None else str(v)


def build_dashboard():
    mode, enforce, shadow = _mode_label()
    upsert = _memory_upsert_enabled()
    reg = _registered()
    fp, memory_only, md_files = _rule_counts()
    obs = _count_lines(path_config.get_observations_log_path()) if hasattr(
        path_config, 'get_observations_log_path') else None
    compliance = _count_lines(path_config.get_compliance_log_path()) if hasattr(
        path_config, 'get_compliance_log_path') else None
    upsert_counts = _upsert_counts()

    if reg is True:
        reg_label = 'yes (Copilot will load the hooks)'
    elif reg is False:
        reg_label = 'NO — not registered; hooks will not fire! run register_plugin.py or reinstall'
    else:
        reg_label = '? (could not read ~/.copilot/config.json)'

    lines = [
        '═══ tellonce dashboard ═══',
        f'mode:          {mode}',
        f'  enforce:     {enforce}',
        f'  shadow:      {shadow}',
        f'  upsert:      {upsert} (background LLM judge)',
        f'registered:    {reg_label}',
        f'rules:         {_fmt(fp)} (fingerprints) + {_fmt(memory_only)} (memory-only)',
        f'memory files:  {_fmt(md_files)} (.md)',
        f'observations:  {_fmt(obs)} logged',
        f'upsert turns:  pending={upsert_counts.get("pending", 0)}, '
        f'resolving={upsert_counts.get("resolving", 0)}, '
        f'needs_user={upsert_counts.get("needs_user", 0)}, '
        f'failed={upsert_counts.get("failed", 0)}',
        f'compliance:    {_fmt(compliance)} log entries',
        f'config:        {path_config.CONFIG_PATH}',
        f'memory dir:    {path_config.get_memory_dir()}',
    ]
    return '\n'.join(lines)


def main():
    try:
        print(build_dashboard())
        return 0
    except Exception as e:
        sys.stderr.write(f'dashboard error: {e}\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
