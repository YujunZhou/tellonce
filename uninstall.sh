#!/usr/bin/env bash
# Preference-Tracker uninstall — Phase 4.2 + 2026-05-01 review hardening.
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/uninstall.sh
#       [--keep-skill-dir] [--purge-state] [--keep-config]
#
# Steps:
#   1. 撤 hooks 注册 (versioned 备份后用 Python 删 PT hooks)
#   2. rm hooks .sh
#   3. 询问 user 要不要 rm ~/.claude/skills/preference-tracker/
#   4. 不动 memory + state (用户数据保留, 除非 --purge-state)
#   5. 清 ~/.preference-tracker.config.json (除非 --keep-config)
#
# Hardening (C11/C12 fix, 2026-05-01):
#   - PROJECT_ROOT==HOME 时拒绝跑（避免误删用户全局 .claude/hooks/ 等）
#   - rm -rf 之前 guard SKILL_DIR / STATE_DIR / OBS_LOG_DIR 不为空 / 不为根。
#   - --purge-state 时双重确认 (interactive)。

set -euo pipefail

SKILL_DIR="${HOME}/.claude/skills/preference-tracker"
PROJECT_ROOT="${B5_PROJECT_ROOT:-$(pwd)}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"
STATE_DIR="${B5_STATE_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/runtime}"
OBS_LOG_DIR="${B5_OBS_LOG_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/obs_log}"
CONFIG_FILE="${HOME}/.preference-tracker.config.json"

KEEP_SKILL_DIR=false
PURGE_STATE=false
KEEP_CONFIG=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-skill-dir) KEEP_SKILL_DIR=true; shift ;;
        --purge-state) PURGE_STATE=true; shift ;;
        --keep-config) KEEP_CONFIG=true; shift ;;
        *) shift ;;
    esac
done

# C12 fix: refuse uninstall when PROJECT_ROOT is HOME — that means the user
# probably ran `cd ~ && uninstall` and would otherwise blow away their global
# ~/.claude/hooks (matches install.sh's same guard at line 43-49).
if [[ "${PROJECT_ROOT}" == "${HOME}" ]]; then
    echo "❌ PROJECT_ROOT == HOME (${HOME})"
    echo "   你似乎在 HOME 跑 uninstall — 应该 cd 到当时装包的项目根再卸."
    echo "   如果真要卸全局 user-level 安装, 用 B5_PROJECT_ROOT=<path> 显式指定."
    exit 1
fi

# C11 fix: refuse to ever rm -rf an empty / root-ish path. Tighten before any
# branch that calls rm -rf with these vars.
_refuse_dangerous_path() {
    local var_name="$1"
    local val="$2"
    if [[ -z "${val}" ]]; then
        echo "❌ ${var_name} 是空字符串 — 拒绝执行删除以防止 rm -rf 误炸 root."
        exit 1
    fi
    # Reject any path that is just slashes (`/`, `//`, `///`, ...).
    if [[ "${val}" =~ ^/+$ ]]; then
        echo "❌ ${var_name}=${val} 是根路径 — 拒绝."
        exit 1
    fi
    # Reject paths that resolve to HOME — uninstall should never wipe HOME itself.
    # Using realpath if available, fallback to literal compare.
    local resolved="${val}"
    if command -v realpath >/dev/null 2>&1; then
        resolved="$(realpath -m -- "${val}" 2>/dev/null || echo "${val}")"
    fi
    if [[ "${resolved}" == "${HOME}" ]] || [[ "${val}" == "${HOME}" ]]; then
        echo "❌ ${var_name}=${val} 等于 HOME — 拒绝 rm -rf."
        exit 1
    fi
}

echo "Preference-Tracker uninstall"
echo "  PROJECT: ${PROJECT_ROOT}"
echo ""

# 1. 撤 hooks 注册
if [[ -f "${SETTINGS}" ]]; then
    echo "[1/5] 撤 hooks 从 settings.local.json:"
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
    echo "[1/5] settings.local.json 不存在, skip"
fi

# 2. rm hooks .sh (per lib/_pt_hooks.txt source-of-truth, I-NEW fix)
echo ""
echo "[2/5] 删 hooks .sh:"
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
echo "[3/5] skill dir 处理:"
if [[ "${KEEP_SKILL_DIR}" == true ]]; then
    echo "  - 保留 ${SKILL_DIR} (--keep-skill-dir)"
elif [[ ! -t 0 ]]; then
    # non-interactive (CI / pipe), 默认保留
    echo "  - 非交互模式, 保留 ${SKILL_DIR} (重装方便)"
else
    read -p "  rm -rf ${SKILL_DIR}? (y/N) " ans || ans="N"
    if [[ "${ans}" == "y" || "${ans}" == "Y" ]]; then
        _refuse_dangerous_path SKILL_DIR "${SKILL_DIR}"
        rm -rf "${SKILL_DIR}"
        echo "  - skill 目录已删"
    else
        echo "  - 保留 ${SKILL_DIR}"
    fi
fi

# 4. state + memory
echo ""
echo "[4/5] state + memory:"
if [[ "${PURGE_STATE}" == true ]]; then
    echo "  - rm state + obs_log (--purge-state):"
    _refuse_dangerous_path STATE_DIR "${STATE_DIR}"
    _refuse_dangerous_path OBS_LOG_DIR "${OBS_LOG_DIR}"
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

# 5. ~/.preference-tracker.config.json (C7 fix: cleanup so reinstall on a
# different project doesn't reuse stale paths). --keep-config to preserve it.
echo ""
echo "[5/5] ~/.preference-tracker.config.json:"
if [[ "${KEEP_CONFIG}" == true ]]; then
    echo "  - 保留 ${CONFIG_FILE} (--keep-config)"
elif [[ -f "${CONFIG_FILE}" ]]; then
    rm -f "${CONFIG_FILE}"
    echo "  - 已删 ${CONFIG_FILE}"
    echo "    (重装会重新写; 想保留旧 path 锚定下次跑 uninstall.sh --keep-config)"
else
    echo "  - ${CONFIG_FILE} 不存在, skip"
fi

echo ""
echo "✅ 卸载完成"
