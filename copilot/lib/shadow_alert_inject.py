#!/usr/bin/env python3
"""Soft injection (UserPromptSubmit hook).

Read B5_SHADOW_ALERT.md (rolling cap N=3 violations from shadow judge), inject
"last turn you violated X" notice into next-turn additionalContext.

Defenses:
  - B5_INJECT_DISABLED=1 env opt-out
  - 24h TTL: alerts older than 24h auto-skipped at read time
  - Empty alert file → exit 0 silently

JSON output.
Defensive fallbacks.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config

SHADOW_ALERT_MD = path_config.get_shadow_alert_md_path()
SHADOW_LOG = path_config.get_shadow_log_path()
CONSUMED_ALERTS = os.path.join(os.path.dirname(SHADOW_LOG), 'b5_shadow_alerts_consumed.json')
CONSUMED_ALERTS_LOCK = os.path.join(os.path.dirname(SHADOW_LOG), 'b5_shadow_alerts_consumed.lock')

B5_INJECT_DISABLED = path_config.pt_env('INJECT_DISABLED', '').lower() in ('1', 'true', 'yes')
TTL_HOURS = float(path_config.pt_env('TTL_HOURS', '24'))
ALERT_ROLLING_CAP = int(path_config.pt_env('ALERT_ROLLING_CAP', '3'))


def _alert_key(alert):
    """Stable key for one logged alert entry."""
    return '|'.join([
        str(alert.get('timestamp', '')),
        str(alert.get('rule_id', '')),
        str(alert.get('feedback', '')),
    ])


def _read_consumed_alert_keys():
    """Read alerts already injected into UserPromptSubmit once."""
    if not os.path.exists(CONSUMED_ALERTS):
        return set()
    try:
        with open(CONSUMED_ALERTS, errors='ignore') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x) for x in data}
    except Exception:
        pass
    return set()


def _write_consumed_alert_keys(keys):
    """Persist consumed alert keys with a small bounded file."""
    try:
        os.makedirs(os.path.dirname(CONSUMED_ALERTS), exist_ok=True)
        ordered = sorted(keys)[-1000:]
        tmp = f'{CONSUMED_ALERTS}.tmp.{os.getpid()}'
        with open(tmp, 'w') as f:
            json.dump(ordered, f, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        path_config.chmod_or_warn(tmp, 0o600, critical=True)
        os.replace(tmp, CONSUMED_ALERTS)
        path_config.chmod_or_warn(CONSUMED_ALERTS, 0o600, critical=True)
    except Exception:
        try:
            if 'tmp' in locals() and os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _mark_alerts_consumed(alerts):
    if not alerts:
        return
    keys = _read_consumed_alert_keys()
    keys.update(_alert_key(a) for a in alerts)
    _write_consumed_alert_keys(keys)


def _claim_recent_alerted_violations(hours=24):
    """Return recent alerts and atomically mark them consumed for this project."""
    try:
        os.makedirs(os.path.dirname(CONSUMED_ALERTS_LOCK), exist_ok=True)
        with open(CONSUMED_ALERTS_LOCK, 'a+') as lock:
            path_config.chmod_or_warn(CONSUMED_ALERTS_LOCK, 0o600, critical=False)
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            alerts = _read_recent_alerted_violations(hours=hours)
            _mark_alerts_consumed(alerts)
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return alerts
    except Exception:
        return []


def _read_recent_alerted_violations(hours=24):
    """Read shadow log, return alerted=True entries within TTL window. Most recent first."""
    if not os.path.exists(SHADOW_LOG):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    consumed = _read_consumed_alert_keys()
    try:
        with open(SHADOW_LOG, errors='ignore') as f:
            for line in f:
                try:
                    o = json.loads(line.strip())
                except Exception:
                    continue
                if not o.get('alerted', False):
                    continue
                ts_str = o.get('timestamp')
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except Exception:
                    continue
                if ts >= cutoff:
                    if _alert_key(o) in consumed:
                        continue
                    out.append(o)
    except Exception:
        pass
    out.sort(key=lambda o: o.get('timestamp', ''), reverse=True)
    # dedupe by rule_id (latest only)
    seen_ids = set()
    deduped = []
    for o in out:
        rid = o.get('rule_id', '')
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        deduped.append(o)
        if len(deduped) >= ALERT_ROLLING_CAP:
            break
    return deduped


def build_inject_context(alerts):
    """Build additionalContext markdown from the alert list: which rules the
    shadow judge flagged last turn, and the suggested fix direction."""
    if not alerts:
        return None
    lines = ['### Rules the shadow judge flagged last turn:']
    for a in alerts:
        rid = a.get('rule_id', '<unknown>')
        desc = a.get('rule_desc', '')
        conf = a.get('judge_confidence', '?')
        feedback = a.get('feedback', '')[:150]
        lines.append(f'- **[{rid}]** {desc} (confidence {conf})')
        if feedback:
            lines.append(f'  fix: {feedback}')
    return '\n'.join(lines)


def main():
    """UserPromptSubmit hook entrypoint."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if B5_INJECT_DISABLED:
        sys.exit(0)

    alerts = _claim_recent_alerted_violations(hours=TTL_HOURS)
    context = build_inject_context(alerts)
    if not context:
        sys.exit(0)

    out = {
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': context,
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
