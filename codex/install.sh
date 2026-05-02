#!/usr/bin/env bash
# Codex preference-tracker install. Installs:
#   1. Global runtime to ~/.codex/skills/preference-tracker/
#      (codex_preftrack/ + shared_lib/ from CC + hooks/ + seed_memory/)
#   2. Hook registrations to ~/.codex/hooks.json
#      (UserPromptSubmit x3 + PostToolUse + SessionStart)
#   3. Per-project state in $CWD (.codex/preference-tracker/)
#
# Idempotent. Safe to re-run after `git pull`.
#
# Flags:
#   --skip-global    : skip global runtime + hook registration (only init
#                      project state). Useful when global is already current.
#   --skip-project   : skip per-project state init (only refresh global).
#   --no-hooks       : install runtime but don't touch ~/.codex/hooks.json
#                      (advanced: user manages hook registration manually).

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../preference-tracker/codex/
REPO_ROOT="$(cd "${SKILL_DIR}/.." && pwd)"                  # .../preference-tracker/
PYTHON="${PYTHON:-python3}"

# shared_lib source resolution: try repo layout first, then standalone-bundle
# layout. Standalone bundles ship a `shared_lib/` directory pre-populated
# alongside `codex_preftrack/` so they don't need the parent repo's lib/.
if [[ -d "${REPO_ROOT}/lib" ]]; then
    SHARED_LIB_SRC="${REPO_ROOT}/lib"
elif [[ -d "${SKILL_DIR}/shared_lib" ]]; then
    SHARED_LIB_SRC="${SKILL_DIR}/shared_lib"
else
    SHARED_LIB_SRC=""
fi
SEED_MEMORY_SRC=""
if [[ -d "${REPO_ROOT}/seed_memory" ]]; then
    SEED_MEMORY_SRC="${REPO_ROOT}/seed_memory"
elif [[ -d "${SKILL_DIR}/seed_memory" ]]; then
    SEED_MEMORY_SRC="${SKILL_DIR}/seed_memory"
fi

SKIP_GLOBAL=false
SKIP_PROJECT=false
NO_HOOKS=false
PASSTHRU=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-global) SKIP_GLOBAL=true; shift ;;
        --skip-project) SKIP_PROJECT=true; shift ;;
        --no-hooks) NO_HOOKS=true; shift ;;
        *) PASSTHRU+=("$1"); shift ;;
    esac
done

GLOBAL_DIR="${HOME}/.codex/skills/preference-tracker"
HOOKS_JSON="${HOME}/.codex/hooks.json"

