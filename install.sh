#!/usr/bin/env bash
# Preference-Tracker one-shot installer — standard install/doctor/uninstall interface.
#
# Usage:
#   bash <skill_dir>/install.sh [--state-dir <path>] [--quiet] [--dry-run]
#                               [--i-know-i-am-installing-globally]
#   (the last flag allows running from HOME and registers into the user-scope
#    ~/.claude/settings.json so every project is covered — INSTALL.md Option A)
#
# Five robust phases:
#   1. Prepare (pre-install doctor-style checks)
#   2. Install (idempotent, versioned backup, trap ERR rollback)
#   3. Collect (state + obs_log paths writable)
#   4. Execute (doctor self-check: unit + smoke)
#   5. Uninstall mechanism ready (uninstall.sh)
#
# Versioned backups; state lives under .claude/preference-tracker-state/.
# Settings merge is done in Python (not jq).

set -euo pipefail

QUIET=false
DRY_RUN=false
STATE_DIR_OVERRIDE=""
ALLOW_GLOBAL=false

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet) QUIET=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --state-dir) STATE_DIR_OVERRIDE="$2"; shift 2 ;;
        --i-know-i-am-installing-globally) ALLOW_GLOBAL=true; shift ;;
        -h|--help)
            sed -n '2,17p' "$0"
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Self-locate: the skill dir is wherever this script lives (doctor.sh does the
# same). A hardcoded ~/.claude/skills path broke installs from any other clone
# location and wrote logs into the wrong directory.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(pwd)"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"
# Global install = user scope. settings.local.json under HOME would only apply
# to sessions whose cwd is HOME itself; ~/.claude/settings.json is what Claude
# Code loads for every project (matches INSTALL.md Option A).
if [[ "${ALLOW_GLOBAL}" == true ]]; then
    SETTINGS="${HOME}/.claude/settings.json"
fi
BACKUP_FILE=""

# Security note (see section 2.3): hooks are registered by absolute path from
# this skill directory. If that directory sits INSIDE the project being
# gated, the agent itself can edit the hook scripts — keep the clone outside
# your working repos (e.g. ~/.claude/skills/).
case "${SKILL_DIR}/" in
    "${PROJECT_ROOT}"/*)
        echo "⚠ skill dir is inside the project root (${SKILL_DIR})."
        echo "  The agent being gated can modify its own gates. Consider cloning to ~/.claude/skills/ instead."
        ;;
esac

# Refuse install when PROJECT_ROOT is HOME / /tmp / has no .claude
if [[ "${PROJECT_ROOT}" == "${HOME}" && "${ALLOW_GLOBAL}" != true ]]; then
    echo "❌ PROJECT_ROOT == HOME (${HOME})"
    echo "   You appear to be running install.sh in HOME — cd into your project root first."
    echo "   If you really want a user-level (cross-project) install, use --i-know-i-am-installing-globally"
    exit 1
fi
if [[ "${PROJECT_ROOT}" == /tmp/* ]] || [[ "${PROJECT_ROOT}" == /tmp ]]; then
    # Allow /tmp/fake_user_test for smoke/chaos test
    if [[ "${PROJECT_ROOT}" != /tmp/fake_user_test* ]] && [[ "${PROJECT_ROOT}" != /tmp/chaos_test* ]]; then
        echo "❌ PROJECT_ROOT is under /tmp (${PROJECT_ROOT})"
        echo "   /tmp is not persistent. Re-run from a persistent path."
        exit 1
    fi
fi

log() {
    if [[ "$QUIET" != true ]]; then
        echo "$@"
    fi
}

# Trap ERR — roll back the settings backup; do not delete hooks .sh / state / memory
# Inside rollback we (a) disable set -e so a failing `cp` doesn't truncate the
# function before printing the rollback summary or reaching `exit 2`; (b) clear
# the ERR trap on entry so a second failure inside rollback doesn't recurse.
rollback() {
    set +e
    trap - ERR
    log ""
    log "❌ install failed, rolling back..."
    if [[ -n "${BACKUP_FILE}" && -f "${BACKUP_FILE}" ]]; then
        if cp "${BACKUP_FILE}" "${SETTINGS}"; then
            log "  settings rolled back: ${BACKUP_FILE} → ${SETTINGS}"
        else
            log "  ⚠ settings rollback failed (cp error): ${BACKUP_FILE} → ${SETTINGS}"
            log "  → restore manually: cp \"${BACKUP_FILE}\" \"${SETTINGS}\""
        fi
    fi
    log "  hooks .sh / state / memory kept (for debugging)"
    log "  see ${SKILL_DIR}/install.log for details"
    exit 2
}
trap 'rollback' ERR

run() {
    if [[ "$DRY_RUN" == true ]]; then
        log "  [dry-run] $@"
    else
        "$@"
    fi
}

LOG_FILE="${SKILL_DIR}/install.log"
mkdir -p "${SKILL_DIR}" 2>/dev/null || true
# Tee BOTH stdout + stderr into install.log; tee-ing only stderr would drop
# log() stdout from the log, breaking `cat install.log` troubleshooting.
exec > >(tee "${LOG_FILE}") 2>&1

log "Preference-Tracker install — version 1.0"
log "  HOME: ${HOME}"
log "  PROJECT: ${PROJECT_ROOT}"
log "  SKILL_DIR: ${SKILL_DIR}"
log ""

# ============================================================
# Phase 1: Prepare (doctor-style pre-checks)
# ============================================================
log "[1/5] Prepare: environment pre-checks"

# 1.1 HOME writable
if ! mkdir -p "${HOME}/.claude/skills/.write_test" 2>/dev/null; then
    log "❌ HOME (${HOME}) is not writable. Check quota / permissions"
    exit 1
fi
rmdir "${HOME}/.claude/skills/.write_test" 2>/dev/null || true

# 1.2 Python 3 >= 3.7
PY_OK=$(python3 -c "import sys; print('OK' if sys.version_info >= (3, 7) else 'OLD')" 2>/dev/null || echo "MISSING")
if [[ "${PY_OK}" != "OK" ]]; then
    log "❌ Python3 missing or version <3.7. Install Python 3.7+ and retry"
    exit 1
fi

# 1.2b PyYAML — used by retrieve_inject + verify_compliance.detect_rules_for_response.
# Without yaml these two hooks exit silently (sys.exit(0)), disabling half of enforcement.
if ! python3 -c 'import yaml' 2>/dev/null; then
    log "⚠ PyYAML not found in system Python. fingerprint retrieval + B3 fp_rules_in_response will be silently disabled."
    log "   Fix:"
    log "     pip install --user pyyaml     (or)"
    log "     pip3 install --user pyyaml    (or a system package)"
    log "     sudo apt install python3-yaml (Ubuntu)"
    log "   You can continue without it: deterministic blocking + shadow judge still run, only fingerprint retrieval is lost."
fi

# 1.2c jq — used by check-observation-log.sh and other hooks. Without it those hooks die under set -e.
if ! command -v jq > /dev/null 2>&1; then
    log "❌ jq not in PATH. check-observation-log.sh and other short-circuit blocks depend on jq."
    log "   Fix: sudo apt install jq (Ubuntu) / brew install jq (macOS)"
    exit 1
fi

# 1.3 claude CLI (shadow judge uses the CLI by default)
if ! command -v claude > /dev/null 2>&1; then
    log "⚠ 'claude' CLI not found in PATH"
    log "   shadow judge uses the CLI subscription by default; without the CLI the shadow judge cannot run"
    log "   set B5_SHADOW_DISABLED=1 to disable the shadow judge (deterministic still works)"
    log "   or install Claude Code: https://claude.com/code"
fi

# 1.4 Existing settings valid (if present). Pass path via env channel to avoid injection + utf-8 encoding.
if [[ -f "${SETTINGS}" ]]; then
    if ! env PT_SETTINGS="${SETTINGS}" PYTHONIOENCODING=utf-8 python3 -c \
        'import json, os; json.load(open(os.environ["PT_SETTINGS"]))' 2>/dev/null; then
        log "❌ ${SETTINGS} is invalid JSON. Run jsonlint to check, then retry"
        exit 1
    fi
fi

# 1.5 SKILL dir complete (lib + hooks subdirs exist)
if [[ ! -d "${SKILL_DIR}/lib" ]]; then
    log "❌ ${SKILL_DIR}/lib does not exist. Check the skill was extracted completely"
    exit 1
fi
if [[ ! -d "${SKILL_DIR}/hooks" ]]; then
    log "❌ ${SKILL_DIR}/hooks does not exist. Check the skill was extracted completely"
    exit 1
fi

log "  ✓ HOME writable"
log "  ✓ Python 3.7+"
log "  ✓ skill directory complete (lib + hooks)"
log "  ✓ existing settings valid"

# ============================================================
# Phase 2: Install (idempotent, versioned backup)
# ============================================================
log ""
log "[2/5] Install: update settings + copy hooks + create state"

# 2.1 detect cwd / paths (PYTHONIOENCODING=utf-8 prevents stdout crashes when the user's LANG isn't utf-8)
CWD_ESCAPED="$(PYTHONIOENCODING=utf-8 python3 -c "import sys; print(sys.argv[1].replace('/', '-'))" "${PROJECT_ROOT}")"
MEMORY_DIR="${HOME}/.claude/projects/${CWD_ESCAPED}/memory"
# PT_* are the documented names; B5_* kept as legacy aliases (Python layer accepts both).
STATE_DIR="${STATE_DIR_OVERRIDE:-${PT_STATE_DIR:-${B5_STATE_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/runtime}}}"
OBS_LOG_DIR="${PT_OBS_LOG_DIR:-${B5_OBS_LOG_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/obs_log}}"

log "  PROJECT_ROOT: ${PROJECT_ROOT}"
log "  STATE_DIR: ${STATE_DIR}"
log "  OBS_LOG_DIR: ${OBS_LOG_DIR}"
log "  MEMORY_DIR: ${MEMORY_DIR}"

# 2.2 versioned backup of settings
# Also GC older backups, keeping the 5 most recent.
# install.sh + _install_merge_settings.cmd_add each copy once = 2 backups per install.
# Without GC, repeated reinstalls leave dozens of copies, wasting disk + retaining old secrets forever.
if [[ -f "${SETTINGS}" ]]; then
    TS="$(date +%Y%m%d-%H%M%S)"
    BACKUP_FILE="${SETTINGS}.v3_pre_pt_${TS}.json"
    run cp "${SETTINGS}" "${BACKUP_FILE}"
    log "  ✓ versioned backup: ${BACKUP_FILE}"
    # GC oldest backups beyond the most recent 5
    OLD_BACKUPS=$(ls -t "${SETTINGS}".v3_pre_pt_*.json 2>/dev/null | tail -n +6 || true)
    if [[ -n "${OLD_BACKUPS}" ]]; then
        log "  GC old backups (keeping the 5 most recent):"
        while IFS= read -r ob; do
            run rm -f "${ob}"
            log "    rm ${ob}"
        done <<< "${OLD_BACKUPS}"
    fi
fi

# 2.3 Register the skill hook paths directly.
# Old design: copy into ${PROJECT_ROOT}/.claude/hooks/ and register project-local paths.
# Vulnerability: a hostile repo could pre-place a hook .sh with the same name; install
#       would see it already exists and "cp -n skip", registering attacker code into
#       settings.local.json so every Stop hook then runs the attacker's script.
# Fix: register ${SKILL_DIR}/hooks/*.sh directly — the skill dir lives in ~/.claude/ and
#     is not affected by in-project files. ${HOOKS_DIR} is no longer written to, avoiding
#     breaking manual customization.
run chmod +x "${SKILL_DIR}"/hooks/*.sh 2>/dev/null || true
HOOKS_REGISTER_DIR="${SKILL_DIR}/hooks"
log "  hooks registration path: ${HOOKS_REGISTER_DIR} (skill dir, cannot be overridden by the project)"

# 2.4 Python merge settings (additive; already-registered hooks are skipped)
log ""
log "  registering hooks into settings.local.json:"
run python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
    --settings "${SETTINGS}" \
    --hooks-dir "${HOOKS_REGISTER_DIR}" \
    --add

# 2.4b Legacy install cleanup: unregister previously registered
# ${PROJECT_ROOT}/.claude/hooks/ paths (so settings.local.json doesn't carry both
# the old project-local paths and the new skill-dir paths).
if [[ -d "${HOOKS_DIR}" ]] && ls "${HOOKS_DIR}"/memory-*.sh > /dev/null 2>&1; then
    log "  detected legacy project-local hooks (${HOOKS_DIR}), unregistering:"
    run python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
        --settings "${SETTINGS}" \
        --hooks-dir "${HOOKS_DIR}" \
        --remove || true
    log "    (project-local .sh files kept in ${HOOKS_DIR}; you can rm them manually)"
fi

# 2.5 Create state subdirs (idempotent)
# Pass the path via the env channel rather than interpolating into Python source.
# The Bash block is single-quoted, so paths containing `'` / `"` / `$` / `\`` cannot inject into Python.
log ""
log "  creating state subdirs:"
run env \
    PT_SKILL_DIR="${SKILL_DIR}" \
    B5_STATE_DIR="${STATE_DIR}" \
    B5_OBS_LOG_DIR="${OBS_LOG_DIR}" \
    B5_PROJECT_ROOT="${PROJECT_ROOT}" \
    PYTHONIOENCODING=utf-8 \
    python3 -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["PT_SKILL_DIR"], "lib"))
import path_config
path_config._clear_cache()
path_config.ensure_dirs()
print("  ✓ state subdirs created")
'

# 2.7 Write ~/.preference-tracker.config.json to anchor PROJECT_ROOT.
# A hook's cwd at runtime may differ from install.sh's; the config anchor lets
# path_config not depend on the cwd each time.
#
# Overwrite this install's path_root / state / obs_log (not setdefault). With
# setdefault, a cross-project reinstall left the old project_root stuck so newly
# installed hooks always wrote to the old project's state dir. We assume the most
# recent install reflects the user's current project; other config fields
# (memory_dir, etc.) are preserved.
log ""
# Issue #1 fix (#2): do NOT pin project_root/state_dir/obs_log_dir in the GLOBAL
# ~/.preference-tracker.config.json by default. That single-file anchor is
# "last writer wins" — installing project B clobbers project A, and on a shared
# HOME multiple users collide. path_config resolves these per-project from the
# hook's cwd at runtime, so no global anchor is needed. Only pin when the user
# explicitly passed --state-dir; either way, clear any stale anchor left behind.
if [[ -n "${STATE_DIR_OVERRIDE}" ]]; then
    log "  anchoring to ~/.preference-tracker.config.json (explicit --state-dir):"
    _PT_CFG_ACTION=pin
else
    log "  per-project mode: not writing a global anchor (hooks resolve by project cwd); clearing any stale anchor:"
    _PT_CFG_ACTION=clean
fi
run env \
    B5_PROJECT_ROOT="${PROJECT_ROOT}" \
    B5_STATE_DIR="${STATE_DIR}" \
    B5_OBS_LOG_DIR="${OBS_LOG_DIR}" \
    PT_CFG_ACTION="${_PT_CFG_ACTION}" \
    PYTHONIOENCODING=utf-8 \
    python3 -c '
import json, os
config_path = os.path.expanduser("~/.preference-tracker.config.json")
config = {}
if os.path.exists(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {}
keys = ("project_root", "state_dir", "obs_log_dir")
if os.environ.get("PT_CFG_ACTION") == "pin":
    for k, ev in zip(keys, ("B5_PROJECT_ROOT", "B5_STATE_DIR", "B5_OBS_LOG_DIR")):
        config[k] = os.environ[ev]
    changed = True
else:
    changed = any(k in config for k in keys)
    for k in keys:
        config.pop(k, None)
if changed:
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("  ✓ config:", config_path)
else:
    print("  ✓ no global anchor (per-project cwd resolution)")
'

# 2.6 Copy seed memory (if absent), replacing the private session id with a generic seed marker.
log ""
log "  installing seed memory (if absent):"
run mkdir -p "${MEMORY_DIR}"
# Issue #1 fix (#5): memory holds your recorded preferences — tighten to 700 so
# it isn't world/group-readable on a shared HOME / multi-user box.
run chmod 700 "${MEMORY_DIR}" 2>/dev/null || true
SEED_SKIPPED=0
if [[ -d "${SKILL_DIR}/seed_memory" ]]; then
    for seed in "${SKILL_DIR}"/seed_memory/*.md; do
        if [[ ! -f "${seed}" ]]; then
            continue
        fi
        # seed_memory/README.md documents the (intentionally empty) seed dir —
        # it is not a rule file and must not be copied into the user's memory.
        if [[ "$(basename "${seed}")" == "README.md" ]]; then
            continue
        fi
        target="${MEMORY_DIR}/$(basename "${seed}")"
        if [[ -f "${target}" ]]; then
            log "  - $(basename "${seed}") already exists, skipping (cp -n)"
            SEED_SKIPPED=$((SEED_SKIPPED + 1))
        else
            # Replace the private originSessionId in seed memory with a generic seed marker.
            # Set PYTHONIOENCODING=utf-8 explicitly in case the user's terminal LANG isn't utf-8.
            run env PYTHONIOENCODING=utf-8 python3 -c "
import sys, re
src, dst = sys.argv[1], sys.argv[2]
with open(src, encoding='utf-8') as f:
    content = f.read()
content = re.sub(
    r'^originSessionId:\s*.*$',
    'originSessionId: seed-from-preference-tracker-skill-v1.0',
    content, flags=re.MULTILINE,
)
with open(dst, 'w', encoding='utf-8') as f:
    f.write(content)
" "${seed}" "${target}"
            log "  ✓ $(basename "${seed}") (originSessionId genericized)"
        fi
    done
    # If any seeds were skipped, warn that their frontmatter may be stale and missing a params: block.
    if [[ "${SEED_SKIPPED}" -gt 0 ]]; then
        log ""
        log "  ⚠ ${SEED_SKIPPED} seed memory file(s) already exist and were not overwritten (preserving your data)"
        log "    if they are an old version without a 'params:' block, adaptive thresholds fall back to code defaults"
        log "    to make the new threshold params explicit, copy the 'params:' section from ${SKILL_DIR}/seed_memory/*.md into your existing memory files"
    fi
else
    log "  - seed_memory/ does not exist, skipping (user already has memory)"
fi

# ============================================================
# Phase 3: Collect (verify log paths writable)
# ============================================================
log ""
log "[3/5] Collect: verify log paths are writable"
if [[ "${DRY_RUN}" == true ]]; then
    log "  [dry-run] skipping log path writability check (Phase 2 dir creation is skipped under dry-run, not a failure)"
else
    for d in "${OBS_LOG_DIR}" "${STATE_DIR}"; do
        if [[ ! -d "${d}" ]]; then
            log "❌ ${d} does not exist (Phase 2 should have created it)"
            exit 1
        fi
        test_file="${d}/.write_test_$$"
        if ! touch "${test_file}" 2>/dev/null; then
            log "❌ ${d} is not writable"
            exit 1
        fi
        rm -f "${test_file}"
    done
    log "  ✓ ${OBS_LOG_DIR} writable"
    log "  ✓ ${STATE_DIR} writable"
fi

# ============================================================
# Phase 4: Execute (run doctor)
# ============================================================
log ""
log "[4/5] Execute: run doctor.sh self-check"
if [[ -x "${SKILL_DIR}/doctor.sh" ]]; then
    run bash "${SKILL_DIR}/doctor.sh"
else
    log "  ⚠ doctor.sh missing / not executable (incomplete skill package). Running basic tests:"
    run python3 "${SKILL_DIR}/lib/test_path_config.py"
fi

# ============================================================
# Phase 5: Uninstall mechanism ready (verify uninstall.sh is executable)
# ============================================================
log ""
log "[5/5] Uninstall mechanism ready:"
if [[ -x "${SKILL_DIR}/uninstall.sh" ]]; then
    log "  ✓ uninstall.sh executable: bash ${SKILL_DIR}/uninstall.sh"
else
    log "  ⚠ ${SKILL_DIR}/uninstall.sh missing or not executable"
    log "    → chmod +x ${SKILL_DIR}/uninstall.sh"
    log "    or bash ${SKILL_DIR}/uninstall.sh (force via bash, no exec bit needed)"
fi

# Disable the trap (subsequent echo isn't an ERR)
trap - ERR

log ""
log "✅ Preference-Tracker installed"
log ""
log "Key paths:"
log "  - skill: ${SKILL_DIR}"
log "  - hooks: ${HOOKS_DIR}"
log "  - state: ${STATE_DIR}"
log "  - memory: ${MEMORY_DIR}"
log ""
log "Notes (to avoid confusion for new users):"
log "  - Default observe mode: record + remind only; no hard blocking, no LLM calls."
log "  - The shadow judge only runs in full mode, and its first run has cold-start latency (it spawns a claude -p"
log "    subprocess that may take tens of seconds to a few minutes) — this is not a hang or a broken install. Observe mode never runs it."
log "    To disable it entirely: export PT_SHADOW_DISABLED=1"
log ""
log "Next steps:"
log "  - Read README.md / FAQ.md"
log "  - Run dashboard.sh for a 7-day compliance summary"
log "  - Disable shadow judge: export PT_SHADOW_DISABLED=1 (recommended when no claude CLI)"
log "  - Disable deterministic: export PT_DETERMINISTIC_DISABLED=1"
log "  - Uninstall: bash ${SKILL_DIR}/uninstall.sh"
log ""
log "Install log: ${LOG_FILE}"
