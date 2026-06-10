#!/usr/bin/env bash
# Preference-Tracker doctor — runs unit + smoke + path/permission/CLI checks.
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/doctor.sh [--quick] [--rollback]
#
# Exit:
#   0 all PASS
#   1 1+ FAIL
#
# Flags:
#   --quick: skip slow tests (subprocess fixtures), run unit-level only
#   --rollback: find the latest settings versioned backup, cp it back + unregister hooks .sh

set -uo pipefail  # don't -e; we count fails

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${B5_PROJECT_ROOT:-$(pwd)}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"

QUICK=false
ROLLBACK=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=true; shift ;;
        --rollback) ROLLBACK=true; shift ;;
        *) echo "Unknown arg: $1 (flags: --quick --rollback)"; exit 1 ;;
    esac
done

# ============================================================
# Rollback mode
# ============================================================
if [[ "${ROLLBACK}" == true ]]; then
    LATEST=$(ls -t "${SETTINGS}".v3_pre_pt_*.json 2>/dev/null | head -1)
    if [[ -z "${LATEST}" ]]; then
        echo "❌ No versioned backup found (settings.local.json.v3_pre_pt_*.json)"
        exit 1
    fi
    # Back up the CURRENT settings before overwriting with the pre-PT snapshot —
    # without this, any hook the user added by hand AFTER the most recent install
    # is lost forever once we cp.
    if [[ -f "${SETTINGS}" ]]; then
        TS=$(date +%Y%m%d-%H%M%S)
        ROLLBACK_BACKUP="${SETTINGS}.v3_pre_rollback_${TS}.json"
        cp "${SETTINGS}" "${ROLLBACK_BACKUP}"
        echo "  backed up current settings to ${ROLLBACK_BACKUP} (prevents rollback permanently losing manual user edits)"
    fi
    echo "Rolling back settings: ${LATEST} → ${SETTINGS}"
    cp "${LATEST}" "${SETTINGS}"
    echo "settings rollback complete. project-local hooks ${HOOKS_DIR}/ kept."
    echo "  PT v1+ no longer registers these (settings rolled back no longer references them); decide for yourself whether to delete them."
    echo "✅ Rollback complete. state + memory preserved."
    exit 0
fi

# ============================================================
# Test mode
# ============================================================
PASS=0
FAIL=0
FAILED_TESTS=()

run_test() {
    local name="$1"
    shift
    if "$@" > /tmp/doctor_test_$$ 2>&1; then
        echo "  ✓ ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ ${name}"
        cat /tmp/doctor_test_$$ | head -10 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("${name}")
    fi
    rm -f /tmp/doctor_test_$$
}

echo "Preference-Tracker doctor — pre-flight"
echo "  HOME: ${HOME}"
echo "  PROJECT: ${PROJECT_ROOT}"
echo ""

# ============================================================
# Test group 1: Unit tests
# ============================================================
echo "[1/4] Unit tests (path_config + deterministic + shadow + chaos + rule_params + auto_light_entry):"
run_test "test_path_config"        python3 "${SKILL_DIR}/lib/test_path_config.py"
run_test "test_deterministic_block" python3 "${SKILL_DIR}/lib/test_deterministic_block.py"
run_test "test_verify_retry_shadow" python3 "${SKILL_DIR}/lib/test_verify_retry_shadow.py"
run_test "test_chaos_fault_injection" python3 "${SKILL_DIR}/lib/test_chaos_fault_injection.py"
run_test "test_rule_params" python3 "${SKILL_DIR}/lib/test_rule_params.py"
# test_b4_blocking now uses path_config + tempfile, so every user can run it.
# No more HOME-based skip.
run_test "test_b4_blocking (14)" python3 "${SKILL_DIR}/lib/test_b4_blocking.py"
# hook auto-fallback prevents 600s false positives; adds 7 units
run_test "test_auto_light_entry (7)" python3 "${SKILL_DIR}/lib/test_auto_light_entry.py"

# ============================================================
# Test group 2: Path / permission
# ============================================================
echo ""
echo "[2/4] Path / permission:"

# 2.1 path_config detect passes
run_test "path_config debug print" \
    python3 "${SKILL_DIR}/lib/path_config.py"

# 2.2 state dirs writable
state_writable_test() {
    # env-channel SKILL_DIR (avoids breaking when path contains ').
    env PT_LIB="${SKILL_DIR}/lib" PYTHONIOENCODING=utf-8 python3 - <<'EOF'
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
import path_config
for d in [path_config.get_state_dir(), path_config.get_obs_log_dir(), path_config.get_memory_dir()]:
    os.makedirs(d, exist_ok=True)
    test_path = os.path.join(d, '.dr_test')
    with open(test_path, 'w') as f:
        f.write('ok')
    os.remove(test_path)
print('OK')
EOF
}
run_test "state/obs_log/memory dirs writable" state_writable_test

# ============================================================
# Test group 3: Hook registration + CLI
# ============================================================
echo ""
echo "[3/4] Hook registration + CLI:"

# Accept user-global registration as PASS. The project-local settings.local.json
# may exist (user has their own hooks) without PT. Claude Code merges
# user+project+local at session start, so user-global is enough for hooks to
# fire from any cwd in this repo.
USER_GLOBAL="${HOME}/.claude/settings.json"
PROJ_OK=false
GLOBAL_OK=false
if [[ -f "${SETTINGS}" ]]; then
    if python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${SETTINGS}" --hooks-dir "${SKILL_DIR}/hooks" --verify > /dev/null 2>&1; then
        PROJ_OK=true
    fi
