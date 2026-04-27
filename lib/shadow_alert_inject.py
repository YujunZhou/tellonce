#!/usr/bin/env python3
"""Phase B5 Tier A item 3 — Soft injection (UserPromptSubmit hook).

Read B5_SHADOW_ALERT.md (rolling cap N=3 violations from shadow judge), inject
"上 turn 你违反 X" notice into next-turn additionalContext.

Defenses:
  - B5_INJECT_DISABLED=1 env opt-out
  - 24h TTL: alerts older than 24h auto-skipped at read time
  - Empty alert file → exit 0 silently

Per `code-pref-101` JSON output.
Per `wf-pref-036` defensive fallbacks.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config  # Phase 4.1 解耦

SHADOW_ALERT_MD = path_config.get_shadow_alert_md_path()
SHADOW_LOG = path_config.get_shadow_log_path()

B5_INJECT_DISABLED = os.environ.get('B5_INJECT_DISABLED', '').lower() in ('1', 'true', 'yes')
TTL_HOURS = float(os.environ.get('B5_TTL_HOURS', '24'))
ALERT_ROLLING_CAP = int(os.environ.get('B5_ALERT_ROLLING_CAP', '3'))


def _read_recent_alerted_violations(hours=24):
    """Read shadow log, return alerted=True entries within TTL window. Most recent first."""
    if not os.path.exists(SHADOW_LOG):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
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
    """Build additionalContext markdown from alert list. v2 (post 续写重复 brainstorm 思路 G):
    内化提醒, 不鼓励本轮前置铺垫/道歉/声明遵守.
    """
    if not alerts:
        return None
    lines = ['### B5 影子判官上轮 flag 的规则 (静默遵守, 不要在本轮显式提及):']
    for a in alerts:
        rid = a.get('rule_id', '<unknown>')
        desc = a.get('rule_desc', '')
        conf = a.get('judge_confidence', '?')
        feedback = a.get('feedback', '')[:150]
        lines.append(f'- **[{rid}]** {desc} (信心 {conf})')
        if feedback:
            lines.append(f'  修正方向: {feedback}')
    lines.append('')
    lines.append('🔇 **本轮回复中静默遵守即可**, 不要做以下任何动作:')
    lines.append('  ❌ 不要道歉或确认收到 (\"我注意到\" / \"上轮我违反了\" / \"会改正\")')
    lines.append('  ❌ 不要前置声明本轮会遵守这些规则')
    lines.append('  ❌ 不要在回复开头铺垫上轮违规背景')
    lines.append('  ✅ 直接给本轮用户问题的答案, 内容自然遵守上述规则')
    return '\n'.join(lines)


def main():
    """UserPromptSubmit hook entrypoint."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if B5_INJECT_DISABLED:
        sys.exit(0)

    alerts = _read_recent_alerted_violations(hours=TTL_HOURS)
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
