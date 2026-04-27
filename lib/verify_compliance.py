#!/usr/bin/env python3
"""Phase B3-lite + B4 — compliance tracker (Stop hook).

B3-lite (existing): log per-Stop turn fp matches + lang ratio + autonomous-summary
flag. Log-only.

B4 (Session B, 2026-04-25): blocking on session pending finalize. When current
session has accumulated > THRESHOLD_PENDING obs entries with detection.detected=True
+ action.saved_to_memory='pending' AND session has run > THRESHOLD_DURATION_MIN
minutes, return Stop hook decision='block' (exit 2) so agent must finalize before
stopping.

Scope: per ROBUSTNESS_PROPOSAL_V22 §1.4, blocking is ONLY for pending-finalize.
Cite-rate / lang-ratio / fp-match remain log-only to avoid over-blocking. B4
threshold loose by design — see experiment/B4_BLOCKING_OBSERVATION_PROTOCOL.md
for the 1-week observation period and tuning method.

Schema deviation from kickoff B.1.1: obs entries do NOT carry session_id field
(verified P-2 round-3 audit). Time-window filter used as proxy: count pending obs
where timestamp within last `SESSION_WINDOW_MIN` minutes of stdin time. This is
robust to the schema and a reasonable session-bounding heuristic since multi-hour
sessions are uncommon and pending finalize is a per-turn action.
"""
import json, sys, os, re, glob, time
from datetime import datetime, timezone, timedelta

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

LOG_PATH = path_config.get_compliance_log_path()
# B4_TEST_OBS_OVERRIDE: lets test_b4_blocking.py inject a fixture obs file (I2 from code review)
OBS_LOG = os.environ.get('B4_TEST_OBS_OVERRIDE', path_config.get_observations_log_path())
FP_YAML = os.path.join(_LIB_DIR, 'fingerprints.yaml')
# /tmp migration 2026-04-25: alert + retry state moved to scratch365 (persistent, no wipe)
ALERT_DIR = os.environ.get('B4_ALERT_DIR', path_config.get_b4_alert_dir())
RETRY_DIR_DEFAULT = path_config.get_b4_retry_dir()

# B4 thresholds (tunable, see B4_BLOCKING_OBSERVATION_PROTOCOL.md)
# Use float() for both pending and duration to be robust to "3.5" etc (I4 from code review)
def _f_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)

THRESHOLD_PENDING = _f_env('B4_THRESHOLD_PENDING', 3)
THRESHOLD_DURATION_MIN = _f_env('B4_THRESHOLD_DURATION_MIN', 20)
SESSION_WINDOW_MIN = _f_env('B4_SESSION_WINDOW_MIN', 60)
MAX_RETRIES_BEFORE_SELF_DISABLE = int(_f_env('B4_MAX_RETRIES', 3))
B4_DISABLED = os.environ.get('B4_DISABLED', '').lower() in ('1', 'true', 'yes')

# Per-session retry tracking for C1 fix (livelock prevention).
# state/runtime/b4_retry/<sid>.json records {"retries": N, "first_block_ts": ISO, "last_block_ts": ISO}
# /tmp migration 2026-04-25: scratch365 persistent (CRC /tmp wipes daily)
RETRY_DIR = os.environ.get('B4_RETRY_DIR', RETRY_DIR_DEFAULT)
os.makedirs(ALERT_DIR, exist_ok=True)
os.makedirs(RETRY_DIR, exist_ok=True)


