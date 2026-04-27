#!/usr/bin/env python3
"""Phase 7 简易自适应阈值 — 读规则 frontmatter 的 `params:` 块, 没设走代码默认值.

用户改 memory `.md` frontmatter 等于改阈值, 不需要重启 hook.

例:
```yaml
---
atomic_id: lang-pit-130
params:
  chinese_ratio_threshold: 0.55   # 默认 0.7, 同学项目混杂多可调低
  min_length: 80                  # 默认 50
---
```

规则文件没写 `params:` 块时, 调用方拿默认值.

Per `code-pref-287` 路径解耦; 此模块用 `path_config.get_memory_dir()`.
Per `wf-pref-292` 自适应阈值机制 (用户拍板, 不私自改).
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
    """读规则 .md 的 frontmatter `params:` 块, 返字典. 不存在 / 解析失败 → {}."""
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
            if 'atomic_id: ' + atomic_id not in content:
                continue
            return _parse_params_block(content)
    except Exception:
        pass
    return {}


def _parse_params_block(content):
    """从 markdown 文件 (含 frontmatter `--- ... ---`) 取 `params:` 块.

    手写最小解析器 (不依赖 PyYAML). 仅支持简单 key: value 对 (int / float / 字符串).
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
    """便捷函数: 读 rule[key], 返 default 若没设."""
    params = read_rule_params(atomic_id)
    if key not in params:
        return default
    return params[key]


def _clear_cache():
    """test only — reset lru_cache after frontmatter 改动."""
    try:
        read_rule_params.cache_clear()
    except AttributeError:
        pass


if __name__ == '__main__':
    """Debug: 列 3 个 enforce 规则当前 params."""
    for rid in ['lang-pit-130', 'lang-pref-001', 'oth-pref-001']:
        p = read_rule_params(rid)
        print(rid + ': ' + str(p))
