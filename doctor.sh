#!/usr/bin/env bash
# Preference-Tracker doctor — Phase 4.2 (跑 unit + smoke + path/permission/CLI 检测).
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/doctor.sh [--quick] [--rollback]
#
# Exit:
#   0 全 PASS
#   1 1+ FAIL
#
# Flags:
#   --quick: skip slow tests (subprocess fixtures), 仅跑 unit-level
#   --rollback: 找 latest settings versioned backup, cp 回 + 撤 hooks .sh

set -uo pipefail  # don't -e; we count fails

SKILL_DIR="${HOME}/.claude/skills/preference-tracker"
PROJECT_ROOT="${B5_PROJECT_ROOT:-$(pwd)}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"

QUICK=false
ROLLBACK=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=true; shift ;;
        --rollback) ROLLBACK=true; shift ;;
        *) shift ;;
    esac
done

# ============================================================
# Rollback mode
# ============================================================
if [[ "${ROLLBACK}" == true ]]; then
    LATEST=$(ls -t "${SETTINGS}".v3_pre_pt_*.json 2>/dev/null | head -1)
    if [[ -z "${LATEST}" ]]; then
        echo "❌ 没找到 versioned backup (settings.local.json.v3_pre_pt_*.json)"
        exit 1
    fi
    echo "回滚 settings: ${LATEST} → ${SETTINGS}"
    cp "${LATEST}" "${SETTINGS}"
    echo "撤 hooks .sh (per lib/_pt_hooks.txt source-of-truth):"
    HOOK_LIST_FILE="${SKILL_DIR}/lib/_pt_hooks.txt"
    if [[ ! -f "${HOOK_LIST_FILE}" ]]; then
        echo "❌ ${HOOK_LIST_FILE} 不存在 (skill 包不完整)"
        exit 1
    fi
    while IFS= read -r hook; do
        # skip blank + 注释
        [[ -z "${hook}" || "${hook}" == \#* ]] && continue
        if [[ -f "${HOOKS_DIR}/${hook}" ]]; then
            rm -f "${HOOKS_DIR}/${hook}"
            echo "  - ${hook}"
        fi
    done < "${HOOK_LIST_FILE}"
    echo "✅ 回滚完成. state + memory 保留."
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
echo "[1/4] Unit tests (path_config + deterministic + shadow + chaos + rule_params):"
run_test "test_path_config (12)"        python3 "${SKILL_DIR}/lib/test_path_config.py"
run_test "test_deterministic_block (14)" python3 "${SKILL_DIR}/lib/test_deterministic_block.py"
run_test "test_verify_retry_shadow (12)" python3 "${SKILL_DIR}/lib/test_verify_retry_shadow.py"
run_test "test_chaos_fault_injection (12)" python3 "${SKILL_DIR}/lib/test_chaos_fault_injection.py"
run_test "test_rule_params (6, Phase 7)" python3 "${SKILL_DIR}/lib/test_rule_params.py"
# Step 4 fix (Phase 8 I5 / kickoff §3 Step 4): test_b4_blocking 已改用 path_config + tempfile,
# 现在所有 user 都能跑. 不再 HOME-based skip.
run_test "test_b4_blocking (14)" python3 "${SKILL_DIR}/lib/test_b4_blocking.py"

# ============================================================
# Test group 2: Path / permission
# ============================================================
echo ""
echo "[2/4] Path / permission:"

# 2.1 path_config detect 通
run_test "path_config debug print" \
    python3 "${SKILL_DIR}/lib/path_config.py"

# 2.2 state dirs 可写
state_writable_test() {
    python3 - <<EOF
import sys
sys.path.insert(0, '${SKILL_DIR}/lib')
import path_config, os
for d in [path_config.get_state_dir(), path_config.get_obs_log_dir(), path_config.get_memory_dir()]:
    os.makedirs(d, exist_ok=True)
    test_path = os.path.join(d, '.dr_test')
    with open(test_path, 'w') as f:
        f.write('ok')
    os.remove(test_path)
print('OK')
EOF
}
run_test "state/obs_log/memory dirs 可写" state_writable_test

# 2.3 chinese_ratio sanity (10 chinese chars + "stub" 4 english = 10/14 ≈ 0.71)
chinese_ratio_test() {
    python3 - <<EOF
import sys
sys.path.insert(0, '${SKILL_DIR}/lib')
from deterministic_block import chinese_ratio
r = chinese_ratio('好好好好好好好好好好 stub')
assert 0.6 < r < 1.0, f'chinese_ratio sanity failed: {r}'
r2 = chinese_ratio('hello world all english')
assert r2 < 0.1, f'chinese_ratio english sanity failed: {r2}'
print('OK')
EOF
}
run_test "chinese_ratio helper" chinese_ratio_test

# ============================================================
# Test group 3: Hook 注册 + CLI
# ============================================================
echo ""
echo "[3/4] Hook 注册 + CLI:"

if [[ -f "${SETTINGS}" ]]; then
    run_test "hooks 注册在 settings.local.json" \
        python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
            --settings "${SETTINGS}" --hooks-dir "${HOOKS_DIR}" --verify
else
    echo "  ⚠ settings.local.json 不存在 (install 先?)"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("settings.local.json")
fi

# claude CLI (warn only)
if command -v claude > /dev/null 2>&1; then
    echo "  ✓ claude CLI 在 PATH"
    PASS=$((PASS + 1))
else
    echo "  ⚠ claude CLI 不在 PATH (shadow judge 跑不了, 但 deterministic 仍 work)"
fi

# ============================================================
# Test group 4: Smoke (subprocess violation 验阻断)
# ============================================================
if [[ "${QUICK}" != true ]]; then
    echo ""
    echo "[4/4] Smoke test (subprocess violation 验):"
    smoke_test() {
        TMP_TRANSCRIPT=$(mktemp)
        cat > "${TMP_TRANSCRIPT}" <<'EOF'
{"type":"user","message":{"content":"帮我修一下"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"好的我来修复这个 stub 的问题。我们需要把它 merge 进主分支,然后处理一下相关的依赖关系,完成后通知所有相关的团队成员。"}]}}
EOF
        SID="doctor-smoke-$$"
        rm -f "${SKILL_DIR}/../../projects/${SID}.json" 2>/dev/null
        # rm any prior streak for this sid
        if [[ -n "${B5_STATE_DIR:-}" ]]; then
            rm -f "${B5_STATE_DIR}/b5_deterministic_streak/${SID}.json" 2>/dev/null
        fi
        STDIN_JSON='{"session_id":"'"${SID}"'","transcript_path":"'"${TMP_TRANSCRIPT}"'"}'
        OUTPUT=$(echo "${STDIN_JSON}" | python3 "${SKILL_DIR}/lib/deterministic_block.py" 2>&1)
        RC=$?
        rm -f "${TMP_TRANSCRIPT}"
        if [[ ${RC} -eq 2 ]] && echo "${OUTPUT}" | grep -q lang-pit-130; then
            return 0
        fi
        echo "  smoke FAIL: rc=${RC}, output=${OUTPUT}" >&2
        return 1
    }
    run_test "smoke: 中文 + inline english → exit 2 + lang-pit-130" smoke_test
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
