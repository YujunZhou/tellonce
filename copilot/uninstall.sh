#!/usr/bin/env bash
# uninstall.sh — ONE-COMMAND uninstaller for tellonce (GitHub Copilot CLI, macOS/Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/uninstall.sh | bash
#
# WHY THIS EXISTS: the hooks keep firing as long as the plugin is REGISTERED in
# ~/.copilot/config.json — deleting the files alone is not enough. This removes
# the registration FIRST (so hooks stop firing) and then the plugin files.
# Your recorded memory/preferences are KEPT. Run with --purge to also delete
# state + memory + the config mode keys:
#   curl -fsSL .../copilot/uninstall.sh | bash -s -- --purge

set -uo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

COPILOT_HOME="${HOME}/.copilot"
PLUGIN_PARENT="${COPILOT_HOME}/installed-plugins/tellonce"
PLUGIN="${PLUGIN_PARENT}/tellonce"

echo "================================================================"
echo "  tellonce — one-command uninstaller (Copilot CLI)"
echo "================================================================"

# Find python3/python.
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        if "$c" -c 'import sys; assert sys.version_info >= (3, 7)' >/dev/null 2>&1; then
            PY="$(command -v "$c")"; break
        fi
    fi
done

# 1. Remove the hook registration (+ optional purge) while files still exist.
UNREG=0
if [ -n "$PY" ] && [ -f "${PLUGIN}/lib/uninstall.py" ]; then
    if [ "$PURGE" = "1" ]; then
        echo "Removing hook registration + state + memory..."
        "$PY" "${PLUGIN}/lib/uninstall.py" --all || true
    else
        echo "Removing hook registration..."
        "$PY" "${PLUGIN}/lib/uninstall.py" --unregister --reset-config || true
    fi
    UNREG=1
elif [ -n "$PY" ] && [ -f "${PLUGIN}/lib/register_plugin.py" ]; then
    echo "Removing hook registration..."
    "$PY" "${PLUGIN}/lib/register_plugin.py" --unregister || true
    UNREG=1
fi
if [ "$UNREG" = "0" ]; then
    echo "[i] Could not run the in-plugin uninstaller (python or plugin missing)."
    echo "    Manually remove the 'tellonce' entry from ~/.copilot/config.json installedPlugins."
fi

# 2. Remove the plugin files.
if [ -d "$PLUGIN_PARENT" ]; then
    rm -rf "$PLUGIN_PARENT"
    echo "Removed plugin files: ${PLUGIN_PARENT}"
fi

echo ""
echo "================================================================"
echo "[OK] tellonce uninstalled."
echo "  >> RESTART Copilot so the hooks fully unload. <<"
if [ "$PURGE" = "0" ]; then
    echo "  Your saved memory/preferences were kept. To remove those too,"
    echo "  re-run with: curl -fsSL .../copilot/uninstall.sh | bash -s -- --purge"
fi
echo "================================================================"