def _parse_iso(ts):
    """Parse ISO-8601 timestamp robustly. Returns None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        # handle trailing Z and offset variants
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _now_utc():
    return datetime.now(timezone.utc)


def detect_rules_for_response(response_text):
    """Heuristic: re-run fp matcher on response to see if any rule keyword
    appears in the response. Returns list of atomic_ids."""
    try:
        import yaml
        with open(FP_YAML) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return []
    fps = data.get('fingerprints', {}) or {}
    hits = []
    low = response_text[:8000].lower()
    for aid, rule in fps.items():
        if not isinstance(rule, dict):
            continue
        for key in ('triggers', 'triggers_force_en', 'triggers_force_zh'):
            for trig in rule.get(key, []) or []:
                if trig and trig.lower() in low:
                    hits.append(aid)
                    break
            else:
                continue
            break
    return hits


def check_lang_compliance(response_text):
    """Deterministic: compute chinese vs english ratio in the response."""
    chinese = sum(1 for c in response_text if '一' <= c <= '鿿')
    english_letters = sum(1 for c in response_text if c.isascii() and c.isalpha())
    total = chinese + english_letters
    if total == 0:
        return {'chinese_chars': 0, 'english_letters': 0, 'chinese_ratio': None}
    return {
        'chinese_chars': chinese,
        'english_letters': english_letters,
        'chinese_ratio': round(chinese / total, 3),
    }


def count_recent_pending(window_min=SESSION_WINDOW_MIN, obs_log_path=OBS_LOG, now=None):
    """Count detected=True + saved_to_memory='pending' obs entries within the last
    `window_min` minutes. Returns (pending_count, oldest_pending_age_min,
    earliest_obs_age_min, pending_details). Used as session-bounding proxy since obs
    entries lack session_id field (P-2 round-3 audit verified).

    `pending_details` is a list of {atomic_id, signal_type, content_excerpt, age_min}
    dicts (max 10 most recent), so Claude knows WHICH preference was missed (not just
    how many). Empty list if no pending in window.

    Returns (0, 0.0, 0.0, []) if no obs file or no entries in window.
    """
    if not os.path.exists(obs_log_path):
        return 0, 0.0, 0.0, []
    if now is None:
        now = _now_utc()
    cutoff = now - timedelta(minutes=window_min)
    pending = 0
    earliest_in_window = None
    earliest_pending = None
    pending_details = []  # list of dicts for reason text
    try:
        with open(obs_log_path, errors='ignore') as f:
            for line in f:
                try:
                    o = json.loads(line.strip())
                except Exception:
                    continue
                ts = _parse_iso(o.get('timestamp'))
                if not ts:
                    continue
                if ts < cutoff:
                    continue
                # in-window
                if earliest_in_window is None or ts < earliest_in_window:
                    earliest_in_window = ts
                det = o.get('detection') or {}
                act = o.get('action') or {}
                if det.get('detected') and str(act.get('saved_to_memory') or '').lower() == 'pending':
                    pending += 1
                    if earliest_pending is None or ts < earliest_pending:
                        earliest_pending = ts
                    age_min = (now - ts).total_seconds() / 60.0
                    pending_details.append({
                        'atomic_id': act.get('proposed_atomic_id') or '<unknown>',
                        'signal_type': det.get('signal_type') or 'unknown',
                        'content_excerpt': (det.get('content') or '')[:120],
                        'age_min': round(age_min, 1),
                    })
    except Exception:
        return 0, 0.0, 0.0, []

    if earliest_in_window is None:
        return 0, 0.0, 0.0, []
    earliest_age = (now - earliest_in_window).total_seconds() / 60.0
    pending_age = (now - earliest_pending).total_seconds() / 60.0 if earliest_pending else 0.0
    # keep most-recent 10 for reason text brevity
    pending_details = sorted(pending_details, key=lambda d: d['age_min'])[:10]
    return pending, pending_age, earliest_age, pending_details


def _retry_state_path(session_id):
    """Path to per-session retry counter file."""
    sid = session_id or 'unknown'
    sid_safe = re.sub(r'[^a-zA-Z0-9_-]', '_', sid)[:64]
    return os.path.join(RETRY_DIR, f'b4_retry_{sid_safe}.json')


def load_retry_state(session_id):
    """Read retry state for session. Returns dict with retries=N, first_block_ts, last_block_ts."""
    path = _retry_state_path(session_id)
    if not os.path.exists(path):
        return {'retries': 0, 'first_block_ts': None, 'last_block_ts': None}
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'retries': 0, 'first_block_ts': None, 'last_block_ts': None}
        # ensure required keys
        data.setdefault('retries', 0)
        data.setdefault('first_block_ts', None)
        data.setdefault('last_block_ts', None)
        return data
    except Exception:
        return {'retries': 0, 'first_block_ts': None, 'last_block_ts': None}


def bump_retry_state(session_id):
    """Increment retry counter atomically. Returns new state dict."""
    state = load_retry_state(session_id)
    now_iso = _now_utc().isoformat()
    state['retries'] = state.get('retries', 0) + 1
    if not state.get('first_block_ts'):
        state['first_block_ts'] = now_iso
    state['last_block_ts'] = now_iso
    try:
        with open(_retry_state_path(session_id), 'w') as f:
            json.dump(state, f)
    except Exception:
        pass
    return state


def write_pending_alert(session_id, pending_count, session_age_min, oldest_pending_age_min, retry_count=0):
    """Write a /tmp/session_pending_alert_<sid>.md file describing the block reason
    so agent has machine-readable alert when retry stop triggers."""
    sid = session_id or 'unknown'
    sid_safe = re.sub(r'[^a-zA-Z0-9_-]', '_', sid)[:64]
    path = os.path.join(ALERT_DIR, f'session_pending_alert_{sid_safe}.md')
    retry_note = ''
    if retry_count >= MAX_RETRIES_BEFORE_SELF_DISABLE - 1:
        retry_note = (
            f'\n\n⚠ **This is retry #{retry_count + 1}**. After retry #{MAX_RETRIES_BEFORE_SELF_DISABLE} '
            f'B4 will SELF-DISABLE for this session (livelock prevention per code-review C1). '
            f'If you keep retrying without finalizing, the next Stop will pass without check.'
        )
    elif retry_count > 0:
        retry_note = f'\n\n(This is retry #{retry_count + 1} of allowed {MAX_RETRIES_BEFORE_SELF_DISABLE} before self-disable.)'

    body = f"""# B4 blocking alert — session pending finalize required

