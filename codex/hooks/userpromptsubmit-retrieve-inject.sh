#!/usr/bin/env bash
# Codex UserPromptSubmit hook: retrieve relevant memory rules + inject as
# additionalContext. Mirrors CC's memory-retrieve-inject.sh.
#
# Codex hook stdin: JSON with `prompt`, `cwd`, `session_id`, `transcript_path`.
# Output: JSON with hookSpecificOutput.additionalContext.
#
# Default backend is local `cli` (codex exec) semantic
# retrieval, matching the other variants. No prompt data leaves the machine to
# a third-party API by default. Opt into an external provider via
# PT_RETRIEVE_BACKEND=api + PT_RETRIEVE_API_PROVIDER/PT_RETRIEVE_MODEL
# (legacy B5_ names still work).
#
# Defensive: any failure -> exit 0 silently (never block codex turns).
set +e

# Portable timeout: GNU `timeout` is absent on stock macOS. Fall back to
# gtimeout (brew coreutils) or, failing that, run without a timeout.
_pt_timeout() {
    _pt_secs="$1"; shift
    if command -v timeout >/dev/null 2>&1; then timeout "${_pt_secs}" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then gtimeout "${_pt_secs}" "$@"
    else "$@"; fi
}


# Recursion guard: if we're inside a nested codex exec spawned by
# retrieve_inject itself, exit immediately so we don't loop.
if [ "${B5_RETRIEVE_RECURSION_GUARD}" = "1" ]; then
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SCRIPT_DIR}/.."
SHARED_LIB="${SKILL_DIR}/shared_lib"

if [[ ! -f "${SHARED_LIB}/retrieve_inject.py" ]]; then
    exit 0
fi

# Per-runtime default: local `cli` (codex) retrieval — no external network
# egress by default. Set PT_RETRIEVE_BACKEND=api to opt into a third-party
# provider (PT_RETRIEVE_API_PROVIDER/PT_RETRIEVE_MODEL then apply; legacy B5_ ok).
export B5_RETRIEVE_BACKEND="${PT_RETRIEVE_BACKEND:-${B5_RETRIEVE_BACKEND:-cli}}"
export B5_RETRIEVE_CLI="${PT_RETRIEVE_CLI:-${B5_RETRIEVE_CLI:-codex}}"

# Capture stdin once so we can route to PYTHONPATH-augmented subprocess.
PT_STDIN="$(cat)"
if [[ -z "${PT_STDIN}" ]]; then
    exit 0
fi

# Pass the pwd to path_config via env (codex hooks' cwd is the project, but
# subprocess sometimes resolves it differently).
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

# Run retrieve_inject. PYTHONIOENCODING=utf-8 prevents stdout from crashing when LANG is not utf-8.
printf '%s' "${PT_STDIN}" | PYTHONIOENCODING=utf-8 PYTHONPATH="${SHARED_LIB}" \
    _pt_timeout 30 python3 "${SHARED_LIB}/retrieve_inject.py" 2>/dev/null
exit 0
