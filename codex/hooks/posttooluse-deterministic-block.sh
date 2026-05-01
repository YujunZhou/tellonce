#!/usr/bin/env bash
# Codex PostToolUse hook: deterministic regex/fingerprint scan over agent-
# authored tool input (Write/Edit content, Bash command). Mirrors CC's
# memory-deterministic-block.sh strategy but adapts to codex's tool-call
# centric surface (codex has no Stop hook).
#
# Mode-aware:
#   - audit_only / wrapper: log + advisory stderr only (exit 0)
#   - blocking: exit 2 + decision:block JSON when violations detected
#
# Defensive: any failure -> exit 0 silently (never break agent's tool loop).
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SCRIPT_DIR}/.."

if [[ ! -f "${SKILL_DIR}/codex_preftrack/codex_posttooluse_block.py" ]]; then
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
    export CODEX_PROJECT_ROOT="${CODEX_CWD}"
fi

# Run the adapter. Forward its exit code so blocking mode actually blocks.
printf '%s' "${PT_STDIN}" | PYTHONIOENCODING=utf-8 PYTHONPATH="${SKILL_DIR}" \
    timeout 15 python3 -m codex_preftrack.codex_posttooluse_block 2>&1
RC=$?
exit $RC
