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

# Parse optional --project-root
PROJECT_ROOT="${1:-$(pwd)}"
if [ "${1:-}" = "--project-root" ] && [ -n "${2:-}" ]; then
    PROJECT_ROOT="$2"
fi

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

# 5. Write config file (if not exists)
CONFIG_PATH="${HOME}/.preference-tracker.config.json"
if [ ! -f "${CONFIG_PATH}" ]; then
    echo ""
    echo "Writing default config to ${CONFIG_PATH}..."
    cat > "${CONFIG_PATH}" <<EOF
{
  "project_root": "${PROJECT_ROOT}",
  "retrieve_cli": "copilot",
  "retrieve_backend": "cli",
  "retrieve_model": "claude-haiku-4-5"
}
EOF
    echo "✅ Config written"
else
    echo "✅ Config already exists at ${CONFIG_PATH}"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ Installation complete!"
echo ""
echo "The plugin hooks are now active for any Copilot CLI session."
echo "State will be written to: ${PROJECT_ROOT}/.copilot/preference-tracker-state/"
echo "Memory rules live at:     ${MEMORY_DIR}"
echo ""
echo "To verify: run 'copilot' in your project and check that the"
echo "Gate Function (SCAN/RECORD/CONFIRM) fires on Stop events."
echo "════════════════════════════════════════════════════════════════"
