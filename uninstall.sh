#!/usr/bin/env bash
# Tellonce uninstall — review-hardened.
#
# Usage:
#   bash ~/.claude/skills/tellonce/uninstall.sh
#       [--keep-skill-dir] [--purge-state] [--keep-config]
#
# Steps:
#   1. Unregister hooks (versioned backup, then remove PT hooks with Python)
#   2. rm hooks .sh
#   3. Ask the user whether to rm ~/.claude/skills/tellonce/
#   4. Leave memory + state untouched (user data preserved unless --purge-state)
#   5. Clean ~/.tellonce.config.json (unless --keep-config)
#
# Hardening:
#   - Refuse to run when PROJECT_ROOT==HOME (avoids deleting the user's global .claude/hooks/, etc.)
#   - Before rm -rf, guard that SKILL_DIR / STATE_DIR / OBS_LOG_DIR are non-empty / not root.
#   - Double-confirm (interactive) on --purge-state.

set -euo pipefail

# Self-locate (same as install.sh/doctor.sh): uninstall must target the clone
# this script lives in, not a hardcoded ~/.claude/skills path.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PT_PROJECT_ROOT:-${B5_PROJECT_ROOT:-$(pwd)}}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"
STATE_DIR="${PT_STATE_DIR:-${B5_STATE_DIR:-${PROJECT_ROOT}/.claude/tellonce-state/runtime}}"
OBS_LOG_DIR="${PT_OBS_LOG_DIR:-${B5_OBS_LOG_DIR:-${PROJECT_ROOT}/.claude/tellonce-state/obs_log}}"
CONFIG_FILE="${HOME}/.tellonce.config.json"

KEEP_SKILL_DIR=false
PURGE_STATE=false
KEEP_CONFIG=false
KEEP_GLOBAL=false
PURGE_LEGACY_HOOKS=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-skill-dir) KEEP_SKILL_DIR=true; shift ;;
        --purge-state) PURGE_STATE=true; shift ;;
        --keep-config) KEEP_CONFIG=true; shift ;;
        --keep-global) KEEP_GLOBAL=true; shift ;;
        --purge-legacy-project-hooks) PURGE_LEGACY_HOOKS=true; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1 (see --help)"; exit 1 ;;
    esac
done

# Refuse uninstall when PROJECT_ROOT is HOME — that means the user probably ran
# `cd ~ && uninstall` and would otherwise blow away their global ~/.claude/hooks
# (matches install.sh's same guard).
# An EXPLICIT PT_PROJECT_ROOT/B5_PROJECT_ROOT means the user knows what they
# are targeting — only refuse when HOME was picked up implicitly from cwd.
if [[ "${PROJECT_ROOT}" == "${HOME}" && -z "${PT_PROJECT_ROOT:-}${B5_PROJECT_ROOT:-}" ]]; then
    echo "❌ PROJECT_ROOT == HOME (${HOME})"
    echo "   You appear to be running uninstall in HOME — cd into the project root you installed from."
    echo "   To remove a user-global (Option A) install instead, run:"
    echo "   python3 ${SKILL_DIR}/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ${SKILL_DIR}/hooks --remove"
    exit 1
fi

# Refuse to ever rm -rf an empty / root-ish path. Tighten before any branch that
# calls rm -rf with these vars.
_refuse_dangerous_path() {
    local var_name="$1"
    local val="$2"
    if [[ -z "${val}" ]]; then
        echo "❌ ${var_name} is an empty string — refusing to delete to prevent rm -rf wiping root."
        exit 1
    fi
    # Reject any path that is just slashes (`/`, `//`, `///`, ...).
    if [[ "${val}" =~ ^/+$ ]]; then
        echo "❌ ${var_name}=${val} is a root path — refusing."
        exit 1
    fi
    # Reject paths that resolve to HOME — uninstall should never wipe HOME itself.
    # Using realpath if available, fallback to literal compare.
    local resolved="${val}"
    if command -v realpath >/dev/null 2>&1; then
        resolved="$(realpath -m -- "${val}" 2>/dev/null || echo "${val}")"
    fi
    if [[ "${resolved}" == "${HOME}" ]] || [[ "${val}" == "${HOME}" ]]; then
        echo "❌ ${var_name}=${val} equals HOME — refusing rm -rf."
        exit 1
    fi
}

