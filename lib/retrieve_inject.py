#!/usr/bin/env python3
"""Preference-tracker memory retrieve + inject (Phase B1 + Round-6/10 cli backend).

Reads UserPromptSubmit JSON from stdin, picks relevant atomic_ids based on
B5_RETRIEVE_BACKEND env var:
  - 'cli' (default since Round-10): semantic match via a small model (claude
    haiku for CC, gpt-5.4-mini for codex). Costs 1-2s latency per
    UserPromptSubmit but uses the same subscription auth path as the
    runtime, no API key needed and no trigger-keyword maintenance.
  - 'keyword': legacy fingerprints.yaml literal/regex matcher. Cheap and
    instant but only as good as the trigger lists in fingerprints.yaml.

CLI dispatch is governed by B5_RETRIEVE_CLI:
  - 'claude' (default, CC runtime): `claude -p --model <model>`
  - 'codex'  (codex runtime): `codex exec --ephemeral ... -m <model>`

Default model is derived from B5_RETRIEVE_CLI when B5_RETRIEVE_MODEL is unset:
  claude → claude-haiku-4-5, codex → gpt-5.4-mini.

Recursion guard: when retrieve_inject invokes the CLI, it sets
B5_RETRIEVE_RECURSION_GUARD=1 in the child env. The hook scripts in
~/.claude/skills/preference-tracker/hooks/memory-retrieve-inject.sh and
~/.codex/skills/preference-tracker/hooks/userpromptsubmit-retrieve-inject.sh
honor this flag and exit 0 immediately, so a nested CLI session doesn't
re-trigger the retrieve hook and loop.

Emits JSON with hookSpecificOutput.additionalContext naming the matched atomic_ids.
Non-destructive: any failure → exit 0 silently (no block).
"""
import json, sys, re, os, glob, subprocess, tempfile, time

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

FP_YAML = os.path.join(_LIB_DIR, 'fingerprints.yaml')
MEMORY_DIR = path_config.get_memory_dir()
MAX_SHOW = 10
PROMPT_TRUNCATE = 4000

# Round-10: backend default flipped to 'cli' (small-model semantic match).
RETRIEVE_BACKEND = os.environ.get('B5_RETRIEVE_BACKEND', 'cli').lower()
# Backwards compat: accept 'haiku' as alias for 'cli' (existing users
# may still have B5_RETRIEVE_BACKEND=haiku exported).
if RETRIEVE_BACKEND == 'haiku':
    RETRIEVE_BACKEND = 'cli'
RETRIEVE_CLI = os.environ.get('B5_RETRIEVE_CLI', 'claude').lower()
_DEFAULT_MODEL_BY_CLI = {
    'claude': 'claude-haiku-4-5',
    'codex': 'gpt-5.4-mini',
}
RETRIEVE_MODEL = os.environ.get('B5_RETRIEVE_MODEL') or _DEFAULT_MODEL_BY_CLI.get(
    RETRIEVE_CLI, 'claude-haiku-4-5'
)
RETRIEVE_TIMEOUT_S = int(os.environ.get('B5_RETRIEVE_TIMEOUT', '12'))
RETRIEVE_HAIKU_PROMPT_BUDGET = 2000  # truncate user prompt
RETRIEVE_HAIKU_RULE_LIMIT = 40       # max rules to show CLI

# Recursion guard: when retrieve_inject calls the CLI, it sets this env.
# Hook scripts check it and exit 0 immediately so a nested session doesn't
# re-fire retrieve and loop.
RETRIEVE_RECURSION_GUARD = os.environ.get('B5_RETRIEVE_RECURSION_GUARD') == '1'


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


def _build_cli_invocation(prompt: str) -> tuple[list[str], str | None]:
    """Build (cmd_argv, output_file_path_or_None) for the configured CLI.

    Round-10: dispatches by B5_RETRIEVE_CLI:
      - 'claude': prompt is positional arg, output on stdout
      - 'codex':  prompt via stdin, output written to --output-last-message file
    Returns (None, None) on unknown CLI.
    """
    if RETRIEVE_CLI == 'claude':
        # Round-10 nested-call fix (2026-05-02): inner claude -p must NOT
        # load any PT hook. Outer session's user-global hooks
        # (~/.claude/settings.json) plus any project-local settings would
        # otherwise fire on inner's Stop event — usually
        # check-observation-log returns decision=block, leaving
        # terminal_reason=stop_hook_prevented + empty result.
        #
        # `--setting-sources project` excludes user-global. The caller
        # (_retrieve_via_cli below) ALSO sets subprocess.run cwd to a
        # known clean dir (/tmp) so no project .claude/settings.json gets
        # loaded either. Together that means the inner session loads zero
        # hooks and returns its real text.
        return (
            ['claude', '-p', prompt,
             '--model', RETRIEVE_MODEL,
             '--output-format', 'text',
             '--setting-sources', 'project'],
            None,
        )
    if RETRIEVE_CLI == 'codex':
        # codex exec reads prompt from stdin, writes last assistant text to a
        # file via --output-last-message. --ephemeral + --ignore-user-config +
        # --ignore-rules + read-only sandbox keep this nested call cheap and
        # side-effect-free.
        out_fd, out_path = tempfile.mkstemp(prefix='pt_retrieve_', suffix='.txt')
        os.close(out_fd)
        return (
            ['codex', 'exec', '--ephemeral', '--ignore-user-config',
             '--ignore-rules', '--skip-git-repo-check',
             '--sandbox', 'read-only',
             '-m', RETRIEVE_MODEL,
             '-c', 'model_reasoning_effort="low"',
             '--output-last-message', out_path],
            out_path,
        )
    return ([], None)


