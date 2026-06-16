#!/usr/bin/env bash
# Tellonce dashboard - show recent shadow-judge alerts (when the shadow judge is on).
#
# Usage:
#   bash ~/.claude/skills/tellonce/dashboard.sh
#
# The shadow judge is opt-in (PT_SHADOW=1); with it off this shows nothing.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Tellonce dashboard"
echo "-----------------------------------------"
echo "Recent shadow alerts (latest 3 rolling cap):"
SHADOW_ALERT_MD=$(env PT_LIB="${SKILL_DIR}/lib" PYTHONIOENCODING=utf-8 python3 -c '
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
import path_config
print(path_config.get_shadow_alert_md_path())
' 2>/dev/null)
if [[ -f "${SHADOW_ALERT_MD}" ]]; then
    head -30 "${SHADOW_ALERT_MD}"
else
    echo "  (no alerts in last 24h, or shadow disabled)"
fi