**Session**: `{sid_safe}`
**Trigger time**: {_now_utc().isoformat()}
**Retry count**: {retry_count}/{MAX_RETRIES_BEFORE_SELF_DISABLE} before self-disable{retry_note}

## Why blocked

Session has accumulated **{pending_count} pending obs entries** (detected=True + saved_to_memory='pending') in the last {SESSION_WINDOW_MIN:.0f} min window.

- Threshold: > {THRESHOLD_PENDING:.1f} pending AND > {THRESHOLD_DURATION_MIN:.1f} min session age
- Current: {pending_count} pending, oldest pending ~{oldest_pending_age_min:.1f} min old, earliest in-window obs ~{session_age_min:.1f} min ago
- Per `wf-pref-030` + ROBUSTNESS_PROPOSAL_V22 §1.4: pending obs must be finalized (write memory file or downgrade to detected=False) before stop

## What to do

1. Run `python3 ~/.claude/skills/preference-tracker/lib/pending_queue_manager.py inject` to see queue (`inject` 输出当前队列内容; `promote/inject/prune` 是 3 个支持的命令)
2. For each pending obs: either (a) write memory file + edit obs to set saved_to_memory='yes', or (b) re-classify obs to detected=False if false-positive
3. Then re-stop — gate will pass when pending count drops below threshold

## Override (only if you really want to bypass)

- Set env var `B4_DISABLED=1` to skip B4 check entirely (logs warning instead)
- Or simply retry {MAX_RETRIES_BEFORE_SELF_DISABLE} times — B4 will self-disable for this session as livelock safeguard