echo "Tellonce uninstall"
echo "  PROJECT: ${PROJECT_ROOT}"
echo ""

# 1. Unregister hooks — remove both path sets to cover new and old installs.
# (New installs register ${SKILL_DIR}/hooks/; old installs register ${HOOKS_DIR}=${PROJECT_ROOT}/.claude/hooks/)
if [[ -f "${SETTINGS}" ]]; then
    echo "[1/5] Removing hooks from settings.local.json:"
    # Take OUR OWN pre-uninstall snapshot as the rollback anchor. The two
    # --remove passes below each write their own versioned backup, and the
    # later ones capture already-stripped settings — pointing the user at
    # "the latest backup" would roll back to a post-removal state.
    PRE_UNINSTALL_BACKUP="${SETTINGS}.v3_pre_pt_uninstall_$(date +%Y%m%d-%H%M%S)-$$.json"
    cp "${SETTINGS}" "${PRE_UNINSTALL_BACKUP}" 2>/dev/null || PRE_UNINSTALL_BACKUP=""
    # New design (skill-dir paths) removal
    python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${SETTINGS}" \
        --hooks-dir "${SKILL_DIR}/hooks" \
        --remove || true
    # Old install (project-local paths) removal
    python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${SETTINGS}" \
        --hooks-dir "${HOOKS_DIR}" \
        --remove || true
    if [[ -n "${PRE_UNINSTALL_BACKUP}" ]]; then
        echo "  backup: ${PRE_UNINSTALL_BACKUP}"
        echo "  rollback (undo this --remove change): cp \"${PRE_UNINSTALL_BACKUP}\" \"${SETTINGS}\""
    fi
else
    echo "[1/5] settings.local.json does not exist, skip"
fi

# 1b. Also remove the USER-GLOBAL registration (~/.claude/settings.json). The
# recommended install (option A) registers hooks THERE, and a project-local
# uninstall alone leaves them firing in EVERY project. Remove it by default so
# the hooks actually stop. (Use --keep-global to keep it.)
GLOBAL_SETTINGS="${HOME}/.claude/settings.json"
if [[ "${KEEP_GLOBAL}" != true && -f "${GLOBAL_SETTINGS}" ]]; then
    echo "[1b] removing hooks from user-global settings.json:"
    python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${GLOBAL_SETTINGS}" \
        --hooks-dir "${SKILL_DIR}/hooks" \
        --remove || true
fi

