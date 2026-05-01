#!/usr/bin/env python3
"""Preference-tracker memory retrieve + inject (Phase B1 + Round-6 haiku backend).

Reads UserPromptSubmit JSON from stdin, picks relevant atomic_ids based on
B5_RETRIEVE_BACKEND env var:
  - 'keyword' (default): match user prompt against fingerprints.yaml triggers
    + regex patterns. Cheap, instant, but misses semantically-similar prompts
    that don't share trigger keywords.
  - 'haiku': ask `claude -p --model claude-haiku-4-5` semantically which rules
    apply. Costs 1-2s latency per UserPromptSubmit, but no need to maintain
    trigger keyword lists. Uses Claude CLI subscription (0 marginal cost).

Emits JSON with hookSpecificOutput.additionalContext naming the matched atomic_ids.
Non-destructive: any failure → exit 0 silently (no block).
"""
import json, sys, re, os, glob, subprocess, time

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

FP_YAML = os.path.join(_LIB_DIR, 'fingerprints.yaml')
MEMORY_DIR = path_config.get_memory_dir()
MAX_SHOW = 10
PROMPT_TRUNCATE = 4000

# Round-6: backend selector. Default 'keyword' preserves v1 behavior.
RETRIEVE_BACKEND = os.environ.get('B5_RETRIEVE_BACKEND', 'keyword').lower()
RETRIEVE_MODEL = os.environ.get('B5_RETRIEVE_MODEL', 'claude-haiku-4-5')
RETRIEVE_TIMEOUT_S = int(os.environ.get('B5_RETRIEVE_TIMEOUT', '12'))
RETRIEVE_HAIKU_PROMPT_BUDGET = 2000  # truncate user prompt
RETRIEVE_HAIKU_RULE_LIMIT = 40       # max rules to show haiku


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


def _write_debug(record):
    """Append a retrieve debug entry to <state>/runtime/retrieve_debug.jsonl.
    Caller controls via B5_RETRIEVE_DEBUG=1. Silent on any IO failure."""
    try:
        state_dir = path_config.get_state_dir()
        log_path = os.path.join(state_dir, 'retrieve_debug.jsonl')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        path_config.chmod_or_warn(log_path, 0o600)
    except Exception:
        pass


