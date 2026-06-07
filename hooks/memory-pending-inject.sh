#!/usr/bin/env bash
# memory-pending-inject.sh — UserPromptSubmit hook (Phase A.1, Session A 2026-04-25).
# Reads pending_queue.jsonl; if non-empty, emits additionalContext warning new
# session about unfinalized memory entries from prior session(s).
# Non-destructive: any failure → exit 0 silently.
_PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
out=$(python3 "${_PT_LIB}/pending_queue_manager.py" inject 2>/dev/null)
if [ -n "$out" ]; then
    # Emit JSON-formatted hookSpecificOutput so the harness injects this as
    # additionalContext (matching memory-retrieve-inject.sh contract).
    python3 - "$out" <<'PY'
import json, sys
text = sys.argv[1]
header = '### Pending memory finalize required (carried over from prior session crash):'
body = header + '\n' + text
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': body,
    }
}, ensure_ascii=False))
PY
fi
exit 0
