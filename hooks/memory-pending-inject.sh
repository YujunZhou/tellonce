#!/usr/bin/env bash
# memory-pending-inject.sh — UserPromptSubmit hook (pending queue → next-turn inject).
# Reads pending_queue.jsonl; if non-empty, emits additionalContext warning new
# session about unfinalized memory entries from prior session(s).
# Non-destructive: any failure → exit 0 silently.
# Child-session guard: the shadow judge / retrieve cli spawn nested CLI
# sessions whose UserPromptSubmit would otherwise prepend this reminder to a
# prompt demanding one-line JSON (verdict contamination). Honors the same
# opt-outs as the sibling inject hooks.
case "${PT_CHILD_SESSION:-}" in 1|true|yes|on) exit 0 ;; esac
if [ "${PT_INJECT_DISABLED:-${B5_INJECT_DISABLED:-}}" = "1" ]; then exit 0; fi
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