def _retrieve_via_haiku(user_prompt, fps_dict, memory_idx):
    """Round-6 backend: ask claude -p --model haiku which rules apply.

    Returns list of dicts shaped like keyword backend's `hits` so the rest of
    main() can reuse the same rendering. Returns [] on any failure (caller
    falls back to keyword backend).
    """
    if not user_prompt:
        return []
    rules_for_prompt = []
    seen_ids = set()
    for atomic_id, rule in (fps_dict or {}).items():
        if not isinstance(rule, dict) or atomic_id in seen_ids:
            continue
        seen_ids.add(atomic_id)
        desc = (rule.get('desc') or '')[:140]
        applies_when, _cond = memory_idx.get(atomic_id, ('', ''))
        rules_for_prompt.append({
            'id': atomic_id,
            'desc': desc,
            'applies_when': (applies_when or '')[:200],
            'priority': rule.get('priority', 'normal'),
            'action': (rule.get('action') or '')[:140],
        })
    for atomic_id, (applies_when, _cond) in memory_idx.items():
        if atomic_id in seen_ids:
            continue
        seen_ids.add(atomic_id)
        rules_for_prompt.append({
            'id': atomic_id,
            'desc': '',
            'applies_when': (applies_when or '')[:200],
            'priority': 'normal',
            'action': '',
        })
    if not rules_for_prompt:
        return []
    rules_for_prompt = rules_for_prompt[:RETRIEVE_HAIKU_RULE_LIMIT]

    rules_lines = [
        f"- {r['id']}: {r['desc']}" + (f" | applies_when: {r['applies_when']}" if r['applies_when'] else '')
        for r in rules_for_prompt
    ]
    rules_text = '\n'.join(rules_lines)
    prompt_text = user_prompt[:RETRIEVE_HAIKU_PROMPT_BUDGET]
    prompt = (
        "You select which preference rules apply to a user message.\n\n"
        "User message:\n\"\"\"\n" + prompt_text + "\n\"\"\"\n\n"
        "Available rules (one per line, format `atomic_id: description | applies_when: ...`):\n"
        + rules_text + "\n\n"
        "Return ONLY a JSON array of atomic_id strings for rules that apply, no prose.\n"
        "Example: [\"lang-pref-001\", \"oth-pref-001\"]\n"
        "If no rule applies, return [].\n"
    )

    debug = os.environ.get('B5_RETRIEVE_DEBUG') == '1'
    debug_record = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'backend': 'haiku',
        'model': RETRIEVE_MODEL,
        'rules_count': len(rules_for_prompt),
        'prompt_truncated_len': len(prompt_text),
    }
    out = ''
    err = None
    try:
        t0 = time.time()
        proc = subprocess.run(
            ['claude', '-p', prompt, '--model', RETRIEVE_MODEL, '--output-format', 'text'],
            capture_output=True, text=True, timeout=RETRIEVE_TIMEOUT_S,
        )
        debug_record['latency_ms'] = round((time.time() - t0) * 1000, 1)
        debug_record['rc'] = proc.returncode
        if proc.returncode != 0:
            debug_record['err'] = f'rc={proc.returncode} stderr={(proc.stderr or "")[:200]}'
            _write_debug(debug_record) if debug else None
            return []
        out = proc.stdout or ''
        debug_record['stdout_len'] = len(out)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        debug_record['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        _write_debug(debug_record) if debug else None
        return []
    except Exception as e:
        debug_record['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        _write_debug(debug_record) if debug else None
        return []

    m = re.search(r'\[[^\[\]]*\]', out, re.DOTALL)
    if not m:
        debug_record['err'] = f'no JSON array in stdout (len={len(out)})'
        debug_record['stdout_head'] = out[:200]
        _write_debug(debug_record) if debug else None
        return []
    try:
        ids = json.loads(m.group())
    except (json.JSONDecodeError, ValueError) as e:
        debug_record['err'] = f'json decode: {e}'
        _write_debug(debug_record) if debug else None
        return []
    if not isinstance(ids, list):
        debug_record['err'] = f'not a list: {type(ids).__name__}'
        _write_debug(debug_record) if debug else None
        return []
    debug_record['ids'] = ids
    _write_debug(debug_record) if debug else None

    rules_by_id = {r['id']: r for r in rules_for_prompt}
    hits = []
    for rid in ids:
        if not isinstance(rid, str):
            continue
        r = rules_by_id.get(rid)
        if not r:
            continue
        hits.append({
            'id': r['id'],
            'trigger': 'haiku-semantic',
            'priority': r['priority'],
            'desc': r['desc'],
            'action': r['action'],
        })
    return hits


def _retrieve_via_keyword(user_prompt, fps_dict):
    """Keyword + regex matching (v1 behavior)."""
    prompt_scan = user_prompt[:PROMPT_TRUNCATE].lower()
    hits = []
    for atomic_id, rule in (fps_dict or {}).items():
        if not isinstance(rule, dict):
            continue
        fired_by = None
        for key in ('triggers', 'triggers_force_en', 'triggers_force_zh'):
            for trig in rule.get(key, []) or []:
                if trig and trig.lower() in prompt_scan:
                    fired_by = trig
                    break
            if fired_by:
                break
        if not fired_by:
            pat = rule.get('triggers_pattern')
            if pat:
                try:
                    if re.search(pat, user_prompt[:PROMPT_TRUNCATE]):
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
    return hits


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

    # Round-6: backend selector
    if RETRIEVE_BACKEND == 'haiku':
        memory_idx = _build_index()
        hits = _retrieve_via_haiku(prompt, fps, memory_idx)
        # Defensive fallback: if haiku call failed (CLI missing / timeout /
        # parse error), fall back to keyword so user still gets some retrieval.
        if not hits:
            hits = _retrieve_via_keyword(prompt, fps)
    else:
        hits = _retrieve_via_keyword(prompt, fps)

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
