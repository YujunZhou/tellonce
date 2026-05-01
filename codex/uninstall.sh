#!/usr/bin/env bash
# Codex preference-tracker uninstall.
#   - Removes per-project state marker (managed_runtime.txt) — keeps logs/state
#     by default; `--purge-state` wipes the project's preference-tracker state.
#   - Optionally removes hook registrations from ~/.codex/hooks.json
#     (--purge-hooks). Default: keep registered, just disable per project.
#   - Optionally removes global skill dir from ~/.codex/skills/preference-tracker/
#     (--purge-skill). Default: leave global runtime so other projects can use it.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SKILL_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"

GLOBAL_DIR="${HOME}/.codex/skills/preference-tracker"
HOOKS_JSON="${HOME}/.codex/hooks.json"

# Resolve where codex_preftrack module lives — prefer global install (most
# common), fall back to repo-local source layout (development).
if [[ -d "${GLOBAL_DIR}/codex_preftrack" ]]; then
    PYTHON_PATH_ROOT="${GLOBAL_DIR}"
elif [[ -d "${SKILL_DIR}/codex_preftrack" ]]; then
    PYTHON_PATH_ROOT="${SKILL_DIR}"
else
    PYTHON_PATH_ROOT="${REPO_ROOT}/codex"
fi

PURGE_STATE=false
PURGE_HOOKS=false
PURGE_SKILL=false
PASSTHRU=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-state) PURGE_STATE=true; PASSTHRU+=("$1"); shift ;;
        --purge-hooks) PURGE_HOOKS=true; shift ;;
        --purge-skill) PURGE_SKILL=true; shift ;;
        *) PASSTHRU+=("$1"); shift ;;
    esac
done

# 1. Project-level uninstall via codex_preftrack
PYTHONPATH="${PYTHON_PATH_ROOT}" "${PYTHON}" -m codex_preftrack uninstall \
    ${PASSTHRU[@]+"${PASSTHRU[@]}"} || true

# 2. Optionally drop hook registrations from ~/.codex/hooks.json
if [[ "${PURGE_HOOKS}" == true ]]; then
    if [[ -f "${HOOKS_JSON}" ]]; then
        echo "Removing PT hook registrations from ${HOOKS_JSON}"
        PYTHONPATH="${PYTHON_PATH_ROOT}" "${PYTHON}" -m codex_preftrack.install_codex_hooks \
            --hooks-json "${HOOKS_JSON}" \
            --remove || true
    fi
else
    echo "(skipping hook registration removal — pass --purge-hooks to drop ~/.codex/hooks.json entries)"
fi

# 3. Optionally remove global skill dir
if [[ "${PURGE_SKILL}" == true ]]; then
    if [[ -d "${GLOBAL_DIR}" ]]; then
        RESOLVED_GLOBAL="$(cd "${GLOBAL_DIR}" && pwd -P)"
        if [[ "${RESOLVED_GLOBAL}" == "/" ]] || [[ "${RESOLVED_GLOBAL}" == "${HOME}" ]]; then
            echo "❌ refusing to remove suspicious skill dir: ${RESOLVED_GLOBAL}"
            exit 1
        fi
        echo "Removing global skill dir ${GLOBAL_DIR}"
        rm -rf "${GLOBAL_DIR}"
    fi
fi

echo ""
echo "✓ codex preference-tracker uninstall complete"
