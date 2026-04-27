#!/usr/bin/env python3
"""Preference-tracker memory retrieve + inject (Phase B1).

Reads UserPromptSubmit JSON from stdin, matches user prompt against fingerprints.yaml,
emits JSON with hookSpecificOutput.additionalContext naming the matched atomic_ids.

Non-destructive: any failure → exit 0 silently (no block).
"""
import json, sys, re, os, glob

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

FP_YAML = os.path.join(_LIB_DIR, 'fingerprints.yaml')
MEMORY_DIR = path_config.get_memory_dir()
MAX_SHOW = 10
PROMPT_TRUNCATE = 4000


_RULE_INDEX = None


def _build_index():
    """One-pass scan of memory dir → {atomic_id: (applies_when, condition)}."""
    global _RULE_INDEX
    if _RULE_INDEX is not None:
        return _RULE_INDEX
    idx = {}
    try:
        for path in glob.glob(os.path.join(MEMORY_DIR, '*.md')):
            if os.path.basename(path) == 'MEMORY.md':
                continue
            try:
                with open(path, errors='ignore') as f:
                    c = f.read()
            except Exception:
                continue
            m = re.search(r'atomic_id:\s*([a-z]+-[a-z]+-\d+)', c)
            if not m:
                continue
            aw = re.search(r'^applies_when:\s*(.+)$', c, re.MULTILINE)
            cond = re.search(r'^condition:\s*"?([^\n"]+)"?', c, re.MULTILINE)
            idx[m.group(1)] = (
                aw.group(1).strip() if aw else '',
                cond.group(1).strip() if cond else '',
            )
    except Exception:
        pass
    _RULE_INDEX = idx
    return idx


def read_rule_applicability(atomic_id):
    return _build_index().get(atomic_id, ('', ''))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        sys.exit(0)
    prompt_scan = prompt[:PROMPT_TRUNCATE].lower()

    try:
        import yaml
    except ImportError:
        sys.exit(0)

    if not os.path.exists(FP_YAML):
        sys.exit(0)

    try:
        with open(FP_YAML) as f:
            fp_data = yaml.safe_load(f)
    except Exception:
        sys.exit(0)

    fps = (fp_data or {}).get('fingerprints', {}) or {}
    hits = []
    for atomic_id, rule in fps.items():
        if not isinstance(rule, dict):
            continue
        fired_by = None
        # literal keyword triggers
        for key in ('triggers', 'triggers_force_en', 'triggers_force_zh'):
            for trig in rule.get(key, []) or []:
                if trig and trig.lower() in prompt_scan:
                    fired_by = trig
                    break
            if fired_by:
                break
        # regex pattern trigger
        if not fired_by:
            pat = rule.get('triggers_pattern')
            if pat:
                try:
                    if re.search(pat, prompt[:PROMPT_TRUNCATE]):
                        fired_by = f'pattern:{pat[:40]}'
                except re.error:
                    pass
        if fired_by:
            hits.append({
                'id': atomic_id,
                'trigger': fired_by,
                'priority': rule.get('priority', 'normal'),
                'desc': rule.get('desc', ''),
                'action': rule.get('action', ''),
            })

    if not hits:
        sys.exit(0)

    priority_order = {'critical': 0, 'high': 1, 'normal': 2}
    hits.sort(key=lambda h: priority_order.get(h['priority'], 3))

    lines = ['### Fingerprint retrieval — memory rules auto-matched for this turn:']
    lines.append('(For each rule, verify its applies_when/condition against the current context before applying. If the rule does not apply, note why and skip.)')
    lines.append('')
    for h in hits[:MAX_SHOW]:
        applies_when, condition = read_rule_applicability(h['id'])
        lines.append(f"- **[{h['id']}]** ({h['priority']}) {h['desc']}")
        if h['action']:
            lines.append(f"    • action: {h['action']}")
        if applies_when:
            lines.append(f"    • applies_when: {applies_when[:200]}")
        if condition:
            lines.append(f"    • condition: {condition[:120]}")
        lines.append(f"    • triggered by: {h['trigger']}")
    lines.append('')
    lines.append('(Phase B1+B2 retrieval + applicability gate. Respect these unless applies_when rules out current context.)')

    out = {
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': '\n'.join(lines),
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