# ============================================================
# Phase 1: Global runtime install
# ============================================================
if [[ "${SKIP_GLOBAL}" != true ]]; then
    echo "[1/3] global runtime → ${GLOBAL_DIR}"
    # Round-7 codex-review P2-8 fix: walk ancestors before mkdir -p so a
    # 0-byte regular file at any component (commonly ~/.codex when another
    # tool created it as a file) gets a clear actionable error instead of
    # bash's generic "File exists" or mkdir's "Not a directory" message.
    _check_dir_path() {
        local p="$1"
        # Build ancestor chain from / down to $p; first existing non-dir wins.
        local current="${p}"
        local chain=()
        while [[ -n "${current}" && "${current}" != "/" && ! -e "${current}" ]]; do
            chain=("${current}" "${chain[@]}")
            current="$(dirname "${current}")"
        done
        # `current` is the deepest existing ancestor.
        if [[ -e "${current}" && ! -d "${current}" ]]; then
            echo "❌ cannot create ${p}:"
            echo "   ancestor ${current} exists but is a regular file (not a directory)."
            echo "   Likely cause: another tool wrote a file at that path."
            echo "   Fix: mv \"${current}\" \"${current}.backup-\$(date +%Y%m%d)\""
            echo "        then re-run install."
            return 1
        fi
        if [[ -e "${p}" && ! -d "${p}" ]]; then
            echo "❌ cannot create ${p}: it exists and is a regular file (not a directory)."
            echo "   Fix: mv \"${p}\" \"${p}.backup-\$(date +%Y%m%d)\""
            echo "        then re-run install."
            return 1
        fi
        return 0
    }
    if ! _check_dir_path "${GLOBAL_DIR}"; then
        exit 1
    fi
    mkdir -p "${GLOBAL_DIR}"

    # Detect self-install (standalone skill folder == GLOBAL_DIR). Skip the
    # cp/rsync steps that would copy a directory onto itself.
    SKILL_DIR_REAL="$(cd "${SKILL_DIR}" && pwd -P)"
    GLOBAL_DIR_REAL="$(cd "${GLOBAL_DIR}" && pwd -P)"
    SELF_INSTALL=false
    if [[ "${SKILL_DIR_REAL}" == "${GLOBAL_DIR_REAL}" ]]; then
        SELF_INSTALL=true
        echo "  (standalone skill folder layout: SKILL_DIR == GLOBAL_DIR, skipping copies)"
    fi

    # 1a. codex_preftrack Python package (the wrapper-driven enforcement code)
    if [[ "${SELF_INSTALL}" != true ]]; then
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='tests/' \
                "${SKILL_DIR}/codex_preftrack/" "${GLOBAL_DIR}/codex_preftrack/"
        else
            rm -rf "${GLOBAL_DIR}/codex_preftrack"
            cp -r "${SKILL_DIR}/codex_preftrack" "${GLOBAL_DIR}/codex_preftrack"
            find "${GLOBAL_DIR}/codex_preftrack" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
            rm -rf "${GLOBAL_DIR}/codex_preftrack/tests" 2>/dev/null || true
        fi
    fi

    # 1b. shared_lib = CC's lib/ (retrieve_inject + deterministic_block + 等)
    if [[ "${SELF_INSTALL}" != true ]]; then
        if [[ -n "${SHARED_LIB_SRC}" && -d "${SHARED_LIB_SRC}" ]]; then
            SHARED_LIB_SRC_REAL="$(cd "${SHARED_LIB_SRC}" && pwd -P)"
            DST_SHARED_LIB_REAL="$(mkdir -p "${GLOBAL_DIR}/shared_lib" && cd "${GLOBAL_DIR}/shared_lib" && pwd -P)"
            if [[ "${SHARED_LIB_SRC_REAL}" == "${DST_SHARED_LIB_REAL}" ]]; then
                : # same dir, skip
            elif command -v rsync >/dev/null 2>&1; then
                rsync -a --delete \
                    --exclude='__pycache__' --exclude='*.pyc' --exclude='test_*' \
                    --exclude='conftest.py' \
                    "${SHARED_LIB_SRC}/" "${GLOBAL_DIR}/shared_lib/"
            else
                rm -rf "${GLOBAL_DIR}/shared_lib"
                cp -r "${SHARED_LIB_SRC}" "${GLOBAL_DIR}/shared_lib"
                find "${GLOBAL_DIR}/shared_lib" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
                find "${GLOBAL_DIR}/shared_lib" -name 'test_*.py' -delete 2>/dev/null || true
                rm -f "${GLOBAL_DIR}/shared_lib/conftest.py" 2>/dev/null || true
            fi
        else
            echo "  ⚠ shared_lib source not found (no lib/ next to install.sh and no shared_lib/ in skill dir)"
            echo "    UserPromptSubmit retrieve / PostToolUse deterministic-block hooks 会 silently no-op"
            echo "    (wrapper-driven enforcement via codex_preftrack exec 仍 work)"
        fi
    fi

    # 1c. seed_memory (used by sessionstart-init when project memory is empty)
    if [[ "${SELF_INSTALL}" != true ]]; then
        if [[ -n "${SEED_MEMORY_SRC}" && -d "${SEED_MEMORY_SRC}" ]]; then
            SEED_SRC_REAL="$(cd "${SEED_MEMORY_SRC}" && pwd -P)"
            DST_SEED_REAL="$(mkdir -p "${GLOBAL_DIR}/seed_memory" && cd "${GLOBAL_DIR}/seed_memory" && pwd -P)"
            if [[ "${SEED_SRC_REAL}" == "${DST_SEED_REAL}" ]]; then
                : # same, skip
            elif command -v rsync >/dev/null 2>&1; then
                rsync -a "${SEED_MEMORY_SRC}/" "${GLOBAL_DIR}/seed_memory/"
            else
                mkdir -p "${GLOBAL_DIR}/seed_memory"
                cp -r "${SEED_MEMORY_SRC}/." "${GLOBAL_DIR}/seed_memory/"
            fi
        fi
    fi

    # 1d-1e. hooks/ + SKILL.md/docs
    if [[ "${SELF_INSTALL}" != true ]]; then
        mkdir -p "${GLOBAL_DIR}/hooks"
        if [[ -d "${SKILL_DIR}/hooks" ]]; then
            cp "${SKILL_DIR}/hooks/"*.sh "${GLOBAL_DIR}/hooks/" 2>/dev/null || true
            chmod +x "${GLOBAL_DIR}/hooks/"*.sh 2>/dev/null || true
        fi
        if [[ -f "${SKILL_DIR}/SKILL.md" ]]; then
            cp "${SKILL_DIR}/SKILL.md" "${GLOBAL_DIR}/SKILL.md"
        fi
        if [[ -d "${SKILL_DIR}/docs" ]]; then
            if command -v rsync >/dev/null 2>&1; then
                rsync -a "${SKILL_DIR}/docs/" "${GLOBAL_DIR}/docs/"
            else
                mkdir -p "${GLOBAL_DIR}/docs"
                cp -r "${SKILL_DIR}/docs/." "${GLOBAL_DIR}/docs/"
            fi
        fi
    else
        # Standalone skill: hooks/SKILL.md/docs already in place; just chmod hooks
        chmod +x "${GLOBAL_DIR}/hooks/"*.sh 2>/dev/null || true
    fi

    echo "  ✓ codex_preftrack/ + shared_lib/ + hooks/ + seed_memory/ + SKILL.md"

    # 1f. register hooks in ~/.codex/hooks.json
    if [[ "${NO_HOOKS}" != true ]]; then
        echo "[2/3] register hooks → ${HOOKS_JSON}"
        PYTHONPATH="${GLOBAL_DIR}" "${PYTHON}" -m codex_preftrack.install_codex_hooks \
            --hooks-json "${HOOKS_JSON}" \
            --hooks-dir "${GLOBAL_DIR}/hooks" \
            --add
    else
        echo "[2/3] hooks registration skipped (--no-hooks)"
    fi
