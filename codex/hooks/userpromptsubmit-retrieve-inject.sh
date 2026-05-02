#!/usr/bin/env bash
# Codex UserPromptSubmit hook: retrieve relevant memory rules + inject as
# additionalContext. Mirrors CC's memory-retrieve-inject.sh.
#
# Codex hook stdin: JSON with `prompt`, `cwd`, `session_id`, `transcript_path`.
# Output: JSON with hookSpecificOutput.additionalContext.
#
# Round-10 (2026-05-02): default backend is now 'cli' (small-model semantic
# match). Codex picks codex CLI + gpt-5.4-mini by default; user can override
# via B5_RETRIEVE_BACKEND/B5_RETRIEVE_CLI/B5_RETRIEVE_MODEL.
#
# Defensive: any failure -> exit 0 silently (never block codex turns).
set +e

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

# Per-runtime defaults: codex hook runs in codex sessions, so route the
# retrieve CLI through codex + gpt-5.4-mini. User can override via env.
export B5_RETRIEVE_CLI="${B5_RETRIEVE_CLI:-codex}"
export B5_RETRIEVE_MODEL="${B5_RETRIEVE_MODEL:-gpt-5.4-mini}"

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

# Run retrieve_inject. PYTHONIOENCODING=utf-8 防 LANG 不是 utf-8 时 stdout 崩.
printf '%s' "${PT_STDIN}" | PYTHONIOENCODING=utf-8 PYTHONPATH="${SHARED_LIB}" \
    timeout 30 python3 "${SHARED_LIB}/retrieve_inject.py" 2>/dev/null
exit 0
