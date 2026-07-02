#!/usr/bin/env bash
# install.sh — Post-install setup for tellonce Copilot CLI plugin.
#
# Run after `copilot plugin install YujunZhou/tellonce:copilot`
# to initialize state directories and seed memory if not already present.
#
# Usage:
#   bash <plugin_root>/install.sh [--project-root /path/to/project]
#
# Idempotent — safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PT_LIB="${SCRIPT_DIR}/lib"

# Parse optional --project-root / --mode (observe|enforce|full) / --python <path>
PROJECT_ROOT="$(pwd)"
MODE="observe"
PY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --project-root) PROJECT_ROOT="${2:-$PROJECT_ROOT}"; shift 2 || shift;;
        --mode)         MODE="${2:-observe}"; shift 2 || shift;;
        --python)       PY="${2:-}"; shift 2 || shift;;
        *)              echo "Unknown arg: $1 (flags: --project-root <dir> --mode observe|enforce|full --python <path>)"; exit 1;;
    esac
done

# Validate --mode up front (mirrors install.ps1's ValidateSet) — otherwise a
# typo'd mode only fails at step 6 and leaves a half-finished install.
case "${MODE}" in
    observe|enforce|full) : ;;
    *) echo "❌ Invalid --mode '${MODE}' (must be observe | enforce | full)"; exit 1;;
esac

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  tellonce — Copilot CLI plugin post-install        ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Plugin root:  ${SCRIPT_DIR}"
echo "Project root: ${PROJECT_ROOT}"
echo ""

# Hooks are registered as shell command strings (sh -c "<path>"), which needs
# the exec bit; restore it in case the transport (zip download, some tars)
# dropped it.
chmod +x "${SCRIPT_DIR}/hooks/"*.sh 2>/dev/null || true

# 1. Resolve Python 3.7+ (prefer --python from bootstrap, else python3, else python).
if [ -z "${PY}" ] || ! "${PY}" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,7) else 1)' 2>/dev/null; then
    PY=""
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,7) else 1)' 2>/dev/null; then PY="$c"; break; fi
    done
fi
if [ -z "${PY}" ]; then
    echo "❌ Python 3.7+ not found in PATH. Please install it."
    exit 1
