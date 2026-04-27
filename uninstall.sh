#!/usr/bin/env bash
# Preference-Tracker uninstall — Phase 4.2.
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/uninstall.sh [--keep-skill-dir] [--purge-state]
#
# Steps:
#   1. 撤 hooks 注册 (versioned 备份后用 Python 删 PT hooks)
#   2. rm hooks .sh
#   3. 询问 user 要不要 rm ~/.claude/skills/preference-tracker/
#   4. 不动 memory + state (用户数据保留, 除非 --purge-state)

set -euo pipefail

SKILL_DIR="${HOME}/.claude/skills/preference-tracker"
PROJECT_ROOT="${B5_PROJECT_ROOT:-$(pwd)}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"
STATE_DIR="${B5_STATE_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/runtime}"
OBS_LOG_DIR="${B5_OBS_LOG_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/obs_log}"

KEEP_SKILL_DIR=false
PURGE_STATE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-skill-dir) KEEP_SKILL_DIR=true; shift ;;
        --purge-state) PURGE_STATE=true; shift ;;
        *) shift ;;
    esac
done

echo "Preference-Tracker uninstall"
echo "  PROJECT: ${PROJECT_ROOT}"
echo ""

# 1. 撤 hooks 注册
if [[ -f "${SETTINGS}" ]]; then
    echo "[1/4] 撤 hooks 从 settings.local.json:"
    python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${SETTINGS}" \
        --hooks-dir "${HOOKS_DIR}" \
        --remove
    # M8 fix: 提示备份文件位置, 让用户知道回滚锚点
    LATEST_BACKUP=$(ls -t "${SETTINGS}".v3_pre_pt_*.json 2>/dev/null | head -1)
    if [[ -n "${LATEST_BACKUP}" ]]; then
        echo "  备份: ${LATEST_BACKUP}"
        echo "  回滚 (撤本次 --remove 改动): cp \"${LATEST_BACKUP}\" \"${SETTINGS}\""
    fi
else
    echo "[1/4] settings.local.json 不存在, skip"
fi

# 2. rm hooks .sh (per lib/_pt_hooks.txt source-of-truth, I-NEW fix)
echo ""
echo "[2/4] 删 hooks .sh:"
HOOK_LIST_FILE="${SKILL_DIR}/lib/_pt_hooks.txt"
if [[ -f "${HOOK_LIST_FILE}" ]]; then
    while IFS= read -r hook; do
        [[ -z "${hook}" || "${hook}" == \#* ]] && continue
        if [[ -f "${HOOKS_DIR}/${hook}" ]]; then
            rm -f "${HOOKS_DIR}/${hook}"
            echo "  - ${hook}"
        fi
    done < "${HOOK_LIST_FILE}"
else
    echo "  ⚠ ${HOOK_LIST_FILE} 不存在, 用 fallback inline 列表"
    for hook in memory-deterministic-block.sh memory-shadow-judge.sh memory-shadow-alert-inject.sh \
                memory-verify-compliance.sh memory-retrieve-inject.sh memory-pending-promote.sh \
                memory-pending-inject.sh check-observation-log.sh; do
        if [[ -f "${HOOKS_DIR}/${hook}" ]]; then
            rm -f "${HOOKS_DIR}/${hook}"
            echo "  - ${hook}"
        fi
    done
fi

# 3. 询问 skill dir
echo ""
echo "[3/4] skill dir 处理:"
if [[ "${KEEP_SKILL_DIR}" == true ]]; then
    echo "  - 保留 ${SKILL_DIR} (--keep-skill-dir)"
elif [[ ! -t 0 ]]; then
    # non-interactive (CI / pipe), 默认保留
    echo "  - 非交互模式, 保留 ${SKILL_DIR} (重装方便)"
else
    read -p "  rm -rf ${SKILL_DIR}? (y/N) " ans || ans="N"
    if [[ "${ans}" == "y" || "${ans}" == "Y" ]]; then
        rm -rf "${SKILL_DIR}"
        echo "  - skill 目录已删"
    else
        echo "  - 保留 ${SKILL_DIR}"
    fi
fi

# 4. state + memory
echo ""
echo "[4/4] state + memory:"
if [[ "${PURGE_STATE}" == true ]]; then
    echo "  - rm state + obs_log (--purge-state):"
    rm -rf "${STATE_DIR}" "${OBS_LOG_DIR}"
    echo "    rm -rf ${STATE_DIR}"
    echo "    rm -rf ${OBS_LOG_DIR}"
else
    echo "  - state + obs_log + memory 保留 (用户数据)"
    echo "    state: ${STATE_DIR}"
    echo "    obs_log: ${OBS_LOG_DIR}"
    echo "    memory: ~/.claude/projects/<cwd_escaped>/memory/ (不动)"
    echo ""
    echo "  完全删: bash ${SKILL_DIR}/uninstall.sh --purge-state"
fi

echo ""
echo "✅ 卸载完成"
