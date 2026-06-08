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
import transcript_adapter  # cross-runtime stdin + transcript parsing (Copilot/Claude)

B5_DETERMINISTIC_DISABLED = os.environ.get('B5_DETERMINISTIC_DISABLED', '').lower() in ('1', 'true', 'yes')

# Idea D safety valve: once the same atomic_id fires >= STREAK_BYPASS times in a row
# within a session, that atomic_id is auto-bypassed for the rest of the session
# (logs a warning but does not block). Prevents cascading transcript disasters.
STREAK_BYPASS = int(os.environ.get('B5_STREAK_BYPASS', '3'))


def _extract_response_and_transcript_lines(stdin_data):
    """From Stop hook stdin JSON, extract (response_text, transcript_lines).

    Delegates to transcript_adapter so both Copilot (transcriptPath +
    assistant.message/data.content) and Claude (transcript_path +
    assistant/message.content) schemas work. Kept for backward-compat /
    existing tests; main() uses the richer adapter call directly.

    Returns (response_text, transcript_lines) or ('', []).
    """
    response, _last_user, _tools, lines = transcript_adapter.read_transcript(stdin_data)
    return response, lines


def evaluate_rules(response, transcript_lines=None, last_user='', tool_commands=None):
    """Return deterministic-rule violations for a response. Ships empty (no
    built-in rules); see the body. Each violation: {rule_id, reason, evidence_excerpt}.

    Args:
      response: last assistant natural-language text.
      transcript_lines: raw transcript lines (legacy).
      last_user: last user prompt text (from transcript_adapter).
      tool_commands: shell/tool command strings from the latest assistant turn.
    """
    violations = []
    if not response and not tool_commands:
        return violations

    # The public release ships with NO built-in deterministic rules: preference-
    # tracker must not hard-block anyone on a maintainer's personal preferences.
    # Deterministic enforcement is opt-in and driven by the user's own recorded
    # rules / the shadow judge. This function is the extension point where rules
    # would be evaluated; by default it adds nothing.
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
    """Idea D: same atomic_id streak >= STREAK_BYPASS → bypass that rule (drop from violations).
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

    session_id = transcript_adapter.get_session_id(data)

    if path_config.is_child_session():
        sys.exit(0)

    if B5_DETERMINISTIC_DISABLED or not path_config.enforcement_enabled():
        log_check(session_id, 'disabled', [], (time.time() - t0) * 1000)
        sys.exit(0)

    response, last_user, tool_commands, transcript_lines = transcript_adapter.read_transcript(data)
    if not response and not tool_commands:
        log_check(session_id, 'pass', [], (time.time() - t0) * 1000)
        sys.exit(0)

    violations = evaluate_rules(response, transcript_lines, last_user=last_user, tool_commands=tool_commands)

    # Idea D: safety valve — bypass a rule after it fires STREAK_BYPASS times in a row
    if violations:
        streak = _load_streak(session_id)
        violations, bypassed = _filter_bypass_streaked(violations, streak)
        if bypassed:
            # log the bypassed rules (don't block, but record the disaster-escape event)
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
        sys.exit(path_config.stop_block_exit_code())

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