def _retrieve_via_cli(user_prompt, fps_dict, memory_idx):
    """Round-10 backend: small-model semantic retrieval via either
    `claude -p` (CC, default) or `codex exec` (codex), selected by
    B5_RETRIEVE_CLI. Returns hit list shaped like keyword backend.

    Recursion guard: when we spawn the CLI we set
    B5_RETRIEVE_RECURSION_GUARD=1 in the child env so the nested session's
    UPS hook short-circuits, avoiding an infinite loop.
    """
    if not user_prompt:
        return []
    if RETRIEVE_RECURSION_GUARD:
        # Nested CLI session — don't re-fire retrieval, return empty so
        # caller falls back to keyword.
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
        'backend': 'cli',
        'cli': RETRIEVE_CLI,
        'model': RETRIEVE_MODEL,
        'rules_count': len(rules_for_prompt),
        'prompt_truncated_len': len(prompt_text),
    }

    cmd, out_path = _build_cli_invocation(prompt)
    if not cmd:
        debug_record['err'] = f'unknown B5_RETRIEVE_CLI={RETRIEVE_CLI!r}'
        _write_debug(debug_record) if debug else None
        return []

    # Round-10: child CLI sessions must NOT re-fire any PT hook AND must
    # NOT inherit the outer Claude Code session's SSE-mode env vars. If
    # CLAUDECODE / CLAUDE_CODE_SSE_PORT etc. leak through, the inner
    # claude detects "I'm inside a Claude Code session" and routes its
    # response over the SSE channel instead of stdout — retrieve gets
    # back len=1 ('\n') and falls back to keyword.
    child_env = dict(os.environ)
    # Recursion guard for our own hook scripts (CC + Codex check this).
    child_env['B5_RETRIEVE_RECURSION_GUARD'] = '1'
    # Disable all PT hooks in the inner session so its Stop hook chain
    # (check-observation-log / deterministic-block / verify-compliance /
    # shadow-judge / pending-promote) doesn't return decision=block and
    # turn the result into terminal_reason=stop_hook_prevented.
    child_env['B5_DETERMINISTIC_DISABLED'] = '1'
    child_env['B5_SHADOW_DISABLED'] = '1'
    child_env['B5_INJECT_DISABLED'] = '1'
    # Strip outer Claude Code session markers so inner claude runs as a
    # fresh top-level CLI session and writes its result to stdout.
    for k in (
        'CLAUDECODE',
        'CLAUDE_CODE_SSE_PORT',
        'CLAUDE_CODE_ENTRYPOINT',
        'CLAUDE_CODE_EXECPATH',
        'AI_AGENT',
    ):
        child_env.pop(k, None)

    # Round-10: run the inner CLI from /tmp so no project .claude/settings.json
    # gets loaded. Combined with --setting-sources project (claude) /
    # --ignore-user-config (codex), the inner session loads zero hooks.
    inner_cwd = '/tmp'
    out = ''
    try:
        t0 = time.time()
        if RETRIEVE_CLI == 'codex':
            # codex reads prompt from stdin
            proc = subprocess.run(
                cmd, input=prompt,
                capture_output=True, text=True,
                timeout=RETRIEVE_TIMEOUT_S, env=child_env,
                cwd=inner_cwd,
            )
        else:
            # claude -p has prompt as argv positional. We MUST close inner
            # stdin (DEVNULL) — otherwise claude -p inherits the hook
            # process's stdin (already drained by json.load) and waits 3s
            # for input, then sometimes returns empty stdout. Setting
            # stdin=DEVNULL makes inner claude proceed immediately.
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True,
                timeout=RETRIEVE_TIMEOUT_S, env=child_env,
                cwd=inner_cwd,
            )
        debug_record['latency_ms'] = round((time.time() - t0) * 1000, 1)
        debug_record['rc'] = proc.returncode
        if proc.returncode != 0:
            debug_record['err'] = f'rc={proc.returncode} stderr={(proc.stderr or "")[:200]}'
            _write_debug(debug_record) if debug else None
            return []
        # claude: stdout = response. codex: read out_path file.
        if out_path and os.path.isfile(out_path):
            try:
                out = open(out_path, encoding='utf-8', errors='replace').read()
            except OSError:
                out = proc.stdout or ''
        else:
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
    finally:
        if out_path:
            try:
                os.unlink(out_path)
            except OSError:
                pass

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
            'trigger': f'{RETRIEVE_CLI}-semantic',
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

    # Round-10: cli backend default. Falls back to keyword on any failure
    # (CLI missing / timeout / parse error / nested-recursion guard).
    if RETRIEVE_BACKEND == 'cli':
        memory_idx = _build_index()
        hits = _retrieve_via_cli(prompt, fps, memory_idx)
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
