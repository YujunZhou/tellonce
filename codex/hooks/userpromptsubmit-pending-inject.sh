#!/usr/bin/env bash
# Codex UserPromptSubmit hook: cross-session pending memory reminders.
# Mirrors CC's memory-pending-inject.sh.
#
# pending_queue_manager.py emits raw text (legacy CC contract); we wrap it in
# the codex hookSpecificOutput JSON envelope (UserPromptSubmitHookSpecificOutputWire
# requires hookEventName + additionalContext, additionalProperties: false).
set +e

# Portable timeout: GNU `timeout` is absent on stock macOS. Fall back to
# gtimeout (brew coreutils) or, failing that, run without a timeout.
_pt_timeout() {
    _pt_secs="$1"; shift
    if command -v timeout >/dev/null 2>&1; then timeout "${_pt_secs}" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then gtimeout "${_pt_secs}" "$@"
    else "$@"; fi
}


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_LIB="${SCRIPT_DIR}/../shared_lib"

if [[ ! -f "${SHARED_LIB}/pending_queue_manager.py" ]]; then
    exit 0
fi

PT_STDIN="$(cat)"
[[ -z "${PT_STDIN}" ]] && exit 0

CODEX_CWD="$(echo "${PT_STDIN}" | PYTHONIOENCODING=utf-8 python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("cwd") or "")
except Exception:
    print("")
' 2>/dev/null)"
if [[ -n "${CODEX_CWD}" && -d "${CODEX_CWD}" ]]; then
    export B5_PROJECT_ROOT="${CODEX_CWD}"
fi

# Same memory-dir bridge as userpromptsubmit-retrieve-inject.sh: shared_lib's
# pt_platform is CC's copy and resolves the memory dir to ~/.claude/... —
# without the bridge, pending_queue_manager's promoted-rule dedup/prune never
# sees rules promoted under .codex, so a queue entry re-nags on every prompt
# forever on mixed CC+Codex projects.
if [[ -z "${B5_MEMORY_DIR:-}" && -n "${B5_PROJECT_ROOT:-}" ]]; then
    export B5_MEMORY_DIR="${B5_PROJECT_ROOT}/.codex/tellonce/memories/active"
fi

PT_TEXT="$(PYTHONIOENCODING=utf-8 PYTHONPATH="${SHARED_LIB}" \
    _pt_timeout 15 python3 "${SHARED_LIB}/pending_queue_manager.py" inject 2>/dev/null)"
if [ -n "${PT_TEXT}" ]; then
    PT_TEXT="${PT_TEXT}" PYTHONIOENCODING=utf-8 python3 -c '
import json, os
text = os.environ.get("PT_TEXT","")
header = "### Pending memory finalize required (carried over from prior session):"
body = header + "\n" + text
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": body,
    }
}, ensure_ascii=False))
' 2>/dev/null
fi
exit 0
