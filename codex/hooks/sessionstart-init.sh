#!/usr/bin/env bash
# Codex SessionStart hook: lazy-initialize project state when codex enters a
# fresh project (no .codex/preference-tracker/ dir yet). Idempotent — exits
# fast if state already exists.
#
#   - Pass --no-hooks so SessionStart NEVER writes ~/.codex/hooks.json.
#     Hooks should only be registered by the bash install.sh phase 2 path
#     where the user explicitly opted in.
#   - Redirect BOTH stdout and stderr to /dev/null. Codex SessionStart hook
#     stdout is the JSON channel; install()'s "added 5 / skipped 0" output
#     would be invalid hook JSON and codex would log "invalid session start
#     JSON output".
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SCRIPT_DIR}/.."

PT_STDIN="$(cat)"

CODEX_CWD="$(echo "${PT_STDIN}" | PYTHONIOENCODING=utf-8 python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("cwd") or "")
except Exception:
    print("")
' 2>/dev/null)"

if [[ -z "${CODEX_CWD}" || ! -d "${CODEX_CWD}" ]]; then
    exit 0
fi

# Already initialized?
if [[ -f "${CODEX_CWD}/.codex/preference-tracker/registration.json" ]]; then
    exit 0
fi

# Init state. Silence stdout entirely (codex parses it as JSON; we have
# nothing to inject) and silence stderr too (any Python noise must not
# surface to the user during routine SessionStart).
PYTHONIOENCODING=utf-8 PYTHONPATH="${SKILL_DIR}" \
    timeout 10 python3 -m codex_preftrack install --project-root "${CODEX_CWD}" --no-hooks \
    > /dev/null 2>/dev/null
exit 0
