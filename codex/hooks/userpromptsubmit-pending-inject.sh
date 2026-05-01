#!/usr/bin/env bash
# Codex UserPromptSubmit hook: cross-session pending memory reminders.
# Mirrors CC's memory-pending-inject.sh.
set +e

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

printf '%s' "${PT_STDIN}" | PYTHONIOENCODING=utf-8 PYTHONPATH="${SHARED_LIB}" \
    timeout 15 python3 "${SHARED_LIB}/pending_queue_manager.py" inject 2>/dev/null
exit 0
