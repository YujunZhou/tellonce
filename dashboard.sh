#!/usr/bin/env bash
# Preference-Tracker dashboard — 跑最近 N 天 compliance summary.
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/dashboard.sh [--days N]
#
# 默认 N=7. 输出 deterministic block / shadow violation / cost / latency 摘要.

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
            echo "  --days N    天数 (default 7)"
            echo "  --json      JSON 输出 (machine-parseable, M5 fix)"
            exit 0 ;;
        *) shift ;;
    esac
done

SKILL_DIR="${HOME}/.claude/skills/preference-tracker"

if [[ "${JSON_MODE}" == true ]]; then
    # M5 fix (Phase 8 minor): JSON output for tooling integration
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
# H14 fix: env-channel argv to avoid breaking when SKILL_DIR contains '
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

# I6 fix (Phase 8 review): 加 superseded memory archive advisory
echo ""
echo "─────────────────────────────────────────"
echo "Superseded memory archive (advisory):"
python3 "${SKILL_DIR}/lib/auto_retire_superseded.py" --dry-run 2>/dev/null | tail -10 || \
    echo "  (auto_retire_superseded 跑失败 / 没 superseded 文件)"
echo ""
echo "  实跑 archive: python3 ${SKILL_DIR}/lib/auto_retire_superseded.py"
echo ""
echo "─────────────────────────────────────────"
echo "Threshold suggestions (Phase 7 完整版 advisor):"
# H14 fix: env-channel argv (single-quote-safe).
LATEST_THRESHOLD_MD=$(env PT_LIB="${SKILL_DIR}/lib" PYTHONIOENCODING=utf-8 python3 -c '
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
import threshold_advisor
print(threshold_advisor.latest_suggestion_path())
' 2>/dev/null)
if [[ -n "${LATEST_THRESHOLD_MD}" && -f "${LATEST_THRESHOLD_MD}" ]]; then
    head -20 "${LATEST_THRESHOLD_MD}"
else
    echo "  (no threshold suggestions yet — 跑 'python3 ${SKILL_DIR}/lib/threshold_advisor.py' 生成)"
fi
