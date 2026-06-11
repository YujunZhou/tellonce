#!/usr/bin/env bash
# Codex tellonce install. Installs:
#   1. Global runtime to ~/.codex/skills/tellonce/
#      (tellonce_codex/ + shared_lib/ from CC + hooks/ + seed_memory/)
#      If a git clone occupies that path, the runtime is installed to
#      ~/.codex/skills/tellonce-runtime instead (keeps the clone clean).
#   2. Hook registrations to ~/.codex/hooks.json
#      (UserPromptSubmit x3 + PostToolUse + SessionStart)
#   3. Per-project state in $CWD (.codex/tellonce/)
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

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../tellonce/codex/
REPO_ROOT="$(cd "${SKILL_DIR}/.." && pwd)"                  # .../tellonce/
PYTHON="${PYTHON:-python3}"

if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "❌ ${PYTHON} not found in PATH. Install Python 3 (or set PYTHON=<path>) and re-run."
    exit 1
fi

# shared_lib source resolution: try repo layout first, then standalone-bundle
# layout. Standalone bundles ship a `shared_lib/` directory pre-populated
# alongside `tellonce_codex/` so they don't need the parent repo's lib/.
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

GLOBAL_DIR="${HOME}/.codex/skills/tellonce"
HOOKS_JSON="${HOME}/.codex/hooks.json"

# If the user cloned the repo to the GLOBAL_DIR path itself (the documented
# clone location), installing runtime files there would dirty the git clone —
# overwriting the tracked repo-root SKILL.md (the Claude Code skill entry) and
# making the promised "safe to re-run after git pull" false (pull would
# conflict). Redirect the generated runtime to a sibling -runtime dir; the
# clone stays pristine.
if [[ -d "${GLOBAL_DIR}" ]]; then
    GLOBAL_DIR_REAL_PRE="$(cd "${GLOBAL_DIR}" && pwd -P)"
    REPO_ROOT_REAL="$(cd "${REPO_ROOT}" && pwd -P)"
    if [[ "${GLOBAL_DIR_REAL_PRE}" == "${REPO_ROOT_REAL}" || -e "${GLOBAL_DIR}/.git" ]]; then
        GLOBAL_DIR="${HOME}/.codex/skills/tellonce-runtime"
        echo "  (clone detected at ~/.codex/skills/tellonce — installing runtime to ${GLOBAL_DIR} to keep the clone clean)"
    fi
fi

