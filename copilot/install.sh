#!/usr/bin/env bash
# install.sh — Post-install setup for preference-tracker Copilot CLI plugin.
#
# Run after `copilot plugin install YujunZhou/preference-tracker:copilot`
# to initialize state directories and seed memory if not already present.
#
# Usage:
#   bash <plugin_root>/install.sh [--project-root /path/to/project]
#
# Idempotent — safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PT_LIB="${SCRIPT_DIR}/lib"

# Parse optional --project-root / --mode (observe|enforce|full)
PROJECT_ROOT="$(pwd)"
MODE="observe"
while [ $# -gt 0 ]; do
    case "$1" in
        --project-root) PROJECT_ROOT="${2:-$PROJECT_ROOT}"; shift 2 || shift;;
        --mode)         MODE="${2:-observe}"; shift 2 || shift;;
        *)              shift;;
    esac
done

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  preference-tracker — Copilot CLI plugin post-install        ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Plugin root:  ${SCRIPT_DIR}"
echo "Project root: ${PROJECT_ROOT}"
echo ""

# 1. Verify Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ python3 not found in PATH. Please install Python 3.7+."
    exit 1
fi
PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python ${PYTHON_VER}"

# 2. Verify jq (needed by bash hooks)
if command -v jq &>/dev/null; then
    echo "✅ jq found"
else
    echo "⚠️  jq not found — bash hooks will degrade gracefully (Windows uses Python directly)"
fi

# 3. Create state directories via path_config
echo ""
echo "Creating state directories..."
env B5_PROJECT_ROOT="${PROJECT_ROOT}" python3 "${PT_LIB}/path_config.py"
env B5_PROJECT_ROOT="${PROJECT_ROOT}" python3 -c "
import sys, os
sys.path.insert(0, '${PT_LIB}')
import path_config
path_config.ensure_dirs()
print('✅ State directories created')
"

# 4. Seed memory (if not already present)
MEMORY_DIR=$(env B5_PROJECT_ROOT="${PROJECT_ROOT}" python3 -c "
import sys, os
sys.path.insert(0, '${PT_LIB}')
import path_config
print(path_config.get_memory_dir())
")

if [ -d "${MEMORY_DIR}" ] && [ "$(ls -A "${MEMORY_DIR}" 2>/dev/null)" ]; then
    echo "✅ Memory directory already has rules (${MEMORY_DIR})"
else
    echo "Seeding memory with starter rules..."
    mkdir -p "${MEMORY_DIR}"
    cp -n "${SCRIPT_DIR}/seed_memory/"*.md "${MEMORY_DIR}/" 2>/dev/null || true
    echo "✅ Seeded $(ls "${MEMORY_DIR}"/*.md 2>/dev/null | wc -l) rules"
fi

# 5. Write config file (retrieve defaults) + set mode switch automatically.
CONFIG_PATH="${HOME}/.preference-tracker.config.json"
if [ ! -f "${CONFIG_PATH}" ]; then
    echo ""
    echo "Writing default config to ${CONFIG_PATH}..."
    cat > "${CONFIG_PATH}" <<EOF
{
  "retrieve_cli": "copilot",
  "retrieve_backend": "cli",
  "retrieve_model": "claude-haiku-4-5"
}
EOF
    echo "✅ Config written"
else
    echo "✅ Config already exists at ${CONFIG_PATH}"
    # Migration: older installs pinned `project_root` into the config, which
    # overrides per-cwd path resolution. Strip it so runtime falls back to cwd.
    python3 - <<'PY' 2>/dev/null || true
import json, io, os
p = os.path.expanduser("~/.preference-tracker.config.json")
try:
    c = json.load(io.open(p, encoding="utf-8-sig"))
except Exception:
    raise SystemExit(0)
if "project_root" in c:
    c.pop("project_root", None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("✅ Migrated config: removed stale project_root")
PY
fi

# Set the on/off switch for the user automatically (no hand-editing).
echo ""
echo "Setting mode = ${MODE} ..."
python3 "${PT_LIB}/pt_mode.py" "${MODE}" >/dev/null && echo "✅ Mode set to ${MODE}"

# Register the plugin with Copilot so its hooks load (side-load installs only;
# `copilot plugin install` already does this). Idempotent + backs up config.json.
case "$(echo "${SCRIPT_DIR}" | tr '[:upper:]' '[:lower:]')" in
    *installed-plugins*)
        echo ""
        echo "Registering plugin with Copilot (so hooks load)..."
        python3 "${PT_LIB}/register_plugin.py" || true
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
echo "The plugin hooks are now active for any Copilot CLI session."
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
echo "State will be written to: ${PROJECT_ROOT}/.copilot/preference-tracker-state/"
echo "Memory rules live at:     ${MEMORY_DIR}"
echo "════════════════════════════════════════════════════════════════"
