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
import sys

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
    return 'observe  (safe default: record + remind only; no block, no LLM)', enforce, shadow


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


def _pending_count():
    try:
        path = path_config.get_pending_queue_path()
        if not os.path.exists(path):
            return 0
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            txt = f.read().strip()
        if not txt:
            return 0
        # queue may be a JSON array or JSONL — handle both
        try:
            obj = json.loads(txt)
            if isinstance(obj, list):
                return len(obj)
            return 1
        except Exception:
            return len([l for l in txt.splitlines() if l.strip()])
    except Exception:
        return None


def _fmt(v):
    return '?' if v is None else str(v)


def build_dashboard():
    mode, enforce, shadow = _mode_label()
    reg = _registered()
    fp, memory_only, md_files = _rule_counts()
    obs = _count_lines(path_config.get_observations_log_path()) if hasattr(
        path_config, 'get_observations_log_path') else None
    compliance = _count_lines(path_config.get_compliance_log_path()) if hasattr(
        path_config, 'get_compliance_log_path') else None
    pending = _pending_count()

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
        f'registered:    {reg_label}',
        f'rules:         {_fmt(fp)} (fingerprints) + {_fmt(memory_only)} (memory-only)',
        f'memory files:  {_fmt(md_files)} (.md)',
        f'observations:  {_fmt(obs)} logged',
        f'pending:       {_fmt(pending)} queued promotions',
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