fi
PYTHON_VER=$("${PY}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python ${PYTHON_VER} ($(command -v "${PY}" || echo "${PY}"))"

# 2. Verify jq (needed by bash hooks)
if command -v jq &>/dev/null; then
    echo "✅ jq found"
else
    echo "⚠️  jq not found — bash hooks will degrade gracefully (Windows uses Python directly)"
fi

# 3. Create state directories via path_config
echo ""
echo "Creating state directories..."
env B5_PROJECT_ROOT="${PROJECT_ROOT}" "${PY}" "${PT_LIB}/path_config.py"
env B5_PROJECT_ROOT="${PROJECT_ROOT}" PT_LIB_DIR="${PT_LIB}" "${PY}" -c "
import sys, os
sys.path.insert(0, os.environ['PT_LIB_DIR'])
import path_config
path_config.ensure_dirs()
print('✅ State directories created')
"

# 4. Seed memory (if not already present)
MEMORY_DIR=$(env B5_PROJECT_ROOT="${PROJECT_ROOT}" PT_LIB_DIR="${PT_LIB}" "${PY}" -c "
import sys, os
sys.path.insert(0, os.environ['PT_LIB_DIR'])
import path_config
print(path_config.get_memory_dir())
")

if [ -d "${MEMORY_DIR}" ] && [ "$(ls -A "${MEMORY_DIR}" 2>/dev/null)" ]; then
    echo "✅ Memory directory already has rules (${MEMORY_DIR})"
else
    echo "Seeding memory with starter rules..."
    mkdir -p "${MEMORY_DIR}"
    # Issue #1 fix (#5): memory holds your recorded preferences — tighten to 700
    # so it isn't world/group-readable on a shared HOME / multi-user box.
    chmod 700 "${MEMORY_DIR}" 2>/dev/null || true
    # seed_memory/README.md documents the (intentionally empty) seed dir — it
    # is not a rule file and must not be copied into the user's memory.
    for seed in "${SCRIPT_DIR}/seed_memory/"*.md; do
        [ -f "${seed}" ] || continue
        [ "$(basename "${seed}")" = "README.md" ] && continue
        cp -n "${seed}" "${MEMORY_DIR}/" 2>/dev/null || true
    done
    echo "✅ Seeded $(ls "${MEMORY_DIR}"/*.md 2>/dev/null | wc -l) rules"
fi

# 5. Write config file (retrieve defaults) + set mode switch automatically.
CONFIG_PATH="${HOME}/.tellonce.config.json"
if [ ! -f "${CONFIG_PATH}" ]; then
    echo ""
    echo "Writing default config to ${CONFIG_PATH}..."
    cat > "${CONFIG_PATH}" <<EOF
{
  "retrieve_cli": "copilot",
  "retrieve_backend": "progressive",
  "retrieve_model": "claude-haiku-4-5"
}
EOF
    echo "✅ Config written"
else
    echo "✅ Config already exists at ${CONFIG_PATH}"
    # Migration: older installs pinned `project_root` into the config, which
    # overrides per-cwd path resolution. Strip it so runtime falls back to cwd.
    "${PY}" - <<'PY' 2>/dev/null || true
import json, io, os
p = os.path.expanduser("~/.tellonce.config.json")
try:
    c = json.load(io.open(p, encoding="utf-8-sig"))
except Exception:
    raise SystemExit(0)
changed = []
if "project_root" in c:
    c.pop("project_root", None)
    changed.append("removed stale project_root")
# Upgrade the old shipped default backend to progressive (zero-LLM full-rule
# index; also fixes the SessionStart 0-rules gap). Only touch the value while
# it is still the old `cli` default — leave a deliberate keyword/api choice be.
if c.get("retrieve_backend") == "cli":
    c["retrieve_backend"] = "progressive"
    changed.append("retrieve_backend cli -> progressive")
if changed:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("✅ Migrated config: " + "; ".join(changed))
PY
fi

# Set the on/off switch for the user automatically (no hand-editing).
echo ""
echo "Setting mode = ${MODE} ..."
# No >/dev/null: pt_mode prints important hints (e.g. the PT_SHADOW_RULE_IDS
# reminder for full mode).
"${PY}" "${PT_LIB}/pt_mode.py" "${MODE}" && echo "✅ Mode set to ${MODE}"

# Register the plugin with Copilot so its hooks load (side-load installs only;
# `copilot plugin install` already does this). Idempotent + backs up config.json.
REGISTERED=false
case "$(echo "${SCRIPT_DIR}" | tr '[:upper:]' '[:lower:]')" in
    *installed-plugins*)
        echo ""
        echo "Registering plugin with Copilot (so hooks load)..."
        "${PY}" "${PT_LIB}/register_plugin.py" && REGISTERED=true || true
        echo "  (restart Copilot to load the hooks)"
        ;;
    *)
        echo ""
        echo "[NOTE] Not under installed-plugins; skipping auto-registration."
        echo "       Install via 'copilot plugin install' OR run this from the copied"
        echo "       plugin dir under ~/.copilot/installed-plugins for hooks to load."
        ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ Installation complete!"
echo ""
if [ "${REGISTERED}" = true ]; then
    echo "The plugin hooks load for any Copilot CLI session after you restart Copilot."
else
    echo "[!] Hooks are NOT active yet — the plugin was not registered (see the NOTE above)."
fi
echo "Current mode = ${MODE}"
echo ""
echo "observe = records preferences + reminds you (safe default; never"
echo "          hard-blocks, never calls an LLM)."
echo "enforce = also hard-blocks replies that violate your saved rules."
echo "full    = enforce + an LLM 'shadow judge' (sends the conversation to"
echo "          copilot -p; redacts secrets first)."
echo ""
echo "Change mode anytime with ONE command (copy-paste):"
echo "  python3 \"${PT_LIB}/pt_mode.py\" enforce     # turn on hard blocking"
echo "  python3 \"${PT_LIB}/pt_mode.py\" full        # blocking + AI judge"
echo "  python3 \"${PT_LIB}/pt_mode.py\" observe     # back to safe default"
echo "  python3 \"${PT_LIB}/pt_mode.py\" status      # show current mode"
echo ""
echo "State will be written to: ${PROJECT_ROOT}/.copilot/tellonce-state/"
echo "Memory rules live at:     ${MEMORY_DIR}"
echo "════════════════════════════════════════════════════════════════"
