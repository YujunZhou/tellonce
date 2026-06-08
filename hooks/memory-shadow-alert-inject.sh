#!/usr/bin/env bash
# memory-shadow-alert-inject.sh — UserPromptSubmit hook (soft inject from shadow alert)
# Read B5_SHADOW_ALERT.md (set by shadow judge), inject "last turn you violated X" notice
# into next-turn additionalContext.
# Set B5_INJECT_DISABLED=1 to opt out.
# 24h TTL on alerts.
# Defensive: any error → exit 0 silently.

_PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"

exec python3 "${_PT_LIB}/shadow_alert_inject.py"
