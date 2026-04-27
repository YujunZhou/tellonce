#!/usr/bin/env python3
"""Tier B item 4 — auto-retire mechanical superseded memory files.

Logic (per round 3 §B.4 ownership safety, mechanical only):
  1. Scan memory dir for files with `superseded_by: <id>` in frontmatter
  2. Verify the pair file (with matching atomic_id) exists in memory dir
  3. Rename file: `<old_name>.md` → `_archived_<old_name>.md` (prefix convention)
  4. Log the action to retire_log.jsonl

Skip if:
  - File already starts with `_archived_` prefix (already archived)
  - Pair file does not exist (broken supersede chain — needs human review)
  - superseded_by is `[]` or empty

Run as standalone script (not hook). Run once per session manually.

Usage:
  python3 auto_retire_superseded.py [--dry-run]
"""
import os
import re
import sys
import glob
import json
from datetime import datetime, timezone

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

MEMORY_DIR = path_config.get_memory_dir()
RETIRE_LOG = path_config.get_retire_log_path()


def parse_frontmatter(path):
    """Return dict of frontmatter fields."""
    out = {}
    try:
        with open(path, errors='ignore') as f:
            content = f.read()
    except Exception:
        return out
    parts = re.split(r'^---\s*$', content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        return out
    fm = parts[1]
    for line in fm.split('\n'):
        m = re.match(r'^([a-zA-Z_]+):\s*(.*)$', line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def find_pair_atomic_id(target_id):
    """Return path of file with matching atomic_id, or None."""
    for path in glob.glob(os.path.join(MEMORY_DIR, '*.md')):
        if os.path.basename(path).startswith('_archived_'):
            continue
        fm = parse_frontmatter(path)
        if fm.get('atomic_id') == target_id:
            return path
    return None


def main():
    dry_run = '--dry-run' in sys.argv
    actions = []

    for path in sorted(glob.glob(os.path.join(MEMORY_DIR, '*.md'))):
        fname = os.path.basename(path)
        if fname == 'MEMORY.md' or fname.startswith('_archived_'):
            continue

        fm = parse_frontmatter(path)
        sup_id = fm.get('superseded_by', '').strip()
        if not sup_id or sup_id in ('[]', 'null', 'None'):
            continue
        # Strip quotes if present
        sup_id = sup_id.strip('"').strip("'").strip()
        if not re.match(r'^[a-z]+-[a-z]+-\d+$', sup_id):
            # malformed atomic_id; skip
            continue

        pair_path = find_pair_atomic_id(sup_id)
        if not pair_path:
            actions.append({
                'file': fname,
                'action': 'SKIP',
                'reason': f'pair atomic_id={sup_id} not found',
                'atomic_id': fm.get('atomic_id', ''),
            })
            continue

        new_path = os.path.join(MEMORY_DIR, f'_archived_{fname}')
        if os.path.exists(new_path):
            actions.append({
                'file': fname,
                'action': 'SKIP',
                'reason': f'_archived_{fname} already exists',
                'atomic_id': fm.get('atomic_id', ''),
            })
            continue

        actions.append({
            'file': fname,
            'action': 'ARCHIVE' if not dry_run else 'WOULD_ARCHIVE',
            'old_path': path,
            'new_path': new_path,
            'atomic_id': fm.get('atomic_id', ''),
            'superseded_by': sup_id,
        })

        if not dry_run:
            os.rename(path, new_path)

    # Write log
    if not dry_run and actions:
        os.makedirs(os.path.dirname(RETIRE_LOG), exist_ok=True)
        try:
            with open(RETIRE_LOG, 'a') as f:
                ts = datetime.now(timezone.utc).isoformat()
                for a in actions:
                    a['timestamp'] = ts
                    f.write(json.dumps(a, ensure_ascii=False) + '\n')
        except Exception:
            pass

    # Summary print
    archive_count = sum(1 for a in actions if a['action'] in ('ARCHIVE', 'WOULD_ARCHIVE'))
    skip_count = sum(1 for a in actions if a['action'] == 'SKIP')
    print(f'\nauto_retire_superseded: {archive_count} archived, {skip_count} skipped')
    for a in actions:
        if a['action'] == 'SKIP':
            print(f'  SKIP {a["file"]}: {a["reason"]}')
        else:
            print(f'  {a["action"]} {a["file"]} (atomic_id={a["atomic_id"]}, superseded_by={a.get("superseded_by", "?")})')

    if dry_run:
        print('\n(--dry-run; no changes made)')


if __name__ == '__main__':
    main()
