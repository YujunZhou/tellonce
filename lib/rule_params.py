#!/usr/bin/env python3
"""Simple adaptive thresholds — read a rule's frontmatter `params:` block, fall back to code defaults.

Editing the memory `.md` frontmatter changes the threshold; no hook restart needed.

Example:
```yaml
---
atomic_id: <domain>-<kind>-NNN
params:
  some_threshold: 0.55   # overrides the code default for this rule
  min_length: 80
---
```

When a rule file has no `params:` block, the caller gets the default value.

This module reads rules from `path_config.get_memory_dir()`. Thresholds change
only when the user edits frontmatter; they are never changed autonomously.
"""
import os
import re
import sys
from functools import lru_cache

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config


@lru_cache(maxsize=64)
def read_rule_params(atomic_id):
    """Read a rule .md's frontmatter `params:` block, return a dict. Missing / parse failure → {}."""
    memory_dir = path_config.get_memory_dir()
    if not os.path.isdir(memory_dir):
        return {}
    try:
        for fname in os.listdir(memory_dir):
            if not fname.endswith('.md'):
                continue
            path = os.path.join(memory_dir, fname)
            try:
                with open(path, errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            if not re.search(r'^atomic_id:\s*' + re.escape(atomic_id) + r'\s*$',
                             content, re.MULTILINE):
                continue
            return _parse_params_block(content)
    except Exception:
        pass
    return {}


def _parse_params_block(content):
    """Extract the `params:` block from a markdown file (with frontmatter `--- ... ---`).

    Hand-written minimal parser (no PyYAML dependency). Only supports simple key: value
    pairs (int / float / string).
    """
    parts = re.split(r'^---\s*$', content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        return {}
    frontmatter = parts[1]

    in_params = False
    out = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line:
            if in_params:
                continue
            continue

        if not line.startswith(' ') and not line.startswith('\t'):
            if in_params:
                break
            if line.startswith('params:'):
                in_params = True
                continue
            else:
                continue

        if in_params:
            m = re.match(r'^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)(?:\s*#.*)?$', line)
            if not m:
                continue
            key = m.group(1)
            raw_val = m.group(2).strip()
            if (raw_val.startswith('"') and raw_val.endswith('"')) or \
               (raw_val.startswith("'") and raw_val.endswith("'")):
                raw_val = raw_val[1:-1]
            try:
                if '.' in raw_val:
                    out[key] = float(raw_val)
                else:
                    out[key] = int(raw_val)
            except ValueError:
                out[key] = raw_val
    return out


def get_param(atomic_id, key, default):
    """Convenience function: read rule[key], return default if unset."""
    params = read_rule_params(atomic_id)
    if key not in params:
        return default
    return params[key]


def _clear_cache():
    """test only — reset lru_cache after frontmatter changes."""
    try:
        read_rule_params.cache_clear()
    except AttributeError:
        pass


if __name__ == '__main__':
    """Debug: list params for each rule discovered in the memory dir."""
    memory_dir = path_config.get_memory_dir()
    found = False
    if os.path.isdir(memory_dir):
        for fname in sorted(os.listdir(memory_dir)):
            if not fname.endswith('.md'):
                continue
            try:
                with open(os.path.join(memory_dir, fname), errors='ignore') as f:
                    m = re.search(r'^atomic_id:\s*(\S+)\s*$', f.read(), re.MULTILINE)
            except Exception:
                continue
            if not m:
                continue
            rid = m.group(1)
            found = True
            print(rid + ': ' + str(read_rule_params(rid)))
    if not found:
        print('no rules with atomic_id found in ' + memory_dir)