fi
if [[ -f "${USER_GLOBAL}" ]]; then
    if python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${USER_GLOBAL}" --hooks-dir "${SKILL_DIR}/hooks" --verify > /dev/null 2>&1; then
        GLOBAL_OK=true
    fi
fi
if [[ "${PROJ_OK}" == true ]]; then
    echo "  ✓ hooks registered in ${SETTINGS} (project-local)"
    PASS=$((PASS + 1))
elif [[ "${GLOBAL_OK}" == true ]]; then
    echo "  ✓ hooks registered in ${USER_GLOBAL} (user-global, effective from any cwd)"
    PASS=$((PASS + 1))
else
    echo "  ✗ No PT hook registration found (neither in ${SETTINGS} nor ${USER_GLOBAL})"
    echo "    User-global: bash ${SKILL_DIR}/install.sh"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("hooks registration")
fi

# claude CLI (warn only)
if command -v claude > /dev/null 2>&1; then
    echo "  ✓ claude CLI in PATH"
    PASS=$((PASS + 1))
else
    echo "  ⚠ claude CLI not in PATH (shadow judge cannot run, but deterministic still works)"
fi

# ============================================================
# Test group 4: Smoke (subprocess violation block)
# ============================================================
if [[ "${QUICK}" != true ]]; then
    echo ""
    echo "[4/4] Smoke test (subprocess violation check):"
    smoke_test() {
        TMP_TRANSCRIPT=$(mktemp)
        cat > "${TMP_TRANSCRIPT}" <<'EOF'
{"type":"user","message":{"content":"help me fix this"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Sure, I will fix the stub function and merge it into main."}]}}
EOF
        SID="doctor-smoke-$$"
        rm -f "${SKILL_DIR}/../../projects/${SID}.json" 2>/dev/null
        # rm any prior streak for this sid
        if [[ -n "${B5_STATE_DIR:-}" ]]; then
            rm -f "${B5_STATE_DIR}/b5_deterministic_streak/${SID}.json" 2>/dev/null
        fi
        STDIN_JSON='{"session_id":"'"${SID}"'","transcript_path":"'"${TMP_TRANSCRIPT}"'"}'
        # The public build ships no built-in rules. PT_TEST_FORCE_VIOLATION drives a
        # synthetic violation and PT_ENFORCE opts the (observe-only-by-default) block
        # gate on, so this exercises the end-to-end block path without any personal rule.
        OUTPUT=$(echo "${STDIN_JSON}" | PT_ENFORCE=1 PT_TEST_FORCE_VIOLATION=1 python3 "${SKILL_DIR}/lib/deterministic_block.py" 2>&1)
        RC=$?
        rm -f "${TMP_TRANSCRIPT}"
        if [[ ${RC} -eq 2 ]] && echo "${OUTPUT}" | grep -q test-synthetic; then
            return 0
        fi
        echo "  smoke FAIL: rc=${RC}, output=${OUTPUT}" >&2
        return 1
    }
    run_test "smoke: forced violation → exit 2 + block reason (Python entry)" smoke_test

    # Also exercise the .sh wrapper end-to-end. The Python smoke above misses the
    # stdin double-read bug class — `_INPUT_SC=$(cat)` in wrapper, then
    # `exec python3` was getting EOF stdin. This wrapper test specifically
    # validates that the printf-pipe re-feeds stdin to the Python entry. If this
    # fails but the Python smoke passes, suspect wrapper plumbing.
    wrapper_smoke_test() {
        TMP_TRANSCRIPT=$(mktemp)
        cat > "${TMP_TRANSCRIPT}" <<'EOF'
{"type":"user","message":{"content":"help me fix this"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Sure, I will fix the stub function and merge it into main."}]}}
EOF
        SID="doctor-wrapper-smoke-$$"
        if [[ -n "${B5_STATE_DIR:-}" ]]; then
            rm -f "${B5_STATE_DIR}/b5_deterministic_streak/${SID}.json" 2>/dev/null
        fi
        STDIN_JSON='{"session_id":"'"${SID}"'","transcript_path":"'"${TMP_TRANSCRIPT}"'"}'
        # NB: pipe through the actual Stop-hook wrapper, not Python directly.
        OUTPUT=$(echo "${STDIN_JSON}" | PT_ENFORCE=1 PT_TEST_FORCE_VIOLATION=1 bash "${SKILL_DIR}/hooks/memory-deterministic-block.sh" 2>&1)
        RC=$?
        rm -f "${TMP_TRANSCRIPT}"
        if [[ ${RC} -eq 2 ]] && echo "${OUTPUT}" | grep -q test-synthetic; then
            return 0
        fi
        echo "  wrapper smoke FAIL: rc=${RC}, output=${OUTPUT}" >&2
        return 1
    }
    run_test "smoke: wrapper end-to-end (catches stdin re-feed regressions)" wrapper_smoke_test
fi

# ============================================================
# Summary
# ============================================================
echo ""
echo "─────────────────────────────────────────"
if [[ ${FAIL} -eq 0 ]]; then
    echo "✅ doctor PASS (${PASS} tests)"
    exit 0
else
    echo "❌ doctor FAIL (${FAIL} fails / ${PASS} pass)"
    echo "Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - ${t}"
    done
    exit 1
fi