# ============================================================
# Phase 1: Global runtime install
# ============================================================
if [[ "${SKIP_GLOBAL}" != true ]]; then
    echo "[1/3] global runtime → ${GLOBAL_DIR}"
    # Walk ancestors before mkdir -p so a
    # 0-byte regular file at any component (commonly ~/.codex when another
    # tool created it as a file) gets a clear actionable error instead of
    # bash's generic "File exists" or mkdir's "Not a directory" message.
    _check_dir_path() {
        local p="$1"
        # Build ancestor chain from / down to $p; first existing non-dir wins.
        local current="${p}"
        local chain=()
        while [[ -n "${current}" && "${current}" != "/" && ! -e "${current}" ]]; do
            chain=("${current}" ${chain[@]+"${chain[@]}"})
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

    # 1a. tellonce_codex Python package (the wrapper-driven enforcement code)
    if [[ "${SELF_INSTALL}" != true ]]; then
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='tests/' \
                "${SKILL_DIR}/tellonce_codex/" "${GLOBAL_DIR}/tellonce_codex/"
        else
            rm -rf "${GLOBAL_DIR}/tellonce_codex"
            cp -r "${SKILL_DIR}/tellonce_codex" "${GLOBAL_DIR}/tellonce_codex"
            find "${GLOBAL_DIR}/tellonce_codex" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
            rm -rf "${GLOBAL_DIR}/tellonce_codex/tests" 2>/dev/null || true
        fi
    fi

    # 1b. shared_lib = CC's lib/ (retrieve_inject + deterministic_block + etc.)
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
            echo "    UserPromptSubmit retrieve / PostToolUse deterministic-block hooks will silently no-op"
            echo "    (wrapper-driven enforcement via tellonce_codex exec still works)"
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

    echo "  ✓ tellonce_codex/ + shared_lib/ + hooks/ + seed_memory/ + SKILL.md"

    # 1e2. Install a shell wrapper at ~/.local/bin/tellonce_codex so
    # `tellonce_codex ...` works from any shell (no PYTHONPATH needed).
    # Without this
    # wrapper, users had to remember `PYTHONPATH=~/.codex/skills/tellonce
    # python3 -m tellonce_codex ...` for every CLI invocation.
    BIN_DIR="${HOME}/.local/bin"
    mkdir -p "${BIN_DIR}"
    WRAPPER="${BIN_DIR}/tellonce_codex"
    cat > "${WRAPPER}" <<WRAPPER_EOF
#!/usr/bin/env bash
# tellonce_codex — shell wrapper installed by codex/install.sh.
# Locks in PYTHONPATH so the tellonce_codex package is importable from
# any shell. Edit-aware: re-running install.sh overwrites this file.
exec env PYTHONPATH="${GLOBAL_DIR}\${PYTHONPATH:+:\$PYTHONPATH}" \
    "${PYTHON}" -m tellonce_codex "\$@"
WRAPPER_EOF
    chmod +x "${WRAPPER}"
    echo "  ✓ shell wrapper: ${WRAPPER}"
    case ":${PATH}:" in
        *":${BIN_DIR}:"*) ;;
        *)
            echo "  ⚠ ${BIN_DIR} not in PATH. Add to your shell rc:"
            echo "      export PATH=\"${BIN_DIR}:\$PATH\""
            echo "    Until then call as: ${WRAPPER} <args>"
            ;;
    esac

    # 1f. register hooks in ~/.codex/hooks.json.
    # Remove-then-add: cmd_remove drops ALL PT entries by path pattern, so an
    # upgrade that changed the hooks location (e.g. clone → -runtime redirect)
    # can't leave stale duplicate registrations pointing at old paths.
    if [[ "${NO_HOOKS}" != true ]]; then
        echo "[2/3] register hooks → ${HOOKS_JSON}"
        if [[ -f "${HOOKS_JSON}" ]]; then
            PYTHONPATH="${GLOBAL_DIR}" "${PYTHON}" -m tellonce_codex.install_codex_hooks \
                --hooks-json "${HOOKS_JSON}" \
                --remove >/dev/null 2>&1 || true
        fi
        PYTHONPATH="${GLOBAL_DIR}" "${PYTHON}" -m tellonce_codex.install_codex_hooks \
            --hooks-json "${HOOKS_JSON}" \
            --hooks-dir "${GLOBAL_DIR}/hooks" \
            --add
        HOOKS_REGISTERED_IN_PHASE1=true
    else
        echo "[2/3] hooks registration skipped (--no-hooks)"
        HOOKS_REGISTERED_IN_PHASE1=false
    fi
else
    echo "[1-2/3] global runtime + hooks skipped (--skip-global)"
fi

# ============================================================
# Phase 3: Per-project state init
# ============================================================
if [[ "${SKIP_PROJECT}" != true ]]; then
    echo "[3/3] per-project state init → $(pwd)/.codex/tellonce/"
    # Forward --no-hooks down so phase 3 does
    # not silently re-register hooks the user already opted out of via the
    # bash --no-hooks flag — and also when phase 1f already registered them
    # (phase 3's auto-registration uses the default path and would create a
    # second, possibly stale, registration).
    PHASE3_ARGS=(${PASSTHRU[@]+"${PASSTHRU[@]}"})
    if [[ "${NO_HOOKS}" == true || "${HOOKS_REGISTERED_IN_PHASE1:-false}" == true ]]; then
        PHASE3_ARGS+=("--no-hooks")
    fi
    if [[ -d "${GLOBAL_DIR}/tellonce_codex" ]]; then
        PYTHONPATH="${GLOBAL_DIR}" "${PYTHON}" -m tellonce_codex install ${PHASE3_ARGS[@]+"${PHASE3_ARGS[@]}"}
    else
        # Fall back to in-tree code (works for repo dev / no global install).
        PYTHONPATH="${REPO_ROOT}/codex" "${PYTHON}" -m tellonce_codex install ${PHASE3_ARGS[@]+"${PHASE3_ARGS[@]}"}
    fi
    echo "  ✓ state initialized"
else
    echo "[3/3] per-project state init skipped (--skip-project)"
fi

echo ""
echo "✓ codex tellonce install complete"
echo "  global: ${GLOBAL_DIR}"
echo "  hooks:  ${HOOKS_JSON}"
echo "  project state: $(pwd)/.codex/tellonce/"
echo ""
echo "Next step: trigger a prompt in a new codex session, then check whether"
echo "  $(pwd)/.codex/tellonce/runtime/posttooluse_log.jsonl gets written to."