else
    echo "[1-2/3] global runtime + hooks skipped (--skip-global)"
fi

# ============================================================
# Phase 3: Per-project state init
# ============================================================
if [[ "${SKIP_PROJECT}" != true ]]; then
    echo "[3/3] per-project state init → $(pwd)/.codex/preference-tracker/"
    # Round-7 codex-review P1-3 fix: forward --no-hooks down so phase 3 does
    # not silently re-register hooks the user already opted out of via the
    # bash --no-hooks flag.
    PHASE3_ARGS=(${PASSTHRU[@]+"${PASSTHRU[@]}"})
    if [[ "${NO_HOOKS}" == true ]]; then
        PHASE3_ARGS+=("--no-hooks")
    fi
    if [[ -d "${GLOBAL_DIR}/codex_preftrack" ]]; then
        PYTHONPATH="${GLOBAL_DIR}" "${PYTHON}" -m codex_preftrack install ${PHASE3_ARGS[@]+"${PHASE3_ARGS[@]}"}
    else
        # Fall back to in-tree code (works for repo dev / no global install).
        PYTHONPATH="${REPO_ROOT}/codex" "${PYTHON}" -m codex_preftrack install ${PHASE3_ARGS[@]+"${PHASE3_ARGS[@]}"}
    fi
    echo "  ✓ state initialized"
else
    echo "[3/3] per-project state init skipped (--skip-project)"
fi

echo ""
echo "✓ codex preference-tracker install complete"
echo "  global: ${GLOBAL_DIR}"
echo "  hooks:  ${HOOKS_JSON}"
echo "  project state: $(pwd)/.codex/preference-tracker/"
echo ""
echo "下一步: 在新 codex session 触发一个 prompt, 看"
echo "  $(pwd)/.codex/preference-tracker/runtime/posttooluse_log.jsonl 有无写入."
