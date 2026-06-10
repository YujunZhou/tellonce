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

# Portable timeout: GNU `timeout` is absent on stock macOS. Fall back to
# gtimeout (brew coreutils) or, failing that, run without a timeout.
_pt_timeout() {
    _pt_secs="$1"; shift
    if command -v timeout >/dev/null 2>&1; then timeout "${_pt_secs}" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then gtimeout "${_pt_secs}" "$@"
    else "$@"; fi
}


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

# Run the adapter. Codex's PostToolUse hook protocol:
#   - stdout: hookSpecificOutput JSON (consumed by codex)
#   - stderr: human-readable block reason (shown to user when exit 2)
# DO NOT redirect stderr to stdout — that would corrupt the JSON channel
# and codex would print "hook returned invalid post-tool-use JSON output".
printf '%s' "${PT_STDIN}" | PYTHONIOENCODING=utf-8 PYTHONPATH="${SKILL_DIR}" \
    _pt_timeout 15 python3 -m codex_preftrack.codex_posttooluse_block
RC=$?
exit $RC
