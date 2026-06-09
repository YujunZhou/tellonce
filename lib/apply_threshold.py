#!/usr/bin/env python3
"""CLI: apply threshold_advisor suggestions to memory rule frontmatter.

Changes are applied only when the user approves, never silently. The CLI
accepts --rule / --param / --value, makes a versioned backup of the original file,
does an atomic write (tmp + rename), and edits only the given key in the `params:` block.

Usage:
  python3 apply_threshold.py --rule <domain>-<kind>-NNN --param some_threshold --value 0.55
  python3 apply_threshold.py --snooze <domain>-<kind>-NNN --days 7
  python3 apply_threshold.py --list

Writes a versioned backup `<rule>.pre_threshold_<TS>.bak`; never overwrites in place.
Paths are derived via path_config (no hardcoded locations).
"""
import argparse
import datetime
import os
import re
import shutil
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config
import rule_params


def find_rule_file(atomic_id: str):
    """Locate memory .md whose frontmatter has `atomic_id: <atomic_id>`."""
    memory_dir = path_config.get_memory_dir()
    if not os.path.isdir(memory_dir):
        return None
    needle_a = f'atomic_id: {atomic_id}\n'
    needle_b = f'atomic_id: {atomic_id}\r\n'
    for fname in sorted(os.listdir(memory_dir)):
        if not fname.endswith('.md'):
            continue
        path = os.path.join(memory_dir, fname)
        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except OSError:
            continue
        if needle_a in content or needle_b in content:
            return path
    return None


def _versioned_backup(file_path: str) -> str:
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = f'{file_path}.pre_threshold_{ts}.bak'
    shutil.copy2(file_path, backup)
    return backup


def update_param(file_path: str, param: str, value) -> str:
    """Update single param in `params:` frontmatter block. Returns backup path.

    Raises ValueError if no frontmatter / no params block / param not found.
    """
    with open(file_path, encoding='utf-8') as f:
        content = f.read()

    parts = re.split(r'^---\s*$', content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise ValueError(f'No frontmatter found in {file_path}')

    fm = parts[1]
    new_fm_lines = []
    in_params = False
    found = False
    for line in fm.splitlines():
        if not line:
            new_fm_lines.append(line)
            continue
        if not (line.startswith(' ') or line.startswith('\t')):
            in_params = line.startswith('params:')
            new_fm_lines.append(line)
            continue
        if in_params:
            match = re.match(
                r'^(\s+)([A-Za-z_][A-Za-z0-9_]*)(\s*:\s*)(.+?)(\s*#.*)?$', line
            )
            if match and match.group(2) == param:
                indent, key, sep, _, comment = match.groups()
                comment = comment or ''
                new_fm_lines.append(f'{indent}{key}{sep}{value}{comment}')
                found = True
                continue
        new_fm_lines.append(line)

    if not found:
        raise ValueError(f'param `{param}` not found under `params:` block in {file_path}')

    backup = _versioned_backup(file_path)
    new_fm = '\n'.join(new_fm_lines)
    new_content = parts[0] + '---\n' + new_fm.lstrip('\n')
    if not new_content.endswith('\n'):
        new_content += '\n'
    new_content += '---\n' + parts[2].lstrip('\n')

    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    os.replace(tmp_path, file_path)

    rule_params._clear_cache()
    return backup


def write_snooze(atomic_id: str, days: int = 7) -> str:
    """Mark a rule's suggestion snoozed; advisor checks this file before re-suggesting."""
    target_dir = path_config.get_b5_alerts_threshold_dir()
    os.makedirs(target_dir, exist_ok=True)
    until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    snooze_path = os.path.join(target_dir, '.snoozed.txt')
    existing = []
    if os.path.exists(snooze_path):
        with open(snooze_path, encoding='utf-8') as f:
            existing = [line.strip() for line in f if line.strip()]
    existing = [line for line in existing if not line.startswith(f'{atomic_id}\t')]
    existing.append(f'{atomic_id}\t{until}')
    with open(snooze_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(existing) + '\n')
    return snooze_path


def list_current_params() -> int:
    """Print current params for all rules with `params:` block. Returns count."""
    memory_dir = path_config.get_memory_dir()
    if not os.path.isdir(memory_dir):
        print(f'memory dir not found: {memory_dir}', file=sys.stderr)
        return 0
    count = 0
    for fname in sorted(os.listdir(memory_dir)):
        if not fname.endswith('.md'):
            continue
        path = os.path.join(memory_dir, fname)
        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except OSError:
            continue
        match = re.search(r'^atomic_id:\s*(\S+)', content, re.MULTILINE)
        if not match:
            continue
        atomic_id = match.group(1)
        params = rule_params.read_rule_params(atomic_id)
        if not params:
            continue
        print(f'{atomic_id} ({fname}):')
        for key, value in params.items():
            print(f'  {key}: {value}')
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description='Apply threshold suggestions to memory rule frontmatter.')
    parser.add_argument('--rule', help='atomic_id of rule to update')
    parser.add_argument('--param', help='param key in params: block')
    parser.add_argument('--value', help='new value (int/float/string)')
    parser.add_argument('--snooze', help='snooze suggestions for given rule', metavar='ATOMIC_ID')
    parser.add_argument('--days', type=int, default=7, help='snooze duration in days (default 7)')
    parser.add_argument('--list', action='store_true', help='list current params for all rules')
    args = parser.parse_args()

    if args.list:
        n = list_current_params()
        print(f'\n{n} rule(s) with params: block')
        return 0

    if args.snooze:
        snooze_path = write_snooze(args.snooze, args.days)
        print(f'Snoozed {args.snooze} for {args.days} days; tracked in {snooze_path}')
        return 0

    if not (args.rule and args.param and args.value):
        parser.error('--rule, --param, and --value are required (or use --list / --snooze)')

    rule_path = find_rule_file(args.rule)
    if not rule_path:
        print(f'ERROR: rule {args.rule} not found in {path_config.get_memory_dir()}', file=sys.stderr)
        return 1

    raw_value = args.value
    try:
        if '.' in raw_value:
            typed_value = float(raw_value)
        else:
            typed_value = int(raw_value)
    except ValueError:
        typed_value = raw_value

    try:
        backup = update_param(rule_path, args.param, typed_value)
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

    print(f'OK: {args.rule} {args.param} → {typed_value}')
    print(f'Backup: {backup}')
    print('Note: lru_cache is process-level; new value applies to fresh sessions only.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