# 2. project-local hook .sh handling
# Do not blindly rm project-local hook files. New installs no longer write into
# ${HOOKS_DIR} (everything registered is ${SKILL_DIR}/hooks/), so this rm only
# affects old installs — but the user or a hostile repo may have placed a hook
# with the same name, and rm-ing it would delete their code. Default: skip; only
# rm when --purge-legacy-project-hooks is explicitly passed.
echo ""
HOOK_LIST_FILE="${SKILL_DIR}/lib/_pt_hooks.txt"
if [[ "${PURGE_LEGACY_HOOKS}" == true ]]; then
    echo "[2/5] Deleting project-local hooks .sh (--purge-legacy-project-hooks):"
    if [[ -f "${HOOK_LIST_FILE}" ]]; then
        while IFS= read -r hook; do
            [[ -z "${hook}" || "${hook}" == \#* ]] && continue
            if [[ -f "${HOOKS_DIR}/${hook}" ]]; then
                rm -f "${HOOKS_DIR}/${hook}"
                echo "  - ${hook}"
            fi
        done < "${HOOK_LIST_FILE}"
    fi
else
    echo "[2/5] project-local hooks .sh kept (default):"
    if [[ -d "${HOOKS_DIR}" ]] && ls "${HOOKS_DIR}"/memory-*.sh > /dev/null 2>&1; then
        echo "  Found .sh files from an old install in ${HOOKS_DIR}/:"
        ls "${HOOKS_DIR}"/memory-*.sh "${HOOKS_DIR}"/check-observation-log.sh 2>/dev/null | sed 's/^/    /'
        echo "  Tellonce v1.2+ no longer registers these (settings.local.json already cleaned). Left for you to review."
        echo "  Decide for yourself whether they are leftovers from an old PT install. To have uninstall delete them, re-run with"
        echo "    --purge-legacy-project-hooks"
    fi
fi

# 3. Ask about the skill dir
echo ""
echo "[3/5] skill dir handling:"
if [[ "${KEEP_SKILL_DIR}" == true ]]; then
    echo "  - keeping ${SKILL_DIR} (--keep-skill-dir)"
elif [[ ! -t 0 ]]; then
    # non-interactive (CI / pipe), keep by default
    echo "  - non-interactive mode, keeping ${SKILL_DIR} (easy to reinstall)"
else
    read -p "  rm -rf ${SKILL_DIR}? (y/N) " ans || ans="N"
    if [[ "${ans}" == "y" || "${ans}" == "Y" ]]; then
        _refuse_dangerous_path SKILL_DIR "${SKILL_DIR}"
        rm -rf "${SKILL_DIR}"
        echo "  - skill directory deleted"
    else
        echo "  - keeping ${SKILL_DIR}"
    fi
fi

# 4. state + memory
echo ""
echo "[4/5] state + memory:"
if [[ "${PURGE_STATE}" == true ]]; then
    echo "  - rm state + obs_log (--purge-state):"
    _refuse_dangerous_path STATE_DIR "${STATE_DIR}"
    _refuse_dangerous_path OBS_LOG_DIR "${OBS_LOG_DIR}"
    # Interactive confirm before destroying observation history (matches the
    # header's "double-confirm on --purge-state" promise). Non-interactive
    # callers (no tty) keep the old behavior: the flag itself is the consent.
    if [[ -t 0 ]]; then
        read -p "  Really delete ${STATE_DIR} and ${OBS_LOG_DIR}? (y/N) " ans || ans="N"
        if [[ "${ans}" != "y" && "${ans}" != "Y" ]]; then
            echo "  - keeping state + obs_log (declined)"
            PURGE_STATE=false
        fi
    fi
fi
if [[ "${PURGE_STATE}" == true ]]; then
    rm -rf "${STATE_DIR}" "${OBS_LOG_DIR}"
    echo "    rm -rf ${STATE_DIR}"
    echo "    rm -rf ${OBS_LOG_DIR}"
else
    echo "  - state + obs_log + memory kept (user data)"
    echo "    state: ${STATE_DIR}"
    echo "    obs_log: ${OBS_LOG_DIR}"
    echo "    memory: ~/.claude/projects/<cwd_escaped>/memory/ (untouched)"
    echo ""
    echo "  Full delete: bash ${SKILL_DIR}/uninstall.sh --purge-state"
fi

# 5. ~/.tellonce.config.json (cleanup so a reinstall on a different
# project doesn't reuse stale paths). --keep-config to preserve it.
echo ""
echo "[5/5] ~/.tellonce.config.json:"
if [[ "${KEEP_CONFIG}" == true ]]; then
    echo "  - keeping ${CONFIG_FILE} (--keep-config)"
elif [[ -f "${CONFIG_FILE}" ]]; then
    rm -f "${CONFIG_FILE}"
    echo "  - deleted ${CONFIG_FILE}"
    echo "    (a reinstall rewrites it; to keep the old path anchor, run uninstall.sh --keep-config next time)"
else
    echo "  - ${CONFIG_FILE} does not exist, skip"
fi

echo ""
echo "✅ Uninstall complete"
