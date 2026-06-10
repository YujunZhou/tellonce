#!/usr/bin/env bash
# Preference-Tracker dashboard — show a compliance summary for the last N days.
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/dashboard.sh [--days N]
#
# Default N=7. Outputs deterministic block / shadow violation / cost / latency summary.

set -euo pipefail

DAYS=7
JSON_MODE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days) DAYS="$2"; shift 2 ;;
        --json) JSON_MODE=true; shift ;;
        --help|-h)
            sed -n '2,9p' "$0"
            echo ""
            echo "Flags:"
            echo "  --days N    number of days (default 7)"
            echo "  --json      JSON output (machine-parseable)"
            exit 0 ;;
        *) echo "Unknown arg: $1 (try --help)"; exit 1 ;;
    esac
done

# Self-locate (same as install.sh/doctor.sh/uninstall.sh).
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${JSON_MODE}" == true ]]; then
    # JSON output for tooling integration
    python3 "${SKILL_DIR}/lib/analyze_b5_compliance.py" --days "${DAYS}" --json 2>/dev/null \
        || python3 "${SKILL_DIR}/lib/analyze_b5_compliance.py" --days "${DAYS}"
    exit 0
fi

echo "Preference-Tracker dashboard (last ${DAYS}d)"
echo "─────────────────────────────────────────"
python3 "${SKILL_DIR}/lib/analyze_b5_compliance.py" --days "${DAYS}"

echo ""
echo "─────────────────────────────────────────"
echo "Recent shadow alerts (latest 3 rolling cap):"
# env-channel argv to avoid breaking when SKILL_DIR contains '
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

# Add superseded memory archive advisory
echo ""
echo "─────────────────────────────────────────"
echo "Superseded memory archive (advisory):"
python3 "${SKILL_DIR}/lib/auto_retire_superseded.py" --dry-run 2>/dev/null | tail -10 || \
    echo "  (auto_retire_superseded failed / no superseded files)"
echo ""
echo "  Run archive for real: python3 ${SKILL_DIR}/lib/auto_retire_superseded.py"
echo ""
echo "─────────────────────────────────────────"
echo "Threshold suggestions (full advisor):"
# env-channel argv (single-quote-safe).
LATEST_THRESHOLD_MD=$(env PT_LIB="${SKILL_DIR}/lib" PYTHONIOENCODING=utf-8 python3 -c '
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
import threshold_advisor
print(threshold_advisor.latest_suggestion_path())
' 2>/dev/null)
if [[ -n "${LATEST_THRESHOLD_MD}" && -f "${LATEST_THRESHOLD_MD}" ]]; then
    head -20 "${LATEST_THRESHOLD_MD}"
else
    echo "  (no threshold suggestions yet — run 'python3 ${SKILL_DIR}/lib/threshold_advisor.py' to generate)"
fi
