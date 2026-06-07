#!/usr/bin/env python3
"""Install helper — merge preference-tracker hooks into <project>/.claude/settings.local.json.

Used by install.sh / uninstall.sh / doctor.sh.

Modes:
  --add: add hooks to settings (idempotent, additive, does not remove existing)
  --remove: remove preference-tracker hooks from settings (uninstall)
  --verify: list registered hooks (doctor)

Per `wf-pref-027`: versioned backup — cp settings.local.json.v3_pre_pt_<ts>.json before editing.
Per `code-pref-291`: Python merge (not jq) — portable, no module-load dependency.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime


# Hooks definition (name → (event, timeout, desc)).
# H15 fix (2026-05-01): order matches README architecture diagram. Claude Code
# Stop hooks run sequentially; if an earlier hook returns exit 2, later ones
# don't run. The README declared:
#
#   Stop chain: check-observation-log → deterministic-block → verify-compliance
#               → shadow-judge → pending-promote
#   UserPromptSubmit: retrieve-inject → pending-inject → shadow-alert-inject
#
# Python 3.7+ dict preserves insertion order, so this dict literally drives the
# settings.local.json registration order.
PT_HOOKS = {
    # ── Stop chain (executed top to bottom) ─────────────────────────────
    'check-observation-log.sh': {
        'event': 'Stop',
        'timeout': 10,
        'desc': 'Iron Law: append-only obs log gate',
    },
    'memory-deterministic-block.sh': {
        'event': 'Stop',
        'timeout': 10,
        'desc': 'Phase B5 Tier A item 1: deterministic regex hard-block',
    },
    'memory-verify-compliance.sh': {
        'event': 'Stop',
        'timeout': 5,
        'desc': 'Phase B3 lite + B4: compliance log + pending-finalize gate',
    },
    'memory-shadow-judge.sh': {
        'event': 'Stop',
        'timeout': 30,
        'desc': 'Phase B5 Tier A item 2: shadow LLM judge (log-only)',
    },
    'memory-pending-promote.sh': {
        'event': 'Stop',
        'timeout': 5,
        'desc': 'Phase A: pending observation → queue',
    },
    # ── UserPromptSubmit chain ──────────────────────────────────────────
    'memory-retrieve-inject.sh': {
        'event': 'UserPromptSubmit',
        'timeout': 5,
        'desc': 'Phase B1: deterministic fingerprint retrieve',
    },
    'memory-pending-inject.sh': {
        'event': 'UserPromptSubmit',
        'timeout': 5,
        'desc': 'Phase A: pending queue → next-turn inject',
    },
    'memory-shadow-alert-inject.sh': {
        'event': 'UserPromptSubmit',
        'timeout': 5,
        'desc': 'Phase B5 Tier A item 3: soft inject from shadow alert',
    },
}


def _versioned_backup(settings_path: str) -> str:
    """Cp settings.local.json → settings.local.json.v3_pre_pt_<ts>.json. Returns backup path."""
    if not os.path.exists(settings_path):
        return ''
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = f'{settings_path}.v3_pre_pt_{ts}.json'
    shutil.copy2(settings_path, backup)
    return backup


def _load_settings(settings_path: str) -> dict:
    """Load settings.local.json (empty file / missing → default dict)."""
    if not os.path.exists(settings_path):
        return {'permissions': {'allow': [], 'defaultMode': 'auto'}, 'hooks': {}}
    try:
        with open(settings_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f'❌ settings.local.json 非法 JSON: {e}', file=sys.stderr)
        sys.exit(1)


def _save_settings(settings_path: str, data: dict):
    """Write back with pretty format."""
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


def _hook_already_registered(settings: dict, event: str, command_path: str) -> bool:
    """Check whether a hook is already registered in settings.<event>[].hooks[]."""
    for entry in settings.get('hooks', {}).get(event, []):
        for h in entry.get('hooks', []):
            if h.get('command') == command_path:
                return True
    return False


def cmd_add(settings_path: str, hooks_dir: str):
    """Add PT hooks to settings.local.json (idempotent).

    The hook .sh files self-locate their skill lib via ${BASH_SOURCE[0]}, so
    hooks_dir may live anywhere (relocated clone, shared HOME, CI, container).
    The only correctness requirement is that the hook .sh files actually exist
    in hooks_dir — checked below.
    """
    missing = [h for h in PT_HOOKS if not os.path.isfile(os.path.join(hooks_dir, h))]
    if missing:
        print(
            f'⚠ {len(missing)} hooks .sh 文件不存在: '
            f'{", ".join(missing[:3])}{"..." if len(missing) > 3 else ""}',
            file=sys.stderr,
        )
        print('  注册成功但运行时 Claude Code harness 报 command not found.', file=sys.stderr)

    backup = _versioned_backup(settings_path)
    if backup:
        print(f'  versioned backup: {backup}')

    settings = _load_settings(settings_path)
    settings.setdefault('hooks', {})

    # H3 fix (2026-05-01): create a dedicated PT entry per event (no matcher,
    # so it always fires) instead of writing into chain[0]. Previously we
    # appended into the user's first entry, which means if the user's entry[0]
    # had a matcher (e.g. for PreToolUse semantics), our hooks could end up
    # scoped to that matcher. Stop / UserPromptSubmit don't take matchers in
    # current Claude Code spec, so the bug was latent — but a dedicated entry
    # is safer across Claude Code versions and easier to remove cleanly later.
    added = 0
    skipped = 0
    for hook_name, info in PT_HOOKS.items():
        cmd = os.path.join(hooks_dir, hook_name)
        event = info['event']
        timeout = info['timeout']
        if _hook_already_registered(settings, event, cmd):
            skipped += 1
            continue
        chain = settings['hooks'].setdefault(event, [])
        # Find OUR entry (one we previously created) by sentinel marker.
        # Fall back to first entry-without-matcher if any, else create new.
        pt_entry = None
        for entry in chain:
            if entry.get('_pt_managed'):
                pt_entry = entry
                break
        if pt_entry is None:
            pt_entry = {'_pt_managed': True, 'hooks': []}
            chain.append(pt_entry)
        pt_entry.setdefault('hooks', []).append({
            'type': 'command',
            'command': cmd,
            'timeout': timeout,
        })
        added += 1
    _save_settings(settings_path, settings)
    print(f'  added {added} hooks, skipped {skipped} (already registered)')


def cmd_remove(settings_path: str, hooks_dir: str):
    """Remove PT hooks from settings.local.json."""
    backup = _versioned_backup(settings_path)
    if backup:
        print(f'  versioned backup: {backup}')

    settings = _load_settings(settings_path)
    pt_commands = {os.path.join(hooks_dir, h) for h in PT_HOOKS}

    removed = 0
    for event, chain in settings.get('hooks', {}).items():
        new_chain = []
        for entry in chain:
            new_hooks = []
            for h in entry.get('hooks', []):
                if h.get('command') in pt_commands:
                    removed += 1
                else:
                    new_hooks.append(h)
            entry['hooks'] = new_hooks
            # H3 fix: drop entries we own (`_pt_managed`) once empty; keep
            # user-owned entries even if empty (user may want to re-fill them).
            if entry.get('_pt_managed') and not new_hooks:
                continue
            new_chain.append(entry)
        settings['hooks'][event] = new_chain
    _save_settings(settings_path, settings)
    print(f'  removed {removed} hooks')


def cmd_verify(settings_path: str, hooks_dir: str):
    """List PT hook registration status."""
    settings = _load_settings(settings_path)
    pt_commands = {os.path.join(hooks_dir, h): h for h in PT_HOOKS}

    found = {}
    for event, chain in settings.get('hooks', {}).items():
        for entry in chain:
            for h in entry.get('hooks', []):
                cmd = h.get('command', '')
                if cmd in pt_commands:
                    found[pt_commands[cmd]] = event

    print('Preference-tracker hooks 注册情况:')
    print(f'  settings: {settings_path}')
    print(f'  hooks dir: {hooks_dir}')
    print()
    missing = []
    for hook_name, info in PT_HOOKS.items():
        if hook_name in found:
            print(f'  ✓ {hook_name} → {info["event"]}')
        else:
            print(f'  ✗ {hook_name} → MISSING (expected {info["event"]})')
            missing.append(hook_name)
    if missing:
        print(f'\n❌ {len(missing)} hooks not registered. Run install.sh.')
        sys.exit(1)
    print(f'\n✅ All {len(PT_HOOKS)} hooks registered.')


def main():
    parser = argparse.ArgumentParser(description='Merge preference-tracker hooks to settings.local.json')
    parser.add_argument('--settings', required=True, help='Path to .claude/settings.local.json')
    parser.add_argument('--hooks-dir', required=True, help='Path to .claude/hooks/ (where .sh wrappers live)')
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--add', action='store_true', help='Add PT hooks (install)')
    g.add_argument('--remove', action='store_true', help='Remove PT hooks (uninstall)')
    g.add_argument('--verify', action='store_true', help='List PT hooks 注册情况 (doctor)')
    args = parser.parse_args()

    if args.add:
        cmd_add(args.settings, args.hooks_dir)
    elif args.remove:
        cmd_remove(args.settings, args.hooks_dir)
    elif args.verify:
        cmd_verify(args.settings, args.hooks_dir)


if __name__ == '__main__':
    main()