---
Generated by `verify_compliance.py:write_pending_alert`. See B4_BLOCKING_OBSERVATION_PROTOCOL.md for tuning context.
"""
    try:
        with open(path, 'w') as f:
            f.write(body)
    except Exception:
        pass
    return path


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = data.get('session_id', '')

    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'session_id': session_id,
        'event': 'Stop',
    }

    response_text = ''
    try:
        if 'transcript_path' in data and os.path.exists(data['transcript_path']):
            with open(data['transcript_path']) as f:
                lines = f.readlines()
            for line in reversed(lines[-200:]):
                try:
                    o = json.loads(line)
                    if o.get('type') == 'assistant':
                        msg = o.get('message', {})
                        for item in msg.get('content', []):
                            if item.get('type') == 'text':
                                response_text = item.get('text', '')
                                break
                    if response_text:
                        break
                except Exception:
                    continue
    except Exception:
        pass

    if response_text:
        entry['response_excerpt'] = response_text[:400]
        entry['response_length'] = len(response_text)
        entry['fp_rules_in_response'] = detect_rules_for_response(response_text)
        entry['lang_ratio'] = check_lang_compliance(response_text)

        # A.3 language dry-run advisory (v22 2026-04-24, per ROBUSTNESS_PROPOSAL_V22 Layer 1.3)
        # Flag autonomous-summary pattern with low chinese ratio as suspected lang-pref-001 violation
        cr = entry['lang_ratio'].get('chinese_ratio')
        low_cr = cr is not None and cr < 0.1
        long_enough = len(response_text) > 200
        autonomous_markers = ('autonomous block', 'session wrap', 'session status', 'session summary',
                              'wrap-up', 'final result', 'all done', 'all complete',
                              '自主执行', 'final numbers', 'sprint report')
        first_3000 = response_text[:3000].lower()
        has_autonomous = any(m in first_3000 for m in autonomous_markers)
        if long_enough and low_cr:
            entry['suspected_lang_violation'] = True
            entry['lang_violation_detail'] = {
                'has_autonomous_marker': has_autonomous,
                'rationale': 'response>200 chars and chinese_ratio<0.1; likely violates lang-pref-001 / lang-pit-001'
            }
    else:
        entry['response_empty'] = True

    # B4 — pending finalize blocking decision (Session B, 2026-04-25)
    pending_count, oldest_pending_age_min, session_age_min, pending_details = count_recent_pending()
    retry_state = load_retry_state(session_id)
    self_disabled = retry_state.get('retries', 0) >= MAX_RETRIES_BEFORE_SELF_DISABLE

    entry['b4_check'] = {
        'session_id': session_id,  # I1 from code review: log sid in b4_check too for jq
        'pending_count': pending_count,
        'oldest_pending_age_min': round(oldest_pending_age_min, 2),
        'session_age_min': round(session_age_min, 2),
        'threshold_pending': THRESHOLD_PENDING,
        'threshold_duration_min': THRESHOLD_DURATION_MIN,
        'window_min': SESSION_WINDOW_MIN,
        'disabled': B4_DISABLED,
        'retry_count': retry_state.get('retries', 0),
        'self_disabled': self_disabled,
        'max_retries': MAX_RETRIES_BEFORE_SELF_DISABLE,
    }

    # C1 fix: livelock prevention — after MAX_RETRIES_BEFORE_SELF_DISABLE retries,
    # gate self-disables for this session even if conditions still met.
    # Logic: would_block (fresh evaluation) AND not B4_DISABLED AND not self_disabled
    would_block = (
        pending_count > THRESHOLD_PENDING
        and session_age_min > THRESHOLD_DURATION_MIN
    )
    should_block = would_block and not B4_DISABLED and not self_disabled
    entry['b4_check']['would_block'] = would_block

    # Write compliance log first (always)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass

    if should_block:
        # Bump retry counter BEFORE writing alert so alert shows next retry's count
        new_state = bump_retry_state(session_id)
        retry_count = new_state.get('retries', 1)

        alert_path = write_pending_alert(session_id, pending_count,
                                         session_age_min, oldest_pending_age_min,
                                         retry_count=retry_count)
        # Stop hook decision contract: print JSON to stdout + exit 2 to block
        # Per Claude Code Stop hook spec: {"decision":"block","reason":...}
        retry_left = MAX_RETRIES_BEFORE_SELF_DISABLE - retry_count
        retry_warning = ''
        if retry_count >= MAX_RETRIES_BEFORE_SELF_DISABLE - 1:
            retry_warning = f'\n\n⚠ LIVELOCK SAFEGUARD: this is retry #{retry_count}/{MAX_RETRIES_BEFORE_SELF_DISABLE}. After {retry_left} more retry, B4 will SELF-DISABLE for this session — do NOT exhaust the safeguard by repeated empty retries; actually finalize.'
        elif retry_count > 1:
            retry_warning = f'\n\n(this is retry #{retry_count}/{MAX_RETRIES_BEFORE_SELF_DISABLE})'

        # Inline the actual pending list — Claude must see WHAT was missed,
        # not just a count. Strengthen reason from informational → imperative.
        pending_lines = []
        for i, p in enumerate(pending_details, 1):
            excerpt = p['content_excerpt'] or '<no content>'
            pending_lines.append(
                f"  {i}. atomic_id={p['atomic_id']} "
                f"({p['signal_type']}, {p['age_min']:.1f} min ago): {excerpt}"
            )
        pending_block = '\n'.join(pending_lines) if pending_lines else '  (no details available)'

        # Imperative reason — tells Claude what to do, not just what's wrong.
        reason = (
            f"⛔ STOP BLOCKED — preference-tracker B4 gate triggered. "
            f"You detected {pending_count} preference signal(s) but did NOT finalize them "
            f"to memory. **Do not just retry stop**; you must act before next stop succeeds.\n\n"
            f"PENDING SIGNALS (you flagged these as detected=True earlier in this session "
            f"but left saved_to_memory='pending'):\n{pending_block}\n\n"
            f"REQUIRED ACTION (per `wf-pit-016` + `wf-pref-030`) — pick exactly one per pending entry:\n"
            f"  (a) FINALIZE: walk through SKILL.md Pre-write checklist (paste **I checked** + **Decision** lines), "
            f"write the memory file, then update the obs entry's saved_to_memory='yes'.\n"
            f"  (b) RECLASSIFY as false-positive: if on reflection the signal was not real, "
            f"edit the obs entry's detection.detected=False (use `pending_queue_manager.py` or direct edit).\n"
            f"  (c) DISCARD via prune: `python3 ~/.claude/skills/preference-tracker/lib/pending_queue_manager.py "
            f"prune --force <queue_entry_id>` if not actionable.\n\n"
            f"Detected NOT acceptable: 'I'll do it later' / silently retry / append to handoff and stop. "
            f"The next stop you attempt will re-fire this gate until pending count drops to ≤{int(THRESHOLD_PENDING)}.\n\n"
            f"Stats: pending={pending_count} > threshold {THRESHOLD_PENDING:.1f}; "
            f"session age {session_age_min:.1f} min > {THRESHOLD_DURATION_MIN:.1f} min; "
            f"oldest pending {oldest_pending_age_min:.1f} min old.\n"
            f"Full protocol + queue tooling: {alert_path}\n"
            f"Emergency escape (use only if you really want to bypass): set env B4_DISABLED=1.{retry_warning}"
        )

        decision = {
            'decision': 'block',
            'reason': reason,
        }
        print(json.dumps(decision, ensure_ascii=False))
        sys.exit(2)

    # Default: log-only, exit 0
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Defensive: never block on internal errors
        sys.exit(0)
