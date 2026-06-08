#!/usr/bin/env python3
"""Deterministic hard-block Stop hook (extension point).

This Stop hook calls evaluate_rules() to decide whether to block (exit 2 +
decision='block') or allow. By default evaluate_rules() ships NO built-in rules,
so the hook allows everything — it is an extension point for projects that want
to register their own deterministic violation classes.

Behavior:
  - Verdict output is JSON.
  - `PT_TEST_FORCE_VIOLATION` drives a synthetic violation for the test path.
  - Enforcement is gated by path_config.enforcement_enabled(); when disabled the
    hook is observational only.
  - `B5_DETERMINISTIC_DISABLED=1` env opt-out.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config  # central path config

B5_DETERMINISTIC_DISABLED = path_config.pt_env('DETERMINISTIC_DISABLED', '').lower() in ('1', 'true', 'yes')

# Safety valve: after the same atomic_id fires >= STREAK_BYPASS times consecutively in a
# session, that atomic_id is auto-bypassed for the rest of the session (logs a warning but
# does not block). Prevents cascading transcript disasters.
STREAK_BYPASS = int(path_config.pt_env('STREAK_BYPASS', '3'))


def _extract_response_and_transcript_lines(stdin_data):
    """From Stop hook stdin JSON {session_id, transcript_path, ...},
    extract (response_text, transcript_lines).

    response_text = last assistant message text.
    transcript_lines = full JSONL lines (for last_user_prompt detection).
    Returns (response_text, transcript_lines) or ('', []).
    """
    transcript_path = stdin_data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        return '', []
    try:
        with open(transcript_path, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return '', []
    # Find last assistant text
    response_text = ''
    for line in reversed(lines[-200:]):
        try:
            o = json.loads(line)
            if o.get('type') == 'assistant':
                msg = o.get('message') or {}
                content = msg.get('content', [])
                if isinstance(content, str):
                    response_text = content
                    break
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            response_text = item.get('text', '')
                            break
                if response_text:
                    break
        except Exception:
            continue
    return response_text, lines


def evaluate_rules(response, transcript_lines):
    """Return deterministic-rule violations for a response. Ships empty (no
    built-in rules); see the body. Each violation: {rule_id, reason, evidence_excerpt}."""
    violations = []
    if not response:
        return violations

    # The public release ships with NO built-in deterministic rules: preference-
    # tracker must not hard-block anyone on a maintainer's personal preferences.
    # Deterministic enforcement is opt-in and driven by the user's own recorded
    # rules / the shadow judge. This is the extension point where rules would be
    # evaluated; by default it adds nothing.
    #
    # Test affordance: PT_TEST_FORCE_VIOLATION=1 emits one synthetic violation so
    # the block / exit-code mechanism stays testable without shipping a real rule.
    if os.environ.get('PT_TEST_FORCE_VIOLATION'):
        violations.append({
            'rule_id': 'test-synthetic',
            'reason': 'forced violation for mechanism testing',
            'evidence_excerpt': (response or '')[:120],
        })

    return violations


def build_block_reason(violations):
    """Build the block-reason text shown to the agent: list the triggered rules
    and their fix direction. No personal writing-style dictation."""
    if not violations:
        return ''
    triggered_lines = []
    for v in violations:
        rid = v.get('rule_id', 'rule')
        hint = v.get('fix') or v.get('reason') or ''
        triggered_lines.append(
            f"  • [{rid}] {str(v.get('evidence_excerpt', ''))[:120]}\n"
            f"    → {hint}"
        )
    triggered = '\n'.join(triggered_lines)

    reason = (
        f"⛔ {', '.join(v.get('rule_id', 'rule') for v in violations)} triggered\n\n"
        f"{triggered}\n\n"
        f"Override: env `B5_DETERMINISTIC_DISABLED=1` disables all; `B5_STREAK_BYPASS=N` "
        f"sets the consecutive-violation bypass threshold (default 3)."
    )
    return reason


def _streak_path(session_id):
    """Per-session streak counter file path."""
    sid = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id or 'unknown')[:64]
    return os.path.join(path_config.get_streak_dir(), f'{sid}.json')


def _load_streak(session_id):
    """Load {atomic_id: count} for current session."""
    path = _streak_path(session_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _bump_streak(session_id, rule_ids):
    """Increment streak counter for given rule_ids. Returns updated dict."""
    os.makedirs(path_config.get_streak_dir(), exist_ok=True)
    streak = _load_streak(session_id)
    for rid in rule_ids:
        streak[rid] = streak.get(rid, 0) + 1
    try:
        with open(_streak_path(session_id), 'w') as f:
            json.dump(streak, f)
    except Exception:
        pass
    return streak


def _filter_bypass_streaked(violations, streak):
    """Safety valve: same atomic_id streak >= STREAK_BYPASS → bypass that rule (drop from violations).
    Returns (filtered_violations, bypassed_rule_ids).
    """
    filtered = []
    bypassed = []
    for v in violations:
        rid = v['rule_id']
        if streak.get(rid, 0) >= STREAK_BYPASS:
            bypassed.append(rid)
        else:
            filtered.append(v)
    return filtered, bypassed


def log_check(session_id, status, violations, latency_ms):
    """Append b5_check entry to compliance_log.jsonl."""
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'session_id': session_id,
        'event': 'Stop',
        'check_source': 'deterministic_block',
        'b5_check': {
            'deterministic_status': status,  # 'pass' | 'block' | 'disabled'
            'deterministic_violations': [v['rule_id'] for v in violations],
            'deterministic_latency_ms': round(latency_ms, 2),
        },
    }
    try:
        log_path = path_config.get_compliance_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        # H10 fix: log carries violation evidence; restrict to user-only.
        path_config.chmod_or_warn(log_path, 0o600)
    except Exception:
        pass


def main():
    """Stop hook entrypoint. Read stdin JSON, evaluate rules, exit 2 + JSON if block."""
    import time
    t0 = time.time()
    try:
        data = json.load(sys.stdin)
    except Exception:
        # Malformed stdin: don't block
        sys.exit(0)

    session_id = data.get('session_id', '')

    if B5_DETERMINISTIC_DISABLED or not path_config.enforcement_enabled():
        log_check(session_id, 'disabled', [], (time.time() - t0) * 1000)
        sys.exit(0)

    response, transcript_lines = _extract_response_and_transcript_lines(data)
    if not response:
        log_check(session_id, 'pass', [], (time.time() - t0) * 1000)
        sys.exit(0)

    violations = evaluate_rules(response, transcript_lines)

    # Safety valve: after the same rule fires STREAK_BYPASS times consecutively, bypass it
    if violations:
        streak = _load_streak(session_id)
        violations, bypassed = _filter_bypass_streaked(violations, streak)
        if bypassed:
            # log the bypassed rules (does not block, but records the disaster-escape event)
            log_check(session_id, 'streak_bypass', [{'rule_id': r, 'reason': 'streak >= bypass threshold', 'evidence_excerpt': f'streak={streak.get(r, 0)}'} for r in bypassed], (time.time() - t0) * 1000)

    latency_ms = (time.time() - t0) * 1000

    if violations:
        # bump streak only for rules that actually fired this turn (not bypassed)
        _bump_streak(session_id, [v['rule_id'] for v in violations])
        log_check(session_id, 'block', violations, latency_ms)
        decision = {
            'decision': 'block',
            'reason': build_block_reason(violations),
        }
        print(json.dumps(decision, ensure_ascii=False))
        sys.exit(2)

    log_check(session_id, 'pass', [], latency_ms)
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Defensive: never block on internal errors
        sys.exit(0)
