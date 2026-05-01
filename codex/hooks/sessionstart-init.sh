#!/usr/bin/env bash
# Codex SessionStart hook: lazy-initialize project state when codex enters a
# fresh project (no .codex/preference-tracker/ dir yet). Idempotent — exits
# fast if state already exists.
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

# Init state.
PYTHONIOENCODING=utf-8 PYTHONPATH="${SKILL_DIR}" \
    timeout 10 python3 -m codex_preftrack install --project-root "${CODEX_CWD}" 2>/dev/null
exit 0
